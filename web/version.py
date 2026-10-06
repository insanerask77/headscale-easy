"""Project metadata shown in the UI.

The version lives in one place: the VERSION file at the root of the repository (it is
/app/VERSION in the image). Everything else reads it or is checked against it by
tests/test_version.py.
"""
import os


def _read_version() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "..", "VERSION"), os.path.join(here, "VERSION")):
        try:
            with open(path, encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError:
            continue
        if value:
            return value
    return "unknown"


VERSION = _read_version()
PROJECT_URL = "https://github.com/insanerask77/headscale-easy"
SPONSOR_URL = "https://ko-fi.com/rafaelmadolell"
DOCS_URL = "https://insanerask77.github.io/headscale-easy/"
