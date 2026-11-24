"""周本与材料副本：异象巡礼入场、次数读取、按缺口刷材料。"""

from __future__ import annotations

import re

from src.Labels import Labels
from src.tasks.cultivation_goal.core import COIN_LABEL
from src.tasks.cultivation_goal.positions import POS


# 地图详情页右下角按钮：已解锁显示「传送」，未解锁显示「追踪」（11-23 实拍标定）。
# 按钮白块实占 (0.7322, 0.8630)-(0.9434, 0.9161)，内缩 1.5% 避开圆角与右下角性能浮层。
WEEKLY_ENTRY_BUTTON_AREA = (0.745, 0.865, 0.935, 0.915)
WEEKLY_LOCKED_WORD = "追踪"
WEEKLY_UNLOCKED_WORDS = ("传送", "前往")


def weekly_entry_is_locked(text) -> bool:
    """右下角读到「追踪」才算未解锁；读到传送/前往、或什么都没读出来都按已解锁处理。

    保守方向很重要：误判已解锁只是让上游照常尝试（最坏一次超时），
    误判未解锁会把能打的副本白白剔掉。
    """
    if not text:
        return False
    if any(word in text for word in WEEKLY_UNLOCKED_WORDS):
        return False
    return WEEKLY_LOCKED_WORD in text


class WeeklyMixin:
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

    def _cg_weekly_entry_text(self):
        """读地图详情页右下角按钮文字，只读不点。"""
        box = self.box_of_screen(
            *WEEKLY_ENTRY_BUTTON_AREA, name="cg_weekly_entry", hcenter=True
        )
        results = self.ocr(box=box, match=None) or []
        return "".join(result.name for result in results)

    def _cg_weekly_goto_x(self):
        """周本列表每行「前往」按钮的横坐标，取上游场景坐标，读不到用实测值兜底。"""
        try:
            x, _ = self.pos.panels.f1.domain.goto
            return x
        except Exception:
            return 0.867

    def _cg_weekly_in_map(self):
        """地图开着时才有缩放控件；用它判断还需不需要继续退出。"""
        try:
            return bool(self.find_one(Labels.map_zoom_in))
        except Exception:
            return False

    def _cg_weekly_back(self):
        """退出地图/详情页，回到能重新打开巡礼列表的状态。

        只在还在地图上时才按 ESC：主界面上多按一次会弹出游戏菜单。
        """
        for _ in range(3):
            if not self._cg_weekly_in_map():
                return
            self.send_key("esc", after_sleep=0.8)

    def _cg_weekly_probe_locked(self, target):
        """点开目标详情只看右下角按钮、不点它；返回 True 表示未解锁。

        列表或目标行打不开就直接抛，交给调用方按「已解锁」兜底。
        ESC 只在真的点开详情之后才按，避免在主界面凭空弹菜单。
        """
        self.open_target_list()
        match = self.find_pilgrimage_target(target)
        _, y = match.center()
        try:
            self.operate_click(self._cg_weekly_goto_x(), y / self.height, after_sleep=1.2)
            return weekly_entry_is_locked(self._cg_weekly_entry_text())
        finally:
            self._cg_weekly_back()

    def _cg_filter_locked_weekly(self, targets):
        """剔掉未解锁的周本目标，剩下的交给上游队列按次数排。

        探测失败的目标保留：宁可让上游照常试一次，也不因为一次 OCR 抖动错杀。
        """
        usable = []
        for target in targets:
            try:
                locked = self._cg_weekly_probe_locked(target)
            except Exception as error:
                self._cg_note("warning", f"{target} 详情打不开, 先按已解锁处理: {error}")
                usable.append(target)
                continue
            if locked:
                self._cg_note("warning", f"{target} 未解锁, 跳过")
            else:
                usable.append(target)
        return usable

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
            # 未解锁的副本点「前往」只会开出「追踪」，上游会一直等传送按钮等到超时，
            # 所以先探测一遍再排队列。
            targets = self._cg_filter_locked_weekly(targets)
            if not targets:
                self._cg_note("warning", "周本目标全部未解锁, 本次跳过周本")
                self._cg_weekly_unconfirmed = 0
                return 0, False
            dict.__setitem__(self.config, AnomalyPilgrimage.CONF_TARGET, targets)
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
                # 原版按面板体力排领取次数, 这里与传入预算取小(预算=当前体力时等同不限制)。
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
        if row.name == COIN_LABEL:
            # 甲硬币是所需材料的最后一行: 不滑到底它的按钮不在固定位置。
            self._cg_scroll_material_to_bottom()
            button_y = POS.coin_button_y
        else:
            button_y = POS.material_row_y[row.tab][row.row_index]
        self._cg_click(POS.material_button_x, button_y, f"{row.label} 获取材料", after_sleep=1.2)
        mode = self._cg_enter_landing()
        if mode is None:
            return False
        return self._cg_fight_once(row, mode, stamina, burst, per_claim)

