"""web/multipart.py: the streaming reader used for backup uploads.

    python3 -m unittest tests.test_multipart
"""
import io
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
import multipart as mp  # noqa: E402

B = "----WebKitFormBoundaryAbC123"
CT = f"multipart/form-data; boundary={B}"


def part(name, value, filename=None):
    head = f'Content-Disposition: form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else "")
    if filename is not None:
        head += "\r\nContent-Type: application/gzip"
    return f"--{B}\r\n{head}\r\n\r\n".encode() + (value if isinstance(value, bytes) else value.encode()) + b"\r\n"


def body(*parts, tail=True):
    return b"".join(parts) + (f"--{B}--\r\n".encode() if tail else b"")


class Sink:
    def __init__(self):
        self.data = bytearray()

    def write(self, chunk):
        self.data.extend(chunk)


class Trickle(io.BytesIO):
    """Returns at most `n` bytes per read: the delimiter then falls across reads."""

    def __init__(self, data, n):
        super().__init__(data)
        self.n = n

    def read(self, size=-1):
        return super().read(min(size, self.n) if size and size > 0 else self.n)


def run(raw, ct=CT, sinks=None, **kw):
    sinks = sinks if sinks is not None else {}

    def on_file(name, filename, fields):
        sinks.setdefault("calls", []).append((name, filename, dict(fields)))
        return sinks.setdefault(name, Sink())
    rf = kw.pop("rfile", None) or io.BytesIO(raw)
    return mp.read_form(rf, ct, len(raw), on_file, **kw), sinks


class ReadForm(unittest.TestCase):
    def test_fields_and_a_file(self):
        raw = body(part("csrf", "tok"), part("restore", "1"), part("file", b"\x1f\x8b payload", "backup.tar.gz"))
        (fields, files), sinks = run(raw)
        self.assertEqual(fields, {"csrf": "tok", "restore": "1"})
        self.assertEqual(files, {"file": ("backup.tar.gz", 10)})
        self.assertEqual(bytes(sinks["file"].data), b"\x1f\x8b payload")
        self.assertEqual(sinks["calls"][0][2], {"csrf": "tok", "restore": "1"})  # fields before the file are known

    def test_binary_content_with_crlf_and_boundary_lookalikes(self):
        blob = bytes(range(256)) * 50 + b"\r\n--" + B.encode()[:10] + b"\r\n--\r\n" + b"\r\n" + f"--{B[:-3]}".encode() + b"end"
        raw = body(part("file", blob, "a.tar.gz"))
        (_f, files), sinks = run(raw)
        self.assertEqual(bytes(sinks["file"].data), blob)
        self.assertEqual(files["file"][1], len(blob))

    def test_the_delimiter_split_across_reads(self):
        blob = os.urandom(5000)
        raw = body(part("csrf", "tok"), part("file", blob, "a.tar.gz"), part("z", "last"))
        for n in (1, 2, 3, 7, 50, 4096):
            (fields, _files), sinks = run(raw, rfile=Trickle(raw, n))
            self.assertEqual(bytes(sinks["file"].data), blob, n)
            self.assertEqual(fields, {"csrf": "tok", "z": "last"}, n)

    def test_random_chunking(self):
        rnd = random.Random(7)
        blob = bytes(rnd.getrandbits(8) for _ in range(20000))
        raw = body(part("csrf", "tok"), part("file", blob, "a.tar.gz"))
        for _ in range(20):
            (_f, _files), sinks = run(raw, rfile=Trickle(raw, rnd.randint(1, 3000)))
            self.assertEqual(bytes(sinks["file"].data), blob)

    def test_empty_file_and_empty_field(self):
        (fields, files), sinks = run(body(part("a", ""), part("file", b"", "empty.tar.gz")))
        self.assertEqual((fields, files["file"][1]), ({"a": ""}, 0))

    def test_quoted_boundary_and_trailing_bytes_are_drained(self):
        raw = body(part("a", "1")) + b"epilogue junk"
        rf = io.BytesIO(raw)
        mp.read_form(rf, f'multipart/form-data; boundary="{B}"', len(raw), lambda *a: Sink())
        self.assertEqual(rf.read(), b"")  # the connection is left clean for the next request

    def test_file_is_refused_by_the_callback(self):
        def refuse(name, filename, fields):
            raise mp.MultipartError("no", 403)
        with self.assertRaises(mp.MultipartError) as cm:
            mp.read_form(io.BytesIO(body(part("file", b"x", "a"))), CT, len(body(part("file", b"x", "a"))), refuse)
        self.assertEqual(cm.exception.status, 403)

    def test_limits(self):
        raw = body(part("big", "x" * 100))
        with self.assertRaises(mp.MultipartError):
            run(raw, max_field=50)
        raw = body(part("file", b"x" * 1000, "a"))
        with self.assertRaises(mp.MultipartError) as cm:
            run(raw, max_file=999)
        self.assertEqual(cm.exception.status, 413)
        (_f, files), _s = run(raw, max_file=1000)
        self.assertEqual(files["file"][1], 1000)
        many = body(*[part(f"f{i}", "v") for i in range(30)])
        with self.assertRaises(mp.MultipartError):
            run(many, max_parts=16)

    def test_malformed_bodies(self):
        good = body(part("a", "1"), part("file", b"data", "a.tar.gz"))
        for raw in (good[:30], good[:-12], b"", b"garbage", good.replace(b"name=", b"nom=", 1),
                    b"--" + B.encode() + b"xx" + good[len(B) + 2:]):
            with self.assertRaises(mp.MultipartError, msg=raw[:40]):
                run(raw) if raw else mp.read_form(io.BytesIO(raw), CT, 0, lambda *a: Sink())
        for ct in ("", "text/plain", "multipart/form-data", "multipart/form-data; boundary="):
            with self.assertRaises(mp.MultipartError):
                run(good, ct=ct)

    def test_body_shorter_than_its_content_length(self):
        raw = body(part("file", b"x" * 100, "a"))
        with self.assertRaises(mp.MultipartError):
            mp.read_form(io.BytesIO(raw[:-40]), CT, len(raw), lambda *a: Sink())

    def test_a_file_part_sent_twice(self):
        with self.assertRaises(mp.MultipartError):
            run(body(part("file", b"1", "a"), part("file", b"2", "b")))

    def test_huge_headers_are_refused(self):
        raw = f"--{B}\r\nContent-Disposition: form-data; name=\"a\"\r\nX: {'y' * 20000}\r\n\r\nv\r\n--{B}--\r\n".encode()
        with self.assertRaises(mp.MultipartError):
            run(raw)

    def test_memory_stays_flat_for_a_large_file(self):
        class Counting:
            peak = 0
            n = 0

            def write(self, chunk):
                Counting.n += len(chunk)
                Counting.peak = max(Counting.peak, len(chunk))
        size = 20 * 1024 * 1024
        raw = body(part("file", b"\0" * size, "big.tar.gz"))
        sink = Counting()
        mp.read_form(io.BytesIO(raw), CT, len(raw), lambda *a: sink)
        self.assertEqual(Counting.n, size)
        self.assertLess(Counting.peak, 4 * mp.CHUNK)  # written in chunks, never as one block


if __name__ == "__main__":
    unittest.main()
