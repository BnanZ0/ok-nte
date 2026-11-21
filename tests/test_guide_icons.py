"""养成指南页材料图标识别的回归测试。

样本是 2026-11-19 主人给的同页截图, 文件名带上真值, 别改名:
- 异能 5 / 弧盘 5 截图(三档材料)
- boss 7 / weekly 3 截图(单格材料)
"""

import unittest
from pathlib import Path

import cv2
import numpy as np

from src.tasks.cultivation_goal.guide_icons import (
    MATCH_MIN_SCORE,
    available_keys,
    find_cells,
    identify_cell,
    identify_row,
    infer_missing,
    load_info,
    vote_dungeon,
)

_ROOT = Path(__file__).resolve().parent.parent
_SHOTS = _ROOT / "screenshots"

# 名 -> (kind, 副本, 材料行中心 y); 行区域取 x 0.30~0.85, 避开左侧角色列表
_SAMPLES = {
    "2026-11-19_guide_ability_扑克茶会.jpg": ("ability", "扑克茶会", 619),
    "2026-11-19_guide_ability_越狱艺术.jpg": ("ability", "越狱艺术", 618),
    "2026-11-19_guide_ability_惊喜派对.jpg": ("ability", "惊喜派对", 619),
    "2026-11-19_guide_ability_小心鸽子.jpg": ("ability", "小心鸽子", 620),
    "2026-11-19_guide_ability_心电感应.jpg": ("ability", "心电感应", 618),
    "2026-11-19_guide_arc_螺旋乐.jpg": ("arc", "泡影罐头·螺旋乐", 798),
    "2026-11-19_guide_arc_液态梦.jpg": ("arc", "泡影罐头·液态梦", 795),
    "2026-11-19_guide_arc_戏剧芯.jpg": ("arc", "泡影罐头·戏剧芯", 800),
    "2026-11-19_guide_arc_冷甜点.jpg": ("arc", "泡影罐头·冷甜点", 800),
    "2026-11-19_guide_arc_冷甜点_2.jpg": ("arc", "泡影罐头·冷甜点", 800),
    # 主人 14:01 补的 5 张, 每种弧盘材料三档齐全(cy 各不同, 现测)
    "2026-11-19_guide_arc_full_液态梦.jpg": ("arc", "泡影罐头·液态梦", 795),
    "2026-11-19_guide_arc_full_戏剧芯.jpg": ("arc", "泡影罐头·戏剧芯", 800),
    "2026-11-19_guide_arc_full_冷甜点.jpg": ("arc", "泡影罐头·冷甜点", 800),
    "2026-11-19_guide_arc_full_苹果核.jpg": ("arc", "泡影罐头·苹果核", 799),
    "2026-11-19_guide_arc_full_螺旋乐.jpg": ("arc", "泡影罐头·螺旋乐", 798),
    # 主人 14:14 补的单格材料: 世界BOSS 7 + 周本 3(第4种周本无养成页图标)
    "2026-11-19_guide_boss_黑之书.jpg": ("boss", "黑之书", 754),
    "2026-11-19_guide_boss_塞润尼缇.jpg": ("boss", "塞润尼缇", 771),
    "2026-11-19_guide_boss_囿巢鸟.jpg": ("boss", "囿巢鸟", 771),
    "2026-11-19_guide_boss_斑蝶.jpg": ("boss", "斑蝶", 775),
    "2026-11-19_guide_boss_无首铁驭.jpg": ("boss", "无首铁驭", 771),
    "2026-11-19_guide_boss_海囚.jpg": ("boss", "海囚", 771),
    "2026-11-19_guide_boss_音霸魔王.jpg": ("boss", "音霸魔王", 768),
    "2026-11-19_guide_weekly_阿拉克涅.jpg": ("weekly", "永不谢幕的阿拉克涅", 747),
    "2026-11-19_guide_weekly_墨菲克斯.jpg": ("weekly", "墨菲克斯", 754),
    "2026-11-19_guide_weekly_讨债人.jpg": ("weekly", "讨债人", 772),
}


def _load(path):
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def _row_strip(img, center_y):
    h, w = img.shape[:2]
    y1, y2 = max(0, center_y - 60), min(h, center_y + 60)
    return img[y1:y2, int(w * 0.30):int(w * 0.85)]


# 样本是 1920x1110(带标题栏), 线上帧是 1080 高的纯游戏区。
_SAMPLE_FRAME_HEIGHT = 1110
_KIND_ROW = {"ability": ("异能", 0), "arc": ("弧盘", 1),
             "boss": ("等级", 1), "weekly": ("异能", 1)}


def _runtime_strip(img, kind):
    """按 flow._cg_identify_row 的方式裁行带, 用于校验线上口径。"""
    from src.tasks.cultivation_goal.positions import POS

    tab, row = _KIND_ROW[kind]
    h, w = img.shape[:2]
    center = (POS.ratio_row_y[tab][row] - POS.icon_row_offset) * h
    half = POS.icon_half_height * h
    return img[max(0, int(center - half)):int(center + half),
               int(w * 0.30):int(w * 0.85)]


class MetadataTests(unittest.TestCase):
    def test_library_has_five_ability_dungeons(self):
        dungeons = {info["dungeon"] for info in load_info().values() if info["kind"] == "ability"}
        self.assertEqual(dungeons, {"小心鸽子", "心电感应", "惊喜派对", "扑克茶会", "越狱艺术"})

    def test_arc_dungeons_are_canned_food(self):
        dungeons = {info["dungeon"] for info in load_info().values() if info["kind"] == "arc"}
        self.assertEqual(dungeons, {
            "泡影罐头·苹果核", "泡影罐头·螺旋乐", "泡影罐头·液态梦",
            "泡影罐头·冷甜点", "泡影罐头·戏剧芯",
        })

    def test_every_arc_dungeon_has_three_tiers(self):
        # 14:01 主人补的 5 张完整截图: 每种弧盘材料都有铜/银/金三档。
        from collections import defaultdict
        tiers = defaultdict(set)
        for info in load_info().values():
            if info["kind"] == "arc":
                tiers[info["dungeon"]].add(info["tier"])
        for dungeon, got in tiers.items():
            self.assertEqual(got, {"绿", "蓝", "紫"}, dungeon)

    def test_boss_and_weekly_dungeons(self):
        info = load_info()
        boss = {v["dungeon"] for v in info.values() if v["kind"] == "boss"}
        weekly = {v["dungeon"] for v in info.values() if v["kind"] == "weekly"}
        self.assertEqual(boss, {"音霸魔王", "无首铁驭", "塞润尼缇", "黑之书", "海囚", "囿巢鸟", "斑蝶"})
        # 第4种周本(贵腐巢母)暂缺养成页图标, 只有 3 种入库
        self.assertEqual(weekly, {"墨菲克斯", "永不谢幕的阿拉克涅", "讨债人"})

    def test_every_template_has_metadata(self):
        info = load_info()
        for key in available_keys():
            self.assertIn(key, info, f"{key} 缺元数据")
            item = info[key]
            if item.get("single"):
                self.assertEqual(item["tier"], "", f"单格材料不该有档位: {key}")
            else:
                self.assertIn(item["tier"], ("绿", "蓝", "紫"), key)


@unittest.skipUnless(_SHOTS.exists(), "缺养成指南截图")
class RowRegressionTests(unittest.TestCase):
    def test_each_row_votes_its_own_dungeon(self):
        for name, (kind, dungeon, center_y) in _SAMPLES.items():
            with self.subTest(name=name):
                path = _SHOTS / name
                if not path.exists():
                    self.skipTest(f"缺样本 {name}")
                matches = identify_row(_row_strip(_load(path), center_y))
                got_kind, got_dungeon, votes = vote_dungeon(matches)
                self.assertEqual((got_kind, got_dungeon), (kind, dungeon))
                self.assertGreaterEqual(votes, 1)
                for match in matches:
                    self.assertGreaterEqual(match.score, MATCH_MIN_SCORE)

    def test_single_material_has_empty_tier_and_no_conflict(self):
        # 世界BOSS/周本是单格材料, 图标颜色跟品质无关。若套了 detect_tier
        # 会被乱标成蓝/紫/绿(实测 BOSS1→蓝/BOSS3→紫/周本3→绿)。
        for name, (kind, dungeon, center_y) in _SAMPLES.items():
            if kind not in ("boss", "weekly"):
                continue
            with self.subTest(name=name):
                if not (_SHOTS / name).exists():
                    self.skipTest(f"缺样本 {name}")
                matches = identify_row(_row_strip(_load(_SHOTS / name), center_y))
                self.assertEqual(len(matches), 1, name)
                match = matches[0]
                self.assertEqual(match.tier, "", name)
                self.assertEqual(match.quality, "", name)
                self.assertEqual(match.kind, kind, name)
                self.assertEqual(match.dungeon, dungeon, name)
                self.assertFalse(match.tier_conflict, name)

    def test_arc_row_tier_comes_from_material_not_position(self):
        # 螺旋乐那张只列 1 格, 位置在第 1 列但材质是金底(紫档), 按列序会判错。
        path = _SHOTS / "2026-11-19_guide_arc_螺旋乐.jpg"
        if not path.exists():
            self.skipTest("缺样本 2026-11-19_guide_arc_螺旋乐.jpg")
        matches = identify_row(_row_strip(_load(path), 798))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].tier, "紫")
        self.assertEqual(matches[0].tier_source, "color")

    def test_every_sample_reports_its_true_tiers(self):
        expectations = {
            "2026-11-19_guide_ability_扑克茶会.jpg": ("绿", "蓝", "紫"),
            "2026-11-19_guide_ability_越狱艺术.jpg": ("绿", "蓝", "紫"),
            "2026-11-19_guide_ability_惊喜派对.jpg": ("绿", "蓝", "紫"),
            "2026-11-19_guide_ability_小心鸽子.jpg": ("绿", "蓝", "紫"),
            "2026-11-19_guide_ability_心电感应.jpg": ("绿", "蓝", "紫"),
            "2026-11-19_guide_arc_螺旋乐.jpg": ("紫",),
            "2026-11-19_guide_arc_液态梦.jpg": ("蓝", "紫"),
            "2026-11-19_guide_arc_戏剧芯.jpg": ("蓝", "紫"),
            "2026-11-19_guide_arc_冷甜点.jpg": ("蓝", "紫"),
            "2026-11-19_guide_arc_冷甜点_2.jpg": ("绿", "蓝", "紫"),
            "2026-11-19_guide_arc_full_液态梦.jpg": ("绿", "蓝", "紫"),
            "2026-11-19_guide_arc_full_戏剧芯.jpg": ("绿", "蓝", "紫"),
            "2026-11-19_guide_arc_full_冷甜点.jpg": ("绿", "蓝", "紫"),
            "2026-11-19_guide_arc_full_苹果核.jpg": ("绿", "蓝", "紫"),
            "2026-11-19_guide_arc_full_螺旋乐.jpg": ("绿", "蓝", "紫"),
        }
        for name, tiers in expectations.items():
            with self.subTest(name=name):
                path = _SHOTS / name
                if not path.exists():
                    self.skipTest(f"缺样本 {name}")
                _, _, center_y = _SAMPLES[name]
                matches = identify_row(_row_strip(_load(path), center_y))
                self.assertEqual(tuple(m.tier for m in matches), tiers)
                # 档位一律来自材质色。弧盘三档形状几乎一致, 模板标签可能与色不符
                # (tier_conflict=True 只是留痕), 但运行时 tier 必须已被材质色纠正。
                for match in matches:
                    self.assertEqual(match.tier_source, "color", match.key)
                    self.assertTrue(match.key.endswith(match.tier), match.key)


class RuntimeCropTests(unittest.TestCase):
    """线上裁剪口径的回归: 条带半高必须装得下整张卡。

    以前测试用 center±60 裁, 线上用 ratio±0.05*1080=±54, 卡片顶部被切掉,
    格子里放不进正确缩放的模板, 三条目标行 (异能/等级/弧盘) 全部识别失败,
    而测试一直是绿的。这里一律按 POS 的口径裁。
    """

    def test_band_is_tall_enough_for_a_card(self):
        from src.tasks.cultivation_goal.positions import POS
        # 1080 高下卡片约 92px, 两侧各留 10px 余量
        self.assertGreaterEqual(POS.icon_half_height * 1080 * 2, 92 + 20)

    def test_rows_are_recognized_with_runtime_crop(self):
        # 1080 高、无标题栏的纯游戏帧: 卡片顶部就在条带边缘上, 半高一缩就切掉。
        # 2026-11-21 现场图(异能页 30/8 · 20/40 · 10/13)当时三条目标行全部识别失败。
        live = _SHOTS / "2026-11-21_guide_ability_扑克茶会_1080.jpg"
        if live.exists():
            with self.subTest(name=live.name):
                got_kind, got_dungeon, votes = vote_dungeon(
                    identify_row(_runtime_strip(_load(live), "ability")))
                self.assertEqual((got_kind, got_dungeon), ("ability", "扑克茶会"))
                self.assertEqual(votes, 3)
        for name, (kind, dungeon, _center) in _SAMPLES.items():
            with self.subTest(name=name):
                path = _SHOTS / name
                if not path.exists():
                    self.skipTest(f"缺样本 {name}")
                strip = _runtime_strip(_load(path), kind)
                got_kind, got_dungeon, votes = vote_dungeon(identify_row(strip))
                self.assertEqual((got_kind, got_dungeon), (kind, dungeon))
                self.assertGreaterEqual(votes, 1)

    def test_digit_band_is_trimmed_before_matching(self):
        # ability_/arc_ 模板连比例数字一起切了进来, 不裁掉底部数字带时
        # 分数只有 0.70 上下, 压在 0.72 阈值下面; 裁掉后同一张图能到 0.9。
        from src.tasks.cultivation_goal import guide_icons

        cell = _load(_SHOTS / "2026-11-19_guide_ability_扑克茶会.jpg")[560:660, 736:810]
        meta = load_info()
        scored = {}
        for trim in (0, guide_icons.DIGIT_BAND_PX):
            best = 0.0
            for key in available_keys():
                prepared = guide_icons.GUIDE_ICON.get_template(key, 1080)
                if prepared is None:
                    continue
                template = prepared[0] if isinstance(prepared, tuple) else prepared
                if trim:
                    template = template[: template.shape[0] - trim]
                best = max(best, guide_icons._score(cell, template))
            scored[trim] = best
        self.assertGreater(scored[guide_icons.DIGIT_BAND_PX], scored[0] + 0.1, scored)
        self.assertGreaterEqual(scored[guide_icons.DIGIT_BAND_PX], MATCH_MIN_SCORE)


class MissingPlanTests(unittest.TestCase):
    def test_low_tier_missing_implies_high_tiers_missing(self):
        plan = infer_missing(["绿", "蓝", "紫"])
        self.assertEqual(plan.expected, ("绿", "蓝", "紫"))
        self.assertTrue(plan.consistent)

    def test_only_gold_shown_means_lower_tiers_already_satisfied(self):
        plan = infer_missing(["紫"])
        self.assertEqual(plan.observed, ("紫",))
        self.assertEqual(plan.expected, ("紫",))
        self.assertTrue(plan.consistent)

    def test_middle_tier_dropped_is_flagged(self):
        # 绿+紫 同时出现但蓝缺 -> 必是中间那格没认出来, 不能当成「只缺绿和紫」
        plan = infer_missing(["绿", "紫"])
        self.assertEqual(plan.expected, ("绿", "蓝", "紫"))
        self.assertEqual(plan.gaps, ("蓝",))
        self.assertFalse(plan.consistent)

    def test_single_low_tier_is_flagged(self):
        plan = infer_missing(["绿"])
        self.assertEqual(plan.gaps, ("蓝", "紫"))
        self.assertFalse(plan.consistent)

    def test_empty_row_means_nothing_missing(self):
        plan = infer_missing([])
        self.assertEqual(plan.expected, ())
        self.assertTrue(plan.consistent)

    def test_unknown_labels_are_ignored(self):
        self.assertEqual(infer_missing(["紫", "?"]).expected, ("紫",))


class GuardTests(unittest.TestCase):
    def test_blank_row_returns_empty(self):
        blank = np.full((120, 900, 3), 32, np.uint8)
        self.assertEqual(find_cells(blank), [])
        self.assertEqual(identify_row(blank), [])

    def test_empty_input_does_not_raise(self):
        self.assertEqual(identify_row(np.zeros((0, 0, 3), np.uint8)), [])
        self.assertIsNone(identify_cell(np.zeros((5, 5, 3), np.uint8)))


if __name__ == "__main__":
    unittest.main()
