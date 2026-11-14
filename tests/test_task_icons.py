import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ui import icons


class TestTaskIcons(unittest.TestCase):
    def test_project_assets_resolve_to_absolute_paths(self):
        project_root = Path(__file__).resolve().parents[1]
        names = (
            "music-note",
            "previous",
            "next",
            "heart-fill",
            "chevron-down-up",
            "chevron-up-down",
        )
        for name in names:
            with self.subTest(name=name):
                icon_path = icons.feature_icon(name)
                assert icon_path is not None
                path = Path(icon_path)
                self.assertEqual(path, project_root / "icons" / "features" / f"{name}.svg")
                self.assertTrue(path.is_file())

    def test_svg_takes_priority_over_png_and_missing_artwork_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            svg = folder / "example.svg"
            png = folder / "example.png"
            png.touch()
            with patch.object(icons, "_TASK_ICON_FOLDER", folder):
                self.assertEqual(icons.task_icon("example"), str(png))
                svg.touch()
                self.assertEqual(icons.task_icon("example"), str(svg))
                self.assertIsNone(icons.task_icon("missing"))


if __name__ == "__main__":
    unittest.main()
