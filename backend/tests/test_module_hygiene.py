"""
No top-level function or class may be defined twice in a module.

Python silently keeps the last definition, so a duplicated block does not fail
any test: it just leaves the earlier copy dead and lets the two drift apart.
A bad edit once re-inserted ~250 lines of intelligence.py this way and the
whole suite stayed green.
"""

from __future__ import annotations

import ast
import collections
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


class NoDuplicateDefinitionsTests(unittest.TestCase):
    def test_no_module_defines_a_name_twice(self) -> None:
        offenders: dict[str, dict[str, int]] = {}
        for path in sorted(APP.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = collections.Counter(
                node.name
                for node in tree.body
                if isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                )
            )
            repeated = {n: c for n, c in names.items() if c > 1}
            if repeated:
                offenders[str(path.relative_to(APP))] = repeated
        self.assertEqual(offenders, {})


if __name__ == "__main__":
    unittest.main()
