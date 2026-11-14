"""Keep custom pages importable without the removed UI dependencies."""

import os
import subprocess
import sys
import unittest
from pathlib import Path


class TestUiImports(unittest.TestCase):
    def test_all_project_ui_modules_import_without_fluent(self):
        script = """
import importlib
import importlib.abc
import pkgutil
import sys

class RemovedUiLibraries(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'qfluentwidgets', 'qframelesswindow'}:
            raise ImportError(f'Removed UI dependency: {fullname}')

sys.meta_path.insert(0, RemovedUiLibraries())
import src.ui
modules = list(pkgutil.walk_packages(src.ui.__path__, src.ui.__name__ + '.'))
for module in modules:
    importlib.import_module(module.name)
print(f'Imported {len(modules)} project UI modules')
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
