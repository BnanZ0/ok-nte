"""培养目标刷本主流程：读养成指南 → 排副本 → 记账 → 收场报告。"""

from __future__ import annotations

from src.tasks.cultivation_goal.core import (
    Action,
    LootLedger,
    PlanConfig,
    PlanState,
    decide,
    diff_ownage,
    estimate_runs,
    lowest_units,
    parse_ratios,
    parse_weekly_remaining,
    row_gap,
)
from src.tasks.cultivation_goal.flow_const import (
    CG_BURST_CONFIRM_RANGE,
    CG_FINAL_CONFIRM_RANGE,
    _MISSING,
    CG_INFO_MUST_USE,
    CG_CONSOLE_STAMINA_OPEN,
)
from src.tasks.cultivation_goal.positions import POS
from src.tasks.cultivation_goal.report import RunReport
from src.tasks.cultivation_goal.report import row_snapshot
from src.tasks.cultivation_goal.version import VERSION as CG_VERSION
from src.tasks.cultivation_goal.flow_combat import CombatMixin
from src.tasks.cultivation_goal.flow_console import ConsoleMixin
from src.tasks.cultivation_goal.flow_scan import ScanMixin
from src.tasks.cultivation_goal.flow_weekly import WeeklyMixin


class CultivationGoalMixin(ScanMixin, CombatMixin, WeeklyMixin, ConsoleMixin):
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
        # 目标只是「今日最低消耗量」, 用于满足日常活跃度；不作为中途截断条件。
        resolved_target = self.resolve_stamina_target()
        if not resolved_target:
            self._cg_note("info", "今日体力目标已达标, 按当前体力继续刷到材料齐全")
        cfg = PlanConfig(
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
            daily_target=resolved_target,
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
        self._cg_start_stamina = None
        self._cg_weekly_budget = None
        # 空幕关卡每场只认一次: 认出来后同场后续几局不再走养成指南。
        self._cg_console_index = _MISSING
        self._cg_arm_reward_capture()
        # 单行失败只跳过该行，不让整场停摆。
        skipped: list[str] = []
        ledger = None
        # 收工前只回面板实测一次体力, 防止账本偏差把外层自动化带偏。
        verify_left = 1
        try:
            while True:
                self.ensure_main(check_exit=True)
                if ledger is None:
                    stamina = self._cg_read_stamina()
                    rows = self._cg_read_rows()
                    if stamina is not None and stamina >= 0:
                        if getattr(self, "_cg_start_stamina", None) is None:
                            self._cg_start_stamina = stamina
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
                    # 不再重读面板时界面会一直停在上一次的实测值, 这里让显示跟着账本走。
                    self.info_set("当前体力", stamina)
                gains = self._cg_track_gains(rows)
                skipped.extend(self._cg_drop_starved(rows, gains, skipped))
                if ledger is None:
                    state = PlanState(
                        stamina=stamina,
                        weekly_remaining=remaining,
                        rows=rows or (),
                        rounds_done=rounds,
                        weekly_blocked=weekly_blocked,
                        weekly_known=weekly_first,
                        read_failed=rows is None,
                        skip_labels=tuple(skipped),
                    )
                else:
                    state = ledger.as_state(rounds, remaining, weekly_blocked, skipped, weekly_known=weekly_first)
                # 每局开打前清掉上一局的抓拍，避免旧掉落重复入账。
                self._cg_reward_loot = []
                self._cg_loot_partial = False
                self._cg_scan_flags = []
                decision = decide(state, cfg)
                if (
                    decision.action is Action.STOP
                    and decision.reason.startswith("体力")
                    and ledger is not None
                    and verify_left > 0
                ):
                    # 账本体力是估算值: 收工前回面板实测一次, 判定和上报都用实测数字。
                    verify_left -= 1
                    real = self._cg_read_stamina()
                    if real is not None and real >= 0:
                        self.info_set("当前体力", real)
                        if real != ledger.stamina:
                            self._cg_note(
                                "warning",
                                f"收工前复核体力: 账本 {ledger.stamina} → 实测 {real}",
                            )
                            ledger.stamina = real
                            continue
                    else:
                        # 读不到就退回账本值, 别让界面留着上一个旧数字。
                        self.info_set("当前体力", ledger.stamina)
                self.info_set("培养目标决策", f"{decision.action.value}: {decision.reason}")
                report.emit(
                    "round",
                    round=rounds + 1,
                    stamina=stamina,
                    effective_stamina=stamina,
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

                if rounds == 0:
                    blocked_weekly = [
                        row.label
                        for row in (rows or ())
                        if row.landing == "weekly"
                        and weekly_first
                        and remaining <= 0
                        and row.satisfied is False
                    ]
                    if blocked_weekly:
                        self._cg_note(
                            "info",
                            f"{'、'.join(blocked_weekly)} 缺料但周本次数已用完, "
                            "本次直接跳过(不计为未完成), 空幕兜底时照常刷",
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
                    # 原版按面板体力排领取次数, 这里把当前体力传下去(不按今日目标截断)。
                    self._cg_weekly_budget = stamina
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
                    # 连打/双倍按当前面板体力规划。
                    budget = stamina
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
                        skipped=not attempted,
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
                        # 未出发的行(周本次数已用完)已在上面记过原因, 不再报「本局未完成」。
                        skipped.append(decision.row.label)
                        if attempted:
                            self._cg_note(
                                "warning",
                                f"{decision.row.label} 本局未完成, 本次跳过该行继续其它行",
                            )
                            self._cg_capture("material_failed")
                elif decision.action is Action.CONSOLE:
                    # 空幕优先级最低且没有次数上限: 别的行都推不动了才走到这里,
                    # 它的任务就是把「没处花的体力」换成材料, 所以一次刷到底。
                    runs = self._cg_run_console(stamina)
                    console_done = runs
                    report.emit("action", round=rounds + 1, action="console", ok=runs > 0, runs=runs)
                    spent = int(getattr(self, "_cg_console_spent", 0) or 0)
                    if ledger is not None and spent:
                        ledger.spend(spent)
                        self._cg_note(
                            "info", f"账本扣体力 {spent} ({runs} 局空幕), 剩 {ledger.stamina}"
                        )
                    reason = f"空幕兜底结束: {runs} 局" if runs else f"空幕未刷成: {decision.reason}"
                    self._cg_stop(report, reason, rounds, weekly_done, material_done, console_done)
                    return runs > 0
                if ledger is not None:
                    spent = 0
                    if decision.action is Action.WEEKLY:
                        spent = cfg.weekly_cost * claimed if ok else 0
                    elif decision.action is Action.MATERIAL and ok:
                        units = getattr(self, "_cg_claim_units", {}).get(decision.row.label, 1)
                        spent = decision.row.cost * units
                    if spent:
                        ledger.spend(spent)
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
        """汇总每行缺口、上局产出与预估局数。

        多档行额外写出「折最低级」的总件数: `48/8 32/40 16/13` 看着还缺蓝,
        折成最低档是 288/245 件, 已经够了 —— 不写出来对不上账。
        """
        parts = []
        for row in rows:
            raw = "·".join(f"{ratio.owned}/{ratio.required}" for ratio in row.ratios or ())
            folded = ""
            if row.ratios and len(row.ratios) > 1:
                owned, required = lowest_units(row.ratios)
                folded = f" 折最低级{owned}/{required}"
            gap = row_gap(row)
            if gap is None:
                parts.append(f"{row.label} 读不出")
            elif gap == 0:
                parts.append(f"{row.label} 已满({raw}{folded})")
            else:
                gain = gains.get(row.label)
                text = f"{row.label} {raw}{folded} 缺{gap}"
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

