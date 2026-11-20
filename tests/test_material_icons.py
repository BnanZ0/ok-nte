"""材料图标识别测试: 元数据 + 真实截图回归 + 负样本拒绝。"""

import unittest
from pathlib import Path

import cv2
import numpy as np

from src.tasks.cultivation_goal.material_icons import (
    MATCH_MIN_SCORE,
    MaterialMatch,
    available_keys,
    load_info,
    summarize,
)

_ROOT = Path(__file__).resolve().parent.parent
_SHOTS = _ROOT / "screenshots"

# 真实回归样本: 10:57/10:59 两局副本结算, 均为泡影罐头·螺旋乐(紫/蓝/绿三档)
_DUNGEON = _SHOTS / "2026-11-19_10-59-08-784_cg_reward_5_original.png"
_DUNGEON_2 = _SHOTS / "2026-11-19_10-57-29-818_cg_reward_4_original.png"
# 负样本: 野外 BOSS 清单, 掉的是等级材料(妄想彼端的一页), 库里没有
_WILD = _SHOTS / "2026-11-19_10-53-46-684_cg_reward_wild_list_1_original.png"


def _load(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _strip(img, x1, x2, y1, y2):
    h, w = img.shape[:2]
    return img[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]


class MetadataTests(unittest.TestCase):
    def test_arc_library_is_complete(self):
        keys = available_keys()
        for row in range(1, 6):
            for slot in ("s2", "s3", "s4"):
                self.assertIn(f"arc_r{row}_{slot}", keys)

    def test_info_covers_every_template(self):
        info = load_info()
        for key in available_keys():
            self.assertIn(key, info, f"{key} 缺元数据")
            entry = info[key]
            self.assertTrue(entry.get("name"))
            # 空幕是金/紫两档, 弧盘是紫蓝绿, 所以只校验是已知品质之一。
            self.assertIn(entry.get("quality"), ("紫", "蓝", "绿", "金"))

    def test_quality_matches_slot_for_arc(self):
        # 弧盘三格固定是紫/蓝/绿, 混了会让缺口折算出错。
        info = load_info()
        expect = {"s2": "紫", "s3": "蓝", "s4": "绿"}
        for key, entry in info.items():
            if not key.startswith("arc_"):
                continue
            slot = key.rsplit("_", 1)[-1]
            self.assertEqual(entry["quality"], expect[slot], key)

    def test_summarize_handles_empty(self):
        self.assertIn("未识别", summarize([]))
        fake = [MaterialMatch("spiral_gold", "螺旋乐齐奏", "紫", 0.91, 120)]
        text = summarize(fake)
        self.assertIn("螺旋乐齐奏", text)
        self.assertIn("0.910", text)


@unittest.skipUnless(_DUNGEON.exists(), "缺副本结算截图")
class DungeonStripTests(unittest.TestCase):
    def test_recognises_three_tiers(self):
        from src.tasks.cultivation_goal.material_icons import identify_strip

        img = _load(_DUNGEON)
        result = identify_strip(_strip(img, 0.26, 0.79, 0.40, 0.60), img.shape[0])
        names = [m.name for m in result]
        self.assertEqual(names, ["螺旋乐齐奏", "螺旋乐小节", "螺旋乐碎屑"])
        self.assertEqual([m.quality for m in result], ["紫", "蓝", "绿"])
        for match in result:
            self.assertGreaterEqual(match.score, MATCH_MIN_SCORE)

    def test_stable_across_two_runs(self):
        from src.tasks.cultivation_goal.material_icons import identify_strip

        if not _DUNGEON_2.exists():
            self.skipTest("缺第二张结算截图")
        first = _load(_DUNGEON)
        second = _load(_DUNGEON_2)
        box = (0.26, 0.79, 0.40, 0.60)
        a = [m.key for m in identify_strip(_strip(first, *box), first.shape[0])]
        b = [m.key for m in identify_strip(_strip(second, *box), second.shape[0])]
        self.assertEqual(a, b)
        self.assertEqual(len(a), 3)

    def test_order_is_left_to_right(self):
        from src.tasks.cultivation_goal.material_icons import identify_strip

        img = _load(_DUNGEON)
        result = identify_strip(_strip(img, 0.26, 0.79, 0.40, 0.60), img.shape[0])
        xs = [m.center_x for m in result]
        self.assertEqual(xs, sorted(xs))


class NegativeTests(unittest.TestCase):
    # 纯内存测试(空图拒绝)不依赖野外截图; 认真读野外清单的测试单独依赖 _WILD。
    @unittest.skipUnless(_WILD.exists(), "缺野外清单截图")
    def test_unknown_material_is_rejected(self):
        # 野外清单掉的是等级材料, 不在弧盘库里 —— 必须一个都不认, 否则会张冠李戴。
        from src.tasks.cultivation_goal.material_icons import identify_strip

        img = _load(_WILD)
        result = identify_strip(_strip(img, 0.39, 0.67, 0.40, 0.60), img.shape[0])
        self.assertEqual(result, [])

    def test_blank_region_is_rejected(self):
        from src.tasks.cultivation_goal.material_icons import identify_strip

        blank = np.full((160, 900, 3), 32, np.uint8)
        self.assertEqual(identify_strip(blank, 1080), [])


class InputGuardTests(unittest.TestCase):
    def test_empty_strip_returns_empty(self):
        from src.tasks.cultivation_goal.material_icons import identify_strip

        self.assertEqual(identify_strip(np.zeros((0, 0, 3), np.uint8), 1080), [])

    def test_tiny_strip_does_not_raise(self):
        from src.tasks.cultivation_goal.material_icons import identify_strip

        self.assertEqual(identify_strip(np.zeros((10, 10, 3), np.uint8), 1080), [])


if __name__ == "__main__":
    unittest.main()
