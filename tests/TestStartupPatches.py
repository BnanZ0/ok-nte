import unittest

from src.patches.startup_patches import resolve_runtime_ui_mode


class TestStartupPatches(unittest.TestCase):
    def test_headless_overrides_configured_qt_mode(self):
        config = {"gui": {"type": "qt"}}

        self.assertEqual(resolve_runtime_ui_mode(config, []), "qt")
        self.assertIsNone(resolve_runtime_ui_mode(config, ["--headless"]))
        self.assertIsNone(resolve_runtime_ui_mode(config, ["-h"]))


if __name__ == "__main__":
    unittest.main()
