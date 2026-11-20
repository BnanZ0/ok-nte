"""奖励弹窗图标识别回归测试。

两类样本：
- 野外 BOSS 领奖：黑之书 / 金币 / 银色卷轴，只认黑之书，货币不认。
- 副本结算：同一次泡影罐头·螺旋乐掉落，认出紫/蓝/绿三档。
模板主库是 relation 的 41 张列表页带底板图，养成页 40 张兜底。
"""

import unittest
from unittest import mock
from pathlib import Path

import cv2
import numpy as np

from src.tasks.cultivation_goal.reward_icons import (
    MIN_INLIERS,
    RewardRecognizer,
    identify_rewards,
)

_ROOT = Path(__file__).resolve().parent.parent
_SHOTS = _ROOT / "screenshots"
_SAMPLES = (
    "2026-11-19_10-50-15-282_cg_reward_wild_list_1_original.png",
    "2026-11-19_10-53-46-684_cg_reward_wild_list_1_original.png",
)
_DUNGEON = "2026-11-19_10-59-08-784_cg_reward_5_original.png"


def _load(name):
    return cv2.imdecode(np.fromfile(str(_SHOTS / name), dtype=np.uint8), cv2.IMREAD_COLOR)


class RewardIconTests(unittest.TestCase):
    def test_recognizes_black_book_on_both_samples(self):
        for name in _SAMPLES:
            with self.subTest(name=name):
                matches = identify_rewards(_load(name))
                keys = [match.key for match in matches]
                self.assertIn("boss_黑之书", keys, name)
                book = next(match for match in matches if match.key == "boss_黑之书")
                self.assertGreaterEqual(book.inliers, MIN_INLIERS)
                self.assertEqual(book.kind, "boss")
                self.assertEqual(book.tier, "")

    def test_does_not_invent_currency_or_unknown_cards(self):
        # 金币和银色卷轴不在 40 张模板里，宁可认不出也不能张冠李戴成某个 boss。
        for name in _SAMPLES:
            with self.subTest(name=name):
                keys = [match.key for match in identify_rewards(_load(name))]
                self.assertEqual(len(keys), len(set(keys)), "同一清单里不该重复认同一材料")
                for key in keys:
                    self.assertTrue(key.startswith("boss_") or key.startswith("weekly_")
                                    or key.startswith("ability_") or key.startswith("arc_"))

    def test_count_box_is_inside_its_card(self):
        for match in identify_rewards(_load(_SAMPLES[0])):
            x1, y1, x2, y2 = match.count_box
            self.assertLess(x1, x2)
            self.assertLess(y1, y2)
            # 数量区应在卡片 0 的右缘（黑之书在最左格）
            if match.key == "boss_黑之书":
                self.assertLess(x2, 0.48)
                self.assertGreater(x2, 0.44)

    def test_dungeon_reward_recognizes_three_tiers_by_body_color(self):
        # 副本结算的紫/蓝/绿三档图标几乎一样，SIFT 命中同一 key，档位靠图标本体判色。
        # key 必须跟随最终判色档位（kind_dungeon_tier），否则掉落会入账到低档槽位。
        matches = identify_rewards(_load(_DUNGEON))
        spiral = [m for m in matches if m.dungeon == "泡影罐头·螺旋乐"]
        self.assertEqual(len(spiral), 3)
        self.assertEqual({m.tier for m in spiral}, {"紫", "蓝", "绿"})
        by_tier = {}
        for match in spiral:
            self.assertEqual(match.key, f"{match.kind}_{match.dungeon}_{match.tier}",
                             f"key 未跟随判色档位: {match.key} vs tier {match.tier}")
            by_tier[match.tier] = match.key
        self.assertEqual(len(by_tier), 3)
        for match in spiral:
            self.assertGreaterEqual(match.inliers, MIN_INLIERS)

    def test_dungeon_card_detection_finds_five_cards(self):
        boxes = RewardRecognizer._detect_cards(_load(_DUNGEON))
        self.assertEqual(len(boxes), 5)
        self.assertTrue(all(60 < y2 - y1 < 200 for _, _, y1, y2 in boxes))

    def test_empty_frame_returns_no_matches(self):
        self.assertEqual(identify_rewards(np.zeros((1080, 1920, 3), np.uint8)), [])

    def test_recognizer_reuses_template_cache(self):
        recognizer = RewardRecognizer(1080)
        self.assertGreater(len(recognizer._templates), 0)
        first = len(recognizer._templates)
        recognizer.identify_strip(_load(_SAMPLES[0]))
        self.assertEqual(len(recognizer._templates), first)

    def test_color_failure_rejects_tiered_card_instead_of_trusting_template(self):
        """判色失败不能拿模板档位顶上: 同副本三档图案几乎一样, 记错档比漏记更糟。"""
        import src.tasks.cultivation_goal.reward_icons as reward_icons

        card = reward_icons._imread(reward_icons._REWARD_DIR / "arc_泡影罐头-螺旋乐_紫.png")
        self.assertIsNotNone(card)
        recognizer = RewardRecognizer(1080)

        # 判色正常时这张卡认出紫档
        match = recognizer.identify_card(card, 0.0, 0.1, 50)
        self.assertIsNotNone(match)
        self.assertEqual(match.tier, "紫")

        # 判色失败时必须拒收, 不能按模板档位入账
        original = reward_icons.detect_tier
        reward_icons.detect_tier = lambda icon: None
        try:
            self.assertIsNone(recognizer.identify_card(card, 0.0, 0.1, 50))
        finally:
            reward_icons.detect_tier = original

    def test_identify_strip_detailed_reports_projected_card_count(self):
        """投影切卡成功时报告待识别卡数, 已排除认出的货币卡。"""
        recognizer = RewardRecognizer(1080)
        matches, total = recognizer.identify_strip_detailed(_load(_DUNGEON))
        # 5 张卡 = 3 材料(紫/蓝/绿) + 银色卷轴/金币两张货币; 货币不算漏识别。
        self.assertEqual(total, 3)
        self.assertEqual(len(matches), 3)
        self.assertEqual(recognizer.identify_strip_detailed(None), ([], None))

    def test_full_list_with_currencies_is_complete(self):
        """3 张材料 + 2 张货币: 货币排除后清单完整, 不误标残缺。"""
        matches, total = RewardRecognizer(1080).identify_strip_detailed(_load(_DUNGEON))
        self.assertEqual(len(matches), 3)
        self.assertEqual(total, 3, "货币卡排除后 3 材料 = 3 待识别, 清单完整")

    def test_unrecognized_material_card_marks_incomplete(self):
        """3 张材料 + 1 张没认出的卡: 只把没认出的计入残缺判定。"""
        original = RewardRecognizer._identify_currency

        def hide_coin(self, card):
            match = original(self, card)
            # 假装金币这张货币没认出 -> 它变成「未识别的材料卡」。
            if match is not None and match.dungeon == "金币":
                return None
            return match

        with mock.patch.object(RewardRecognizer, "_identify_currency", hide_coin):
            matches, total = RewardRecognizer(1080).identify_strip_detailed(_load(_DUNGEON))
        self.assertEqual(len(matches), 3)
        self.assertEqual(total, 4, "银色卷轴排除, 金币算未识别材料卡")
        self.assertLess(len(matches), total, "3 < 4 -> 清单残缺")


if __name__ == "__main__":
    unittest.main()
