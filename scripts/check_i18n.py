#!/usr/bin/env python3
"""Check that every UI string has a translation in each web/locales/*.json.

Strings are the literal arguments of _() and ngettext() in web/*.py.
Exits 1 when a translation is missing or a call does not use a literal.
"""
import ast
import json
import pathlib
import sys

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


def ui_strings():
    keys, errors = [], []
    for f in sorted(WEB.glob("*.py")):
        if f.name == "i18n.py":  # defines _() and ngettext() themselves
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("_", "ngettext")):
                continue
            for arg in node.args[:2 if node.func.id == "ngettext" else 1]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value not in keys:
                        keys.append(arg.value)
                else:
                    errors.append(f"{f.name}:{node.lineno}: {node.func.id}() needs a string literal")
    return keys, errors


def main():
    keys, errors = ui_strings()
    failed = bool(errors)
    for e in errors:
        print(e)
    for catalog in sorted((WEB / "locales").glob("*.json")):
        entries = json.loads(catalog.read_text(encoding="utf-8"))
        missing = [k for k in keys if k not in entries]
        unused = [k for k in entries if k not in keys]
        print(f"{catalog.stem}: {len(keys) - len(missing)}/{len(keys)} translated, {len(unused)} unused")
        for k in missing:
            print(f"  missing: {k!r}")
        for k in unused:
            print(f"  unused:  {k!r}")
        failed |= bool(missing)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
