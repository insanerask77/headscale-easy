#!/usr/bin/env python3
"""Run pyright and fail if it reports more errors than the recorded ceiling.

The ceiling is the number of errors that existed when the checker was introduced (mostly values typed as
possibly None). New code must not add to it; when you fix some, lower CEILING in the same change.

    python3 scripts/check_types.py        (needs pyright: pip install pyright, or PYRIGHT="uvx pyright")
"""
import json
import os
import shutil
import subprocess
import sys

CEILING = 47


def main() -> int:
    cmd = os.environ.get("PYRIGHT", "").split() or [shutil.which("pyright") or ""]
    if not cmd[0]:
        print("pyright is not installed (pip install pyright, or set PYRIGHT=\"uvx pyright\")", file=sys.stderr)
        return 2
    out = subprocess.run([*cmd, "--outputjson"], capture_output=True, text=True)
    errors = json.loads(out.stdout)["summary"]["errorCount"]
    print(f"pyright: {errors} errors (ceiling {CEILING})")
    if errors > CEILING:
        print("New type errors: run `pyright` and fix them.", file=sys.stderr)
        return 1
    if errors < CEILING:
        print(f"Fewer errors than the ceiling: lower CEILING to {errors} in scripts/check_types.py.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
