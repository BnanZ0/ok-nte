"""空幕：按推荐套装挑关卡，借用上游入场，循环、记账、失败重进自己管。"""

from __future__ import annotations

from src.tasks.cultivation_goal.core import (
    CONSOLE_DUNGEONS,
    console_index_for,
)
from src.tasks.cultivation_goal.flow_const import (
    CG_BURST_CONFIRM_RANGE,
    CG_CONSOLE_STAMINA_OPEN,
    CG_FINAL_CONFIRM_RANGE,
    CG_INFO_MUST_USE,
    _MISSING,
)
from src.tasks.AnomalyTask import AnomalyTask
from src.tasks.cultivation_goal.positions import POS


class ConsoleMixin:
    def _cg_run_console(self, budget=None) -> int:
        """空幕兜底: 按缺口选序号, 自己循环并逐局记账, 返回实际领到几局。

        空幕在刷取优先级里排最后, 理由是它**没有次数上限**: 别的行推不动了(材料已满、
        周本次数用完), 只有它还能继续把体力换成材料, 否则剩余体力就只能溢出浪费。

        以前是把任务类型切给原版 `AnomalyTask.do_run` 一把跑完, 插件只拿到一个 bool:
        序号恒为 1、掉落在账本里看不见、失败分不清第几局。现在只借原版的入场导航
        (传送 + 体力面板 + 序号点击已成熟), 循环/记账/失败重试自己做。
        """
        from src.tasks.AnomalyTask import AnomalyTask

        per_claim = AnomalyTask.TASK_COST
        # 先清零: 提前返回时决策侧会读这个值扣账本, 留着上次的数会重复扣一遍体力。
        self._cg_console_spent = 0
        if budget is not None and budget <= 0:
            self._cg_note("warning", "今日体力目标已用完, 不再刷空幕")
            return 0
        if budget is None:
            goal = CG_CONSOLE_STAMINA_OPEN
        else:
            # 原版按 ceil(目标/每份) 排次数, 预算 60 会取整成 2 份(80 点)超支, 这里向下取整。
            goal = (int(budget) // per_claim) * per_claim
            if goal < per_claim:
                self._cg_note(
                    "warning", f"剩余预算 {budget} 不足一份空幕奖励({per_claim}), 不再刷空幕"
                )
                return 0

        index = self._cg_pick_console_index()
        if index is None:
            return 0

        original_type = self.config.get(self.CONF_TASK_TYPE, _MISSING)
        original_id = self.config.get(AnomalyTask.CONF_CONSOLE_ID, _MISSING)
        saved_goal = self.info.get(CG_INFO_MUST_USE, _MISSING)
        entered = False
        try:
            dict.__setitem__(self.config, self.CONF_TASK_TYPE, self.TASK_CONSOLE)
            dict.__setitem__(self.config, AnomalyTask.CONF_CONSOLE_ID, index + 1)
            # 上一轮残留的局数会让原版判错次数, 清掉让本次按预算重算。
            for key in ("双倍次数", "单倍次数"):
                self.info.pop(key, None)
            if saved_goal is _MISSING or not saved_goal:
                self.info_set(CG_INFO_MUST_USE, goal)
            if self.enter_anomaly(index) is not True:
                self._cg_note("warning", "空幕入场失败, 本次不再刷空幕")
                self._cg_capture("console_failed")
                return 0
            entered = True
            double_count = int(self.info_get("双倍次数") or 0)
            single_count = int(self.info_get("单倍次数") or 0)
            total = double_count + single_count
            if total <= 0:
                # 体力刚够一份或上游没算出次数时 total 会是 0, 不喊一声就会静默返回 0。
                self._cg_note(
                    "warning",
                    f"空幕入场后没算出局数(双倍 {double_count} / 单倍 {single_count}), 本次不刷",
                )
                return 0
            self._cg_note("info", f"空幕入场成功(序号 {index + 1}), 计划 {total} 局, 预算 {goal}")
            done, failures = 0, 0
            while done < total and failures < 2:
                self.wait_until(self.find_exit, time_out=30, raise_if_not_found=False)
                self.wait_in_team()
                self.sleep(1)
                # 每局单独清抓拍, 否则多局掉落会累加成一笔糊涂账。
                self._cg_reward_loot = []
                self._cg_loot_partial = False
                if not self.do_combat_and_claim(done < double_count):
                    claimed, why_failed = False, "领奖失败"
                elif not self._cg_reward_settled():
                    # 点了按钮还停在领取界面(11-22 15:35 实测形态): 这局没到账, 不能记成功。
                    self._cg_capture("claim_unsettled")
                    claimed, why_failed = False, "领奖后未见结算界面"
                else:
                    claimed, why_failed = True, ""
                if not claimed:
                    failures += 1
                    self._cg_note(
                        "warning", f"空幕第 {done + 1}/{total} 局{why_failed}, 退出后重试"
                    )
                    self._cg_capture("console_failed")
                    self.exit_anomaly()
                    if failures >= 2 or self.enter_anomaly(index, retry=True) is not True:
                        break
                    continue
                double_run = done < double_count
                spent = per_claim * (2 if double_run else 1)
                done += 1
                failures = 0
                self._cg_console_spent += spent
                # 每局单独记掉落: 顺带用奖励卡上的关卡名反查序号选的对不对。
                self._cg_log_console_loot(index, done)
                if done < total:
                    # 点不到「继续挑战」就是连打到头了(体力/次数用尽), 硬撑下一局只会空打。
                    if not self.wait_click_confirm(
                        range=CG_BURST_CONFIRM_RANGE, raise_if_not_found=False
                    ):
                        self._cg_note("info", f"空幕无继续挑战按钮, 第 {done} 局后收场")
                        break
            self.wait_click_confirm(range=CG_FINAL_CONFIRM_RANGE, raise_if_not_found=False)
            self._cg_note("info", f"空幕结束: 共领 {done}/{total} 局")
            return done
        finally:
            if saved_goal is _MISSING:
                self.info.pop(CG_INFO_MUST_USE, None)
            else:
                self.info_set(CG_INFO_MUST_USE, saved_goal)
            for key, value in (
                (self.CONF_TASK_TYPE, original_type),
                (AnomalyTask.CONF_CONSOLE_ID, original_id),
            ):
                if value is _MISSING:
                    self.config.pop(key, None)
                else:
                    dict.__setitem__(self.config, key, value)
            if entered:
                # 没进过本就不要调: exit_anomaly 会在主界面上反复按 ESC 找「已离开副本」。
                self.exit_anomaly()

    def _cg_pick_console_index(self):
        """读「空幕」tab 的推荐套装，反查兔子洞关卡；读不出/未收录返回 None。

        空幕页是「当前套装 / 推荐套装」两块，没有异能页那种四行 xx/xx，判定依据
        只能是套装名：套装名 → 关卡的表来自游戏内卡带详情页的「获取途径」。
        认出的关卡缓存一次，本次任务后续几局不再重复走养成指南（那条路最慢）。
        """
        cached = getattr(self, "_cg_console_index", _MISSING)
        if cached is not _MISSING:
            if cached is None:
                return None
            self._cg_note("info", f"沿用已认出的空幕序号 {cached + 1}")
            return cached
        self._cg_console_index = None
        if not self._cg_ensure_guide():
            return None
        self._cg_click(POS.tab_x["空幕"], POS.overview_tabs_y, "空幕 tab", after_sleep=0.7)
        current, suggest = self._cg_read_console_sets()
        self._cg_capture("console_guide")
        index, why = console_index_for((suggest, current))
        if index is None:
            self._cg_note(
                "warning", f"空幕 {why}, 为避免刷错副本白花体力, 本次不刷空幕"
            )
            return None
        if index >= 4:
            # 第 5/6 节点（心智图谱 / 轨道之夜）需要猎人等级解锁。空幕本来就不记收获,
            # 所以不掉落认不出也无所谓; 只在未解锁时入场会失败 —— 那样不扣体力, 可放心试。
            self._cg_note(
                "info",
                f"空幕 {why}: 属后段节点, 若未解锁会入场失败(不扣体力)",
            )
        self._cg_console_index = index
        self._cg_note("info", f"空幕 {why}")
        return index

    def _cg_read_console_sets(self):
        """OCR「当前套装」「推荐套装」两行，返回 (当前, 推荐)。

        只能读到套装名（如「音速蓝刺猬」x4），关卡要靠 core.CONSOLE_SETS 反查；
        读不出就返回空串，由 console_index_for 判为未收录，不猜。
        """
        texts = []
        for label, center_y in zip(("当前套装", "推荐套装"), POS.console_set_y):
            x1, x2 = POS.console_set_x
            box = self.box_of_screen(
                x1,
                center_y - POS.console_set_half_height,
                x2,
                center_y + POS.console_set_half_height,
                name=f"cg_console_set_{len(texts)}",
                hcenter=True,
            )
            text = self._cg_read_line(box)
            self._cg_note("info", f"空幕{label} (y {center_y:.3f}): {text or '<空>'}")
            texts.append(text or "")
        return texts[0], texts[1]

    def _cg_log_console_loot(self, index: int, run_no: int):
        """记一局空幕的结算掉落（尽力而为），顺带用副本名反查序号选对没。

        空幕不需要把掉落算进培养缺口——它是「体力没处花」的兜底，刷什么材料都行，
        到不到账只看结算页有没有出现。这里纯粹留证据：奖励卡几乎全是金色圆盘、
        图案差异极小，认错很正常，所以只记日志不参与任何判定。
        """
        loot = getattr(self, "_cg_reward_loot", None) or []
        if not loot:
            self._cg_note("info", f"空幕第 {run_no} 局: 未读出掉落(金圆盘模板相近, 属正常)")
            return
        text = " ".join(f"{item['dungeon']}×{item['count']}" for item in loot)
        self._cg_note("info", f"空幕第 {run_no} 局掉落: {text}")
        # 奖励卡上的名字是「钟表把戏 金」这种「关卡 + 档位」, 反查要用包含而不是全等。
        dungeons = {item["dungeon"] for item in loot if item.get("dungeon")}
        expected = CONSOLE_DUNGEONS[index] if 0 <= index < len(CONSOLE_DUNGEONS) else None
        if expected and dungeons and not any(expected in name for name in dungeons):
            self._cg_note(
                "info",
                f"空幕第 {run_no} 局产出 {sorted(dungeons)} 与预期 {expected} 不符"
                " —— 金圆盘模板相近, 多半是认错图标, 仅供参考",
            )
        self._cg_report().emit(
            "console_loot",
            run=run_no,
            console_id=index + 1,
            entries=[{"name": item.get("dungeon"), "key": item["key"], "count": item["count"]}
                     for item in loot],
        )

