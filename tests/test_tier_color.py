"""材质色判档的回归测试。

真值是 2026-11-19 主人逐张确认的档位(铜=绿/银=蓝/金=紫), 坐标由
tools/probe_guide_live.py 亮度投影现测, 不同截图之间不能硬套。
"""

import unittest
from pathlib import Path

import cv2
import numpy as np

from src.tasks.cultivation_goal.tier_color import GOLD_MIN_SAT, SILVER_MAX_WARM, detect_tier

_ROOT = Path(__file__).resolve().parent.parent
_SHOTS = _ROOT / "screenshots"
_PAD = 8

# (截图, [(x, cy, w, h), ...], 真值档位)
_CASES = [
    ("2026-11-19_guide_ability_扑克茶会.jpg", [(768, 619, 64, 92), (939, 619, 64, 92), (1110, 619, 64, 92)], ("绿", "蓝", "紫")),
    ("2026-11-19_guide_ability_越狱艺术.jpg", [(767, 618, 64, 92), (938, 618, 64, 92), (1109, 618, 64, 92)], ("绿", "蓝", "紫")),
    ("2026-11-19_guide_ability_惊喜派对.jpg", [(767, 619, 63, 92), (938, 619, 63, 92), (1109, 619, 63, 92)], ("绿", "蓝", "紫")),
    ("2026-11-19_guide_ability_小心鸽子.jpg", [(769, 620, 85, 89), (941, 620, 70, 89), (1110, 620, 57, 89)], ("绿", "蓝", "紫")),
    ("2026-11-19_guide_ability_心电感应.jpg", [(768, 618, 63, 93), (939, 618, 63, 93), (1110, 618, 63, 93)], ("绿", "蓝", "紫")),
    # 弧盘这几张只列未满足的档位: 1 格是紫档, 2 格是蓝+紫
    ("2026-11-19_guide_arc_螺旋乐.jpg", [(769, 798, 82, 87)], ("紫",)),
    ("2026-11-19_guide_arc_液态梦.jpg", [(767, 795, 59, 93), (939, 795, 57, 94)], ("蓝", "紫")),
    ("2026-11-19_guide_arc_戏剧芯.jpg", [(768, 800, 84, 83), (938, 800, 83, 84)], ("蓝", "紫")),
    ("2026-11-19_guide_arc_冷甜点.jpg", [(768, 800, 78, 83), (938, 800, 79, 83)], ("蓝", "紫")),
    ("2026-11-19_guide_arc_冷甜点_2.jpg", [(765, 800, 73, 83), (938, 800, 79, 83), (1109, 800, 79, 83)], ("绿", "蓝", "紫")),
]


def _load(path):
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def _cut(img, cx, cy, w, h):
    x1, y1 = max(0, cx - w // 2 - _PAD), max(0, cy - h // 2 - _PAD)
    return img[y1:cy + h // 2 + _PAD, x1:cx + w // 2 + _PAD]


class ThresholdTests(unittest.TestCase):
    def test_thresholds_keep_margin(self):
        # 阈值是拿 20 个样本定的, 两条线都要落在实测量之间且不贴边:
        #   银白 warm_ratio 上界 0.023 / 铜下界 0.261
        #   铜 warm_sat 上界 119 / 紫下界 139
        self.assertGreater(SILVER_MAX_WARM, 0.023)
        self.assertLess(SILVER_MAX_WARM, 0.261)
        self.assertGreater(GOLD_MIN_SAT, 119)
        self.assertLess(GOLD_MIN_SAT, 139)


@unittest.skipUnless(_SHOTS.exists(), "缺养成指南截图")
class DetectTierTests(unittest.TestCase):
    def test_every_sample_matches_its_tier(self):
        for name, boxes, tiers in _CASES:
            with self.subTest(name=name):
                path = _SHOTS / name
                if not path.exists():
                    self.skipTest(f"缺样本 {name}")
                img = _load(path)
                for i, ((cx, cy, w, h), tier) in enumerate(zip(boxes, tiers)):
                    with self.subTest(name=name, slot=i):
                        got = detect_tier(_cut(img, cx, cy, w, h))
                        self.assertIsNotNone(got, f"{name} 第{i + 1}格判不出档位")
                        self.assertEqual(got[0], tier)
                        self.assertIn("warm_ratio", got[1])

    def test_arc_single_gold_cell_is_not_green(self):
        # 螺旋乐只列 1 格, 位置在最左边; 按列序会判成绿档, 按材质是紫档。
        path = _SHOTS / "2026-11-19_guide_arc_螺旋乐.jpg"
        if not path.exists():
            self.skipTest("缺样本 2026-11-19_guide_arc_螺旋乐.jpg")
        img = _load(path)
        self.assertEqual(detect_tier(_cut(img, 769, 798, 82, 87))[0], "紫")


class GuardTests(unittest.TestCase):
    def test_blank_returns_none(self):
        self.assertIsNone(detect_tier(np.full((80, 80, 3), 30, np.uint8)))
        self.assertIsNone(detect_tier(np.zeros((0, 0, 3), np.uint8)))
        self.assertIsNone(detect_tier(np.zeros((10, 10, 3), np.uint8)))


if __name__ == "__main__":
    unittest.main()
