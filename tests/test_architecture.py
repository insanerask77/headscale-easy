"""Architecture rules, checked on the source (imports at any level):

  - no import cycles between the console's modules;
  - the data and service modules do not depend on the page-rendering ones;
  - handlers never import ``app`` (it would be a second copy when app.py runs as a script).

    python3 -m unittest tests.test_architecture
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")

# Modules that render HTML: nothing that stores, fetches or decides may depend on them.
RENDERING = {"ui", "pages", "admin_pages", "acl_pages", "audit_pages", "backup_pages", "derp_pages",
             "status_pages", "docker_tab", "qr"}
LOWER_LAYER = {"audit", "headscale", "pgwire", "sessions", "local_accounts", "notify", "naming", "apikey",
               "mailer", "expiry", "derp", "policy", "status", "signup", "live", "multipart", "signing",
               "config", "version", "http_base"}


# Feature modules that still hold both logic and HTML. They may only shrink this set: splitting them into
# <name> and <name>_pages is planned, and no new module may be added here.
KNOWN_MIXED = {"expiry", "signup"}


def modules() -> dict[str, str]:
    found = {}
    for dirpath, _dirs, files in os.walk(WEB):
        if "static" in dirpath or "locales" in dirpath:
            continue
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, WEB)[:-3].replace(os.sep, ".")
                found[rel.removesuffix(".__init__")] = path
    return found


def imports(path: str, known: set[str]) -> set[str]:
    deps = set()
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        else:
            continue
        deps |= {n for n in names if n in known}
    return deps


class Architecture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mods = modules()
        cls.graph = {m: imports(p, set(mods)) - {m} for m, p in mods.items()}

    def test_no_import_cycles(self):
        state: dict[str, int] = {}
        stack: list[str] = []
        cycles: list[list[str]] = []

        def visit(m: str):
            state[m] = 1
            stack.append(m)
            for d in sorted(self.graph[m]):
                if state.get(d) == 1:
                    cycles.append(stack[stack.index(d):] + [d])
                elif d not in state:
                    visit(d)
            stack.pop()
            state[m] = 2

        for m in sorted(self.graph):
            if m not in state:
                visit(m)
        self.assertEqual(cycles, [])

    def test_lower_layers_do_not_import_rendering(self):
        bad = {m: sorted(self.graph[m] & RENDERING) for m in LOWER_LAYER if m in self.graph and m not in KNOWN_MIXED and self.graph[m] & RENDERING}
        self.assertEqual(bad, {})

    def test_nothing_imports_app(self):
        self.assertEqual([m for m, deps in self.graph.items() if "app" in deps], [])


if __name__ == "__main__":
    unittest.main()
