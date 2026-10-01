"""PostgreSQL support: the stdlib wire-protocol client (web/pgwire.py) and the
Hostinfo it feeds the pages when Headscale runs on PostgreSQL.

The client is tested against a fake server on a local socket: recorded
protocol messages for the plain flow, a real SCRAM-SHA-256 server side
(proofs checked) for authentication, plus the RFC 7677 and RFC 4013 vectors.
Standard library only:

    python3 tests/test_postgresql.py
"""
import base64
import hashlib
import hmac
import json
import secrets
import os
import socket
import struct
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_security import MEMBER, B, BOB, BOB_NODE, Base, request  # noqa: E402  (sets the env, imports app)

import headscale as hs  # noqa: E402
import pgwire  # noqa: E402

# A random password per run for the fake servers (not a real credential)
SECRET = secrets.token_hex(12)

# -----------------------------------------------------------------------------
# Recorded backend messages (bytes as a PostgreSQL 17 server sends them)
# -----------------------------------------------------------------------------

AUTH_OK = b"R\x00\x00\x00\x08\x00\x00\x00\x00"
PARAM_VERSION = b"S\x00\x00\x00\x18server_version\x0017.6\x00"
BACKEND_KEY = b"K\x00\x00\x00\x0c\x00\x00\x30\x39\x12\x34\x56\x78"
READY_IDLE = b"Z\x00\x00\x00\x05I"
# SELECT id, host_info, endpoints FROM nodes ... -> (1, '{"OS":"linux"}', NULL)
ROW_DESCRIPTION = (
    b"T\x00\x00\x00\x53\x00\x03"
    b"id\x00" + b"\x00\x00\x40\x01\x00\x01\x00\x00\x00\x14\x00\x08\xff\xff\xff\xff\x00\x00"
    b"host_info\x00" + b"\x00\x00\x40\x01\x00\x0c\x00\x00\x00\x19\xff\xff\xff\xff\xff\xff\x00\x00"
    b"endpoints\x00" + b"\x00\x00\x40\x01\x00\x0d\x00\x00\x00\x19\xff\xff\xff\xff\xff\xff\x00\x00"
)
DATA_ROW = b"D\x00\x00\x00\x21\x00\x03" b"\x00\x00\x00\x011" b"\x00\x00\x00\x0e{\"OS\":\"linux\"}" b"\xff\xff\xff\xff"
COMMAND_COMPLETE = b"C\x00\x00\x00\x0dSELECT 1\x00"
QUERY_SQL = "SELECT id, host_info, endpoints FROM nodes WHERE id IN (1)"


def error_response(code: str, message: str) -> bytes:
    body = b"SERROR\x00" + b"C" + code.encode() + b"\x00" + b"M" + message.encode() + b"\x00\x00"
    return b"E" + struct.pack("!i", len(body) + 4) + body


def message(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("!i", len(body) + 4) + body


def auth(code: int, payload: bytes = b"") -> bytes:
    return message(b"R", struct.pack("!i", code) + payload)


# -----------------------------------------------------------------------------
# Fake server
# -----------------------------------------------------------------------------

class Peer:
    """The server side of one client connection."""

    def __init__(self, conn: socket.socket):
        self.conn, self.buf = conn, b""

    def recv_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.conn.recv(65536)
            if not chunk:
                raise ConnectionError("client closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def startup(self) -> dict:
        """Read the startup packet (answering 'N' to a TLS request first)."""
        length, code = struct.unpack("!ii", self.recv_exact(8))
        if code == pgwire.SSL_REQUEST_CODE:
            self.conn.sendall(b"N")
            length, code = struct.unpack("!ii", self.recv_exact(8))
        assert code == pgwire.PROTOCOL_V3, code
        parts = self.recv_exact(length - 8).split(b"\0")
        return {parts[i].decode(): parts[i + 1].decode() for i in range(0, len(parts) - 2, 2)}

    def read(self) -> tuple[bytes, bytes]:
        kind = self.recv_exact(1)
        length = struct.unpack("!i", self.recv_exact(4))[0]
        return kind, self.recv_exact(length - 4)

    def send(self, *chunks: bytes) -> None:
        self.conn.sendall(b"".join(chunks))

    def ready(self) -> None:
        self.send(AUTH_OK, PARAM_VERSION, BACKEND_KEY, READY_IDLE)


class FakeServer:
    """Runs `script(peer)` for each connection, in a thread. Any assertion
    failing in the server side is re-raised by stop()."""

    def __init__(self, script):
        self.script, self.errors, self.seen = script, [], []
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        self.sock.settimeout(5)
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            try:
                self.script(Peer(conn), self)
            except ConnectionError:
                pass
            except Exception as exc:  # noqa: BLE001 - reported by stop()
                self.errors.append(exc)

    def stop(self):
        self.thread.join(5)
        self.sock.close()
        if self.errors:
            raise self.errors[0]

    def kwargs(self, **extra) -> dict:
        settings = dict(host="127.0.0.1", port=self.port, user="headscale_ro", password=SECRET,
                        database="headscale", sslmode="disable", timeout=5)
        return {**settings, **extra}


def answer_query(peer: Peer, server: FakeServer) -> None:
    kind, body = peer.read()
    assert kind == b"Q", kind
    server.seen.append(body.rstrip(b"\0").decode())
    peer.send(ROW_DESCRIPTION, DATA_ROW, COMMAND_COMPLETE, READY_IDLE)


def expect_terminate(peer: Peer) -> None:
    kind, _ = peer.read()
    assert kind == b"X", kind


def scram_server(password: str, *, tamper_signature: bool = False, skip_final: bool = False):
    """Server side of SCRAM-SHA-256: checks the client's proof for real."""
    salt, iterations = b"headscale-easy-salt", 4096

    def run(peer: Peer, server: FakeServer) -> None:
        server.startup = peer.startup()
        peer.send(auth(10, b"SCRAM-SHA-256-PLUS\0SCRAM-SHA-256\0\0"))
        kind, body = peer.read()
        assert kind == b"p"
        mech_end = body.index(b"\0")
        assert body[:mech_end] == b"SCRAM-SHA-256"
        size = struct.unpack("!i", body[mech_end + 1:mech_end + 5])[0]
        client_first = body[mech_end + 5:mech_end + 5 + size].decode()
        assert client_first.startswith("n,,n=,r="), client_first
        bare = client_first[3:]
        nonce = bare.split("r=", 1)[1] + "server-nonce"
        server_first = f"r={nonce},s={base64.b64encode(salt).decode()},i={iterations}"
        peer.send(auth(11, server_first.encode()))

        kind, body = peer.read()
        client_final = body.decode()
        without_proof, _, proof = client_final.rpartition(",p=")
        assert without_proof == f"c=biws,r={nonce}"
        salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        stored = hashlib.sha256(client_key).digest()
        auth_message = f"{bare},{server_first},{without_proof}".encode()
        signature = hmac.new(stored, auth_message, hashlib.sha256).digest()
        recovered = bytes(a ^ b for a, b in zip(base64.b64decode(proof), signature))
        if hashlib.sha256(recovered).digest() != stored:
            peer.send(error_response("28P01", 'password authentication failed for user "headscale_ro"'))
            return
        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        server_sig = hmac.new(server_key, auth_message, hashlib.sha256).digest()
        if tamper_signature:
            server_sig = bytes(32)
        if not skip_final:
            peer.send(auth(12, b"v=" + base64.b64encode(server_sig)))
        peer.ready()
        answer_query(peer, server)
        expect_terminate(peer)

    return run


# -----------------------------------------------------------------------------
# SCRAM, SASLprep, MD5
# -----------------------------------------------------------------------------

class Scram(unittest.TestCase):
    # RFC 7677, section 3
    NONCE = "rOprNGfwEbeRWgbNEkqO"
    SERVER_FIRST = "r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096"
    CLIENT_FINAL = ("c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
                    "p=dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    SERVER_FINAL = "v=6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="

    def scram(self):
        return pgwire.ScramSHA256("pencil", username="user", nonce=self.NONCE)

    def test_rfc7677_vector(self):
        s = self.scram()
        self.assertEqual(s.client_first(), "n,,n=user,r=rOprNGfwEbeRWgbNEkqO")
        self.assertEqual(s.client_final(self.SERVER_FIRST), self.CLIENT_FINAL)
        s.verify_server_final(self.SERVER_FINAL)   # does not raise

    def test_wrong_server_signature_is_rejected(self):
        s = self.scram()
        s.client_final(self.SERVER_FIRST)
        with self.assertRaises(pgwire.PgError):
            s.verify_server_final("v=" + base64.b64encode(bytes(32)).decode())
        with self.assertRaises(pgwire.PgError):
            s.verify_server_final("e=invalid-proof")

    def test_server_nonce_must_extend_the_client_nonce(self):
        for first in ("r=someone-else,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096",
                      f"r={self.NONCE},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096"):
            with self.assertRaises(pgwire.PgError):
                self.scram().client_final(first)

    def test_absurd_iteration_counts_are_refused(self):
        for i in ("0", "100000000", "x"):
            with self.assertRaises(pgwire.PgError):
                self.scram().client_final(f"r={self.NONCE}abc,s=W22ZaJ0SNY7soEsUEjb6gQ==,i={i}")

    def test_user_name_is_escaped(self):
        s = pgwire.ScramSHA256("x", username="a=b,c", nonce="n")
        self.assertEqual(s.client_first(), "n,,n=a=3Db=2Cc,r=n")

    def test_saslprep_rfc4013_examples(self):
        self.assertEqual(pgwire.saslprep("I­X"), "IX")
        self.assertEqual(pgwire.saslprep("user"), "user")
        self.assertEqual(pgwire.saslprep("USER"), "USER")
        self.assertEqual(pgwire.saslprep("ª"), "a")
        self.assertEqual(pgwire.saslprep("Ⅸ"), "IX")
        self.assertEqual(pgwire.saslprep("a b"), "a b")
        for bad in ("\u0007", "ا1"):
            with self.assertRaises(ValueError):
                pgwire.saslprep(bad)

    def test_unpreparable_password_is_used_raw(self):
        # Like PostgreSQL: a password SASLprep rejects is used as it is
        self.assertEqual(pgwire.ScramSHA256("a\u0007b").password, "a\u0007b".encode())

    def test_md5_password(self):
        inner = hashlib.md5((SECRET + "headscale_ro").encode()).hexdigest()
        expected = "md5" + hashlib.md5(inner.encode() + b"\x01\x02\x03\x04").hexdigest()
        self.assertEqual(pgwire.md5_password("headscale_ro", SECRET, b"\x01\x02\x03\x04"), expected)
        self.assertEqual(len(expected), 35)


# -----------------------------------------------------------------------------
# The client against the fake server
# -----------------------------------------------------------------------------

class Client(unittest.TestCase):
    def serve(self, script) -> FakeServer:
        server = FakeServer(script)
        self.addCleanup(server.sock.close)
        return server

    def test_recorded_session(self):
        def script(peer, server):
            server.startup = peer.startup()
            peer.ready()
            answer_query(peer, server)
            expect_terminate(peer)

        server = self.serve(script)
        with pgwire.Connection(**server.kwargs()) as con:
            rows = con.query(QUERY_SQL)
            self.assertEqual(con.columns, ["id", "host_info", "endpoints"])
            self.assertEqual(con.parameters["server_version"], "17.6")
        server.stop()
        self.assertEqual(rows, [("1", '{"OS":"linux"}', None)])
        self.assertEqual(server.seen, [QUERY_SQL])
        self.assertEqual(server.startup["user"], "headscale_ro")
        self.assertEqual(server.startup["database"], "headscale")
        self.assertEqual(server.startup["default_transaction_read_only"], "on")

    def test_scram_sha_256(self):
        server = self.serve(scram_server(SECRET))
        self.assertEqual(pgwire.query(QUERY_SQL, **server.kwargs()), [("1", '{"OS":"linux"}', None)])
        server.stop()

    def test_wrong_password(self):
        server = self.serve(scram_server("something else"))
        with self.assertRaises(pgwire.PgError) as cm:
            pgwire.query(QUERY_SQL, **server.kwargs())
        server.stop()
        self.assertEqual(cm.exception.code, "28P01")
        self.assertIn("password authentication failed", str(cm.exception))

    def test_server_that_does_not_know_the_password_is_detected(self):
        server = self.serve(scram_server(SECRET, tamper_signature=True))
        with self.assertRaisesRegex(pgwire.PgError, "signature"):
            pgwire.query(QUERY_SQL, **server.kwargs())
        server.stop()

    def test_server_skipping_the_scram_final_message_is_rejected(self):
        server = self.serve(scram_server(SECRET, skip_final=True))
        with self.assertRaisesRegex(pgwire.PgError, "skipped"):
            pgwire.query(QUERY_SQL, **server.kwargs())
        server.stop()

    def test_md5(self):
        def script(peer, server):
            peer.startup()
            peer.send(auth(5, b"\x01\x02\x03\x04"))
            kind, body = peer.read()
            assert kind == b"p"
            assert body == pgwire.md5_password("headscale_ro", SECRET, b"\x01\x02\x03\x04").encode() + b"\0"
            peer.ready()
            answer_query(peer, server)
            expect_terminate(peer)

        server = self.serve(script)
        self.assertEqual(len(pgwire.query(QUERY_SQL, **server.kwargs())), 1)
        server.stop()

    def test_unsupported_authentication(self):
        def script(peer, server):
            peer.startup()
            peer.send(auth(7))           # GSSAPI

        server = self.serve(script)
        with self.assertRaisesRegex(pgwire.PgError, "unsupported authentication"):
            pgwire.query(QUERY_SQL, **server.kwargs())
        server.stop()

    def test_query_error_keeps_the_connection_usable(self):
        def script(peer, server):
            peer.startup()
            peer.ready()
            peer.read()
            peer.send(error_response("42501", "permission denied for table nodes"), READY_IDLE)
            answer_query(peer, server)
            expect_terminate(peer)

        server = self.serve(script)
        with pgwire.Connection(**server.kwargs()) as con:
            with self.assertRaises(pgwire.PgError) as cm:
                con.query("SELECT key FROM pre_auth_keys")
            self.assertEqual(cm.exception.code, "42501")
            self.assertEqual(len(con.query(QUERY_SQL)), 1)
        server.stop()

    def test_tls_required_but_refused(self):
        def script(peer, server):
            length, code = struct.unpack("!ii", peer.recv_exact(8))
            assert code == pgwire.SSL_REQUEST_CODE
            peer.send(b"N")

        server = self.serve(script)
        with self.assertRaisesRegex(pgwire.PgError, "TLS"):
            pgwire.query(QUERY_SQL, **server.kwargs(sslmode="require"))
        server.stop()

    def test_tls_preferred_falls_back_to_plain(self):
        def script(peer, server):
            peer.startup()             # answers 'N' to the TLS request
            peer.ready()
            answer_query(peer, server)
            expect_terminate(peer)

        server = self.serve(script)
        with pgwire.Connection(**server.kwargs(sslmode="prefer")) as con:
            self.assertFalse(con.encrypted)
            self.assertEqual(len(con.query(QUERY_SQL)), 1)
        server.stop()

    def test_oversized_message_is_refused(self):
        def script(peer, server):
            peer.startup()
            peer.send(b"R" + struct.pack("!i", pgwire.MAX_MESSAGE + 1))

        server = self.serve(script)
        with self.assertRaisesRegex(pgwire.PgError, "length"):
            pgwire.query(QUERY_SQL, **server.kwargs())
        server.stop()

    def test_bad_sslmode(self):
        with self.assertRaises(ValueError):
            pgwire.Connection("127.0.0.1", user="x", sslmode="allow")


# -----------------------------------------------------------------------------
# Hostinfo for the pages
# -----------------------------------------------------------------------------

HOSTINFO = {"OS": "linux", "Distro": "debian", "DistroVersion": "12", "IPNVersion": "1.80.0-t"}


def hostinfo_server(peer, server):
    peer.startup()
    peer.ready()
    kind, body = peer.read()
    server.seen.append(body.rstrip(b"\0").decode())
    info = json.dumps(HOSTINFO).encode()
    endpoints = b'["203.0.113.7:41641"]'
    row = (struct.pack("!h", 3) + struct.pack("!i", 1) + b"7" + struct.pack("!i", len(info)) + info
           + struct.pack("!i", len(endpoints)) + endpoints)
    peer.send(ROW_DESCRIPTION, message(b"D", row), COMMAND_COMPLETE, READY_IDLE)
    expect_terminate(peer)


class HostDetails(Base):
    def use_postgres(self, server: FakeServer | None, port: int | None = None):
        settings = dict(hs.HEADSCALE_PG, host="127.0.0.1", port=port or server.port, sslmode="disable")
        for p in (mock.patch.object(hs, "HEADSCALE_DB_TYPE", "postgres"),
                  mock.patch.object(hs, "HEADSCALE_PG", settings)):
            p.start()
            self.addCleanup(p.stop)

    def test_reads_hostinfo_from_postgres(self):
        server = FakeServer(hostinfo_server)
        self.addCleanup(server.sock.close)
        self.use_postgres(server)
        details = hs.host_details(["7", "9"])
        server.stop()
        self.assertEqual(server.seen, ["SELECT id, host_info, endpoints FROM nodes WHERE id IN (7,9)"])
        self.assertEqual(details, {"7": {"hostinfo": HOSTINFO, "endpoints": ["203.0.113.7:41641"]}})

    def test_ids_must_be_integers(self):
        self.use_postgres(None, port=1)
        with self.assertRaises(ValueError):
            hs.host_details(["7) OR (1=1"])

    def test_unreachable_database_gives_empty_details(self):
        with socket.create_server(("127.0.0.1", 0)) as s:
            port = s.getsockname()[1]
        self.use_postgres(None, port=port)            # nothing listens there now
        with self.assertLogs("headscale-easy", "WARNING"):
            self.assertEqual(hs.host_details(["7"]), {})

    def test_sqlite_is_still_the_default(self):
        with mock.patch.object(hs, "HEADSCALE_DB", "/nonexistent/db.sqlite"), \
             mock.patch.object(pgwire, "query", side_effect=AssertionError("PostgreSQL used")):
            self.assertEqual(hs.host_details(["7"]), {})

    def test_machine_page_shows_the_os_from_postgres(self):
        server = FakeServer(hostinfo_server)
        self.addCleanup(server.sock.close)
        self.use_postgres(server)
        with mock.patch.object(hs, "owned_node", lambda user, nid: BOB_NODE if user is BOB and nid == "7" else None), \
             mock.patch.object(hs, "dns_config", lambda: {}), \
             mock.patch.object(hs, "latest_tailscale_version", lambda: ""), \
             mock.patch.object(hs, "derp_regions", lambda: {}):
            status, _, body = request("GET", f"{B}/machines/7", MEMBER)
        server.stop()
        self.assertEqual(status, 200)
        self.assertIn("Debian 12", body)
        self.assertIn("1.80.0", body)


if __name__ == "__main__":
    unittest.main()
