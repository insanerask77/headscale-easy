"""The real headscale module honours the HeadscaleClient contract (web/ports.py).

    python3 -m unittest tests.test_ports
"""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
os.environ.setdefault("HEADSCALE_API_KEY", "x")

import headscale  # noqa: E402
import ports  # noqa: E402


def contract() -> dict[str, inspect.Signature]:
    return {n: inspect.signature(f) for n, f in inspect.getmembers(ports.HeadscaleClient, inspect.isfunction)
            if not n.startswith("_")}


class PortTest(unittest.TestCase):
    def test_every_operation_exists_with_the_same_parameters(self):
        problems = []
        for name, wanted in contract().items():
            real = getattr(headscale, name, None)
            if not callable(real):
                problems.append(f"{name}: missing")
                continue
            got = inspect.signature(real)
            strip = lambda sig: [(p.name, p.kind, p.default) for p in sig.parameters.values() if p.name != "self"]  # noqa: E731
            if strip(got) != strip(wanted):
                problems.append(f"{name}: {got} != {wanted}")
        self.assertEqual(problems, [])

    def test_the_contract_is_not_empty(self):
        self.assertGreater(len(contract()), 25)


if __name__ == "__main__":
    unittest.main()
