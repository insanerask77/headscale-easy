#!/usr/bin/env python3
"""Check that every environment variable the image reads is documented.

aio/render.py's SETTINGS table is the list of variables that reach Headscale, Caddy and
the console (key -> (variable, default)). Each one must appear, in backticks, in the
reference of docs/configuration.md and docs/configuration.es.md, so the docs cannot drift
from the code.

    python3 scripts/gen_env_reference.py            # check; exit 1 and list what is missing
    python3 scripts/gen_env_reference.py --list     # print "VARIABLE<TAB>default" for every variable
    python3 scripts/gen_env_reference.py --table    # print a Markdown table skeleton to paste

Standard library only (the table is read with ast, render.py is not imported).
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
RENDER = os.path.join(ROOT, "aio", "render.py")
DOCS = ("docs/configuration.md", "docs/configuration.es.md")

# Read by the image or the sidecar but not part of SETTINGS: they must be documented too.
EXTRA = ("HSE_ADMIN_PASSWORD", "BACKUP_MODE", "BACKUP_SYNC_INTERVAL")



def settings():
    """The SETTINGS dict of aio/render.py: {key: (variable, default)}."""
    with open(RENDER, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), RENDER)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SETTINGS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("SETTINGS not found in aio/render.py")


def variables():
    found = {}
    for key, (var, default) in settings().items():
        found[var] = default
    for var in EXTRA:
        found.setdefault(var, None)
    return found


def missing():
    names = list(variables())
    problems = {}
    for doc in DOCS:
        with open(os.path.join(ROOT, doc), encoding="utf-8") as fh:
            text = fh.read()
        absent = [n for n in names if "`%s`" % n not in text]
        if absent:
            problems[doc] = absent
    return problems


def main(argv):
    if "--list" in argv:
        for var, default in sorted(variables().items()):
            print("%s\t%s" % (var, "" if default is None else default))
        return 0
    if "--table" in argv:
        print("| Variable | Default | Meaning |\n|---|---|---|")
        for var, default in sorted(variables().items()):
            print("| `%s` | %s | |" % (var, "" if default is None else "`%s`" % default))
        return 0
    problems = missing()
    for doc, names in problems.items():
        print("%s does not document: %s" % (doc, ", ".join(names)), file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
