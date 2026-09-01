"""
Enforces the Yahoo Fantasy sprint's own ABSOLUTE SEPARATION rule
(fantasy/__init__.py's docstring): nothing under fantasy/ may import
from operational/ (real-money/paper-bankroll/prospective ledgers),
research/live_sog_pricing/ (the DraftKings pricing pipeline), or
research/player_props/decision_policy.py (the betting BET/WATCH/WAIT/
PASS gate). AST-based, not a text search -- a docstring mentioning
these modules can never trip this test; only a real `import` statement
can.
"""
from __future__ import annotations

import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FANTASY_DIR = os.path.join(REPO_ROOT, "fantasy")

FORBIDDEN_MODULE_PREFIXES = (
    "operational",
    "research.live_sog_pricing",
    "research.player_props.decision_policy",
)


def _all_fantasy_py_files() -> list[str]:
    files = []
    for root, _dirs, filenames in os.walk(FANTASY_DIR):
        for name in filenames:
            if name.endswith(".py"):
                files.append(os.path.join(root, name))
    return files


def _imported_module_names(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


class TestFantasyNeverImportsBettingProductionModules(unittest.TestCase):
    def test_no_forbidden_imports_anywhere_under_fantasy(self):
        violations = []
        for path in _all_fantasy_py_files():
            with open(path) as f:
                tree = ast.parse(f.read(), filename=path)
            for module_name in _imported_module_names(tree):
                for forbidden in FORBIDDEN_MODULE_PREFIXES:
                    if module_name == forbidden or module_name.startswith(forbidden + "."):
                        violations.append(f"{path} imports {module_name!r} (forbidden: {forbidden!r})")
        self.assertEqual(violations, [], "\n".join(violations))

    def test_fantasy_package_exists_and_has_files_to_check(self):
        self.assertTrue(os.path.isdir(FANTASY_DIR))
        self.assertGreater(len(_all_fantasy_py_files()), 5)


if __name__ == "__main__":
    unittest.main()
