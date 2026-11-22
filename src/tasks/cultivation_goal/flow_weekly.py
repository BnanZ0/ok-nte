"""周本与材料副本：异象巡礼入场、次数读取、按缺口刷材料。"""

from __future__ import annotations

import re

from src.tasks.cultivation_goal.positions import POS


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
        button_y = POS.material_row_y[row.tab][row.row_index]
        self._cg_click(POS.material_button_x, button_y, f"{row.label} 获取材料", after_sleep=1.2)
        mode = self._cg_enter_landing()
        if mode is None:
            return False
        return self._cg_fight_once(row, mode, stamina, burst, per_claim)

