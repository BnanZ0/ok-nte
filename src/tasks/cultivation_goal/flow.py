"""培养目标刷本流程：独立任务 CultivationGoalTask 调用 run_cultivation_goal 进入。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

import cv2

from src.Labels import Labels
from src.tasks.cultivation_goal.core import (
    RATIO_RE,
    Action,
    LootLedger,
    MaterialRow,
    PlanConfig,
    PlanState,
    Ratio,
    decide,
    diff_ownage,
    effective_stamina,
    estimate_runs,
    parse_ratios,
    plan_burst,
    parse_weekly_remaining,
    repair_swallowed_slash,
    row_gap,
)
from src.tasks.cultivation_goal.positions import HUNTER_BOSS_NAMES, POS, TARGET_ROWS
from src.tasks.cultivation_goal.report import RunReport, row_snapshot
from src.tasks.cultivation_goal.version import VERSION as CG_VERSION

# 连打确认框：小区域对应继续挑战弹窗，大区域对应收场弹窗。
CG_BURST_CONFIRM_RANGE = (0.6469, 0.8174, 0.7027, 0.9097)
CG_FINAL_CONFIRM_RANGE = (0.4074, 0.8174, 0.4578, 0.9056)

# 用来区分「实例上本来没有这个属性」和「属性值就是 None」。
_MISSING = object()

# 原版按这个 info 值决定要不要进副本, 0 表示今日体力目标已达标。
CG_INFO_MUST_USE = "must use stamina"
# 培养目标模式要刷空体力, 委派空幕时用一个大值让它不限次。
CG_CONSOLE_STAMINA_OPEN = 9999


class CultivationGoalMixin:
    CONF_CULTIVATION_GOAL = "按培养目标养成刷本"
    CONF_CG_WEEKLY_FIRST = "周本刷取"
    CONF_CG_LOOT_STATS = "掉落统计"
    # 连打不再暴露配置: 产出不足以覆盖缺口就一直连打, 这里只留内部保险丝。
    CG_BURST_LIMIT = 20

    def _cg_report(self):
        """报告对象可注入，便于离线测试。"""
        report = getattr(self, "cg_report", None)
        if report is None:
            report = RunReport()
            self.cg_report = report
        return report

    def _cg_note(self, level: str, message: str):
        text = f"[培养目标] {message}"
        if level == "warning":
            self.log_warning(text)
            self._cg_report().emit("log", level=level, message=message)
        elif level == "error":
            self.log_error(text)
            self._cg_report().emit("log", level=level, message=message)
        else:
            self.log_info(text)

    def _cg_capture(self, tag: str):
        """关键失败留现场截图。"""
        try:
            self.screenshot(f"cg_{tag}")
            self._cg_report().emit("capture", tag=tag)
        except Exception as error:
            self._cg_note("warning", f"截图失败 {tag}: {error}")

    def _cg_scan_reward_list(self, tag: str):
        """认奖励卡材料与数量, 存入 _cg_reward_loot; 每次扫描都登记一次完整性结果。"""

        flags = getattr(self, "_cg_scan_flags", None)
        if flags is None:
            flags = self._cg_scan_flags = []

        def mark(complete: bool) -> bool:
            flags.append(bool(complete))
            return complete

        try:
            from src.tasks.cultivation_goal.reward_icons import identify_rewards_detailed, summarize

            frame = getattr(self, "frame", None)
            if frame is None or getattr(frame, "size", 0) == 0:
                # 没画面也是一次没读到的清单, 必须登记, 否则漏扫会被当成整轮完整。
                self._cg_note("warning", f"奖励清单识别({tag}): 无可用画面")
                mark(False)
                return []
            matches, card_total = identify_rewards_detailed(frame, self.height)
            loot = []
            partial = False
            for match in matches:
                # 副本结算页数量印在卡片下沿外, 野外清单在卡内右下: 各自优先, 读不出再换另一个框。
                below = getattr(match, "count_box_below", None)
                if tag == "副本" and below:
                    count = self._cg_read_reward_count(below, match.count_box)
                else:
                    count = self._cg_read_reward_count(match.count_box)
                if count is None or count <= 0:
                    self._cg_note("warning", f"奖励卡 {match.dungeon} 数量读不出, 该格跳过")
                    partial = True
                    continue
                loot.append({"key": match.key, "count": count, "dungeon": match.dungeon,
                             "inliers": match.inliers})
            if card_total is not None and len(matches) < card_total:
                # 投影切出了卡但有的没认出模板: 缺的那张不能按零入账, 整轮按残缺处理。
                self._cg_note(
                    "warning",
                    f"奖励清单切出 {card_total} 张卡只认出 {len(matches)} 张, 本轮标记残缺",
                )
                partial = True
            # 有奖励卡认出模板但数量 OCR 失败时视为本轮读数残缺, 主循环不入账。
            self._cg_loot_partial = partial or getattr(self, "_cg_loot_partial", False)
            # 连打多局时逐次累计, 同材料数量相加; 每轮开打前由主循环清零。
            merged = list(getattr(self, "_cg_reward_loot", None) or [])
            by_key = {item["key"]: i for i, item in enumerate(merged)}
            for item in loot:
                idx = by_key.get(item["key"])
                if idx is not None:
                    merged[idx] = {**merged[idx], "count": merged[idx]["count"] + item["count"]}
                else:
                    merged.append(item)
                    by_key[item["key"]] = len(merged) - 1
            self._cg_reward_loot = merged
            # 逐次记下这份清单是否读全: 同一轮里有清单没读全, 整轮不能入账。
            mark(bool(loot) and not partial)
            self._cg_note("info", f"奖励清单识别({tag}): {summarize(matches)}, 累计 {merged}")
            if loot:
                self._cg_report().emit("loot", tag=tag, items=loot)
            return loot
        except Exception as error:
            mark(False)
            self._cg_note("warning", f"奖励清单识别失败: {error}")
            return []

    def _cg_read_reward_count(self, box_ratio: tuple[float, float, float, float],
                              alt_box_ratio: tuple[float, float, float, float] | None = None
                              ) -> int | None:
        """读奖励卡右下角数量; 只接受唯一一个数字段, 多组数字视为读不出。

        主框读不出(空/多组数字)时换备选框再试一次, 两框都失败才判读不出。
        """
        for candidate in (box_ratio, alt_box_ratio):
            if not candidate:
                continue
            try:
                box = self.box_of_screen(*candidate, name="cg_reward_count", hcenter=True)
                results = self.ocr(box=box, match=None) or []
                import re as _re
                for result in results:
                    text = (result.name or "").strip()
                    if not text:
                        continue
                    # "×4"这类取 4; "4张5个"把两组数字拼起来就是错账, 宁可重读也不入账。
                    groups = _re.findall(r"\d+", text)
                    if len(groups) > 1:
                        self._cg_note("warning", f"奖励数量出现多组数字({text}), 换框重读")
                        break
                    if groups:
                        count = int(groups[0])
                        if count > 0:
                            return count
            except Exception as error:
                self._cg_note("warning", f"奖励数量 OCR 失败: {error}")
        return None

    def _cg_arm_reward_capture(self, limit=6):
        """在点掉确认框前识别奖励清单（on_found 回调是唯一时机），逐次累计掉落。"""
        try:
            raw = self.wait_click_confirm
            state = {"n": 0, "skipped": 0, "limit": limit}
            self._cg_scan_state = state
            loot_ranges = self._cg_loot_confirm_ranges()

            def patched(*args, **kwargs):
                confirm_range = kwargs.get("range")
                if confirm_range is None and len(args) >= 2:
                    confirm_range = args[1]
                # 只扫白名单确认框(连打继续/收场/周本领奖): ESC 离场、团灭、入场等确认没有奖励清单。
                if not isinstance(confirm_range, tuple) or confirm_range not in loot_ranges:
                    return raw(*args, **kwargs)
                if state["n"] >= state["limit"]:
                    # 额度用完仍要照常确认, 但要记下有领取没能扫描, 主循环据此回页复核。
                    state["skipped"] += 1
                    return raw(*args, **kwargs)
                found = kwargs.get("on_found")
                if found is None and len(args) >= 3:
                    found = args[2]

                def on_found():
                    state["n"] += 1
                    self._cg_scan_reward_list("副本")
                    if callable(found):
                        found()

                if len(args) >= 3:
                    args = args[:2] + (on_found,) + args[3:]
                else:
                    kwargs["on_found"] = on_found
                return raw(*args, **kwargs)

            self._cg_shadow("wait_click_confirm", patched)
            return True
        except Exception as error:
            self._cg_note("warning", f"奖励清单识别未挂上: {error}")
            return False

    def _cg_loot_confirm_ranges(self):
        """带奖励清单的确认框白名单: 连打继续、收场、周本领奖; 其余确认一律不扫。"""
        ranges = {CG_BURST_CONFIRM_RANGE, CG_FINAL_CONFIRM_RANGE}
        try:
            from src.tasks.AnomalyPilgrimage import AnomalyPilgrimage

            box = getattr(AnomalyPilgrimage, "REWARD_CLAIM_BOX", None)
            if isinstance(box, tuple):
                ranges.add(box)
        except Exception:
            pass
        return tuple(ranges)

    def _cg_borrow(self, owner_cls):
        """临时借用上游任务类的常量与方法，用完归还。

        直接 Owner.do_run(self) 时 self 仍是本实例，Owner 内部 self.xxx() 找不到它自己的
        方法和类常量；挂到实例上再摘掉，比复制流程更不易跟上游漂移。
        """
        borrowed = []
        for name in dir(owner_cls):
            if name.startswith("__") or hasattr(self, name):
                continue
            raw = owner_cls.__dict__.get(name)
            if raw is None:
                continue  # 继承来的属性，本类和上游同基类，无需借
            if isinstance(raw, staticmethod):
                value = raw.__func__
            else:
                value = raw.__get__(self, type(self)) if callable(raw) else raw
            setattr(self, name, value)
            borrowed.append(name)
        return borrowed

    def _cg_release(self, borrowed):
        for name in borrowed:
            try:
                delattr(self, name)
            except AttributeError:
                pass

    def _cg_shadow(self, name, value):
        """在实例上盖一层同名方法，不改类、不写盘。"""
        if not callable(value):
            raise TypeError(f"_cg_shadow 只用于方法, {name} 收到 {type(value).__name__}")
        self.__dict__[name] = value

    def _cg_push_shadows(self, mapping):
        """批量盖方法并记住原值（含"本来没有"），退出时按原样还原。"""
        saved = {}
        for name, value in mapping.items():
            if not callable(value):
                raise TypeError(f"_cg_shadow 只用于方法, {name} 收到 {type(value).__name__}")
            saved[name] = self.__dict__.get(name, _MISSING)
            self.__dict__[name] = value
        return saved

    def _cg_pop_shadows(self, saved):
        """还原 _cg_push_shadows 记录的方法, 原本没有的才删除。"""
        for name, original in (saved or {}).items():
            if original is _MISSING:
                self.__dict__.pop(name, None)
            else:
                self.__dict__[name] = original

    def _cg_reset_scan_quota(self, limit=6):
        """每个动作单独发扫描额度; 额度用完的已确认领取由主循环回页复核, 不默默丢账。"""
        state = getattr(self, "_cg_scan_state", None)
        if state is None:
            self._cg_scan_state = {"n": 0, "skipped": 0, "limit": limit}
        else:
            state.update(n=0, skipped=0, limit=limit)
        self._cg_scan_flags = []

    def run_cultivation_goal(self):
        """主流程：按缺口选本刷体力，账本模式下不回页重读。"""
        self.info_set("培养目标版本", CG_VERSION)
        weekly_first = bool(self.config.get(self.CONF_CG_WEEKLY_FIRST, True))
        daily_target = self.resolve_stamina_target()
        if not daily_target:
            # 今日 180 体力 / 100 活跃已达标时原任务会跳过；培养目标模式是主人主动勾选的，继续按当前体力跑。
            self._cg_note("info", "今日体力目标已达标, 培养目标模式不受原任务跳过限制")
            daily_target = None
        cfg = PlanConfig(
            daily_target=daily_target,
            max_rounds=30,
            prefer_weekly=weekly_first,
        )
        report = self._cg_report()
        if not weekly_first:
            remaining, weekly_blocked = 0, True
            self._cg_note(
                "info",
                "未开启「周本刷取」: 直接刷其它材料, 周本只在异能第2行缺料时才打",
            )
        else:
            remaining = self._cg_read_weekly_remaining()
            weekly_blocked = remaining is None
            if weekly_blocked:
                remaining = 0
                self._cg_note("warning", "周本剩余次数读不出, 本轮跳过周本")
        self.info_set("周本剩余次数", remaining)
        report.emit(
            "start",
            daily_target=cfg.daily_target,
            weekly_remaining=remaining,
            weekly_blocked=weekly_blocked,
            config={
                "weekly_cost": cfg.weekly_cost,
                "console_cost": cfg.console_cost,
                "weekly_limit": cfg.weekly_limit,
                "max_rounds": cfg.max_rounds,
                "prefer_weekly": cfg.prefer_weekly,
            },
        )

        rounds = weekly_done = material_done = console_done = 0
        # 每次运行从零记账。
        self._cg_prev_owned = {}
        # 上一目标的槽位记录不能泄漏到本次: 换角色后槽位数会变, 启动时清掉重建。
        self._cg_last_slots = {}
        # 每行上一轮领了几份（双倍算 2），用于把面板增量摊成单局产出。
        self._cg_claim_units = {}
        self._cg_burst_next = (1, None)
        self._cg_zero_streak = {}
        self._cg_last_action_labels = None
        self._cg_reward_loot = []
        self._cg_loot_partial = False
        self._cg_scan_flags = []
        self._cg_burst_unconfirmed = False
        # 今日还需消耗的体力: 每花一笔就递减, 否则同一个目标值会被每轮重复放行。
        budget_left = daily_target
        # 已确认花掉的体力, 只增不减; 回页补体力时不能因此把预算放回去。
        spent_confirmed = 0
        self._cg_start_stamina = None
        self._cg_weekly_budget = None
        self._cg_arm_reward_capture()
        # 单行失败只跳过该行，不让整场停摆。
        skipped: list[str] = []
        ledger = None
        try:
            while True:
                self.ensure_main(check_exit=True)
                if ledger is None:
                    stamina = self._cg_read_stamina()
                    rows = self._cg_read_rows()
                    if stamina is not None and stamina >= 0:
                        if getattr(self, "_cg_start_stamina", None) is None:
                            self._cg_start_stamina = stamina
                        # 面板体力是真值, 但中途补体力会让差值偏小; 已确认的消耗只增不减。
                        spent_measured = max(0, self._cg_start_stamina - stamina)
                        spent_total = max(spent_confirmed, spent_measured)
                        budget_left = (
                            None
                            if daily_target is None
                            else max(0, daily_target - spent_total)
                        )
                    if rows:
                        ledger = LootLedger(stamina, rows)
                        # 基准里沿用旧值的行不能累计掉落, 防止陈旧基准把掉落记重;
                        # 未核验行(图标本轮没认出)的 OCR 读数是新鲜的, 照常入账。
                        self._cg_ledger_stale = set(getattr(self, "_cg_stale_labels", ()) or ())
                        self._cg_note("info", "账本模式: 开局读一次面板, 之后靠掉落增量推进")
                else:
                    # 账本模式：体力与材料按本局领奖本地更新。
                    stamina = ledger.stamina
                    rows = ledger.rows
                gains = self._cg_track_gains(rows)
                skipped.extend(self._cg_drop_starved(rows, gains, skipped))
                if ledger is None:
                    state = PlanState(
                        stamina=stamina,
                        weekly_remaining=remaining,
                        rows=rows or (),
                        rounds_done=rounds,
                        weekly_blocked=weekly_blocked,
                        read_failed=rows is None,
                        skip_labels=tuple(skipped),
                    )
                else:
                    state = ledger.as_state(rounds, remaining, weekly_blocked, skipped)
                # 每局开打前清掉上一局的抓拍，避免旧掉落重复入账。
                self._cg_reward_loot = []
                self._cg_loot_partial = False
                self._cg_scan_flags = []
                round_cfg = cfg if budget_left is None else replace(cfg, daily_target=budget_left)
                decision = decide(state, round_cfg)
                self.info_set("培养目标决策", f"{decision.action.value}: {decision.reason}")
                report.emit(
                    "round",
                    round=rounds + 1,
                    stamina=stamina,
                    effective_stamina=effective_stamina(stamina, round_cfg.daily_target),
                    weekly_remaining=remaining,
                    weekly_blocked=weekly_blocked,
                    read_failed=rows is None,
                    rows=row_snapshot(rows),
                    gains=gains,
                    gaps={row.label: row_gap(row) for row in (rows or ())},
                    ocr_texts=getattr(self, "_cg_last_texts", {}),
                    action=decision.action.value,
                    reason=decision.reason,
                    label=decision.row.label if decision.row else None,
                    cost=decision.cost,
                )
                if rows:
                    self._cg_note("info", self._cg_material_brief(rows, gains))
                self._cg_note(
                    "info",
                    f"第 {rounds + 1} 轮 体力={stamina} 周本剩余={remaining} "
                    f"→ {decision.action.value} / {decision.reason}",
                )

                if decision.action is Action.STOP:
                    self._cg_stop(report, decision.reason, rounds, weekly_done, material_done, console_done)
                    return True if rounds else None

                # 本轮是否真的打了一局: 没出发的行(如周本次数已用完)不能连累账本。
                attempted = True

                if decision.action is Action.WEEKLY:
                    # 巡礼一次可能同时补两行, 所以待核产出的行按 landing 取, 不只看当前行。
                    self._cg_last_action_labels = [
                        row.label for row in (rows or ()) if row.landing == "weekly"
                    ]
                    # 原版按面板体力排领取次数, 这里把今日剩余预算一起传下去。
                    self._cg_weekly_budget = effective_stamina(stamina, round_cfg.daily_target)
                    self._cg_reset_scan_quota(6)
                    claimed, finished = self._cg_run_weekly()
                    ok = claimed > 0
                    report.emit("action", round=rounds + 1, action="weekly", label="异象巡礼", ok=ok)
                    self.info_set("周本本次领到", claimed)
                    if ok:
                        # 领到几次扣几次; finished 只表示本次队列跑完, 不代表次数清零。
                        remaining = max(0, remaining - claimed)
                        weekly_done += claimed
                        self.info_set("周本剩余次数", remaining)
                    if not finished:
                        # 中途失败则本场不再碰周本，已领次数照样算数。
                        weekly_blocked = True
                        self._cg_note(
                            "warning",
                            f"周本未跑完, 已领到 {claimed} 次; 本次不再尝试周本",
                        )
                        if not ok:
                            self._cg_capture("weekly_failed")
                elif decision.action is Action.MATERIAL:
                    self._cg_last_action_labels = [decision.row.label]
                    # 连打/双倍只按生效体力(含日预算)规划, 防止面板余额绕过预算。
                    budget = effective_stamina(stamina, round_cfg.daily_target)
                    self._cg_weekly_budget = budget
                    burst, per_claim = self._cg_burst_plan(decision.row, budget, gains)
                    self._cg_burst_next = (burst, per_claim)
                    # 一局一次确认: 额度按本轮计划局数放宽, 用完的由主循环回页复核。
                    self._cg_reset_scan_quota(max(6, int(burst) + 2))
                    if decision.row.landing == "weekly" and weekly_first and remaining <= 0:
                        # 面板读到周本次数=0: 巡礼必然空手而归, 还会误报「领奖未确认」把账本作废。
                        self._cg_note(
                            "warning",
                            f"{decision.row.label} 依赖异象巡礼, 周本次数已用完, 本轮跳过该行",
                        )
                        ok = False
                        attempted = False
                    else:
                        ok = self._cg_run_material(decision.row, budget)
                    report.emit(
                        "action",
                        round=rounds + 1,
                        action="material",
                        label=decision.row.label,
                        ok=ok,
                        burst=burst,
                        per_claim=per_claim,
                    )
                    if burst > 1:
                        estimate = f"{per_claim:.2f}" if per_claim else "未知"
                        self._cg_note(
                            "info",
                            f"{decision.row.label} 本轮连打上限 {burst} 局 (单局约 +{estimate} 份)",
                        )
                    if ok:
                        material_done += 1
                    else:
                        skipped.append(decision.row.label)
                        self._cg_note(
                            "warning",
                            f"{decision.row.label} 本局未完成, 本次跳过该行继续其它行",
                        )
                        if attempted:
                            self._cg_capture("material_failed")
                elif decision.action is Action.CONSOLE:
                    console_done = 1
                    result = self._cg_run_console(effective_stamina(stamina, round_cfg.daily_target))
                    report.emit("action", round=rounds + 1, action="console", ok=bool(result))
                    self._cg_stop(report, "切换空幕交回原版逻辑", rounds, weekly_done, material_done, console_done)
                    return result
                if ledger is not None:
                    spent = 0
                    if decision.action is Action.WEEKLY:
                        spent = cfg.weekly_cost * claimed if ok else 0
                    elif decision.action is Action.MATERIAL and ok:
                        units = getattr(self, "_cg_claim_units", {}).get(decision.row.label, 1)
                        spent = decision.row.cost * units
                    if spent:
                        ledger.spend(spent)
                        spent_confirmed += spent
                        if budget_left is not None:
                            budget_left = max(0, budget_left - spent)
                        self._cg_note("info", f"账本扣体力 {spent}, 剩 {ledger.stamina}")
                    if decision.action in (Action.WEEKLY, Action.MATERIAL):
                        labels = getattr(self, "_cg_last_action_labels", None) or ()
                        if labels:
                            last_label = labels[0]
                        elif decision.action is Action.MATERIAL and decision.row:
                            last_label = decision.row.label
                        else:
                            last_label = None
                        loot = self._cg_read_loot(last_label)
                        flags = list(getattr(self, "_cg_scan_flags", None) or ())
                        if not ok and not attempted:
                            # 预检就没出发(周本次数已用完): 体力没动、掉落没发生, 账本照旧。
                            self._cg_note("info", "本行未出发, 账本与体力保持不变")
                        elif not ok:
                            # 领奖未确认: 体力是否被扣未知, 本轮账本整体作废, 回页复核两项。
                            self._cg_reward_loot = []
                            self._cg_loot_partial = False
                            ledger = None
                            self._cg_note(
                                "warning", "领奖未确认, 本轮账本作废, 下轮回养成指南复核体力与材料"
                            )
                        elif decision.action is Action.WEEKLY and getattr(self, "_cg_weekly_unconfirmed", 0):
                            # 周本有领取没看到结算确认: 已确认次数照记, 但掉落与扣费都不完整。
                            ledger = None
                            self._cg_note(
                                "warning",
                                f"周本有 {self._cg_weekly_unconfirmed} 次领取未确认, "
                                "已确认次数照记, 下轮回养成指南复核体力与材料",
                            )
                        elif getattr(self, "_cg_burst_unconfirmed", False):
                            # 连打中有一局未确认: 前几局统计保留, 但体力扣没扣未知, 回页复核。
                            self._cg_note(
                                "warning",
                                "连打中有局未确认领奖, 已确认部分照记, 下轮回页复核体力与材料",
                            )
                            ledger = None
                        elif (getattr(self, "_cg_scan_state", None) or {}).get("skipped"):
                            self._cg_note(
                                "warning", "本轮有已确认领取没能扫描奖励清单, 下轮回页复核"
                            )
                            ledger = None
                        elif flags and not all(flags):
                            self._cg_note(
                                "warning", "本轮有奖励清单没认出材料, 不按部分掉落入账, 下轮回页复核"
                            )
                            ledger = None
                        elif not loot:
                            # 掉落识别还没接上: 不凭空猜数, 下轮回养成指南复核一次。
                            self._cg_note("info", "本局掉落未能读取, 下轮回养成指南复核一次")
                            ledger = None
                        elif getattr(self, "_cg_loot_partial", False):
                            self._cg_note(
                                "warning", "本轮有奖励卡数量读不出, 掉落不入账, 下轮回养成指南复核"
                            )
                            ledger = None
                        else:
                            for label, counts in loot.items():
                                if label in getattr(self, "_cg_ledger_stale", ()):
                                    self._cg_note(
                                        "warning",
                                        f"{label} 账本基准沿用旧值, 不能累计掉落, 下轮回页复核",
                                    )
                                    ledger = None
                                    break
                                if not ledger.apply_loot(label, counts):
                                    self._cg_note(
                                        "warning", f"掉落 {label} 槽位数不符, 下轮回页复核"
                                    )
                                    ledger = None
                                    break
                            if ledger is not None and self.config.get(self.CONF_CG_LOOT_STATS, True):
                                self._cg_log_loot_stats(ledger, loot)
                rounds += 1

        finally:
            # 无论正常结束还是异常中断, 都要摘掉奖励抓拍阴影。
            self.__dict__.pop("wait_click_confirm", None)
    def _cg_stop(self, report, reason, rounds, weekly_done, material_done, console_done):
        self.__dict__.pop("wait_click_confirm", None)
        # 所有停止路径统一留现场图: 体力不够/材料行全失败/读取失败/空幕交回都经过这里,
        # 复盘时靠这张图判断是页面没打开、坐标错位还是材料真的刷满了。
        self._cg_capture("stop")
        report.emit(
            "stop",
            reason=reason,
            rounds=rounds,
            weekly_done=weekly_done,
            material_done=material_done,
            console_done=console_done,
        )
        self.info_set(
            "培养目标统计",
            f"共 {rounds} 轮 / 周本 {weekly_done} / 材料 {material_done} / 空幕 {console_done}; 停止: {reason}",
        )
        self._cg_note(
            "info",
            f"结束: {reason}, 共 {rounds} 轮 (周本 {weekly_done} / 材料 {material_done} / 空幕 {console_done})",
        )

    def _cg_read_stamina(self):
        """体力数字只在 F1 域面板可见，先开面板再读。"""
        try:
            self.ensure_main(check_exit=True)
            self.open_f1_domain()
            stamina = self.get_stamina()
            if stamina < 0:
                self._cg_note("warning", "域面板内仍未读到体力, 本轮停止")
                self._cg_capture("stamina_read_failed")
            return stamina
        except Exception as error:
            self._cg_note("warning", f"体力读取失败: {error}")
            self._cg_capture("stamina_read_failed")
            return -1
        finally:
            self.ensure_main(check_exit=True)

    def _cg_read_weekly_remaining(self):
        """会话开始读一次 n/3，之后按领奖确认结果记账。"""
        try:
            self.ensure_main(check_exit=True)
            self.open_f1_domain()
            self.operate_click(*self.pos.panels.f1.domain.pilgrimage, after_sleep=1.5)
            box = self.box_of_screen(*POS.weekly_remaining_box, name="cg_weekly", hcenter=True)
            results = self.ocr(box=box, match=None) or []
            text = " ".join(result.name for result in results)
            remaining = parse_weekly_remaining(text)
            self._cg_note("info", f"周本剩余次数 OCR='{text}' → {remaining}")
            if remaining is None:
                self._cg_capture("weekly_read_failed")
            return remaining
        except Exception as error:
            self._cg_note("warning", f"周本剩余次数读取失败: {error}")
            self._cg_capture("weekly_read_failed")
            return None
        finally:
            self.ensure_main(check_exit=True)

    def _cg_click(self, x, y, what: str, after_sleep: float = 0.5):
        """点击并记坐标，便于排查。"""
        self._cg_note("info", f"点击 {what} @ ({x:.3f}, {y:.3f})")
        self.operate_click(x, y, after_sleep=after_sleep)

    @staticmethod
    def _cg_usable_ratios(text):
        """解析比例并把单串猜出的低置信结果当作读不出。

        单串如 47160 切法唯一(47/60)可信; 3150 这类切法不唯一的标 guessed, 调用方据此重试。
        """
        ratios = parse_ratios(text)
        if ratios and any(getattr(ratio, "guessed", False) for ratio in ratios):
            return None
        return ratios or None

    def _cg_read_rows(self):
        """按 tab 分组读完所有目标行，返回值按 TARGET_ROWS 优先级排。"""
        if not self._cg_ensure_guide():
            return None
        texts = {}
        self._cg_row_dungeon = {}
        # 槽位映射同样重建, 只认本轮确认的 key, 防止行内容变化后用旧槽位入账。
        self._cg_row_slots = {}
        self._cg_unverified_labels = set()
        # 档位异常标记只对本次读数有效: 上轮不连续不代表这轮还不可信。
        self._cg_inconsistent_labels = set()
        # 本轮沿用旧值的行: 决策照用, 但不参与收益/零产出统计, 免得把"没读到"算成"没涨"。
        self._cg_stale_labels = set()
        read: dict[tuple[str, int], tuple | None] = {}
        grouped: dict[str, list] = {}
        for tab, row_index, cost, landing in TARGET_ROWS:
            grouped.setdefault(tab, []).append((row_index, cost, landing))
        for tab, specs in grouped.items():
            self._cg_click(POS.tab_x[tab], POS.overview_tabs_y, f"{tab} tab", after_sleep=0.6)
            for row_index, cost, landing in specs:
                x1, x2 = POS.ratio_box_x
                center_y = POS.ratio_row_y[tab][row_index]
                y1 = center_y - POS.ratio_half_height
                y2 = center_y + POS.ratio_half_height
                box = self.box_of_screen(x1, y1, x2, y2, name=f"cg_{tab}_{row_index}", hcenter=True)
                slot_text, slot_ratios = self._cg_read_row_ratios(tab, row_index)
                slot_mode = bool(slot_text) or slot_ratios is not None
                if slot_mode:
                    # 分框读数接管: 单槽解析, 斜杠误读不会再跨槽拼接成假比例。
                    text = slot_text
                    ratios = slot_ratios
                else:
                    text = self._cg_read_line(box)
                    ratios = self._cg_usable_ratios(text)
                self._cg_note(
                    "info", f"{tab} 第 {row_index + 1} 行 (y {y1:.3f}-{y2:.3f}): {text or '<空>'}"
                )
                label = f"{tab}-第{row_index + 1}行"
                if not slot_mode:
                    # 只有整行模式才做单串猜出重试与分级重试; 分框模式在槽内已重试,
                    # 且槽残缺时不能拿整行拼接串回填, 否则 8140/13 这类假比例又回来了。
                    if ratios is None and parse_ratios(text):
                        # 单串猜出来的比例(如 3150→3/50)切法不唯一, 先重试一轮再放弃。
                        self._cg_note("warning", f"{label} 比例由单串猜出({text}), 重试识别")
                    if ratios is None:
                        retry = self._cg_read_retry(box)
                        retry_ratios = self._cg_usable_ratios(retry)
                        if retry_ratios is not None:
                            self._cg_note("info", f"{tab} 第 {row_index + 1} 行 重试成功: {retry}")
                            text = retry
                            ratios = retry_ratios
                texts[f"{tab}-第{row_index + 1}行"] = text
                if ratios is None:
                    # 读不出时沿用上一轮值, 避免一次识别抖动就收摊; 槽位对不上会在下方统一作废。
                    ratios = getattr(self, "_cg_prev_owned", {}).get(label)
                    if ratios:
                        self._cg_stale_labels.add(label)
                        shown = " ".join(f"{r.owned}/{r.required}" for r in ratios)
                        self._cg_note("warning", f"{label} 读不出, 本轮沿用上一轮 {shown}")
                if ratios is None:
                    self._cg_capture(f"row_empty_{tab}_{row_index + 1}")
                read[(tab, row_index)] = ratios
                dungeon = self._cg_identify_row(tab, row_index)
                if label in getattr(self, "_cg_inconsistent_labels", ()):
                    self._cg_capture(f"row_inconsistent_{tab}_{row_index + 1}")
                    read[(tab, row_index)] = None
                    dungeon = None
                if dungeon is None and ratios is not None:
                    # 图标本轮没认出: 仍可用历史槽位数核对读数完整性, 完全没历史才放弃核验。
                    self._cg_unverified_labels.add(label)
                if ratios is not None:
                    # 沿用旧值的行同样要过这道检查: 槽位对不上说明行内容换过, 旧值对新行无效。
                    slots = (getattr(self, "_cg_row_slots", {}) or {}).get(label) \
                        or (getattr(self, "_cg_last_slots", {}) or {}).get(label) or ()
                    if slots and len(ratios) != len(slots):
                        self._cg_note(
                            "warning",
                            f"{label} 比例读数 {len(ratios)} 项与材料槽位 {len(slots)} 个不一致, 视为读数残缺回页复核",
                        )
                        self._cg_capture(f"row_ratio_mismatch_{tab}_{row_index + 1}")
                        read[(tab, row_index)] = None
        self._cg_last_texts = texts
        # 记住最近一次确认的槽位映射, 供后续图标本轮没认出时核对读数完整性。
        self._cg_last_slots = {**(getattr(self, "_cg_last_slots", {}) or {}), **self._cg_row_slots}
        return tuple(
            MaterialRow(tab, row_index, cost, landing, read.get((tab, row_index)))
            for tab, row_index, cost, landing in TARGET_ROWS
        )

    def _cg_identify_row(self, tab, row_index):
        """认这一行材料图标所属副本，并记录槽位 key 供掉落入账。"""
        try:
            from src.tasks.cultivation_goal.guide_icons import (
                identify_row, infer_missing, summarize, vote_dungeon)

            frame = getattr(self, "frame", None)
            if frame is None or getattr(frame, "size", 0) == 0:
                return None
            height, width = frame.shape[:2]
            center_y = POS.ratio_row_y[tab][row_index] - POS.icon_row_offset
            half = POS.icon_half_height
            strip = frame[
                max(0, int(height * (center_y - half))):int(height * (center_y + half)),
                int(width * 0.30):int(width * 0.85),
            ]
            matches = identify_row(strip, height)
            kind, dungeon, votes = vote_dungeon(matches)
            label = f"{tab}-第{row_index + 1}行"
            if not dungeon:
                self._cg_note("warning", f"{label} 材料图标未识别, 该行缺口无法规划")
                # 图标识别失败单独留图: 区分是这一行图标变了/页面错位, 还是整页没加载。
                self._cg_capture(f"row_icon_unknown_{tab}_{row_index + 1}")
                return None
            # 缺口档位须是连续高档区间；不连续说明漏识别, 本行标记未读, 不写槽位与副本。
            plan = infer_missing(match.tier for match in matches)
            if not plan.consistent:
                self._cg_note("warning",
                              f"{label} 缺口档位不连续, 疑似漏识别, 本行标记未读回页复核: 缺 {''.join(plan.gaps)}")
                inconsistent = getattr(self, "_cg_inconsistent_labels", set())
                inconsistent.add(label)
                self._cg_inconsistent_labels = inconsistent
                return None
            # 存下从左到右的材料 key（与 ratios 同序），掉落识别据此摊到槽位。
            slots = getattr(self, "_cg_row_slots", {})
            slots[label] = tuple(match.key for match in matches)
            self._cg_row_slots = slots
            self._cg_row_dungeon[label] = dungeon
            self._cg_note("info", f"{label} = {dungeon} ({votes} 票, {summarize(matches)})"
                                  f" 缺 {''.join(plan.observed) or '无'}")
            self._cg_report().emit("material", row=label, dungeon=dungeon, kind=kind,
                                   items=[m.as_dict() for m in matches],
                                   missing=plan.as_dict())
            return dungeon
        except Exception as error:
            self._cg_note("warning", f"材料图标识别失败: {error}")
            return None

    def _cg_track_gains(self, rows):
        """按面板增量算上一局产出；读数断掉时基准作废，避免多局产出记成一局。"""
        prev = getattr(self, "_cg_prev_owned", {})
        stale = getattr(self, "_cg_stale_labels", set())
        gains = {}
        for row in rows or ():
            if row.ratios is None:
                prev.pop(row.label, None)
                continue
            if row.label in stale:
                # 沿用值差分为 0，当收益会污染零产出判据。
                prev.pop(row.label, None)
                continue
            gains[row.label] = diff_ownage(prev.get(row.label), row.ratios)
            prev[row.label] = row.ratios
        self._cg_prev_owned = prev
        return gains

    def _cg_material_brief(self, rows, gains):
        """汇总每行缺口、上局产出与预估局数。"""
        parts = []
        for row in rows:
            owned = "/".join(f"{ratio.owned}/{ratio.required}" for ratio in row.ratios or ())
            gap = row_gap(row)
            if gap is None:
                parts.append(f"{row.label} 读不出")
            elif gap == 0:
                parts.append(f"{row.label} 已满({owned})")
            else:
                gain = gains.get(row.label)
                text = f"{row.label} {owned} 缺{gap}"
                if gain is not None:
                    runs = estimate_runs(gap, gain)
                    text += f" 上局+{gain}" + (f" 约{runs}局" if runs else "")
                parts.append(text)
        return "材料 " + " | ".join(parts)

    def _cg_drop_starved(self, rows, gains, skipped):
        """连打 2 局零产出就跳过该行；只统计上一轮确实刷过的行。"""
        streak = getattr(self, "_cg_zero_streak", {})
        checked = getattr(self, "_cg_last_action_labels", None) or ()
        dropped = []
        for row in rows or ():
            gap = row_gap(row)
            if gap is None or gap <= 0:
                streak[row.label] = 0
                continue
            if row.label not in checked:
                continue
            gain = gains.get(row.label)
            if gain is None:
                continue
            if gain > 0:
                streak[row.label] = 0
                continue
            streak[row.label] = streak.get(row.label, 0) + 1
            if streak[row.label] >= 2 and row.label not in skipped:
                dropped.append(row.label)
                self._cg_note(
                    "warning",
                    f"{row.label} 连打 {streak[row.label]} 局零产出(仍缺 {gap}), 本场不再刷该行",
                )
        self._cg_zero_streak = streak
        self._cg_last_action_labels = None
        return dropped

    def _cg_read_line(self, box, frame_processor=None, threshold=0):
        """OCR 一行数字，异常按读空处理。"""
        try:
            results = self.ocr(
                box=box, match=None, threshold=threshold, frame_processor=frame_processor
            ) or []
            return " ".join(result.name for result in results)
        except Exception as error:
            self._cg_note("warning", f"OCR 异常(按读空处理): {error}")
            return ""

    def _cg_read_retry(self, box):
        """分级重试：原样 → 二值化 → 灰度降阈值。

        斜杠常被 OCR 认成 `1`，二值化 200 可还原；不能用 use_grayscale（转单通道会让
        onnxocr 检测算子 ValueError 带崩任务）。
        """
        gray3 = lambda img: cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        for delay, processor, threshold in (
            (0.6, None, 0),
            (0.2, self._cg_binarize_frame, 0),
            (0.2, gray3, 0.3),
        ):
            self.sleep(delay)
            text = self._cg_read_line(box, frame_processor=processor, threshold=threshold)
            if parse_ratios(text):
                return text
        return ""

    @staticmethod
    def _cg_binarize_frame(img):
        """二值化 200：斜杠被 OCR 认成 `1` 时还原字形。"""
        return cv2.cvtColor(
            cv2.threshold(
                cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 200, 255, cv2.THRESH_BINARY
            )[1],
            cv2.COLOR_GRAY2BGR,
        )

    def _cg_split_icon_slots(self, frame, tab, row_index):
        """亮度投影切出本行材料图标的 x 段(比例坐标)，失效返回空列表。

        图标卡片在深色背景上是亮块，列投影即可分开；空槽位整列全暗。
        """
        try:
            if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim < 2:
                return []
            height, width = frame.shape[:2]
            center_y = POS.ratio_row_y[tab][row_index] - POS.icon_row_offset
            y1 = int(height * (center_y - POS.icon_half_height))
            y2 = int(height * (center_y + POS.icon_half_height))
            x1 = int(width * 0.28)
            x2 = int(width * 0.88)
            strip = cv2.cvtColor(frame[y1:y2, x1:x2, :3], cv2.COLOR_BGR2GRAY)
            proj = (strip > 50).sum(axis=0)
            lit = max(1, y2 - y1)
            segs = []
            start = None
            for i, v in enumerate(proj):
                on = v > lit * 0.3
                if on and start is None:
                    start = i
                elif not on and start is not None:
                    segs.append((start, i))
                    start = None
            if start is not None:
                segs.append((start, len(proj)))
            # 过滤窄噪声并转比例坐标; 一行最多 3 个材料, 切出更多说明投影被污染。
            slots = []
            for sx1, sx2 in segs:
                if sx2 - sx1 < width * 0.015:
                    continue
                slots.append(((x1 + sx1) / width, (x1 + sx2) / width))
            return slots if 1 <= len(slots) <= 3 else []
        except Exception:
            return []

    @staticmethod
    def _cg_parse_slot(text):
        """单槽解析：正常 xx/xx 直接收；纯数字单串走斜杠吞字修复；其余 None。"""
        normalized = unicodedata.normalize("NFKC", (text or "").strip())
        if not normalized:
            return None
        matches = RATIO_RE.findall(normalized)
        if len(matches) == 1:
            left, right = matches[0]
            return Ratio(int(left), int(right))
        if len(matches) > 1:
            return None  # 单槽出现多个比例说明切槽串了, 判残缺不猜
        return (repair_swallowed_slash(normalized) or (None,))[0]

    def _cg_read_row_ratios(self, tab, row_index):
        """按图标槽位分框读比例，防 OCR 把 `/` 读成 `1` 后跨槽拼接。

        整行连读时 `8/40 4/13` 的斜杠被认成 1 会拼出 `8140/13`，把缺料判成满。
        分框后单槽串错只污染一格：纯数字单串交给 repair_swallowed_slash，
        失败槽二值化重试一次；任何槽解析不出则整行返回 None，绝不把缺当满。
        返回 (槽文本, ratios)；切槽失效返回 ("", None) 由调用方退回整行读数。
        """
        frame = getattr(self, "frame", None)
        slots = self._cg_split_icon_slots(frame, tab, row_index)
        if not slots:
            return "", None
        texts = []
        ratios = []
        cy = POS.ratio_row_y[tab][row_index]
        ny1, ny2 = cy - POS.ratio_half_height, cy + POS.ratio_half_height
        for i, (sx1, sx2) in enumerate(slots):
            # 数字比图标窄但可能溢出边缘, 以槽中心 ±0.028 取框, 相邻槽间距 0.09 不会串。
            cx = (sx1 + sx2) / 2
            box = self.box_of_screen(
                cx - 0.028, ny1, cx + 0.028, ny2,
                name=f"cg_slot_{tab}_{row_index}_{i}", hcenter=True,
            )
            text = self._cg_read_line(box)
            ratio = self._cg_parse_slot(text)
            if ratio is None or ratio.guessed:
                # 斜杠被读成 1 的槽: 二值化重试一次, 还原不出就整行判残缺。
                retry = self._cg_read_line(box, frame_processor=self._cg_binarize_frame)
                retry_ratio = self._cg_parse_slot(retry)
                if retry_ratio is not None and not retry_ratio.guessed:
                    text, ratio = retry, retry_ratio
                else:
                    ratio = None
            texts.append(text or "<空>")
            ratios.append(ratio)
        joined = " | ".join(texts)
        if any(r is None for r in ratios):
            self._cg_note(
                "warning", f"{tab} 第 {row_index + 1} 行 槽读数残缺({joined}), 判读不出防误判满"
            )
            return joined, None
        return joined, tuple(ratios)

    def _cg_ensure_guide(self):
        """导航到养成指南材料页。"""
        self.ensure_main(check_exit=True)
        self.openF1panel()
        self.operate_click(POS.guide_sidebar_x, POS.guide_goal_icon_y, after_sleep=0.8)
        if not self._cg_wait_text("前往培养", (0.76, 0.14, 0.98, 0.32)):
            self._cg_note("warning", "未进入培养目标页, 停止本轮")
            self._cg_capture("goal_page_failed")
            return False
        self.operate_click(*POS.goal_goto_cultivate, after_sleep=1.0)
        # 详情页到养成指南还需再点一次；验证词不能含「养成指南」（详情页已有此词）。
        if not self._cg_wait_text("角色总览|所需材料|等级提升", (0.28, 0.02, 0.96, 0.16)):
            self._cg_note("info", "角色详情页, 点右上角养成指南按钮")
            self.operate_click(*POS.detail_guide_button, after_sleep=1.2)
            if not self._cg_wait_text("角色总览|所需材料|等级提升", (0.28, 0.02, 0.96, 0.16)):
                self._cg_note("warning", "未进入养成指南材料页, 停止本轮")
                self._cg_capture("guide_failed")
                return False
        return True

    def _cg_wait_text(self, pattern, rect):
        regex = re.compile(pattern)

        def find():
            box = self.box_of_screen(*rect, name="cg_text", hcenter=True)
            results = self.ocr(box=box, match=None) or []
            text = " ".join(result.name for result in results)
            return text if regex.search(text) else None

        return bool(
            self.wait_until(find, time_out=6, settle_time=0.3, raise_if_not_found=False)
        )

    def _cg_run_weekly(self):
        """打异象巡礼周本，返回 (领到次数, 是否跑完)。"""
        from src.tasks.AnomalyPilgrimage import AnomalyPilgrimage
        from src.utils.game_templates import WEEKLYBOSS_ITEM

        targets = list(WEEKLYBOSS_ITEM.list_names())
        if not targets:
            self._cg_note("warning", "没有可用的周本素材, 本次跳过周本")
            self._cg_weekly_unconfirmed = 0
            return 0, False

        probe = {"boss_claim": 0, "confirm": 0, "claimed": 0, "unconfirmed": 0}
        backup = self.config.get(AnomalyPilgrimage.CONF_TARGET, None)
        shadows = {}
        borrowed = []
        try:
            # setup_config 没在 AnomalyTask 上跑过, 目标清单要临时注入; 用原生赋值避免写盘。
            # 注入和借用都放进 try: 准备阶段抛异常也能在 finally 还原。
            dict.__setitem__(self.config, AnomalyPilgrimage.CONF_TARGET, targets)
            self._cg_note("info", f"周本目标清单: {', '.join(targets)}")
            borrowed = self._cg_borrow(AnomalyPilgrimage)
            # 领奖走原版，这里只加观测：领奖时截图并统计宝箱/确认按钮检出次数。
            raw_claim = self.claim_target_reward
            raw_boss_claim = self.find_boss_claim
            raw_confirm = self.find_confirm
            raw_stamina = self.get_stamina
            budget = getattr(self, "_cg_weekly_budget", None)

            def probe_boss_claim():
                found = raw_boss_claim()
                if found:
                    probe["boss_claim"] += 1
                return found

            def probe_confirm(box=None, threshold=0.7):
                found = raw_confirm(box=box, threshold=threshold)
                if found:
                    probe["confirm"] += 1
                return found

            def trace_claim():
                probe["boss_claim"] = 0
                probe["confirm"] = 0
                self._cg_capture("claim_begin")
                claimed = raw_claim()
                if claimed:
                    probe["claimed"] += 1
                else:
                    # 原版返回 False 说明这次领取没看到结算确认: 体力扣没扣未知, 记下来供账本裁决。
                    probe["unconfirmed"] += 1
                self._cg_note(
                    "info",
                    f"原版领奖返回: {claimed}; 期间宝箱检出 {probe['boss_claim']} 次, "
                    f"确认按钮检出 {probe['confirm']} 次, 累计领到 {probe['claimed']} 次",
                )
                return claimed

            def budget_stamina():
                # 原版按面板体力排领取次数, 这里压到今日剩余预算, 防止越过目标多领。
                current = raw_stamina()
                if current is None or current < 0 or budget is None:
                    return current
                return min(current, budget)

            # 探针只是观测, 参数写错不能把任务带崩: 单独兜一层, 失败就退化成不插探针。
            try:
                shadows = self._cg_push_shadows({
                    "find_boss_claim": probe_boss_claim,
                    "find_confirm": probe_confirm,
                    "claim_target_reward": trace_claim,
                    "get_stamina": budget_stamina,
                })
            except Exception as error:
                self._cg_note("warning", f"领奖探针未挂上, 按原版裸跑: {error}")
            result = AnomalyPilgrimage.do_run(self)
            self._cg_note("info", f"周本执行结果: {result}, 共领到 {probe['claimed']} 次")
            self._cg_weekly_unconfirmed = probe["unconfirmed"]
            return probe["claimed"], bool(result)
        except Exception as error:
            self._cg_note("warning", f"周本执行异常: {error}")
            self._cg_capture("weekly_error")
            self._cg_weekly_unconfirmed = probe["unconfirmed"]
            return probe["claimed"], False
        finally:
            self._cg_pop_shadows(shadows)
            self._cg_release(borrowed)
            if backup is None:
                self.config.pop(AnomalyPilgrimage.CONF_TARGET, None)
            else:
                dict.__setitem__(self.config, AnomalyPilgrimage.CONF_TARGET, backup)

    def _cg_run_material(self, row, stamina=None):
        # 连打参数走实例属性传递, 保持这个入口签名不变, 兼容测试桩和上游调用。
        burst, per_claim = getattr(self, "_cg_burst_next", (1, None))
        # 「获取材料」的按钮落点不可靠的情况直接走独立玩法入口: 周本材料只能靠异象巡礼。
        if row.landing == "weekly":
            return self._cg_run_weekly()[0] > 0
        if not self._cg_ensure_guide():
            return False
        self._cg_click(POS.tab_x[row.tab], POS.overview_tabs_y, f"{row.tab} tab", after_sleep=0.6)
        button_y = POS.material_row_y[row.tab][row.row_index]
        self._cg_click(POS.material_button_x, button_y, f"{row.label} 获取材料", after_sleep=1.2)
        mode = self._cg_enter_landing()
        if mode is None:
            return False
        return self._cg_fight_once(row, mode, stamina, burst, per_claim)

    def _cg_screen_text(self, region=(0.26, 0.0, 1.0, 1.0)):
        """落点页 OCR 全文，用于判定与排查。"""
        box = self.box_of_screen(*region, name="cg_screen", hcenter=True)
        results = self.ocr(box=box, match=None) or []
        return " ".join(result.name for result in results)

    def _cg_probe_landing(self):
        """识别落点：材料页 / 大地图 / 副本门口 / 异象追猎。"""
        text = self._cg_screen_text()
        if "所需材料" in text and "获取材料" in text:
            return None, text  # 点击还没生效, 仍停在材料页
        # 顺手记下屏幕上的 BOSS 名(大地图和追猎页都会出现), 走到跟前时要用它选走路方向。
        hit = next((name for name in HUNTER_BOSS_NAMES if name in text), None)
        if hit:
            self._cg_hunter_name = hit
        if self.find_traval_button():
            return "map", text
        if any(name in text for name in HUNTER_BOSS_NAMES):
            return "hunter", text
        if "进入" in text or "消耗" in text:
            return "domain", text
        if "前往" in text and "前往培养" not in text and "前往获取" not in text:
            return "hunter", text
        return None, text

    def _cg_enter_landing(self):
        """按落点进入战斗：地图/追猎走野外，副本门口走界域。"""
        self._cg_hunter_name = None
        holder = {"text": ""}

        def probe():
            kind, text = self._cg_probe_landing()
            holder["text"] = text
            return kind

        landing = self.wait_until(probe, time_out=15, settle_time=0.4, raise_if_not_found=False)
        self._cg_report().emit("landing", kind=landing, text=holder["text"])
        if not landing:
            self._cg_note("warning", f"获取材料后未识别出落点, 屏幕文字: {holder['text'][:160]}")
            self._cg_capture("landing_unknown")
            return None
        self._cg_note("info", f"落点: {landing}")
        if landing == "domain":
            if not self._cg_enter_domain():
                return None
            return "dungeon"
        if landing == "map":
            if not self.click_traval_button(raise_if_not_found=False):
                self._cg_note("warning", "地图页传送按钮未点到")
                self._cg_capture("travel_failed")
                return None
        elif not self._cg_enter_hunter():
            return None
        # 传送按钮偶尔点了不生效，短等超时后按固定坐标补点。
        if self.wait_in_team(time_out=45, raise_if_not_found=False):
            return "wild"
        self._cg_note("warning", "传送后 45 秒未进队, 补点传送按钮")
        self._cg_capture("travel_retry")
        try:
            self.bring_to_front()
        except Exception as error:
            self._cg_note("warning", f"窗口置前失败(不影响补点): {error}")
        self._cg_click(*POS.map_travel_button_center, "补点传送", after_sleep=1.5)
        if self.wait_in_team(time_out=45, raise_if_not_found=False):
            return "wild"
        self._cg_note("warning", "补点传送后仍未进队, 放弃该行")
        self._cg_capture("travel_failed")
        return None

    def _cg_enter_domain(self):
        self.operate_click(*POS.domain_entry_button, after_sleep=0.5)
        if self.wait_until(
            lambda: not self.find_stamina_icon(),
            pre_action=lambda: self.operate_click(*POS.domain_entry_button),
            time_out=10,
            raise_if_not_found=False,
        ):
            return True
        self._cg_capture("domain_entry_failed")
        return False

    def _cg_find_text_box(self, keyword, region=(0.55, 0.0, 1.0, 1.0)):
        box = self.box_of_screen(*region, name="cg_find", hcenter=True)
        results = self.ocr(box=box, match=None) or []
        for result in results:  # ocr 结果已按 y 排序, 取最上面那个
            if keyword in (result.name or ""):
                return result
        return None

    def _cg_enter_hunter(self):
        """追猎页选定目标：优先点「前往」，读不到用实测坐标兜底。"""
        go = self._cg_find_text_box("前往")
        if go is not None:
            x, y = go.center()
            self._cg_click(x / self.width, y / self.height, "追猎目标 前往", after_sleep=1.5)
        else:
            self._cg_note("warning", "追猎页没读到「前往」, 用第一行坐标兜底")
            self._cg_click(*POS.hunter_first_row, "追猎第一行 坐标兜底", after_sleep=1.5)
        if not self.click_traval_button(raise_if_not_found=False):
            self._cg_note("info", "追猎页无传送按钮, 可能点前往后已直接传送")
        return True

    def _cg_read_loot(self, last_label=None):
        """返回本次掉落 {行标签: 各槽位增量}；认不出返回 None 让主循环回页复核。"""
        reader = getattr(self, "cg_loot_reader", None)
        if callable(reader):
            try:
                return reader()
            except Exception as error:
                self._cg_note("warning", f"掉落识别回调失败: {error}")
        loot = getattr(self, "_cg_reward_loot", None) or []
        if not loot:
            return None
        slots = (getattr(self, "_cg_row_slots", {}) or {}).get(last_label)
        if not slots:
            self._cg_note("info", f"{last_label} 没有槽位 key, 掉落无法入账")
            return None
        from src.tasks.cultivation_goal.core import map_reward_to_counts
        counts = map_reward_to_counts(
            ((item["key"], item["count"]) for item in loot), slots
        )
        if counts is None:
            self._cg_note("warning", f"掉落材料不在 {last_label} 的槽位里: {[i['key'] for i in loot]}")
            return None
        self._cg_note("info", f"{last_label} 掉落入账: {slots} +{counts}")
        return {last_label: counts}

    def _cg_log_loot_stats(self, ledger, gains=None):
        """掉落统计: 每行各槽位输出 本次掉落 / 已有 / 还差。已有与缺口从账本读, 掉落用本次增量。"""
        gains = gains or {}
        parts = []
        stats = []
        for row in ledger.rows:
            if row.ratios is None:
                continue
            dropped = gains.get(row.label) or ()
            seg = []
            slots = []
            for i, ratio in enumerate(row.ratios):
                got = dropped[i] if i < len(dropped) else 0
                short = max(0, ratio.required - ratio.owned)
                seg.append(f"+{got} 有{ratio.owned}/{ratio.required} 还差{short}")
                slots.append({"dropped": got, "owned": ratio.owned, "required": ratio.required})
            parts.append(f"{row.label}[{' | '.join(seg)}]")
            stats.append({"label": row.label, "slots": slots})
        if parts:
            self._cg_note("info", "掉落统计: " + " ".join(parts))
            self._cg_report().emit("loot_stats", rows=stats)

    def _cg_burst_plan(self, row, stamina, gains):
        """自动连打: 上一局产出不足以覆盖缺口就继续连打, 只对副本生效。"""
        if row.landing != "domain":
            return 1, None
        gain = gains.get(row.label)
        units = getattr(self, "_cg_claim_units", {}).get(row.label, 1) or 1
        per_claim = gain / units if gain and gain > 0 else None
        count = plan_burst(row_gap(row), per_claim, stamina, row.cost, self.CG_BURST_LIMIT)
        return max(1, count), per_claim

    def _cg_pick_double(self, row, stamina, gap=None):
        """体力够两份且缺口≥2 时走双倍；缺口只剩 1 份或体力不足退回单倍。"""
        if stamina is None or stamina < 2 * row.cost:
            return False
        gap = row_gap(row) if gap is None else gap
        return gap is None or gap >= 2

    def _cg_reward_settled(self, time_out=6) -> bool:
        """领奖点击后要看到结算确认框(或已回到副本), 才算这一局真的到账。"""
        burst = self.box_of_screen(*CG_BURST_CONFIRM_RANGE, hcenter=True)
        final = self.box_of_screen(*CG_FINAL_CONFIRM_RANGE, hcenter=True)
        try:
            found = self.wait_until(
                lambda: self.find_confirm(box=burst)
                or self.find_confirm(box=final)
                or self.find_exit(),
                time_out=time_out,
                settle_time=0.3,
                raise_if_not_found=False,
            )
            return bool(found)
        except Exception as error:
            self._cg_note("warning", f"结算确认检查失败: {error}")
            return False

    def do_combat_and_claim(self, double: bool):
        """副本打完走到宝箱领奖。

        上游版按 F 后只找一帧领奖按钮, 实测连续三帧扑空就判「领奖未确认」把整轮作废;
        这里改成: 先看按钮是否已开(避免 F 把界面切关), 没开再交互, 交互后给界面留 2.5 秒。
        搜索带同时向上放宽: 领奖按钮是世界锚定的, 站得越近画面上越靠上。
        """
        self.log_info("开始执行战斗流程")
        self.walk_until_combat(run=True, delay=1)
        self.combat_once()
        self.log_info("战斗结束，正在前往领取奖励")
        area = self.box_of_screen(0.2645, 0.560, 0.7352, 0.700, name="cg_reward_area_wide")

        def find_claim():
            return self.find_feature(Labels.claim_icon, box=area)

        def action():
            claims = self.wait_until(find_claim, time_out=0.8, raise_if_not_found=False)
            if claims:
                return claims
            self.walk_to_treasure()
            self.send_interac(handle_claim=False)
            return self.wait_until(find_claim, time_out=2.5, raise_if_not_found=False)

        self.rotate_and_find_treasure()
        claims = self.retry_on_action(action)
        if not claims:
            self._cg_note("warning", "走到宝箱前但没找到领奖按钮")
            return False
        target = max(claims, key=lambda x: x.x) if double else min(claims, key=lambda x: x.x)
        self.operate_click(target.copy(x_offset=target.width * 3))
        return True

    def _cg_fight_once(self, row, mode="dungeon", stamina=None, burst=1, per_claim=None):
        """副本走「退出」，野外 BOSS 走「走到跟前打完捡宝箱」；支持连打与单/双倍。"""
        if mode == "wild":
            return self._cg_fight_wild(row)
        if not hasattr(self, "_cg_claim_units"):
            self._cg_claim_units = {}
        self._cg_burst_unconfirmed = False
        if not self.wait_until(self.find_exit, time_out=30, raise_if_not_found=False):
            self._cg_note("warning", f"未进入副本: {row.label}")
            self._cg_capture("enter_dungeon_failed")
            return False
        self.wait_in_team()
        self.sleep(1)
        remaining = stamina if stamina is not None else 10 ** 6
        total = max(1, int(burst or 1))
        if total > 1:
            self._cg_note("info", f"{row.label} 连打开始: 最多 {total} 局, 入场体力 {remaining}")
        done = units = 0
        gap = row_gap(row)
        failed = False
        while done < total:
            if remaining < row.cost:
                if done == 0:
                    self._cg_note("warning", f"体力 {remaining} 不够 {row.label} ({row.cost})")
                break
            double = self._cg_pick_double(row, remaining, gap)
            if done or double:
                self._cg_note(
                    "info",
                    f"{row.label} 第 {done + 1}/{total} 局 "
                    f"{'双倍' if double else '单倍'}, 剩余体力 {remaining}, 估算缺口 {gap}",
                )
            if not self.do_combat_and_claim(double):
                self._cg_note("warning", f"未确认领奖成功: {row.label}, 不计入已完成")
                self._cg_capture("claim_failed")
                failed = True
                # 已经领过几局的话, 这一局的扣费状态不明, 由主循环回页复核。
                self._cg_burst_unconfirmed = done > 0
                break
            if not self._cg_reward_settled():
                # 点完领奖按钮还停在领取界面: 这一局不能记成已领, 交给主循环复核。
                self._cg_note("warning", f"领奖后未见结算界面: {row.label}, 不计入已完成")
                self._cg_capture("claim_unsettled")
                failed = True
                self._cg_burst_unconfirmed = done > 0
                break
            share = 2 if double else 1
            done += 1
            units += share
            remaining -= row.cost * share
            if per_claim and gap is not None:
                gap -= per_claim * share
            self._cg_report().emit(
                "claim",
                label=row.label,
                index=done,
                double=double,
                units=share,
                remaining_stamina=remaining,
            )
            self.sleep(2)
            cont = done < total and remaining >= row.cost
            if cont and (gap is None or gap > 1):
                # 小区域是「继续挑战」；找不到就正常收场。
                if not self.wait_click_confirm(
                    range=CG_BURST_CONFIRM_RANGE, raise_if_not_found=False
                ):
                    self._cg_note("info", f"{row.label} 无继续挑战按钮, 第 {done} 局后收场")
                    break
                if not self.wait_until(
                    self.find_exit, time_out=30, raise_if_not_found=False
                ):
                    self._cg_note("warning", f"{row.label} 连打后未回到副本, 收场")
                    break
                self.wait_in_team()
                self.sleep(1)
                continue
            if done < total:
                if gap is not None and gap <= 1:
                    self._cg_note(
                        "info", f"{row.label} 估算材料已够(余 {gap:.1f}), 提前收场回养成页复核"
                    )
                else:
                    self._cg_note("info", f"{row.label} 剩余体力 {remaining}, 连打收场")
                break
        if not failed:
            self.wait_click_confirm(range=CG_FINAL_CONFIRM_RANGE, raise_if_not_found=False)
        self.exit_anomaly()
        if units:
            self._cg_claim_units[row.label] = units
        if not done:
            return False
        self._cg_note(
            "info", f"{row.label} 本轮共领 {done} 局 / {units} 份, 剩余体力 {remaining}"
        )
        return True

    def _cg_fight_wild(self, row):
        """世界 BOSS：传送后先走到怪跟前，再开战捡宝箱。"""
        from src.tasks.AnomalyHunter import AnomalyHunter

        # 野外只领一份: 清掉同行上一轮副本的份数记录, 免得按双倍摊体力。
        if hasattr(self, "_cg_claim_units"):
            self._cg_claim_units[row.label] = 1

        if not self.wait_in_team(time_out=60, raise_if_not_found=False):
            self._cg_note("warning", f"野外传送后没进队伍: {row.label}")
            self._cg_capture("wild_team_failed")
            return False
        borrowed = self._cg_borrow(AnomalyHunter)
        # 基类也有同名方法, _cg_borrow 会跳过它; 野外必须换成 Hunter 的宝箱搜索。
        wild_extra = self._cg_push_shadows({
            "rotate_and_find_treasure": AnomalyHunter.rotate_and_find_treasure.__get__(self, type(self)),
        })
        try:
            self._cg_arm_wild_reward_capture()
            # 照搬上游 enter_hunter：落地回正镜头再走到怪跟前，避免原地空打。
            target = getattr(self, "_cg_hunter_name", None)
            self._cg_note("info", f"野外落地, 走向目标: {target or '默认方向'}")
            self.enter_hunter(target)
            # 上游把「锁上怪」当到位，可能一步没走，故再按名牌补走。
            self._cg_approach_boss(target)
            # 世界 BOSS 只有领奖才扣体力, 没打死不扣, 所以没确认成功还能再打一次。
            ok = bool(AnomalyHunter.do_combat_and_claim(self))
            if not ok:
                # 脱战瞬间血条未消会打断找宝箱，等血条消失再找。
                self._cg_note("warning", f"首次未确认宝箱: {row.label}, 等 BOSS 血条消失后原地再找")
                self.sleep(3)
                self._cg_note("info", f"再次找宝箱, is_boss={bool(self.is_boss())}")
                # walk_to_boss_treasure 只到「走到宝箱」, 还须交互+点领奖+确认回队才算领到。
                ok = bool(self.walk_to_boss_treasure()) and self._cg_claim_treasure()
            if not ok:
                # 上游只找当前朝向，宝箱在侧后方时改用转视角搜索。
                self._cg_note("info", f"原版没找到宝箱, 转视角搜索: {row.label}")
                ok = self._cg_search_treasure()
            if not ok:
                self._cg_note("warning", f"第 2 次未确认宝箱: {row.label}, 原地再打一轮")
                ok = bool(AnomalyHunter.do_combat_and_claim(self))
        except Exception as error:
            self._cg_note("warning", f"世界 BOSS 战斗异常: {error}")
            self._cg_capture("wild_error")
            self.exit_anomaly()
            return False
        finally:
            self._cg_pop_shadows(getattr(self, "_cg_wild_shadows", None))
            self.__dict__.pop("_cg_wild_shadows", None)
            self._cg_pop_shadows(wild_extra)
            self.__dict__.pop("find_boss_treasure", None)
            self._cg_release(borrowed)
        if not ok:
            self._cg_note("warning", f"世界 BOSS 战斗或宝箱未确认: {row.label}")
            self._cg_capture("wild_claim_failed")
            self.exit_anomaly()
            return False
        self.sleep(2)
        self.exit_anomaly()
        return True

    def _cg_arm_wild_reward_capture(self):
        """野外奖励清单在点掉领奖确认后才出现，故挂在 operate_click 上连拍。"""
        try:
            raw_probe = self.is_claim_btn_ready
            raw_click = self.operate_click
            # 一场野外只有一份战利品: 扫描额度固定 1, 补领点第二次不会把同一份清单再记一遍。
            state = {"armed": False, "n": 0}

            def probe():
                found = raw_probe()
                if found:
                    state["armed"] = True
                return found

            def click(x, y, *args, **kwargs):
                should_scan = state["armed"] and state["n"] < 1
                if should_scan:
                    try:
                        should_scan = (
                            abs(float(x) - 0.609) < 0.012
                            and abs(float(y) - 0.659) < 0.012
                        )
                    except (TypeError, ValueError):
                        should_scan = False
                result = raw_click(x, y, *args, **kwargs)
                if should_scan:
                    state["armed"] = False
                    state["n"] += 1
                    # 清单只挂 6 秒左右, 领奖点下后立即识别一次。
                    self._cg_scan_reward_list("野外")
                return result

            self._cg_wild_shadows = self._cg_push_shadows({
                "is_claim_btn_ready": probe,
                "operate_click": click,
            })
        except Exception as error:
            self._cg_note("warning", f"野外奖励界面抓拍未挂上: {error}")

    def _cg_find_treasure_wide(self):
        """多尺度查找宝箱（0.8~1.8，阈值 0.6），比上游更宽容。"""
        try:
            name = "boss_treasure"
            mat = self.get_feature_by_name(name).mat
            gray = cv2.cvtColor(mat, cv2.COLOR_BGR2GRAY)
            height, width = gray.shape[:2]
            box = self.pos.screen.main_viewport.to_box()

            def to_gray(frame):
                return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            for scale in (None, 1.2, 1.4, 1.6, 1.8, 0.9, 0.8):
                if scale is None:
                    template = gray
                else:
                    template = cv2.resize(
                        gray,
                        (max(1, int(width * scale)), max(1, int(height * scale))),
                        interpolation=cv2.INTER_NEAREST,
                    )
                hit = self.find_one(
                    feature_name=name,
                    template=template,
                    box=box,
                    threshold=0.6,
                    frame_processor=to_gray,
                )
                if hit:
                    self._cg_note(
                        "info",
                        f"宝箱命中 scale={scale} conf={hit.confidence:.3f} "
                        f"(素材 {width}x{height})",
                    )
                    return hit
            return None
        except Exception as error:
            self._cg_note("warning", f"宝箱查找异常: {error}")
            return None

    def _cg_claim_treasure(self):
        """宝箱交互收尾: 点领奖并确认返回队伍; 只走到宝箱不算领到。"""
        if not self.wait_until(
            self.is_claim_btn_ready,
            pre_action=lambda: self.send_interac(handle_claim=False),
            raise_if_not_found=False,
            time_out=5,
        ):
            self._cg_note("warning", "走到宝箱前但没出现领奖按钮")
            return False
        return bool(
            self.wait_until(
                self.is_in_team,
                pre_action=lambda: self.operate_click(0.609, 0.659, after_sleep=2),
                raise_if_not_found=False,
            )
        )

    def _cg_search_treasure(self, turns=5):
        """用鼠标真正转动镜头搜索宝箱，每转一次找一遍。"""
        step_px = int(self.width_of_screen(1.0) * 0.18)
        for index in range(turns):
            hit = self._cg_find_treasure_wide()
            if hit:
                self._cg_note("info", f"第 {index} 次转向后找到宝箱")
                if not self.walk_to_box(
                    self._cg_find_treasure_wide,
                    time_out=15,
                    end_condition=self.find_interac,
                    y_offset=0.1,
                    x_threshold=0.15,
                ):
                    self._cg_note("warning", "走向宝箱超时")
                    return False
                if not self.wait_until(
                    self.is_claim_btn_ready,
                    pre_action=lambda: self.send_interac(handle_claim=False),
                    raise_if_not_found=False,
                    time_out=5,
                ):
                    self._cg_note("warning", "走到宝箱前但没出现领奖按钮")
                    return False
                return bool(
                    self.wait_until(
                        self.is_in_team,
                        pre_action=lambda: self.operate_click(0.609, 0.659, after_sleep=2),
                        raise_if_not_found=False,
                    )
                )
            self.move_mouse_relative(step_px, 0)
            self.sleep(0.6)
        self._cg_note("warning", f"转 {turns} 个方向都没找到宝箱")
        return False

    def _cg_approach_boss(self, target, max_steps=6, step_time=0.5):
        """enter_hunter 把「锁上怪」当「已到位」，这里按 is_boss 名牌补走。"""
        try:
            if self.is_boss():
                self._cg_note("info", "落点已在 BOSS 战中, 无需补走")
                return True
            script = getattr(self, "WALK_METHOD", {}).get(target, [["w"]])
            direction = script[-1]
            keys = [direction] if isinstance(direction, str) else list(direction)
            for step in range(1, max_steps + 1):
                for key in keys:
                    self.send_key_down(key)
                self.sleep(step_time)
                for key in keys:
                    self.send_key_up(key)
                self.sleep(0.2)
                if self.is_boss():
                    self._cg_note("info", f"补走 {step} 步后进入 BOSS 战")
                    return True
            self._cg_note(
                "warning",
                f"补走 {max_steps} 步仍未出现 BOSS 名牌, 按现状开打",
            )
            return False
        except Exception as error:
            self._cg_note("warning", f"补走异常, 按现状开打: {error}")
            return False

    def _cg_run_original(self):
        """交回原版 AnomalyTask 逻辑。"""
        from src.tasks.AnomalyTask import AnomalyTask

        result = AnomalyTask.do_run(self)
        self._cg_note("info", f"原版任务执行结果: {result}")
        return result

    def _cg_run_console(self, budget=None):
        """材料全满时切到空幕，交回原版逻辑刷完体力。

        budget 是本轮剩余体力预算: None 表示不限额(跟随面板), 数字则原版最多只领这么多。
        """
        from src.tasks.AnomalyTask import AnomalyTask

        if budget is not None and budget <= 0:
            self._cg_note("warning", "今日体力目标已用完, 不再委派空幕")
            return False
        original_type = self.config.get(self.CONF_TASK_TYPE, _MISSING)
        original_console_id = self.config.get(AnomalyTask.CONF_CONSOLE_ID, _MISSING)
        # 原版看到 must use stamina 为 0 会直接不进副本; 这里换成剩余预算(或放开)。
        # 原版按 ceil(目标/每份消耗) 排次数, 预算 60 会被取整成 2 份(80 点)超支,
        # 这里向下取整成整份: 预算 60 只放行 1 份(40), 预算 80 放行 2 份。
        per_claim = AnomalyTask.TASK_COST
        if budget is None:
            goal = CG_CONSOLE_STAMINA_OPEN
        else:
            goal = (int(budget) // per_claim) * per_claim
            if goal < per_claim:
                self._cg_note("warning", f"剩余预算 {budget} 不足一份空幕奖励({per_claim}), 不再委派")
                return False
        saved_goal = self.info.get(CG_INFO_MUST_USE, _MISSING)
        try:
            # 本任务 setup_config 未注册空幕序号, 临时注入默认值避免原版 get_sub_idx 取不到。
            if original_console_id is _MISSING:
                dict.__setitem__(self.config, AnomalyTask.CONF_CONSOLE_ID, 1)
            # 用 dict 原生赋值避免临时切换写盘。
            dict.__setitem__(self.config, self.CONF_TASK_TYPE, self.TASK_CONSOLE)
            if saved_goal is _MISSING or not saved_goal:
                self.info_set(CG_INFO_MUST_USE, goal)
            self._cg_bypass = True
            self._cg_note("info", f"材料全满, 切换空幕按原版刷体力 (预算 {goal})")
            return self._cg_run_original()
        finally:
            if saved_goal is _MISSING:
                self.info.pop(CG_INFO_MUST_USE, None)
            else:
                self.info_set(CG_INFO_MUST_USE, saved_goal)
            for key, value in (
                (self.CONF_TASK_TYPE, original_type),
                (AnomalyTask.CONF_CONSOLE_ID, original_console_id),
            ):
                if value is _MISSING:
                    self.config.pop(key, None)
                else:
                    dict.__setitem__(self.config, key, value)
            self._cg_bypass = False
