import unittest
from pathlib import Path

import cv2
import numpy as np

from src.tasks.BaseNTETask import BaseNTETask
from src.utils import image_utils as iu

THRESHOLD = 0.7
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates"


def load_f1_domain_template():
    matches = sorted(TEMPLATE_DIR.glob("f1_domain_*.png"))
    if not matches:
        raise unittest.SkipTest("f1_domain template asset is missing")
    template = cv2.imread(str(matches[0]), cv2.IMREAD_COLOR)
    if template is None:
        raise unittest.SkipTest("f1_domain template asset cannot be decoded")
    return template


class TestF1SidebarMask(unittest.TestCase):
    def test_mask_blocks_only_top_right_corner(self):
        template = np.full((69, 48, 3), 255, np.uint8)
        mask = BaseNTETask.f1_sidebar_mask(template)

        self.assertEqual((69, 48), mask.shape)
        self.assertEqual(np.uint8, mask.dtype)
        blocked_height = int(round(69 * 0.40))
        blocked_width = int(round(48 * 0.55))
        self.assertEqual(0, mask[0, -1])
        self.assertEqual(0, mask[blocked_height - 1, blocked_width])
        self.assertEqual(255, mask[-1, 0])
        self.assertEqual(255, mask[blocked_height, -1])
        self.assertGreater(int((mask > 0).sum()) / mask.size, 0.75)

    def test_matching_ignores_badge_content_in_corner(self):
        template = load_f1_domain_template()
        template_mask = iu.binarize_bgr_by_brightness(template, threshold=20)
        mask = BaseNTETask.f1_sidebar_mask(template_mask)
        height, width = template.shape[:2]
        x, y = 61, 44

        frame = np.full((200, 240, 3), 8, dtype=np.uint8)
        frame[y : y + height, x : x + width] = template
        # a notification badge may draw arbitrary content over the masked corner
        window = frame[y : y + height, x : x + width].copy()
        window[mask == 0] = 255
        frame[y : y + height, x : x + width] = window
        frame_mask = iu.binarize_bgr_by_brightness(frame, threshold=20)

        unmasked = cv2.matchTemplate(frame_mask, template_mask, cv2.TM_CCOEFF_NORMED)
        masked = cv2.matchTemplate(frame_mask, template_mask, cv2.TM_CCOEFF_NORMED, mask=mask)
        unmasked = np.nan_to_num(unmasked, nan=0.0, posinf=0.0, neginf=0.0)
        masked = np.nan_to_num(masked, nan=0.0, posinf=0.0, neginf=0.0)
        _, unmasked_score, _, _ = cv2.minMaxLoc(unmasked)
        _, masked_score, _, masked_location = cv2.minMaxLoc(masked)

        self.assertEqual((x, y), masked_location)
        self.assertGreaterEqual(masked_score, 0.95)
        self.assertLess(unmasked_score, masked_score)

    def test_matching_still_finds_clean_icon(self):
        template = load_f1_domain_template()
        template_mask = iu.binarize_bgr_by_brightness(template, threshold=20)
        mask = BaseNTETask.f1_sidebar_mask(template_mask)
        height, width = template.shape[:2]

        frame = np.full((200, 240, 3), 8, dtype=np.uint8)
        frame[44 : 44 + height, 61 : 61 + width] = template
        frame_mask = iu.binarize_bgr_by_brightness(frame, threshold=20)
        scores = cv2.matchTemplate(frame_mask, template_mask, cv2.TM_CCOEFF_NORMED, mask=mask)
        scores = np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0)
        _, score, _, location = cv2.minMaxLoc(scores)

        self.assertEqual((61, 44), location)
        self.assertGreaterEqual(score, THRESHOLD)
