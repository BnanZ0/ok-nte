import unittest

from src.tasks.cultivation_goal.core import (
    Action,
    MaterialRow,
    PlanConfig,
    PlanState,
    Ratio,
    decide,
    diff_ownage,
    effective_stamina,
    LootLedger,
    estimate_runs,
    map_reward_to_counts,
    claims_needed,
    lowest_units,
    next_weekly_remaining,
    parse_ratios,
    parse_weekly_remaining,
    console_index_for,
    match_console_set,
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
    def test_gap_is_counted_in_lowest_tier_units(self):
        # 3 绿 + 5 蓝 vs 10 绿 + 15 蓝 = 18 件 vs 55 件(最低档) → 缺 37。
        self.assertEqual(row_gap(row("弧盘", 1, "3/10 5/15")), 37)

    def test_gap_is_zero_when_satisfied(self):
        self.assertEqual(row_gap(row("等级", 1, "195/195 1906/81")), 0)

    def test_gap_is_none_when_unreadable(self):
        self.assertIsNone(row_gap(row("等级", 1, "读不出")))

    def test_gain_counts_each_material_slot(self):
        # 弧盘一行掉两种材料, 只看第一个数字会漏掉后一种; 增量也折最低档(4 + 6*3)。
        self.assertEqual(diff_ownage(parse_ratios("0/10 0/15"), parse_ratios("4/10 6/15")), 22)

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
        # 4 绿 + 5 蓝 = 19 件, 需求 55 件 → 缺 36。
        self.assertEqual(row_gap(ledger.rows[1]), 36)

    def test_wrong_slot_count_is_rejected(self):
        ledger = LootLedger(300, (row("弧盘", 1, "0/10 0/15"),))
        self.assertFalse(ledger.apply_loot("弧盘-第2行", (4,)))
        self.assertFalse(ledger.apply_loot("不存在", (4, 5)))
        self.assertEqual(row_gap(ledger.rows[0]), 55)

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


class ConsoleSetTests(unittest.TestCase):
    """空幕页只有套装名，关卡只能靠套装名反查：认不出就要停，不许盲刷 1 号。"""

    def test_reads_set_name_out_of_ocr_noise(self):
        # 实际读到的是 `推荐套装 「失落光芒」x4`，书名号/空格/x4 都要吃掉。
        self.assertEqual(match_console_set("推荐套装 「失落光芒」x4"), ("钟表把戏", "失落光芒"))

    def test_colon_variants_all_match(self):
        for text in ("「恶魔之血：诅咒」", "「恶魔之血:诅咒」", "恶魔之血 诅咒"):
            self.assertEqual(match_console_set(text), ("雕塑展馆", "恶魔之血：诅咒"))

    def test_truncated_name_falls_back_to_prefix(self):
        # 冒号后半段被 OCR 吞掉是常态：只读到「恶魔之血」也要认出来。
        self.assertEqual(match_console_set("推荐套装「恶魔之血」"), ("雕塑展馆", "恶魔之血：诅咒"))

    def test_unknown_set_stops_instead_of_farming_first(self):
        index, why = console_index_for(("推荐套装 「虚构套装甲」x4", "当前套装 「虚构套装甲」x4"))
        self.assertIsNone(index)
        self.assertIn("未收录", why)

    def test_suggest_set_wins_over_current(self):
        index, why = console_index_for(("「缇娅的夜间酒馆」", "「失落光芒」"))
        self.assertEqual(index, 3)  # 守卫萝卜
        self.assertIn("推荐套装", why)

    def test_current_set_used_when_suggest_unreadable(self):
        index, _ = console_index_for(("", "「街头拳王」"))
        self.assertEqual(index, 1)  # 雕塑展馆

    def test_dungeon_index_matches_console_id(self):
        # 下标 +1 就是异象界域的「空幕序号」：纬线织机 = 3。
        self.assertEqual(console_index_for(("「森林萤火之心」", ""))[0] + 1, 3)

    def test_late_nodes_and_aliases(self):
        # 兔子洞共 6 个节点: 音速蓝刺猬属于第 5 个「心智图谱」(11-22 主人查证)。
        self.assertEqual(console_index_for(("「音速蓝刺猬」x4", ""))[0] + 1, 5)
        self.assertEqual(console_index_for(("「影之信条」x4", ""))[0] + 1, 6)
        # 攻略站旧译也要认: 静谧山庄 = 宁静庄园, 西雅的夜酒馆 = 缇娅的夜间酒馆。
        self.assertEqual(match_console_set("「静谧山庄」"), ("心智图谱", "静谧山庄"))
        self.assertEqual(match_console_set("「西雅的夜酒馆」"), ("守卫萝卜", "西雅的夜酒馆"))


class LowestTierTests(unittest.TestCase):
    """异能/弧盘一行三档, 3 个低档合 1 个高档, 反过来 1 个高档也能拆 3 个低档。

    既然合成与拆解都允许, 档位只是同一材料的形态, 判定必须把所有档位折成最低档件数
    再比总量; 逐档比会把「高档多出来的能拆下来补低档」误判成缺料, 白刷一局。
    """

    def test_all_tiers_fold_into_lowest_units(self):
        self.assertEqual(
            lowest_units([Ratio(48, 8), Ratio(32, 40), Ratio(16, 13)]), (288, 245))

    def test_low_tier_surplus_fills_the_row(self):
        r = row("异能", 0, "48/8 32/40 16/13")
        self.assertTrue(r.satisfied)
        self.assertEqual(row_gap(r), 0)

    def test_high_tier_surplus_can_be_broken_down(self):
        # 低档一件没有、高档多出 60 个: 拆下来足够补低档的 15, 这一行已经够了。
        r = row("异能", 0, "0/15 60/0")
        self.assertTrue(r.satisfied)
        self.assertEqual(row_gap(r), 0)

    def test_spare_high_tier_covers_the_last_missing_piece(self):
        # 11-22 第 3 轮实况 42/8 28/40 14/13: 折最低档 252 件 > 245 件, 靠拆 1 个紫就够。
        r = row("异能", 0, "42/8 28/40 14/13")
        self.assertTrue(r.satisfied)
        self.assertEqual(row_gap(r), 0)

    def test_two_short_is_still_short(self):
        # 13 个紫刚好够自己, 没有余量可拆: 243 件 < 245 件, 还差 2 件。
        r = row("异能", 0, "42/8 28/40 13/13")
        self.assertFalse(r.satisfied)
        self.assertEqual(row_gap(r), 2)

    def test_two_low_short_is_not_counted(self):
        # 绿只多出 2 个, 不到 3 个, 折不上也拆不下去: 70 件 vs 128 件。
        self.assertEqual(lowest_units([Ratio(10, 8), Ratio(20, 40)]), (70, 128))
        r = row("异能", 0, "10/8 20/40")
        self.assertFalse(r.satisfied)
        self.assertEqual(row_gap(r), 58)

    def test_single_tier_row_unchanged(self):
        r = row("等级", 1, "61/60")
        self.assertTrue(r.satisfied)
        self.assertEqual(row_gap(r), 0)

    def test_unreadable_row_stays_unknown(self):
        r = row("异能", 0, "")
        self.assertIsNone(r.satisfied)
        self.assertIsNone(row_gap(r))

    def test_claims_needed_uses_per_claim_when_known(self):
        self.assertEqual(claims_needed(120, 40), 3)
        # 没有产出记录时按「一份 ≈ 1 件最高档」估: 三档行 1 紫 = 9 件最低档。
        self.assertEqual(claims_needed(90, None, 3), 10)
        self.assertIsNone(claims_needed(None, 40))


class WeeklyAccountingTests(unittest.TestCase):
    def test_only_confirmed_claim_decrements(self):
        s = state(243, 3, [])
        self.assertEqual(next_weekly_remaining(s, True), 2)
        self.assertEqual(next_weekly_remaining(s, False), 3)

    def test_never_goes_below_zero(self):
        s = state(243, 0, [])
        self.assertEqual(next_weekly_remaining(s, True), 0)


class GroupLockTests(unittest.TestCase):
    """宿主加载插件后会用 script_name 覆盖 group_name, 必须锁回「体力本」。"""

    def test_task_declares_locked_group(self):
        from src.tasks.CultivationGoalTask import CultivationGoalTask
        from src.tasks.cultivation_goal.group_lock import FixedGroup

        self.assertIsInstance(vars(CultivationGoalTask).get("group_name"), FixedGroup)
        self.assertEqual(CultivationGoalTask.group_name, "体力本")

    def test_fixed_group_ignores_assignment(self):
        from src.tasks.cultivation_goal.group_lock import FixedGroup

        class Target:
            group_name = FixedGroup("体力本")

        task = Target()
        task.group_name = "消耗体力培养角色"
        self.assertEqual(task.group_name, "体力本")


if __name__ == "__main__":
    unittest.main()
