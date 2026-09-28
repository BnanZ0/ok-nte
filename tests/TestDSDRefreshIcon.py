import unittest
from pathlib import Path

import cv2
import numpy as np

from src.tasks.dsd_refresh import RefreshIcon, classify_refresh_icon


class TestDSDRefreshIcon(unittest.TestCase):
    def test_reference_icons_at_supported_sizes(self):
        folder = Path(__file__).resolve().parents[1] / "assets" / "dsd_refresh"
        for state in (RefreshIcon.READY, RefreshIcon.COOLDOWN):
            image = cv2.imread(str(folder / f"{state.value}.png"))
            self.assertIsNotNone(image)
            for height in (1080, 1440, 2160):
                with self.subTest(state=state, height=height):
                    scaled = cv2.resize(
                        image, None, fx=height / 1440, fy=height / 1440,
                        interpolation=cv2.INTER_AREA,
                    )
                    self.assertIs(classify_refresh_icon(scaled), state)

    def test_blank_or_unrelated_image_is_unknown(self):
        for image in (
            None,
            np.zeros((0, 0, 3), dtype=np.uint8),
            np.zeros((66, 81, 3), dtype=np.uint8),
            np.full((66, 81, 3), 255, dtype=np.uint8),
            np.random.default_rng(123).integers(0, 256, (66, 81, 3), dtype=np.uint8),
        ):
            self.assertIs(classify_refresh_icon(image), RefreshIcon.UNKNOWN)

    def test_brightness_change_does_not_turn_ready_into_cooldown(self):
        folder = Path(__file__).resolve().parents[1] / "assets" / "dsd_refresh"
        image = cv2.imread(str(folder / "ready.png"))
        for offset in (-20, 20):
            changed = np.clip(image.astype(np.int16) + offset, 0, 255).astype(np.uint8)
            self.assertIs(classify_refresh_icon(changed), RefreshIcon.READY)
