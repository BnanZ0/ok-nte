"""副本内动作：入场导航、战斗、走位领奖、宝箱搜索、双倍/连打。"""

from __future__ import annotations

import cv2

from src.tasks.cultivation_goal.core import (
    COIN_LABEL,
    COIN_REWARD_KEY,
    claims_needed,
    lowest_units,
    plan_burst,
    row_gap,
)
from src.tasks.cultivation_goal.flow_const import (
    CG_BURST_CONFIRM_RANGE,
    CG_FINAL_CONFIRM_RANGE,
)
from src.Labels import Labels
from src.tasks.cultivation_goal.positions import HUNTER_BOSS_NAMES
from src.tasks.cultivation_goal.positions import POS


class CombatMixin:
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
        if last_label == COIN_LABEL:
            return self._cg_read_coin_loot()
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

    def _cg_read_coin_loot(self):
        """甲硬币没有材料槽位, 直接取结算页币种卡的数量累加。

        读不到就返回 None, 由主循环回页复核: 甲硬币一局掉十几万, 猜一个数会把
        后面十几局的缺口全算错, 比多读一次面板贵得多。
        """
        loot = getattr(self, "_cg_reward_loot", None) or []
        total = sum(
            int(item.get("count") or 0)
            for item in loot
            if item.get("key") == COIN_REWARD_KEY
        )
        if total <= 0:
            self._cg_note("info", f"{COIN_LABEL} 结算页没有读到币种卡, 本局不入账")
            return None
        self._cg_note("info", f"{COIN_LABEL} 掉落入账: +{total}")
        return {COIN_LABEL: (total,)}

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
            parts.append(f"{row.label}[{' | '.join(seg)}]{self._cg_fold_tail(row)}")
            stats.append({"label": row.label, "slots": slots})
        if parts:
            self._cg_note("info", "掉落统计: " + " ".join(parts))
            self._cg_report().emit("loot_stats", rows=stats)

    def _cg_fold_tail(self, row) -> str:
        """多档行的折算结论；单档行没有折算可言, 返回空串。"""
        if not row.ratios or len(row.ratios) < 2:
            return ""
        gap = row_gap(row)
        if gap is None:
            return ""
        owned, required = lowest_units(row.ratios)
        tail = f" → 折最低级{owned}/{required}"
        return tail + (" 已满" if gap == 0 else f" 还缺{gap}")

    def _cg_burst_plan(self, row, stamina, gains):
        """自动连打: 上一局产出不足以覆盖缺口就继续连打, 只对副本生效。"""
        if row.landing != "domain":
            return 1, None
        gain = gains.get(row.label)
        units = getattr(self, "_cg_claim_units", {}).get(row.label, 1) or 1
        per_claim = gain / units if gain and gain > 0 else None
        count = plan_burst(row_gap(row), per_claim, stamina, row.cost, self.CG_BURST_LIMIT)
        return max(1, count), per_claim

    def _cg_pick_double(self, row, stamina, gap=None, per_claim=None):
        """体力够两份且还差≥2 份时走双倍；只差 1 份或体力不足退回单倍。

        缺口是最低档件数, 不能直接和「份」比 —— 先用单份产出换成份数。
        """
        if stamina is None or stamina < 2 * row.cost:
            return False
        gap = row_gap(row) if gap is None else gap
        if gap is None:
            return True
        need = claims_needed(gap, per_claim, len(row.ratios or ()))
        return need is not None and need >= 2

    def _cg_reward_settled(self, time_out=6) -> bool:
        """领奖点击后必须看到结算确认框, 才算这一局真的到账。

        旧版把 `find_exit`(左上角 ESC 图标) 也当成功信号, 但该图标在主界面/副本内常驻,
        判断恒为真 —— 没领到也会被记成已领, 账本虚记。这里只认结算页的确认按钮。
        """
        burst = self.box_of_screen(*CG_BURST_CONFIRM_RANGE, hcenter=True)
        final = self.box_of_screen(*CG_FINAL_CONFIRM_RANGE, hcenter=True)
        try:
            found = self.wait_until(
                lambda: self.find_confirm(box=burst) or self.find_confirm(box=final),
                time_out=time_out,
                settle_time=0.3,
                raise_if_not_found=False,
            )
            if not found:
                self._cg_note("info", "结算确认框未出现, 本局不计入到账")
            return bool(found)
        except Exception as error:
            self._cg_note("warning", f"结算确认检查失败: {error}")
            return False

    def do_combat_and_claim(self, double: bool):
        """副本打完走到纪念品处领奖。

        走位交给上游 walk_to_treasure: 它按纪念品在屏幕上的位置算 w/a/s/d 方向,
        目标丢失一秒会自动转视角重找 —— 14:18/14:20 两次实机领奖成功走的就是这条路径。
        盲按 W 的六面搜索走不通, 因为方向来自镜头朝向, 镜头没对准就越走越远。

        上游唯一的坑是 end_condition 用 find_interac: F 提示 3 米外就亮, 角色停在
        2 米外按 F 游戏不响应。所以提示亮起后要分小步蹭进去, 每步都试一次 F。
        """
        self.log_info("开始执行战斗流程")
        self.walk_until_combat(run=True, delay=1)
        self.combat_once()
        self.log_info("战斗结束，正在前往领取奖励")
        # 双倍/单倍卡片偶尔在战斗刚结束就已经在屏幕上。
        claims = self.wait_until(self._cg_find_claim, time_out=1.0, raise_if_not_found=False)
        if not claims:
            claims = self.retry_on_action(self._cg_walk_claim)
        if not claims:
            claims = self._cg_find_claim()
        if not claims:
            self._cg_note("warning", "走到宝箱前但没找到领奖按钮")
            return False
        target = max(claims, key=lambda x: x.x) if double else min(claims, key=lambda x: x.x)
        self.operate_click(target.copy(x_offset=target.width * 3))
        return True

    def _cg_find_claim(self):
        """奖励卡片搜索带：与上游 find_all_claim 同一区域, 实测领奖条目落在 62%-65% 高度。"""
        box = self.box_of_screen(0.2645, 0.6167, 0.7352, 0.6785, name="cg_reward_area")
        return self.find_feature(Labels.claim_icon, box=box)

    def _cg_walk_claim(self):
        """走向纪念品并按 F; 提示亮起时还差一两米, 分小步蹭进去再按。"""
        if not self.find_treasure() and not self.find_interac():
            # 纪念品在视野外, 先转一圈把它找出来, 否则 walk_to_treasure 直接放弃。
            self.rotate_and_find_treasure()
        self.walk_to_treasure()
        for _ in range(3):
            claims = self._cg_press_claim()
            if claims:
                return claims
            if not self.find_interac():
                break
            self.send_key_down("w")
            self.sleep(0.35)
            self.send_key_up("w")
            self.sleep(0.1)
        return self._cg_press_claim()

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
            double = self._cg_pick_double(row, remaining, gap, per_claim)
            if done or double:
                self._cg_note(
                    "info",
                    f"{row.label} 第 {done + 1}/{total} 局 "
                    f"{'双倍' if double else '单倍'}, 剩余体力 {remaining}, "
                    f"估算缺口 {gap} 件最低档",
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
            # 缺口是最低档件数, 换成份数才能和「还差一局」比。
            need = claims_needed(gap, per_claim, len(row.ratios or ()))
            if cont and (need is None or need > 1):
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
                if need is not None and need <= 1:
                    self._cg_note(
                        "info",
                        f"{row.label} 估算材料已够(余 {gap} 件最低档 ≈ {need:.1f} 份), "
                        "提前收场回养成页复核",
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

    def _cg_find_treasure_wide(self, name="boss_treasure"):
        """多尺度查找宝箱/纪念品标记（0.8~1.8，阈值 0.6），比上游更宽容。"""
        try:
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

    def _cg_press_claim(self, find_claim=None):
        """按 F 并等奖励卡片出现; 没出现就返回 None。"""
        self.send_key("f", after_sleep=0.6)
        return self.wait_until(find_claim or self._cg_find_claim, time_out=1.4, raise_if_not_found=False)

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

