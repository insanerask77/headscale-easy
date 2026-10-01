"""Minimal PostgreSQL client (frontend/backend protocol v3), standard library only.

Just enough for the web UI to read Hostinfo from Headscale's database when
Headscale runs on PostgreSQL: the web image has no third-party packages, so
there is no psycopg.

  - Startup with optional TLS (sslmode disable | prefer | require | verify-ca
    | verify-full, like libpq).
  - Authentication: trust, cleartext password, MD5 and SCRAM-SHA-256 (RFC 5802
    / 7677, without channel binding). The server's SCRAM signature is always
    verified, so a server that does not know the password cannot pretend to.
  - Simple query protocol only, results in text format (str or None). Every
    session is read-only: default_transaction_read_only=on is sent in the
    startup packet, on top of the read-only database role the installer
    creates for the web UI.

There are no query parameters: callers build SQL from trusted values only
(e.g. integers they converted themselves).

    with Connection("headscale-postgresql", user="headscale_ro", password="...",
                    database="headscale") as con:
        rows = con.query("SELECT id, host_info FROM nodes")
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import socket
import ssl
import stringprep
import struct
import unicodedata

PROTOCOL_V3 = 196608           # 3 << 16
SSL_REQUEST_CODE = 80877103    # 1234 << 16 | 5679
MAX_MESSAGE = 64 * 1024 * 1024  # refuse absurd lengths from a broken server
SCRAM_MAX_ITERATIONS = 1_000_000
SSLMODES = ("disable", "prefer", "require", "verify-ca", "verify-full")


class PgError(Exception):
    """An error from the server (ErrorResponse) or a protocol failure.

    `fields` has the ErrorResponse fields by their one-letter code
    (S severity, C SQLSTATE, M message, ...).
    """

    def __init__(self, message: str, fields: dict | None = None):
        super().__init__(message)
        self.fields = fields or {}

    @property
    def code(self) -> str:
        return self.fields.get("C", "")


# -----------------------------------------------------------------------------
# SASLprep (RFC 4013) and SCRAM-SHA-256 (RFC 5802, RFC 7677)
# -----------------------------------------------------------------------------

def _prohibited(c: str) -> bool:
    return (stringprep.in_table_c12(c) or stringprep.in_table_c21_c22(c) or stringprep.in_table_c3(c)
            or stringprep.in_table_c4(c) or stringprep.in_table_c5(c) or stringprep.in_table_c6(c)
            or stringprep.in_table_c7(c) or stringprep.in_table_c8(c) or stringprep.in_table_c9(c)
            or stringprep.in_table_a1(c))


def saslprep(text: str) -> str:
    """RFC 4013 SASLprep of a password. Raises ValueError if it is not allowed."""
    mapped = []
    for c in text:
        if stringprep.in_table_b1(c):       # "commonly mapped to nothing"
            continue
        mapped.append(" " if stringprep.in_table_c12(c) else c)
    out = unicodedata.normalize("NFKC", "".join(mapped))
    if any(_prohibited(c) for c in out):
        raise ValueError("prohibited character")
    if any(stringprep.in_table_d1(c) for c in out):
        if any(stringprep.in_table_d2(c) for c in out):
            raise ValueError("mixed bidirectional text")
        if not (stringprep.in_table_d1(out[0]) and stringprep.in_table_d1(out[-1])):
            raise ValueError("bidirectional text must start and end with RandALCat")
    return out


def _scram_password(password: str) -> bytes:
    # Like PostgreSQL itself: SASLprep when possible, the raw bytes otherwise
    try:
        return saslprep(password).encode()
    except ValueError:
        return password.encode()


def _hmac(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def _attrs(message: str) -> dict[str, str]:
    out = {}
    for part in message.split(","):
        key, sep, value = part.partition("=")
        if sep and len(key) == 1:
            out.setdefault(key, value)
    return out


class ScramSHA256:
    """Client side of one SCRAM-SHA-256 exchange.

    PostgreSQL ignores the SCRAM user name (it uses the startup packet's), so
    the client sends an empty one; tests pass RFC 7677's.
    """

    def __init__(self, password: str, *, username: str = "", nonce: str | None = None):
        self.password = _scram_password(password)
        self.nonce = nonce or base64.b64encode(os.urandom(18)).decode()
        user = username.replace("=", "=3D").replace(",", "=2C")
        self.client_first_bare = f"n={user},r={self.nonce}"
        self._server_signature: bytes | None = None

    def client_first(self) -> str:
        return "n,," + self.client_first_bare   # gs2 header: no channel binding

    def client_final(self, server_first: str) -> str:
        attrs = _attrs(server_first)
        nonce, salt, iterations = attrs.get("r", ""), attrs.get("s", ""), attrs.get("i", "")
        if not nonce.startswith(self.nonce) or len(nonce) == len(self.nonce):
            raise PgError("SCRAM: the server nonce does not extend the client nonce")
        try:
            salt_bytes = base64.b64decode(salt, validate=True)
            count = int(iterations)
        except ValueError as exc:
            raise PgError("SCRAM: malformed server-first-message") from exc
        if not salt_bytes or not 1 <= count <= SCRAM_MAX_ITERATIONS:
            raise PgError("SCRAM: unacceptable salt or iteration count")

        salted = hashlib.pbkdf2_hmac("sha256", self.password, salt_bytes, count)
        client_key = _hmac(salted, b"Client Key")
        stored_key = hashlib.sha256(client_key).digest()
        without_proof = f"c=biws,r={nonce}"     # biws = base64("n,,")
        auth_message = f"{self.client_first_bare},{server_first},{without_proof}".encode()
        signature = _hmac(stored_key, auth_message)
        proof = bytes(a ^ b for a, b in zip(client_key, signature))
        self._server_signature = _hmac(_hmac(salted, b"Server Key"), auth_message)
        return f"{without_proof},p={base64.b64encode(proof).decode()}"

    def verify_server_final(self, server_final: str) -> None:
        attrs = _attrs(server_final)
        if "e" in attrs:
            raise PgError(f"SCRAM: server error: {attrs['e']}")
        try:
            got = base64.b64decode(attrs.get("v", ""), validate=True)
        except ValueError as exc:
            raise PgError("SCRAM: malformed server-final-message") from exc
        if self._server_signature is None or not hmac.compare_digest(got, self._server_signature):
            raise PgError("SCRAM: the server signature does not match (wrong server?)")


def md5_password(user: str, password: str, salt: bytes) -> str:
    """Response to AuthenticationMD5Password.

    MD5 is what the protocol mandates for this (legacy) method; nothing is
    stored. Servers configured for SCRAM-SHA-256 (the default since
    PostgreSQL 14) never ask for it.
    """
    inner = hashlib.md5((password + user).encode(), usedforsecurity=False).hexdigest()
    return "md5" + hashlib.md5(inner.encode() + salt, usedforsecurity=False).hexdigest()


# -----------------------------------------------------------------------------
# Connection
# -----------------------------------------------------------------------------

def _cstr(value: str) -> bytes:
    return value.encode() + b"\0"


def _tls_context(sslmode: str, sslrootcert: str | None) -> ssl.SSLContext:
    if sslmode in ("prefer", "require"):
        # libpq semantics: encrypted, but the certificate is not checked
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ctx = ssl.create_default_context(cafile=sslrootcert or None)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = sslmode == "verify-full"
    return ctx


class Connection:
    """One PostgreSQL session. Use as a context manager, or call close()."""

    def __init__(self, host: str, port: int = 5432, *, user: str, password: str = "",
                 database: str | None = None, sslmode: str = "prefer", sslrootcert: str | None = None,
                 timeout: float = 5, application_name: str = "headscale-easy"):
        if sslmode not in SSLMODES:
            raise ValueError(f"sslmode must be one of {', '.join(SSLMODES)}")
        self.host, self.port, self.user, self.password = host, int(port), user, password
        self.database = database or user
        self.sslmode, self.sslrootcert = sslmode, sslrootcert
        self.timeout, self.application_name = timeout, application_name
        self.parameters: dict[str, str] = {}
        self.columns: list[str] = []
        self.encrypted = False
        self._sock: socket.socket | None = None
        self._buf = b""
        self._connect()

    # --- context manager ---------------------------------------------------------

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._send(b"X", b"")                  # Terminate
        except OSError:
            pass
        try:
            self._sock.close()
        finally:
            self._sock = None

    # --- I/O --------------------------------------------------------------------------

    def _send(self, kind: bytes, body: bytes) -> None:
        assert self._sock is not None
        self._sock.sendall(kind + struct.pack("!i", len(body) + 4) + body)

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        while len(self._buf) < n:
            chunk = self._sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise PgError("connection closed by the server")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_message(self) -> tuple[bytes, bytes]:
        head = self._recv_exact(5)
        kind, length = head[:1], struct.unpack("!i", head[1:])[0]
        if length < 4 or length > MAX_MESSAGE:
            raise PgError(f"protocol error: message length {length}")
        return kind, self._recv_exact(length - 4)

    @staticmethod
    def _error(body: bytes) -> PgError:
        fields = {}
        for part in body.split(b"\0"):
            if part:
                fields[chr(part[0])] = part[1:].decode(errors="replace")
        return PgError(fields.get("M", "PostgreSQL error"), fields)

    # --- startup ----------------------------------------------------------------------

    def _connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        try:
            self._sock = sock
            if self.sslmode != "disable":
                sock.sendall(struct.pack("!ii", 8, SSL_REQUEST_CODE))
                answer = sock.recv(1)
                if answer == b"S":
                    ctx = _tls_context(self.sslmode, self.sslrootcert)
                    self._sock = ctx.wrap_socket(sock, server_hostname=self.host)
                    self.encrypted = True
                elif answer == b"N":
                    if self.sslmode != "prefer":
                        raise PgError(f"the server does not support TLS (sslmode={self.sslmode})")
                else:
                    raise PgError("protocol error: unexpected answer to the TLS request")
            self._startup()
        except BaseException:
            try:
                (self._sock or sock).close()
            finally:
                self._sock = None
            raise

    def _startup(self) -> None:
        params = {
            "user": self.user,
            "database": self.database,
            "application_name": self.application_name,
            "client_encoding": "UTF8",
            # Belt and braces: the role the web UI uses is read-only too
            "default_transaction_read_only": "on",
        }
        body = struct.pack("!i", PROTOCOL_V3) + b"".join(_cstr(k) + _cstr(v) for k, v in params.items()) + b"\0"
        assert self._sock is not None
        self._sock.sendall(struct.pack("!i", len(body) + 4) + body)
        self._authenticate()
        while True:
            kind, body = self._read_message()
            if kind == b"Z":                      # ReadyForQuery
                return
            if kind == b"E":
                raise self._error(body)
            if kind == b"S":                      # ParameterStatus
                key, value, _ = body.split(b"\0", 2)
                self.parameters[key.decode()] = value.decode()
            # BackendKeyData (K), NoticeResponse (N): nothing to do

    def _authenticate(self) -> None:
        scram: ScramSHA256 | None = None
        scram_verified = False
        while True:
            kind, body = self._read_message()
            if kind == b"E":
                raise self._error(body)
            if kind != b"R":
                raise PgError(f"protocol error: unexpected message {kind!r} during authentication")
            code = struct.unpack("!i", body[:4])[0]
            if code == 0:                                     # AuthenticationOk
                if scram is not None and not scram_verified:
                    raise PgError("SCRAM: the server skipped its final message")
                return
            if code == 3:                                     # cleartext
                self._send(b"p", _cstr(self.password))
            elif code == 5:                                   # MD5
                self._send(b"p", _cstr(md5_password(self.user, self.password, body[4:8])))
            elif code == 10:                                  # SASL
                mechanisms = [m.decode() for m in body[4:].split(b"\0") if m]
                if "SCRAM-SHA-256" not in mechanisms:
                    raise PgError(f"no supported SASL mechanism in {mechanisms}")
                scram = ScramSHA256(self.password)
                first = scram.client_first().encode()
                self._send(b"p", _cstr("SCRAM-SHA-256") + struct.pack("!i", len(first)) + first)
            elif code == 11 and scram is not None:            # SASLContinue
                self._send(b"p", scram.client_final(body[4:].decode()).encode())
            elif code == 12 and scram is not None:            # SASLFinal
                scram.verify_server_final(body[4:].decode())
                scram_verified = True
            else:
                raise PgError(f"unsupported authentication method (code {code})")

    # --- queries ----------------------------------------------------------------------

    def query(self, sql: str) -> list[tuple]:
        """Run one statement; its rows as tuples of str (or None for NULL).

        The column names are left in self.columns. An error is raised after the
        server is ready again, so the connection stays usable.
        """
        if self._sock is None:
            raise PgError("connection is closed")
        self._send(b"Q", _cstr(sql))
        rows: list[tuple] = []
        error: PgError | None = None
        while True:
            kind, body = self._read_message()
            if kind == b"T":                                  # RowDescription
                count = struct.unpack("!h", body[:2])[0]
                names, pos = [], 2
                for _ in range(count):
                    end = body.index(b"\0", pos)
                    names.append(body[pos:end].decode())
                    pos = end + 1 + 18                         # table oid, attnum, type oid, size, mod, format
                self.columns, rows = names, []
            elif kind == b"D":                                # DataRow
                count = struct.unpack("!h", body[:2])[0]
                values, pos = [], 2
                for _ in range(count):
                    size = struct.unpack("!i", body[pos:pos + 4])[0]
                    pos += 4
                    if size < 0:
                        values.append(None)
                    else:
                        values.append(body[pos:pos + size].decode())
                        pos += size
                rows.append(tuple(values))
            elif kind == b"E":
                error = self._error(body)
            elif kind == b"Z":                                # ReadyForQuery
                if error is not None:
                    raise error
                return rows
            elif kind == b"S":
                key, value, _ = body.split(b"\0", 2)
                self.parameters[key.decode()] = value.decode()
            # CommandComplete (C), EmptyQueryResponse (I), NoticeResponse (N):
            # nothing to do


def query(sql: str, **connection) -> list[tuple]:
    """Connect, run one statement, disconnect."""
    with Connection(**connection) as con:
        return con.query(sql)
