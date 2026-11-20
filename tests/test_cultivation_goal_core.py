import unittest

from src.tasks.cultivation_goal.core import (
    Action,
    MaterialRow,
    PlanConfig,
    PlanState,
    Ratio,
    decide,
    diff_ownage,
    effective_gap,
    effective_stamina,
    LootLedger,
    estimate_runs,
    map_reward_to_counts,
    fold_up,
    next_weekly_remaining,
    parse_ratios,
    parse_weekly_remaining,
    plan_burst,
    row_gap,
)


def row(tab, index, text, cost=40, landing="domain"):
    return MaterialRow(tab, index, cost, landing, parse_ratios(text))


def state(stamina, weekly, rows, **kw):
    return PlanState(stamina, weekly, tuple(rows), **kw)


class RatioParsingTests(unittest.TestCase):
    def test_single_ratio(self):
        self.assertEqual(parse_ratios("0/76"), (Ratio(0, 76),))

    def test_multiple_ratios_in_one_row(self):
        self.assertEqual(parse_ratios("0/10 0/15"), (Ratio(0, 10), Ratio(0, 15)))

    def test_owned_can_exceed_required(self):
        ratios = parse_ratios("1902/212 292/292")
        self.assertEqual(ratios, (Ratio(1902, 212), Ratio(292, 292)))
        self.assertTrue(all(r.satisfied for r in ratios))

    def test_zero_required_is_satisfied(self):
        self.assertTrue(parse_ratios("3/0")[0].satisfied)

    def test_fullwidth_and_spaces(self):
        self.assertEqual(parse_ratios(" ０ ／ ７６ "), (Ratio(0, 76),))

    def test_unreadable_returns_none(self):
        self.assertIsNone(parse_ratios(""))
        self.assertIsNone(parse_ratios("材料不足"))
        self.assertIsNone(parse_ratios("Lv.50 → Lv.80"))

    def test_does_not_match_chained_numbers(self):
        self.assertIsNone(parse_ratios("1/2/3"))
        # 带空格的链式写法同样不是槽位比例, 不能被截成 10/10 当有效读数。
        self.assertIsNone(parse_ratios("10 / 10 / 20"))
        self.assertEqual(parse_ratios("0/10 0/15"), (Ratio(0, 10), Ratio(0, 15)))

    def test_weekly_counter(self):
        self.assertEqual(parse_weekly_remaining("本周剩余 2/3"), 2)
        self.assertEqual(parse_weekly_remaining("０／３"), 0)
        self.assertIsNone(parse_weekly_remaining("2/3 与 1/3"))
        self.assertIsNone(parse_weekly_remaining("无次数"))


class SwallowedSlashTests(unittest.TestCase):
    """真机实证: OCR 把 `/` 认成 `1`，七档预处理全一样，只能在解析层还原。"""

    def test_real_device_values(self):
        # 11-19 10:40 等级第 2 行实际是 47/60；11-18 等级第 2 行 7/36。
        self.assertEqual(parse_ratios("47160"), (Ratio(47, 60),))
        self.assertEqual(parse_ratios("7136"), (Ratio(7, 36),))

    def test_rejects_ambiguous_or_impossible(self):
        # 两个 1 就不知道该切哪，宁可读不出也不猜。
        self.assertIsNone(parse_ratios("1105"))
        # 拥有 > 需要说明这不是"斜杠被吞"，按读不出处理。
        self.assertIsNone(parse_ratios("72160"))
        # 切点落在开头或结尾也拼不出合法比例。
        self.assertIsNone(parse_ratios("160"))
        self.assertIsNone(parse_ratios("471"))

    def test_single_digit_split_is_marked_guessed(self):
        """一边只有一位的切法不唯一(3150→3/50), 必须标低置信交给复核。"""
        ratios = parse_ratios("3150")
        self.assertIsNotNone(ratios)
        self.assertTrue(ratios[0].guessed)
        self.assertFalse(parse_ratios("47160")[0].guessed)

    def test_real_ratios_not_affected(self):
        self.assertEqual(parse_ratios("47/60"), (Ratio(47, 60),))
        self.assertEqual(
            parse_ratios("195/195 184/184 1908/156"),
            (Ratio(195, 195), Ratio(184, 184), Ratio(1908, 156)),
        )


class StaminaBudgetTests(unittest.TestCase):
    def test_no_daily_target_uses_current_stamina(self):
        self.assertEqual(effective_stamina(243, None), 243)

    def test_daily_target_caps_current_stamina(self):
        self.assertEqual(effective_stamina(243, 100), 100)
        self.assertEqual(effective_stamina(30, 100), 30)

    def test_target_reached_or_invalid_reading(self):
        self.assertEqual(effective_stamina(243, 0), 0)
        self.assertEqual(effective_stamina(-1, None), -1)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row("等级", 1, "0/76", cost=60, landing="hunter_map"),
            row("弧盘", 1, "0/10 0/15", cost=40),
            row("异能", 0, "7/20 0/42 0/12", cost=40),
        ]

    def test_weekly_first_even_when_materials_are_pending(self):
        d = decide(state(243, 3, self.rows))
        self.assertEqual(d.action, Action.WEEKLY)
        self.assertEqual(d.cost, 60)

    def test_weekly_exhausted_goes_to_first_pending_material(self):
        d = decide(state(243, 0, self.rows))
        self.assertEqual(d.action, Action.MATERIAL)
        self.assertEqual(d.row.label, "等级-第2行")

    def test_satisfied_row_is_skipped_and_next_material_chosen(self):
        self.rows[0] = row("等级", 1, "80/76", cost=60, landing="hunter_map")
        d = decide(state(100, 0, self.rows))
        self.assertEqual(d.action, Action.MATERIAL)
        self.assertEqual(d.row.label, "弧盘-第2行")

    def test_all_materials_satisfied_goes_to_console(self):
        rows = [row("等级", 1, "80/76", 60), row("弧盘", 1, "10/10 15/15"), row("异能", 0, "20/20")]
        d = decide(state(100, 0, rows))
        self.assertEqual(d.action, Action.CONSOLE)
        self.assertEqual(d.cost, 40)

    def test_unreadable_row_is_fail_closed(self):
        rows = [row("等级", 1, "读不出"), row("弧盘", 1, "0/10"), row("异能", 0, "20/20")]
        d = decide(state(243, 0, rows))
        self.assertEqual(d.action, Action.STOP)
        self.assertIn("读不出", d.reason)

    def test_read_failed_flag_stops(self):
        d = decide(state(243, 0, self.rows, read_failed=True))
        self.assertEqual(d.action, Action.STOP)
        self.assertIn("fail-closed", d.reason)

    def test_invalid_stamina_stops(self):
        d = decide(state(-1, 3, self.rows))
        self.assertEqual(d.action, Action.STOP)

    def test_stamina_below_cheapest_stops(self):
        d = decide(state(39, 0, self.rows))
        self.assertEqual(d.action, Action.STOP)
        self.assertIn("不够任何一次", d.reason)

    def test_stamina_40_skips_weekly_but_can_still_farm_material(self):
        d = decide(state(50, 3, self.rows))
        self.assertEqual(d.action, Action.MATERIAL)
        self.assertEqual(d.cost, 40)

    def test_stamina_only_fits_expensive_row_stops(self):
        rows = [row("等级", 1, "0/76", 60, "hunter_map")]
        d = decide(state(50, 0, rows))
        self.assertEqual(d.action, Action.STOP)
        self.assertIn("不够剩余材料副本", d.reason)

    def test_daily_target_zero_stops_even_with_full_stamina(self):
        d = decide(state(243, 3, self.rows), PlanConfig(daily_target=0))
        self.assertEqual(d.action, Action.STOP)

    def test_max_rounds_guard(self):
        d = decide(state(243, 3, self.rows, rounds_done=30))
        self.assertEqual(d.action, Action.STOP)
        self.assertIn("最大循环", d.reason)

    def test_weekly_blocked_falls_through_to_material(self):
        d = decide(state(243, 3, self.rows, weekly_blocked=True))
        self.assertEqual(d.action, Action.MATERIAL)

    def test_prefer_weekly_can_be_disabled(self):
        d = decide(state(243, 3, self.rows), PlanConfig(prefer_weekly=False))
        self.assertEqual(d.action, Action.MATERIAL)


class MaterialGainTests(unittest.TestCase):
    def test_gap_sums_every_material_in_the_row(self):
        self.assertEqual(row_gap(row("弧盘", 1, "3/10 5/15")), 17)

    def test_gap_is_zero_when_satisfied(self):
        self.assertEqual(row_gap(row("等级", 1, "195/195 1906/81")), 0)

    def test_gap_is_none_when_unreadable(self):
        self.assertIsNone(row_gap(row("等级", 1, "读不出")))

    def test_gain_counts_each_material_slot(self):
        # 弧盘一行掉两种材料, 只看第一个数字会漏掉后一种。
        self.assertEqual(diff_ownage(parse_ratios("0/10 0/15"), parse_ratios("4/10 6/15")), 10)

    def test_gain_never_goes_negative(self):
        self.assertEqual(diff_ownage(parse_ratios("9/10"), parse_ratios("2/10")), 0)

    def test_gain_none_when_slots_change(self):
        self.assertIsNone(diff_ownage(parse_ratios("0/10"), parse_ratios("0/10 0/15")))
        self.assertIsNone(diff_ownage(None, parse_ratios("0/10")))

    def test_estimate_runs_rounds_up(self):
        self.assertEqual(estimate_runs(70, 12), 6)
        self.assertEqual(estimate_runs(76, 76), 1)

    def test_estimate_runs_needs_positive_gain_and_gap(self):
        self.assertIsNone(estimate_runs(70, 0))
        self.assertIsNone(estimate_runs(0, 5))
        self.assertIsNone(estimate_runs(None, 5))
        self.assertIsNone(estimate_runs(70, None))


class LootLedgerTests(unittest.TestCase):
    def test_loot_updates_owned_and_gap(self):
        rows = (row("异能", 0, "0/9"), row("弧盘", 1, "0/10 0/15"))
        ledger = LootLedger(300, rows)
        self.assertTrue(ledger.apply_loot("异能-第1行", (9,)))
        self.assertEqual(row_gap(ledger.rows[0]), 0)
        self.assertTrue(ledger.apply_loot("弧盘-第2行", (4, 5)))
        self.assertEqual(row_gap(ledger.rows[1]), 16)

    def test_wrong_slot_count_is_rejected(self):
        ledger = LootLedger(300, (row("弧盘", 1, "0/10 0/15"),))
        self.assertFalse(ledger.apply_loot("弧盘-第2行", (4,)))
        self.assertFalse(ledger.apply_loot("不存在", (4, 5)))
        self.assertEqual(row_gap(ledger.rows[0]), 25)

    def test_spend_and_state(self):
        ledger = LootLedger(300, (row("异能", 0, "0/9"),))
        ledger.spend(80)
        ledger.spend(300)
        self.assertEqual(ledger.stamina, 0)
        state = ledger.as_state(2, 1, False, ("等级-第2行",))
        self.assertEqual(state.stamina, 0)
        self.assertEqual(state.rows, ledger.rows)
        self.assertEqual(state.rounds_done, 2)
        self.assertEqual(state.skip_labels, ("等级-第2行",))


class BurstPlanTests(unittest.TestCase):
    def test_gap_based_burst_with_safety_margin(self):
        # 缺 9 份、每局约 2 份、上限 3 → 留 1 份复核后最多连打 3 局
        self.assertEqual(plan_burst(9, 2, 300, 40, 3), 3)
        # 缺 5 份 → (5-1)//2 = 2 局
        self.assertEqual(plan_burst(5, 2, 300, 40, 3), 2)

    def test_no_burst_without_reliable_gain(self):
        self.assertEqual(plan_burst(9, None, 300, 40, 3), 0)
        self.assertEqual(plan_burst(None, 2, 300, 40, 3), 0)
        self.assertEqual(plan_burst(9, 0, 300, 40, 3), 0)
        self.assertEqual(plan_burst(2, 2, 300, 40, 3), 0)

    def test_stamina_and_limit_clamp(self):
        self.assertEqual(plan_burst(30, 2, 100, 40, 3), 2)
        self.assertEqual(plan_burst(30, 2, 39, 40, 3), 0)
        self.assertEqual(plan_burst(30, 2, 300, 40, 1), 1)


class RewardMappingTests(unittest.TestCase):
    def test_single_slot_boss_maps_to_its_only_slot(self):
        self.assertEqual(
            map_reward_to_counts([("boss_黑之书", 7)], ("boss_黑之书",)),
            (7,),
        )

    def test_three_tiers_land_in_matching_slots(self):
        slots = ("ability_扑克茶会_紫", "ability_扑克茶会_蓝", "ability_扑克茶会_绿")
        got = map_reward_to_counts(
            [("ability_扑克茶会_绿", 3), ("ability_扑克茶会_紫", 1)], slots
        )
        self.assertEqual(got, (1, 0, 3))

    def test_unknown_key_returns_none_instead_of_guessing(self):
        self.assertIsNone(map_reward_to_counts([("金币", 12000)], ("boss_黑之书",)))
        self.assertIsNone(map_reward_to_counts([("boss_黑之书", 7)], ()))

    def test_multiple_cards_of_same_key_are_summed(self):
        self.assertEqual(
            map_reward_to_counts([("boss_黑之书", 2), ("boss_黑之书", 3)],
                                 ("boss_黑之书",)),
            (5,),
        )


class CombineUpTests(unittest.TestCase):
    """弧盘材料绿蓝紫三档，3 个低档合 1 个高档（11-19 合成界面实证）。"""

    def test_folds_each_tier_by_ratio(self):
        # (紫, 蓝, 绿)：3 紫 + 6 蓝(合 2) + 9 绿(合 1 紫) = 6 个紫等价
        self.assertEqual(fold_up((3, 6, 9)), 6)

    def test_partial_tier_never_counts(self):
        # 2 个蓝拼不出 1 个紫，不能当整的用。
        self.assertEqual(fold_up((0, 2, 0)), 0)
        self.assertEqual(fold_up((0, 0, 8)), 0)

    def test_no_tiers(self):
        self.assertEqual(fold_up(()), 0)
        self.assertEqual(fold_up(None), 0)

    def test_effective_gap_subtracts_combine_result(self):
        # 真实一局: 弧盘第 2 行 3/15 缺 12, 蓝有 6 个能合 2 个 -> 实缺口 10。
        r = row("弧盘", 1, "3/15")
        self.assertEqual(row_gap(r), 12)
        self.assertEqual(effective_gap(r, extras=[2]), 10)

    def test_effective_gap_never_negative(self):
        r = row("弧盘", 1, "3/15")
        self.assertEqual(effective_gap(r, extras=[99]), 0)

    def test_effective_gap_without_extras_matches_row_gap(self):
        r = row("弧盘", 1, "0/10 0/15")
        self.assertEqual(effective_gap(r), row_gap(r))


class WeeklyAccountingTests(unittest.TestCase):
    def test_only_confirmed_claim_decrements(self):
        s = state(243, 3, [])
        self.assertEqual(next_weekly_remaining(s, True), 2)
        self.assertEqual(next_weekly_remaining(s, False), 3)

    def test_never_goes_below_zero(self):
        s = state(243, 0, [])
        self.assertEqual(next_weekly_remaining(s, True), 0)


if __name__ == "__main__":
    unittest.main()
