"""A streaming multipart/form-data reader for one upload (standard library only: ``cgi`` is gone in 3.13).

The console has to take a backup file that can be hundreds of megabytes inside a container that idles at
~70 MB of RAM, so the file part is written to a sink chunk by chunk and never held in memory. Text fields are
small and capped. The caller decides, when the file part starts, whether to accept it (for example after the
CSRF field, which the form sends first, was checked).
"""
from __future__ import annotations

import re

CHUNK = 64 * 1024
_BOUNDARY = re.compile(r'boundary=(?:"([^"\r\n]{1,70})"|([^\s;,"]{1,70}))', re.I)
_NAME = re.compile(r'[;\s]name="([^"]*)"', re.I)
_FILENAME = re.compile(r'[;\s]filename="([^"]*)"', re.I)


class MultipartError(Exception):
    """The body is not usable; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def read_form(rfile, content_type: str, length: int, on_file, *, max_field: int = 8192, max_file: int = 1 << 30,
              max_parts: int = 16) -> tuple[dict, dict]:
    """Read ``length`` bytes of a multipart body from ``rfile``.

    ``on_file(field_name, filename, fields_so_far)`` is called when a file part starts and returns an object
    with ``write(bytes)`` (or raises MultipartError to refuse the upload). Returns ``(fields, files)``:
    the text fields, and ``{field_name: (filename, size)}`` for the file parts. Reads the whole body (the rest
    is discarded) so the connection stays usable.
    """
    match = _BOUNDARY.search(content_type or "")
    if not match or length <= 0:
        raise MultipartError("not a multipart form")
    boundary = (match.group(1) or match.group(2)).encode("latin-1", "replace")
    delim = b"--" + boundary
    sep = b"\r\n" + delim
    remaining = length
    buf = bytearray()

    def fill() -> bool:
        nonlocal remaining
        if remaining <= 0:
            return False
        data = rfile.read(min(CHUNK, remaining))
        if not data:
            remaining = 0
            return False
        remaining -= len(data)
        buf.extend(data)
        return True

    def need(n: int):
        while len(buf) < n:
            if not fill():
                raise MultipartError("the form was cut short")

    def drain():
        nonlocal remaining
        while remaining > 0:
            data = rfile.read(min(CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)

    fields: dict = {}
    files: dict = {}
    need(len(delim) + 2)
    if not buf.startswith(delim):
        raise MultipartError("not a multipart form")
    del buf[:len(delim)]

    for _part in range(max_parts + 1):
        need(2)
        if buf[:2] == b"--":
            break
        if buf[:2] != b"\r\n":
            raise MultipartError("bad multipart boundary")
        del buf[:2]
        while buf.find(b"\r\n\r\n") < 0:
            if len(buf) > 8192:
                raise MultipartError("part headers are too long")
            if not fill():
                raise MultipartError("the form was cut short")
        end = buf.find(b"\r\n\r\n")
        if end > 8192:
            raise MultipartError("part headers are too long")
        head = bytes(buf[:end]).decode("utf-8", "replace")
        del buf[:end + 4]
        name = _NAME.search(head)
        if not name:
            raise MultipartError("a part has no name")
        fname = _FILENAME.search(head)
        name = name.group(1)
        sink = None
        if fname is not None:
            if name in files:
                raise MultipartError("a file was sent twice")
            sink = on_file(name, fname.group(1), dict(fields))
        size = 0
        value = bytearray()
        while True:
            idx = buf.find(sep)
            if idx >= 0:
                chunk, done = bytes(buf[:idx]), True
                del buf[:idx + len(sep)]
            else:
                keep = len(sep) - 1  # a delimiter may be split across two reads
                chunk, done = bytes(buf[:max(0, len(buf) - keep)]), False
                del buf[:len(chunk)]
            size += len(chunk)
            if sink is not None:
                if size > max_file:
                    raise MultipartError("the file is too large", 413)
                if chunk:
                    sink.write(chunk)
            else:
                value.extend(chunk)
                if len(value) > max_field:
                    raise MultipartError("a form field is too long")
            if done:
                break
            if not fill():
                raise MultipartError("the form was cut short")
        if sink is not None:
            files[name] = (fname.group(1), size)
        else:
            fields[name] = value.decode("utf-8", "replace")
    else:
        raise MultipartError("too many parts")
    drain()
    return fields, files
