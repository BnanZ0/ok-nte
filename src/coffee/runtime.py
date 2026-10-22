"""一咖舍运行时.

承担与 BaseTask 无关的咖舍业务逻辑：开店面板进入、收益领取、商品识别与切换、
补货购买、状态读取等。本模块不继承任何 task 基类，由 ``CoffeeTask`` 在
``do_run`` 中直接传入自身后调用。
"""

import difflib
import re
import time
from dataclasses import dataclass, field

from ok import TaskDisabledException


@dataclass
class CoffeeFoodOption:
    identity: str
    price_value: int | None = None
    category: str = ""
    trend_match: bool = False
    visible_order: int = 0
    target: object | None = None


@dataclass
class CoffeeSupplySlot:
    identity: str
    options: list[CoffeeFoodOption] = field(default_factory=list)
    current_food_identity: str = ""
    needs_supply: bool = True
    safe: bool = True
    target: object | None = None


@dataclass
class CoffeeShopState:
    trend_category: str = ""
    income_claim_target: object | None = None
    supply_target: object | None = None
    slots: list[CoffeeSupplySlot] = field(default_factory=list)


@dataclass
class CoffeeDetectedBox:
    name: str
    x: int
    y: int
    width: int
    height: int
    confidence: float = 1.0


ALLOWED_DURATIONS = ("2小时", "4小时", "8小时", "24小时")
_DURATION_ALIASES = {
    "auto": "24小时",
    "2h": "2小时",
    "2hour": "2小时",
    "2小时": "2小时",
    "4h": "4小时",
    "4hour": "4小时",
    "4小时": "4小时",
    "8h": "8小时",
    "8hour": "8小时",
    "8小时": "8小时",
    "24h": "24小时",
    "24hour": "24小时",
    "24小时": "24小时",
}


def normalize_duration(duration):
    text = str(duration or "").strip().lower().replace(" ", "")
    return _DURATION_ALIASES.get(text, text)


def is_allowed_duration(duration):
    return normalize_duration(duration) in ALLOWED_DURATIONS


class CoffeeRuntime:
    """一咖舍运行时. 操作来自 ``task`` 自身的方法.

    ``run()`` 返回 ``(ok, skip_reason)`` 二元组. 副作用通过 ``income_claimed``、
    ``real_purchase_performed``、``selected_options`` 等属性暴露.
    """

    COFFEE_POINT_CANDIDATES = (
        (0.405, 0.740),
        (0.420, 0.740),
        (0.390, 0.740),
        (0.405, 0.765),
        (0.420, 0.765),
        (0.390, 0.765),
        (0.405, 0.790),
        (0.420, 0.790),
        (0.390, 0.790),
    )
    COFFEE_PANEL_REGION = (0.02, 0.05, 0.98, 0.95)
    COFFEE_LEFT_REGION = (0.02, 0.08, 0.36, 0.92)
    COFFEE_PRODUCT_REGION = (0.38, 0.62, 0.98, 0.95)
    COFFEE_PRODUCT_POPUP_REGION = (0.02, 0.02, 0.36, 0.96)
    COFFEE_SUPPLY_POPUP_REGION = (0.18, 0.10, 0.84, 0.86)
    COFFEE_CONFIRM_REGION = (0.20, 0.12, 0.86, 0.88)
    COFFEE_PRODUCT_EDITOR_ENTRY_FALLBACK_POSITION = (0.793, 0.864)
    COFFEE_SUPPLY_BUTTON_POSITION = (0.145, 0.525)
    COFFEE_PRODUCT_SCROLL_FALLBACK_POINT = (0.20, 0.58)
    COFFEE_PRODUCT_SCROLL_EDGE_MARGIN = 0.08
    COFFEE_PRODUCT_SCROLL_WHEEL_COUNT = -8
    COFFEE_PRODUCT_DEFAULT_SCAN_SCROLLS = 5
    COFFEE_SUPPLY_CLICK_SETTLE_SECONDS = 1.2
    COFFEE_SUPPLY_CLICK_DOWN_TIME = 0.04
    COFFEE_KEY_SETTLE_SECONDS = 1.0
    COFFEE_SUPPLY_BLOCKER_TEXTS = (
        "库存提示",
        "缺少",
        "方斯不足",
        "不足",
        "失败",
        "无法",
        "已满",
        "上限",
    )
    PRODUCT_CATEGORIES = ("主食", "饮料", "甜品")
    TYCOON_TEXT_MARKERS = (
        "都市大亨",
        "大亨等级",
        "一咖舍",
        "咖舍",
        "猎人交易所",
        "车辆赛事",
        "都市闲趣",
    )
    TYCOON_ASCII_MARKERS = ("CITYTYCOON", "CTYTYCOON", "TYCOON")
    # 可能性的回溯通过 possessive (``++`` / ``?+``) 量词消除, 由 Python 3.11+ 支持.
    # 保留 ``[0-9]`` 而非 ``\d`` 以避免 ``\d`` 匹配全角/Unicode 十进制数字时
    # ``float()`` 抛出 ValueError. ``\s?+`` 在保持 0/1 个空格的同时阻止回溯,
    # 使 SonarCloud python:S5852 不再判定为 polynomial backtracking 风险.
    PRICE_RE = re.compile(r"([0-9]++(?:\.[0-9]++)?+)\s?+/\s?+h", re.IGNORECASE)

    def __init__(self, task):
        self.task = task
        self.actions: list[str] = []
        self.selected_options: list[CoffeeFoodOption] = []
        self.product_switch_error = ""
        self.pending_supply_completed = False
        self.pending_supply_error = ""
        self.income_claimed = False
        self.real_purchase_performed = False

    def run(self, card=None):
        self.actions = []
        self.selected_options = []
        self.product_switch_error = ""
        self.pending_supply_completed = False
        self.pending_supply_error = ""
        self.income_claimed = False
        self.real_purchase_performed = False

        if not self.open_coffee_shop(card):
            return False, "未能进入一咖舍界面"
        if self.pending_supply_error:
            return False, self.pending_supply_error

        if self._action_enabled("collect_income"):
            self.income_claimed = self.claim_income_if_present()
            if self.is_income_report_popup():
                self.actions.append("income_popup_not_closed_after_claim")
                return False, "营收报告弹窗未关闭，未进入商品/补货流程"
        else:
            self.actions.append("collect_income_skipped_disabled")

        if self._action_enabled("optimize_products"):
            self.optimize_products()
            if self.product_switch_error:
                return False, self.product_switch_error
        else:
            self.actions.append("optimize_products_skipped_disabled")

        skip_reason = ""
        if self._action_enabled("replenish_supply"):
            ok, skip_reason, real_purchase = self.replenish_supply()
            if not ok:
                return False, skip_reason
            self.real_purchase_performed = real_purchase
        else:
            self.actions.append("replenish_supply_skipped_disabled")
        return True, skip_reason

    def _action_enabled(self, name):
        return bool(self._config_get(f"coffee_action_{name}", True))

    def open_coffee_shop(self, card=None):
        if self._allow_pending_supply_completion():
            self.complete_pending_supply_delivery_if_present()
        elif self.pending_supply_delivery_present():
            self.actions.append("pending_supply_delivery_skipped")
            return False
        if self.pending_supply_error:
            return False

        self.complete_coffee_challenge_if_present()

        self.close_product_popup_if_present()
        self.dismiss_blank_close_overlay_if_present()
        if self.confirm_income_popup_if_present() and self.wait_for_coffee_shop_panel(timeout=2):
            return True
        if self.is_income_report_popup():
            self.actions.append("income_popup_not_closed_before_open")
            return False

        target = getattr(card, "action_box", None)
        if target is not None:
            self._click(target, "enter_daily_coffee_card")
            if self.wait_for_coffee_shop_panel():
                return True

        if self.is_coffee_shop_panel():
            return True

        self._send_key("f5", "open_city_tycoon_f5")
        tycoon_ready = self.wait_for_tycoon_panel()
        if not tycoon_ready:
            self.actions.append("retry_open_city_tycoon_f5_foreground")
            self._send_foreground_key("f5", "open_city_tycoon_f5_foreground")
            tycoon_ready = self.wait_for_tycoon_panel()
        if tycoon_ready:
            self.wait_for_tycoon_map_settle()
            if self.select_coffee_from_tycoon():
                return True

        return self.is_coffee_shop_panel()

    def close_product_popup_if_present(self):
        if self.find_text_box("商品列表", self.COFFEE_PRODUCT_POPUP_REGION) is None:
            return False
        self.close_popup()
        self.wait_for_coffee_shop_panel(timeout=3)
        return True

    def collect_shop_state(self):
        return CoffeeShopState(
            trend_category=self.detect_trend_category(),
            income_claim_target=self.find_text_box("提取收益", self.COFFEE_LEFT_REGION),
            supply_target=self.find_text_box("补货", self.COFFEE_LEFT_REGION),
            slots=self.collect_current_product_slots(),
        )

    def claim_income_if_present(self):
        target = self.find_text_box("提取收益", self.COFFEE_LEFT_REGION)
        if target is None:
            self.actions.append("income_not_found")
            return False
        self._click(target, "claim_income")
        self._sleep(0.8)
        self.confirm_income_popup_if_present()
        return not self._dry_run()

    def confirm_income_popup_if_present(self):
        if not self.is_income_report_popup():
            return False
        for attempt in range(3):
            target = self.find_button_text_box("确定", self.COFFEE_PANEL_REGION)
            if target is None:
                self.actions.append("income_popup_confirm_not_found")
                return False
            self._click(target, "confirm_income_popup")
            self._sleep(0.6)
            self.dismiss_blank_close_overlay_if_present()
            if self.wait_for(lambda: not self.is_income_report_popup(), timeout=2):
                return True
            self.actions.append(f"income_popup_confirm_still_visible:{attempt + 1}")
        return False

    def dismiss_blank_close_overlay_if_present(self):
        target = self.find_text_box("点击空白区域关闭", self.COFFEE_PANEL_REGION)
        if target is None:
            return False
        self._send_key("esc", "dismiss_blank_close_overlay")
        self._sleep(0.6)
        return True

    def optimize_products(self):
        if self._price_table_disabled():
            self.actions.append("optimize_products_disabled_by_price_table")
            return
        state = self.collect_shop_state()
        slots = self._managed_product_slots(state.slots)
        entry_slot = self._product_editor_entry_slot(slots)
        if entry_slot is None or entry_slot.target is None:
            self.actions.append("product_editor_entry_not_found")
            return

        if not self.open_product_editor(entry_slot):
            self.actions.append("product_editor_popup_not_found")
            self.product_switch_error = "未检测到商品列表弹窗"
            return

        options = self.collect_product_options_with_scroll()
        current_options = self._current_product_options(slots, options)
        protected_current_options = self._protected_current_product_options(slots, current_options)
        target_count = self._target_product_count(slots)
        self._record_product_scan(slots, options, state.trend_category, target_count=target_count)
        if len(current_options) < target_count:
            fill_count = target_count - len(current_options)
            candidates = self._fill_product_candidates(
                options,
                current_options,
                state.trend_category,
                conflict_options=protected_current_options,
            )
            self.close_popup()
            empty_slot = self.find_empty_product_slot()
            if empty_slot is None:
                self.actions.append("empty_product_slot_not_found")
                selected = []
            else:
                self._click_detected_box(empty_slot, "open_empty_product_slot")
                if not self.wait_for_product_popup():
                    self.actions.append("empty_product_slot_popup_not_found")
                    selected = []
                else:
                    selected = self._select_product_candidates_single_pass(candidates, fill_count)
            self.close_popup()
            if len(selected) < fill_count:
                self.actions.append(f"product_fill_incomplete:{len(selected)}/{fill_count}")
                self.product_switch_error = f"商品补位失败: 只选择{len(selected)}/{fill_count}"
            elif not self._verify_product_slot_count(target_count):
                self.product_switch_error = f"商品补位后未验证到{target_count}个商品"
            else:
                self.actions.append(f"product_fill_completed:{len(selected)}/{fill_count}")
            return
        switches = self._product_switch_plan(
            slots,
            options,
            state.trend_category,
            target_count=target_count,
        )
        switch_performed = False
        if not switches:
            self.actions.append("product_switch_not_needed")
        else:
            switch_performed = self._execute_product_switches_single_popup(switches)
        self.close_popup()
        if self.product_switch_error:
            return
        if switches and switch_performed:
            if not self._verify_product_slot_count(target_count):
                self.product_switch_error = f"商品替换后未验证到{target_count}个商品"
            else:
                self.actions.append(f"product_switch_verified:{target_count}")

    def _execute_product_switches_single_popup(self, switches):
        additions = [option for _, option in switches]
        reachable_additions = self._visible_product_candidates_single_pass(
            additions,
            len(additions),
            allow_price_fallback=False,
        )
        self.reset_product_options_scroll(steps=self._product_scrolls())

        executable = []
        for current, option in switches:
            if not self._option_matches_any(option, reachable_additions):
                self.actions.append(f"select_product_not_visible:{option.identity}")
                continue
            executable.append((current, option))
        if not executable:
            return False

        removals = [current for current, _ in executable if current is not None]
        removed = []
        if removals:
            removed = self._click_product_candidates_single_pass(
                removals,
                len(removals),
                "deselect_product",
                allow_price_fallback=True,
                record_selected=False,
            )
            for current in removals:
                if not self._option_matches_any(current, removed):
                    self.actions.append(f"deselect_product_not_visible:{current.identity}")
            self.reset_product_options_scroll(steps=self._product_scrolls())

        switch_performed = bool(removed)
        additions_to_select = [
            option
            for current, option in executable
            if current is None or self._option_matches_any(current, removed)
        ]
        if not additions_to_select:
            return switch_performed

        selected = self._click_product_candidates_single_pass(
            additions_to_select,
            len(additions_to_select),
            "select_product",
            allow_price_fallback=False,
            record_selected=True,
        )
        switch_performed = switch_performed or bool(selected)
        if len(selected) < len(additions_to_select):
            missing = next(
                (
                    option
                    for option in additions_to_select
                    if not self._option_matches_any(option, selected)
                ),
                None,
            )
            if missing is not None:
                self.product_switch_error = f"商品替换失败: 未能选择{missing.identity}"
                self.actions.append(f"product_switch_failed:{missing.identity}")
        return switch_performed

    def replenish_supply(self):
        """返回 (ok, skip_reason, real_purchase_performed)."""
        if self.pending_supply_completed:
            self.actions.append("supply_already_completed_from_pending_confirm")
            return True, "", not self._dry_run()

        supply_target = self.find_text_box("补货", self.COFFEE_LEFT_REGION)
        if supply_target is None:
            self.actions.append("supply_not_needed_or_not_found")
            return True, "supply_not_needed_or_not_found", False
        if not self.open_supply_popup(supply_target):
            return False, "未检测到原料库存补货界面", False

        configured_duration = self._supply_duration()
        if not is_allowed_duration(configured_duration):
            allowed = "/".join(ALLOWED_DURATIONS)
            return False, f"补货时长必须是固定选项之一: {allowed}", False
        last_error = ""
        attempts = self._supply_duration_attempts(configured_duration)
        for index, duration in enumerate(attempts):
            duration_target = self.find_text_box(duration, self.COFFEE_SUPPLY_POPUP_REGION)
            if duration_target is None:
                if index == 0:
                    return False, f"未检测到{duration}补货选项，停止购买", False
                self.actions.append(f"supply_duration_not_found:{duration}")
                continue
            self._click_supply_button(duration_target, f"select_supply_duration:{duration}")
            self._sleep(0.8)

            buy_target = self.wait_for_supply_buy_button(timeout=5)
            if buy_target is None:
                return False, "未检测到补货购买按钮，停止购买", False
            self._click_supply_button(buy_target, "buy_supply")

            verify_error = self.finish_home_delivery_flow()
            if verify_error:
                last_error = f"{duration}补货未完成: {verify_error}"
                if index < len(attempts) - 1:
                    self.actions.append(f"supply_duration_blocked:{duration}:{verify_error}")
                    self.close_popup()
                    self.wait_for_supply_popup()
                    continue
                self.close_popup()
                return False, last_error, False
            self.close_popup()
            return True, "", not self._dry_run()

        return False, last_error or "补货材料或库存不足，所有固定时长均未补货", False

    def open_supply_popup(self, supply_target):
        self._click(supply_target, "open_supply")
        if self.wait_for_supply_popup():
            return True
        self._click_ui(
            *self.COFFEE_SUPPLY_BUTTON_POSITION, "open_supply_fallback_button", move=True
        )
        return bool(self.wait_for_supply_popup())

    def open_product_editor(self, entry_slot):
        self._click(entry_slot.target, f"open_product_editor:{entry_slot.identity}")
        if self.wait_for_product_popup():
            return True
        self._click_ui(
            *self.COFFEE_PRODUCT_EDITOR_ENTRY_FALLBACK_POSITION,
            "open_product_editor_fallback_slot",
            move=True,
        )
        return bool(self.wait_for_product_popup())

    def select_coffee_from_tycoon(self):
        label = self.wait_for_coffee_tycoon_label()
        if label is not None:
            x = self._center_x(label)
            y = self._center_y(label) + self._screen_height() * 0.125
            self._click_screen_point(x, y, "select_yikafei_from_tycoon_ocr", move=True)
            if self.wait_for_coffee_shop_panel(timeout=4):
                return True

        for index, point in enumerate(self.COFFEE_POINT_CANDIDATES, start=1):
            self._click_ui(
                *point,
                action=f"select_yikafei_from_tycoon_candidate:{index}",
                move=True,
            )
            if self.wait_for_coffee_shop_panel(timeout=4):
                return True
        return False

    def wait_for_coffee_tycoon_label(self):
        return self.wait_for(self.find_coffee_tycoon_label, timeout=3)

    def find_coffee_tycoon_label(self):
        for box in self.ocr_region(self.COFFEE_PANEL_REGION):
            text = self.box_text(box)
            if "一咖舍" in text or "咖舍" in text:
                return box
        return None

    def collect_current_product_slots(self):
        options = self._collect_food_options_from_region(self.COFFEE_PRODUCT_REGION)
        slots = []
        for option in options:
            slots.append(
                CoffeeSupplySlot(
                    identity=option.identity,
                    current_food_identity=option.identity,
                    options=[option],
                    target=option.target,
                )
            )
        return slots

    def collect_product_options(self):
        return self._collect_food_options_from_region(self.COFFEE_PRODUCT_POPUP_REGION)

    def collect_product_options_with_scroll(self):
        options = self._dedupe_food_options(self.collect_product_options())
        page_options = list(options)
        last_signature = self._product_page_signature(page_options)
        scrolls_performed = 0
        stable_price_pages = 0
        for _ in range(self._product_scrolls()):
            self.scroll_product_options(page_options, steps=1)
            scrolls_performed += 1
            page_options = self._dedupe_food_options(self.collect_product_options())
            options = self._dedupe_food_options([*options, *page_options])
            signature = self._product_page_signature(page_options)
            if (
                signature
                and last_signature
                and self._page_signature_overlap(signature, last_signature) >= 0.9
            ):
                stable_price_pages += 1
            else:
                stable_price_pages = 0
            if stable_price_pages and scrolls_performed >= 2:
                self.actions.append("product_scan_reached_bottom")
                break
            last_signature = signature
        if scrolls_performed:
            self.reset_product_options_scroll(page_options, steps=scrolls_performed)
        self.actions.append(f"product_scan_pages:{scrolls_performed + 1}")
        self.actions.append(f"product_scan_options:{len(options)}")
        return options

    def verify_supply_purchase(self):
        if self._dry_run():
            self.actions.append("supply_purchase_verified_dry_run")
            return ""
        self._sleep(0.8)
        blocker = self.wait_for_supply_blocker_text(timeout=3)
        if blocker:
            return f"补货确认后出现库存或材料限制: {blocker}"
        still_confirming = self.find_button_text_box("确认", self.COFFEE_CONFIRM_REGION)
        if still_confirming is not None:
            return "补货确认后确认窗口仍存在，未验证到送货成功"
        stock_prompt = self.find_text_box("库存提示", self.COFFEE_CONFIRM_REGION)
        if stock_prompt is not None:
            return "补货确认后库存提示仍存在，未验证到送货成功"
        self._sleep(0.6)
        return ""

    def finish_home_delivery_flow(
        self,
        select_action="select_home_delivery",
        confirm_action="confirm_home_delivery",
        allow_buy_retry=True,
    ):
        self._sleep(1.0)
        home_delivery = self.wait_for_button_text_box(
            "送货上门", self.COFFEE_CONFIRM_REGION, timeout=8
        )
        if home_delivery is None:
            blocker = self.wait_for_supply_blocker_text(timeout=2)
            if blocker:
                return f"补货确认后出现库存或材料限制: {blocker}"
            if (
                allow_buy_retry
                and self.find_text_box("原料库存", self.COFFEE_SUPPLY_POPUP_REGION) is not None
            ):
                buy_target = self.wait_for_supply_buy_button(timeout=2)
                if buy_target is not None:
                    self._click_supply_button(buy_target, "buy_supply_retry_after_no_prompt")
                    return self.finish_home_delivery_flow(
                        select_action=select_action,
                        confirm_action=confirm_action,
                        allow_buy_retry=False,
                    )
            return self.verify_supply_purchase_without_delivery_prompt()
        self._click_supply_button(home_delivery, select_action)

        confirm = self.wait_for_button_text_box("确认", self.COFFEE_CONFIRM_REGION, timeout=6)
        if confirm is not None:
            self._click_supply_button(confirm, confirm_action)
        return self.verify_supply_purchase()

    def verify_supply_purchase_without_delivery_prompt(self):
        if self._dry_run():
            self.actions.append("supply_purchase_verified_without_delivery_prompt_dry_run")
            return ""
        self._sleep(0.8)
        blocker = self.wait_for_supply_blocker_text(timeout=2)
        if blocker:
            return f"补货确认后出现库存或材料限制: {blocker}"
        if self.find_text_box("原料库存", self.COFFEE_SUPPLY_POPUP_REGION) is not None:
            return "未检测到送货上门确认按钮，补货弹窗仍存在，未验证补货成功"
        if not self.wait_for_coffee_shop_panel(timeout=3):
            return "未检测到送货上门确认按钮，未回到一咖舍界面，未验证补货成功"
        self.actions.append("supply_purchase_verified_without_delivery_prompt")
        return ""

    def wait_for_supply_blocker_text(self, timeout=2):
        return self.wait_for(self.find_supply_blocker_text, timeout=timeout)

    def find_supply_blocker_text(self):
        for box in self.ocr_region(self.COFFEE_CONFIRM_REGION):
            text = self.box_text(box)
            if any(marker in text for marker in self.COFFEE_SUPPLY_BLOCKER_TEXTS):
                self.actions.append(f"supply_purchase_blocked:{text}")
                return text
        return ""

    def detect_trend_category(self):
        boxes = self.ocr_region(self.COFFEE_LEFT_REGION)
        texts = [self.box_text(box) for box in boxes]
        for category in self.PRODUCT_CATEGORIES:
            if any(category in text for text in texts):
                return category
        return ""

    def _collect_food_options_from_region(self, region):
        boxes = self.ocr_region(region)
        options = []
        trend = self.detect_trend_category()
        for index, box in enumerate(boxes):
            text = self.box_text(box)
            match = self.PRICE_RE.search(text)
            if not match:
                continue
            price_value = int(round(float(match.group(1)) * 100))
            category = self._nearest_category(box, boxes)
            name = self._nearest_name(box, boxes)
            identity = name or f"coffee_food_{index}"
            options.append(
                CoffeeFoodOption(
                    identity=identity,
                    price_value=price_value,
                    category=category,
                    trend_match=bool(category and category == trend),
                    visible_order=index,
                    target=box,
                )
            )
        return options

    def _nearest_category(self, price_box, boxes):
        price_center_y = self._center_y(price_box)
        candidates = []
        for box in boxes:
            text = self.box_text(box)
            if text not in self.PRODUCT_CATEGORIES:
                continue
            dy = abs(self._center_y(box) - price_center_y)
            if dy < 180:
                candidates.append((dy, text))
        return sorted(candidates)[0][1] if candidates else ""

    def _nearest_name(self, price_box, boxes):
        price_center_x = self._center_x(price_box)
        price_center_y = self._center_y(price_box)
        candidates = []
        for box in boxes:
            text = self.box_text(box)
            if not text or self.PRICE_RE.search(text) or text in self.PRODUCT_CATEGORIES:
                continue
            dx = abs(self._center_x(box) - price_center_x)
            dy = self._center_y(box) - price_center_y
            if dx < 180 and 0 < dy < 120:
                candidates.append((dy, dx, text))
        return sorted(candidates)[0][2] if candidates else ""

    def _matching_option(self, options, identity):
        for option in options:
            if self._same_identity(option.identity, identity):
                return option
        return None

    def _managed_product_slots(self, slots):
        safe_slots = [slot for slot in slots if slot.safe]
        max_slots = self._max_supply_slots()
        if max_slots:
            safe_slots = safe_slots[:max_slots]
        return safe_slots

    def _product_editor_entry_slot(self, slots):
        candidates = [slot for slot in slots if slot.target is not None]
        if not candidates:
            return None
        return sorted(candidates, key=lambda slot: self._center_x(slot.target))[-1]

    def _product_switch_plan(self, slots, options, trend_category, target_count=None):
        current_options = self._current_product_options(slots, options)
        if not current_options:
            return []
        protected_current_options = self._protected_current_product_options(slots, current_options)

        target_count = max(len(current_options), int(target_count or 0))
        ranked_options = self._rank_product_options(options, trend_category)
        desired_options = self._desired_product_options_from_ranked(ranked_options, target_count)
        if not desired_options:
            return []

        removable = sorted(
            [
                option
                for option in current_options
                if not self._option_matches_any(option, desired_options)
                and option.price_value is not None
                and option.target is not None
            ],
            key=lambda option: int(option.price_value),
        )
        replacements = self._replacement_candidates(
            ranked_options,
            current_options,
            conflict_options=protected_current_options,
        )

        switches = []
        used_current = set()
        selected_count = len(current_options)
        for option in replacements:
            if not self._replacement_option_is_stable(option, options, current_options):
                self.actions.append(f"product_replacement_unstable:{option.identity}")
                continue
            replacement_price = int(option.price_value)
            current = next(
                (
                    candidate
                    for candidate in removable
                    if self._identity_key(candidate.identity) not in used_current
                    and candidate.price_value is not None
                    and replacement_price > int(candidate.price_value)
                ),
                None,
            )
            if current is None and selected_count < target_count:
                switches.append((None, option))
                selected_count += 1
                continue
            if current is None:
                continue
            used_current.add(self._identity_key(current.identity))
            switches.append((current, option))
        return switches

    def _replacement_option_is_stable(self, option, options, current_options):
        key = self._identity_key(option.identity)
        if not key:
            return False
        if len(key) < 3:
            return False
        if option.price_value is not None:
            same_price_current = [
                current
                for current in current_options
                if current.price_value is not None
                and int(current.price_value) == int(option.price_value)
                and not self._same_product_option(option, current)
            ]
            if same_price_current and len(key) < 5:
                return False
        return True

    def _current_product_options(self, slots, options):
        current = []
        seen = set()
        current_prices = [
            int(slot.options[0].price_value)
            for slot in slots
            if getattr(slot, "options", None) and slot.options[0].price_value is not None
        ]
        lowest_current_price = min(current_prices, default=None)
        for slot in slots:
            identity = slot.current_food_identity or slot.identity
            option = self._matching_current_option(options, slot, lowest_current_price)
            if option is None:
                slot_option = self._matching_option(getattr(slot, "options", []), identity)
                if slot_option is not None:
                    option = CoffeeFoodOption(
                        identity=slot_option.identity,
                        price_value=slot_option.price_value,
                        category=slot_option.category,
                        trend_match=slot_option.trend_match,
                        visible_order=slot_option.visible_order,
                        target=None,
                    )
            if option is None:
                self.actions.append(f"current_product_not_found:{identity}")
                continue
            key = self._current_product_key(option)
            if key in seen:
                continue
            seen.add(key)
            current.append(option)
        return current

    def _matching_current_option(self, options, slot, lowest_current_price=None):
        identity = slot.current_food_identity or slot.identity
        option = self._matching_option(options, identity)
        if option is not None:
            return option

        slot_option = self._matching_option(getattr(slot, "options", []), identity)
        price_value = getattr(slot_option, "price_value", None)
        if price_value is None:
            return None
        if lowest_current_price is not None and int(price_value) != int(lowest_current_price):
            return None

        same_price = [
            option
            for option in options
            if option.price_value is not None and int(option.price_value) == int(price_value)
        ]
        if len(same_price) == 1:
            return same_price[0]
        return None

    def _desired_product_options_from_ranked(self, ranked_options, count):
        desired = []
        seen = set()
        for option in ranked_options:
            key = self._identity_key(option.identity)
            if key in seen:
                continue
            seen.add(key)
            desired.append(option)
            if len(desired) >= count:
                break
        return desired

    def _replacement_candidates(self, ranked_options, current_options, conflict_options=None):
        candidates = []
        conflict_options = list(conflict_options or current_options or [])
        for option in ranked_options:
            key = self._identity_key(option.identity)
            if not key:
                continue
            if self._option_matches_any(option, current_options):
                continue
            if self._option_matches_any(option, candidates):
                continue
            if option.price_value is None or option.target is None:
                continue
            if self._same_price_current_conflict(option, conflict_options):
                self.actions.append(f"product_replacement_same_price_conflict:{option.identity}")
                continue
            candidates.append(option)
        return candidates

    def _fill_product_candidates(
        self, options, current_options, trend_category, conflict_options=None
    ):
        candidates = []
        conflict_options = list(conflict_options or current_options or [])
        for option in self._rank_product_options(options, trend_category):
            if self._option_matches_any(option, current_options) or self._option_matches_any(
                option, candidates
            ):
                continue
            if self._same_price_current_conflict(option, conflict_options):
                self.actions.append(f"product_fill_same_price_conflict:{option.identity}")
                continue
            if not self._replacement_option_is_stable(option, options, current_options):
                self.actions.append(f"product_replacement_unstable:{option.identity}")
                continue
            candidates.append(option)
        ranked = [f"{option.identity}:{option.price_value}" for option in candidates[:5]]
        self.actions.append(f"product_fill_candidates:{'|'.join(ranked)}")
        return candidates

    def _same_price_current_conflict(self, option, current_options):
        if option.price_value is None:
            return False
        option_key = self._identity_key(option.identity)
        if not option_key:
            return False
        for current in current_options or []:
            if current.price_value is None or int(current.price_value) != int(option.price_value):
                continue
            return True
        return False

    def _protected_current_product_options(self, slots, current_options):
        protected = []
        seen = set()

        def add(option):
            if option is None or option.price_value is None:
                return
            key = (self._identity_key(option.identity), int(option.price_value))
            if not key[0] or key in seen:
                return
            seen.add(key)
            protected.append(option)

        for option in current_options or []:
            add(option)

        for slot in slots or []:
            identity = slot.current_food_identity or slot.identity
            slot_option = self._matching_option(getattr(slot, "options", []), identity)
            if slot_option is None and getattr(slot, "options", None):
                slot_option = slot.options[0]
            if slot_option is None:
                continue
            add(
                CoffeeFoodOption(
                    identity=identity or slot_option.identity,
                    price_value=slot_option.price_value,
                    category=slot_option.category,
                    trend_match=slot_option.trend_match,
                    visible_order=slot_option.visible_order,
                    target=getattr(slot_option, "target", None),
                )
            )
        return protected

    def _select_product_candidates_single_pass(self, candidates, count):
        return self._click_product_candidates_single_pass(
            candidates,
            count,
            "select_product",
            allow_price_fallback=False,
            record_selected=True,
        )

    def _visible_product_candidates_single_pass(self, candidates, count, allow_price_fallback=True):
        found = []
        pending = list(candidates or [])
        target_count = max(0, int(count or 0))
        if target_count <= 0 or not pending:
            return found

        scrolls = self._product_scrolls()
        for page_index in range(scrolls + 1):
            page_options = self._dedupe_food_options(self.collect_product_options())
            for candidate in list(pending):
                option = self._matching_visible_product_option(
                    page_options,
                    candidate,
                    allow_price_fallback=allow_price_fallback,
                )
                if option is None or option.target is None:
                    continue
                found.append(candidate)
                pending.remove(candidate)
                if len(found) >= target_count:
                    return found
            if page_index >= scrolls:
                break
            self.scroll_product_options(page_options, steps=1)
        return found

    def _click_product_candidates_single_pass(
        self,
        candidates,
        count,
        action,
        allow_price_fallback=True,
        record_selected=False,
    ):
        selected = []
        pending = list(candidates or [])
        target_count = max(0, int(count or 0))
        if target_count <= 0 or not pending:
            return selected

        scrolls = self._product_scrolls()
        for page_index in range(scrolls + 1):
            page_options = self._dedupe_food_options(self.collect_product_options())
            for candidate in list(pending):
                option = self._matching_visible_product_option(
                    page_options,
                    candidate,
                    allow_price_fallback=allow_price_fallback,
                )
                if option is None or option.target is None:
                    continue
                self._click(option.target, f"{action}:{option.identity}")
                selected.append(candidate)
                if record_selected:
                    self.selected_options.append(candidate)
                pending.remove(candidate)
                if len(selected) >= target_count:
                    return selected
            if page_index >= scrolls:
                break
            self.scroll_product_options(page_options, steps=1)

        for candidate in pending[: max(0, target_count - len(selected))]:
            self.actions.append(f"{action}_skipped_not_visible:{candidate.identity}")
        return selected

    def _verify_product_slot_count(self, target_count):
        self._sleep(1.0)
        if not self.wait_for_coffee_shop_panel(timeout=3):
            self.actions.append("product_fill_panel_not_restored")
            return False
        state = self.collect_shop_state()
        count = len(self._managed_product_slots(state.slots))
        self.actions.append(f"product_slot_count_after:{count}")
        return count >= int(target_count or 0)

    def find_empty_product_slot(self):
        frame = self._fresh_frame()
        shape = getattr(frame, "shape", None)
        if frame is None or shape is None or len(shape) < 2:
            return None
        try:
            import cv2
            import numpy as np
        except Exception:
            return None

        height, width = shape[:2]
        left, top, right, bottom = self._region_pixel_bounds(self.COFFEE_PRODUCT_REGION)
        left = max(0, min(width - 1, left))
        right = max(left + 1, min(width, right))
        top = max(0, min(height - 1, top))
        bottom = max(top + 1, min(height, bottom))
        crop = frame[top:bottom, left:right]
        if getattr(crop, "size", 0) == 0:
            return None

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        red_mask = cv2.bitwise_or(
            cv2.inRange(hsv, np.array([0, 80, 80]), np.array([12, 255, 255])),
            cv2.inRange(hsv, np.array([170, 80, 80]), np.array([180, 255, 255])),
        )
        count, _, stats, centroids = cv2.connectedComponentsWithStats(red_mask, 8)
        candidates = []
        crop_height = bottom - top
        for index in range(1, count):
            x, y, box_width, box_height, area = stats[index]
            if not (100 <= area <= 2500 and 10 <= box_width <= 90 and 10 <= box_height <= 90):
                continue
            center_x = float(centroids[index][0]) + left
            center_y = float(centroids[index][1]) + top
            if center_y > top + crop_height * 0.42:
                continue
            slot = self._empty_slot_box_from_red_marker(frame, center_x, center_y)
            if slot is not None:
                candidates.append(slot)
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (-item.confidence, item.x))[0]

    def _empty_slot_box_from_red_marker(self, frame, marker_x, marker_y):
        import cv2
        import numpy as np

        height, width = frame.shape[:2]
        left = max(0, int(marker_x - width * 0.11))
        right = min(width, int(marker_x + width * 0.08))
        top = max(0, int(marker_y - height * 0.06))
        bottom = min(height, int(marker_y + height * 0.24))
        if right <= left or bottom <= top:
            return None
        crop = frame[top:bottom, left:right]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([0, 0, 145]), np.array([180, 70, 255]))
        count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        best = None
        window_area = max(1, (right - left) * (bottom - top))
        for index in range(1, count):
            x, y, box_width, box_height, area = stats[index]
            ratio = float(area) / window_area
            if area < 1000 or ratio < 0.80:
                continue
            if best is None or area > best[0]:
                best = (area, x, y, box_width, box_height, centroids[index], ratio)
        if best is None:
            return None
        area, x, y, box_width, box_height, centroid, ratio = best
        center_x = int(round(float(centroid[0]) + left))
        center_y = int(round(float(centroid[1]) + top))
        box_size = max(24, min(80, int(min(box_width, box_height) * 0.25)))
        return CoffeeDetectedBox(
            "empty_product_slot",
            center_x - box_size // 2,
            center_y - box_size // 2,
            box_size,
            box_size,
            confidence=ratio,
        )

    @staticmethod
    def _matches_trend(option, trend_category):
        if option.trend_match:
            return True
        return bool(trend_category and option.category and option.category == trend_category)

    def _rank_product_options(self, options, trend_category):
        priced = [
            option
            for option in options
            if option.price_value is not None and option.target is not None
        ]
        return sorted(
            priced,
            key=lambda option: (
                -int(option.price_value),
                not self._matches_trend(option, trend_category),
                int(option.visible_order),
            ),
        )

    def _record_product_scan(self, slots, options, trend_category, target_count=None):
        current = [
            f"{slot.current_food_identity or slot.identity}:{slot.options[0].price_value}"
            for slot in slots
            if getattr(slot, "options", None) and slot.options[0].price_value is not None
        ]
        best_count = max(1, int(target_count or 0), len(slots))
        ranked = [
            f"{option.identity}:{option.price_value}"
            for option in self._rank_product_options(options, trend_category)[:best_count]
        ]
        self.actions.append(f"product_current_options:{'|'.join(current)}")
        self.actions.append(f"product_best_candidates:{'|'.join(ranked)}")

    def _target_product_count(self, slots):
        configured = self._config_get("coffee_product_target_slots", 0)
        try:
            configured = max(0, int(configured or 0))
        except (TypeError, ValueError):
            configured = 0
        return configured or len(slots)

    def _matching_visible_product_option(self, options, candidate, allow_price_fallback=True):
        option = self._matching_option(options, candidate.identity)
        if option is not None and option.target is not None:
            return option

        if not allow_price_fallback:
            return None

        same_price = [
            option
            for option in options
            if option.target is not None
            and option.price_value is not None
            and candidate.price_value is not None
            and int(option.price_value) == int(candidate.price_value)
            and (
                not candidate.category
                or not option.category
                or option.category == candidate.category
            )
        ]
        if len(same_price) == 1:
            return same_price[0]
        return None

    def _option_matches_any(self, option, candidates):
        return any(self._same_product_option(option, candidate) for candidate in candidates or [])

    @classmethod
    def _same_product_option(cls, left, right):
        if cls._same_identity(getattr(left, "identity", ""), getattr(right, "identity", "")):
            return True
        left_price = getattr(left, "price_value", None)
        right_price = getattr(right, "price_value", None)
        if left_price is None or right_price is None or int(left_price) != int(right_price):
            return False
        left_key = cls._identity_key(getattr(left, "identity", ""))
        right_key = cls._identity_key(getattr(right, "identity", ""))
        if min(len(left_key), len(right_key)) < 4:
            return False
        return difflib.SequenceMatcher(None, left_key, right_key).ratio() >= 0.68

    def _dedupe_food_options(self, options):
        deduped = []
        seen = set()
        for option in options:
            key = (self._identity_key(option.identity), option.price_value, option.category)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(option)
        return deduped

    def _current_product_key(self, option):
        return (self._identity_key(option.identity), option.price_value)

    def _product_page_signature(self, options):
        return tuple(option.price_value for option in options if option.price_value is not None)

    @staticmethod
    def _page_signature_overlap(current, previous):
        if not current or not previous:
            return 0.0
        current_counts = {}
        for value in current:
            current_counts[value] = current_counts.get(value, 0) + 1
        previous_counts = {}
        for value in previous:
            previous_counts[value] = previous_counts.get(value, 0) + 1
        overlap = sum(
            min(count, previous_counts.get(value, 0)) for value, count in current_counts.items()
        )
        return overlap / max(1, min(len(current), len(previous)))

    @classmethod
    def _same_identity(cls, left, right):
        left_key = cls._identity_key(left)
        right_key = cls._identity_key(right)
        if left_key == right_key:
            return True
        if min(len(left_key), len(right_key)) < 4:
            return False
        return (
            left_key in right_key
            or right_key in left_key
            or difflib.SequenceMatcher(None, left_key, right_key).ratio() >= 0.72
        )

    @staticmethod
    def _identity_key(identity):
        text = str(identity or "").strip().lower()
        text = text.translate(
            str.maketrans(
                {
                    "鮮": "鲜",
                    "魚": "鱼",
                    "！": "",
                    "!": "",
                    "：": "",
                    ":": "",
                    "；": "",
                    ";": "",
                    "，": "",
                    ",": "",
                    "。": "",
                    ".": "",
                    "、": "",
                    " ": "",
                }
            )
        )
        return re.sub(r"[^\w一-鿿]+", "", text).strip()

    def scroll_product_options(self, options=None, steps=None):
        self._scroll_product_options(
            options=options,
            steps=steps,
            wheel_count=self.COFFEE_PRODUCT_SCROLL_WHEEL_COUNT,
            action="scroll_product_options",
        )

    def reset_product_options_scroll(self, options=None, steps=None):
        self._scroll_product_options(
            options=options,
            steps=steps,
            wheel_count=-self.COFFEE_PRODUCT_SCROLL_WHEEL_COUNT,
            action="reset_product_options_scroll",
        )

    def _scroll_product_options(
        self, options=None, steps=None, wheel_count=None, action="scroll_product_options"
    ):
        scrolls = self._product_scrolls() if steps is None else max(0, int(steps or 0))
        if scrolls <= 0:
            return
        count = self.COFFEE_PRODUCT_SCROLL_WHEEL_COUNT if wheel_count is None else int(wheel_count)
        for _ in range(scrolls):
            self.actions.append(action)
            if self._dry_run():
                continue
            try:
                x, y = self._product_list_scroll_point(options)
                self._task_scroll(x, y, count)
                self._sleep(0.6)
            except TaskDisabledException:
                raise
            except Exception as exc:
                self.actions.append(f"scroll_failed:{exc!r}")
                break

    def _product_list_scroll_point(self, options=None):
        boxes = self._safe_product_option_boxes(options)
        if boxes:
            popup_left, popup_top, popup_right, popup_bottom = self._region_pixel_bounds(
                self.COFFEE_PRODUCT_POPUP_REGION
            )
            popup_width = max(1, popup_right - popup_left)
            popup_height = max(1, popup_bottom - popup_top)
            left = min(int(getattr(box, "x", 0) or 0) for box in boxes)
            right = max(
                int(getattr(box, "x", 0) or 0) + int(getattr(box, "width", 0) or 0) for box in boxes
            )
            top = min(int(getattr(box, "y", 0) or 0) for box in boxes)
            bottom = max(
                int(getattr(box, "y", 0) or 0) + int(getattr(box, "height", 0) or 0)
                for box in boxes
            )

            safe_left = popup_left + popup_width * self.COFFEE_PRODUCT_SCROLL_EDGE_MARGIN
            safe_right = popup_right - popup_width * self.COFFEE_PRODUCT_SCROLL_EDGE_MARGIN
            safe_top = popup_top + popup_height * self.COFFEE_PRODUCT_SCROLL_EDGE_MARGIN
            safe_bottom = popup_bottom - popup_height * self.COFFEE_PRODUCT_SCROLL_EDGE_MARGIN
            x = int(self._clamp((left + right) / 2, safe_left, safe_right))
            y = int(self._clamp((top + bottom) / 2, safe_top, safe_bottom))
            return x, y

        return self._fallback_product_list_scroll_point()

    def _safe_product_option_boxes(self, options=None):
        boxes = []
        for option in list(options or []):
            box = getattr(option, "target", None)
            width = int(getattr(box, "width", 0) or 0)
            height = int(getattr(box, "height", 0) or 0)
            if width <= 0 or height <= 0:
                continue
            if self._box_center_in_region(
                box, self.COFFEE_PRODUCT_POPUP_REGION, self.COFFEE_PRODUCT_SCROLL_EDGE_MARGIN
            ):
                boxes.append(box)
        return boxes

    def _box_center_in_region(self, box, region, margin=0.0):
        left, top, right, bottom = self._region_pixel_bounds(region)
        width = max(1, right - left)
        height = max(1, bottom - top)
        safe_left = left + width * margin
        safe_right = right - width * margin
        safe_top = top + height * margin
        safe_bottom = bottom - height * margin
        center_x = self._center_x(box)
        center_y = self._center_y(box)
        return safe_left <= center_x <= safe_right and safe_top <= center_y <= safe_bottom

    def _region_pixel_bounds(self, region):
        left, top = self._ui_point(region[0], region[1])
        right, bottom = self._ui_point(region[2], region[3])
        return min(left, right), min(top, bottom), max(left, right), max(top, bottom)

    def _fallback_product_list_scroll_point(self):
        return self._ui_point(*self.COFFEE_PRODUCT_SCROLL_FALLBACK_POINT)

    @staticmethod
    def _clamp(value, lower, upper):
        if lower > upper:
            return value
        return max(lower, min(upper, value))

    def close_popup(self):
        self._send_key("esc", "close_popup")

    def wait_for_tycoon_panel(self):
        return self.wait_for(self.is_tycoon_panel, timeout=8)

    def wait_for_tycoon_map_settle(self):
        self.actions.append("wait_city_tycoon_transition")
        if not self._dry_run():
            self._sleep(1.5)

    def wait_for_coffee_shop_panel(self, timeout=5):
        return self.wait_for(self.is_coffee_shop_panel, timeout=timeout)

    def wait_for_product_popup(self):
        return self.wait_for(
            lambda: self.find_text_box("商品列表", self.COFFEE_PRODUCT_POPUP_REGION), timeout=3
        )

    def wait_for_supply_popup(self):
        return self.wait_for(
            lambda: self.find_text_box("原料库存", self.COFFEE_SUPPLY_POPUP_REGION), timeout=3
        )

    def is_coffee_shop_panel(self):
        texts = [self.box_text(box) for box in self.ocr_region(self.COFFEE_PANEL_REGION)]
        return self._is_coffee_shop_texts(texts)

    def _is_coffee_shop_texts(self, texts):
        if self._has_income_report_text(texts):
            return False
        has_shop = any("一咖舍" in text for text in texts)
        has_controls = any(
            "补货" in text
            or "提取收益" in text
            or text == "商品"
            or "累计营业时间" in text
            or "累计营收" in text
            for text in texts
        )
        return has_shop and has_controls

    def is_income_report_popup(self):
        texts = [self.box_text(box) for box in self.ocr_region(self.COFFEE_PANEL_REGION)]
        return self._has_income_report_text(texts)

    def complete_pending_supply_delivery_if_present(self):
        if not self.pending_supply_delivery_present():
            return False
        verify_error = self.finish_home_delivery_flow(
            select_action="select_pending_home_delivery",
            confirm_action="confirm_pending_home_delivery",
        )
        if verify_error:
            self.pending_supply_error = verify_error
            self.close_popup()
            return True
        self.pending_supply_completed = True
        self.close_popup()
        self.wait_for_coffee_shop_panel(timeout=3)
        return True

    def pending_supply_delivery_present(self):
        texts = [self.box_text(box) for box in self.ocr_region(self.COFFEE_CONFIRM_REGION)]
        if not any(
            "送货上门" in text or "确认花费" in text or "库存提示" in text for text in texts
        ):
            return False
        return any("送货上门" in text or "确认" in text for text in texts)

    def complete_coffee_challenge_if_present(self):
        if self.is_coffee_challenge_active():
            self.actions.append("wait_coffee_challenge_result")
            self.wait_for(self.is_coffee_challenge_result, timeout=140)
        return self.claim_or_exit_coffee_challenge_result_if_present()

    def claim_or_exit_coffee_challenge_result_if_present(self):
        if not self.is_coffee_challenge_result():
            return False
        claim_button = self.find_button_text_box("领取", self.COFFEE_PANEL_REGION)
        if claim_button is not None:
            self._click(claim_button, "claim_coffee_challenge_reward")
            self.wait_for(lambda: not self.is_coffee_challenge_result(), timeout=5)
            return True
        exit_button = self.find_button_text_box("退出", self.COFFEE_PANEL_REGION)
        if exit_button is None:
            self.actions.append("coffee_challenge_result_without_exit")
            return False
        self._click(exit_button, "exit_coffee_challenge_result")
        self.wait_for(lambda: not self.is_coffee_challenge_result(), timeout=3)
        return True

    def is_coffee_challenge_result(self):
        texts = [self.box_text(box) for box in self.ocr_region(self.COFFEE_PANEL_REGION)]
        return any("挑战成功" in text or "挑战失败" in text for text in texts)

    def is_coffee_challenge_active(self):
        texts = [self.box_text(box) for box in self.ocr_region(self.COFFEE_PANEL_REGION)]
        if any("挑战成功" in text or "挑战失败" in text for text in texts):
            return False
        has_revenue_goal = any("营业额" in text for text in texts)
        has_timed_goal = any(
            ("分钟内" in text) or (("分" in text) and ("秒" in text)) for text in texts
        )
        return has_revenue_goal and has_timed_goal and not self.is_coffee_shop_panel()

    @staticmethod
    def _has_income_report_text(texts):
        return any(
            "营收报告" in text or "收入明细" in text or "店内总营收额" in text for text in texts
        )

    def is_tycoon_panel(self):
        if self.is_coffee_shop_panel():
            return False
        texts = [self.box_text(box) for box in self.ocr_region(self.COFFEE_PANEL_REGION)]
        return self._is_tycoon_texts(texts)

    def _is_tycoon_texts(self, texts):
        normalized_texts = ["".join(str(text).upper().split()) for text in texts]
        return any(marker in text for text in texts for marker in self.TYCOON_TEXT_MARKERS) or any(
            marker in text for text in normalized_texts for marker in self.TYCOON_ASCII_MARKERS
        )

    def panel_probe_details(self):
        texts = [self.box_text(box) for box in self.ocr_region(self.COFFEE_PANEL_REGION)]
        non_empty_texts = [text for text in texts if text][:40]
        return {
            "texts": non_empty_texts,
            "tycoon_marker_detected": self._is_tycoon_texts(non_empty_texts),
            "coffee_shop_marker_detected": self._is_coffee_shop_texts(non_empty_texts),
            "income_report_detected": self._has_income_report_text(non_empty_texts),
        }

    def wait_for_text_box(self, text, region, timeout=4):
        return self.wait_for(lambda: self.find_text_box(text, region), timeout=timeout)

    def wait_for_button_text_box(self, text, region, timeout=4):
        return self.wait_for(lambda: self.find_button_text_box(text, region), timeout=timeout)

    def wait_for_supply_buy_button(self, timeout=5):
        return self.wait_for(self.find_supply_buy_button, timeout=timeout)

    def find_supply_buy_button(self):
        target = self.find_button_text_box("补货", self.COFFEE_SUPPLY_POPUP_REGION)
        if target is not None:
            return target
        return self.find_text_box("补货", self.COFFEE_SUPPLY_POPUP_REGION)

    def wait_for(self, predicate, timeout=4):
        timer = self.task.clock.timer(timeout=timeout)
        while not timer.expired():
            value = predicate()
            if value:
                return value
            self._sleep(0.2)
        return None

    def find_text_box(self, needle, region):
        for box in self.ocr_region(region):
            if needle in self.box_text(box):
                return box
        return None

    def find_button_text_box(self, needle, region):
        candidates = []
        for box in self.ocr_region(region):
            text = self.box_text(box)
            if needle not in text:
                continue
            normalized = text.strip()
            exact = normalized == needle
            short = len(normalized) <= len(needle) + 2
            candidates.append((not exact, not short, len(normalized), -self._center_y(box), box))
        if not candidates:
            return None
        return sorted(candidates)[0][-1]

    def ocr_region(self, region):
        try:
            result = self._task_ocr(*region, frame=self._fresh_frame())
        except TaskDisabledException:
            raise
        except Exception:
            return []
        if result is None or isinstance(result, (str, bytes)):
            return []
        try:
            return list(result)
        except TypeError:
            return []

    def _fresh_frame(self):
        if not self._dry_run():
            try:
                getter = getattr(self.task, "next_frame", None)
                if callable(getter):
                    frame = getter()
                    if frame is not None:
                        return frame
            except TaskDisabledException:
                raise
            except Exception:
                pass
        return getattr(self.task, "frame", None)

    @staticmethod
    def box_text(box):
        text = getattr(box, "text", None)
        return str(text if text else getattr(box, "name", "")).strip()

    @staticmethod
    def _center_x(box):
        return getattr(box, "x", 0) + getattr(box, "width", 0) / 2

    @staticmethod
    def _center_y(box):
        return getattr(box, "y", 0) + getattr(box, "height", 0) / 2

    # ---- task call helpers (replaces TaskUIAdapter) ----

    def _config_get(self, key, default=None):
        config = getattr(self.task, "config", None) or {}
        getter = getattr(config, "get", None)
        if callable(getter):
            return getter(key, default)
        try:
            return config[key]
        except (KeyError, TypeError):
            return default

    def _task_scroll(self, x, y, count):
        scroll = getattr(self.task, "scroll", None)
        if callable(scroll):
            return scroll(x, y, count)
        executor = getattr(self.task, "executor", None)
        interaction = getattr(executor, "interaction", None)
        scroll = getattr(interaction, "scroll", None)
        if callable(scroll):
            return scroll(x, y, count)
        raise AttributeError("task does not provide scroll()")

    def _task_ocr(self, *region, frame=None):
        ocr_ui = getattr(self.task, "ocr_ui", None)
        if callable(ocr_ui):
            return ocr_ui(*region, frame=frame)
        ocr = getattr(self.task, "ocr", None)
        if callable(ocr):
            return ocr(*region, frame=frame)
        return None

    def _operate_click(self, click):
        operate = getattr(self.task, "operate", None)
        if callable(operate):
            try:
                operate(click, block=True)
                return
            except TaskDisabledException:
                raise
            except Exception:
                pass
        click()

    def _click(self, target, action):
        self.actions.append(action)
        if self._dry_run() or target is None:
            return
        self.task.click(target)
        self._sleep(1)

    def _click_detected_box(self, target, action):
        if all(hasattr(target, attr) for attr in ("x", "y", "width", "height")):
            self._click_screen_point(
                self._center_x(target), self._center_y(target), action, move=True
            )
            return
        self._click(target, action)

    def _click_supply_button(self, target, action):
        self.actions.append(action)
        if self._dry_run() or target is None:
            return
        self._operate_click(
            lambda: self.task.click(
                target,
                move=True,
                down_time=self.COFFEE_SUPPLY_CLICK_DOWN_TIME,
                after_sleep=self.COFFEE_SUPPLY_CLICK_SETTLE_SECONDS,
            )
        )

    def _click_ui(self, x, y, action, move=False):
        self.actions.append(action)
        if self._dry_run():
            return
        px, py = self._ui_point(x, y)
        self._operate_click(
            lambda: self.task.click(int(px), int(py), after_sleep=1, move=move, down_time=0.01)
        )

    def _click_screen_point(self, x, y, action, move=False):
        self.actions.append(action)
        if self._dry_run():
            return
        self._operate_click(
            lambda: self.task.click(
                int(x),
                int(y),
                after_sleep=1,
                move=move,
                down_time=0.01,
            )
        )

    def _send_key(self, key, action):
        self.actions.append(action)
        if self._dry_run():
            return
        try:
            self.task.send_key(key, after_sleep=0)
            self._settle_after_key()
        except TaskDisabledException:
            raise
        except Exception:
            self._send_foreground_key_raw(key)
            self._settle_after_key()

    def _send_foreground_key(self, key, action):
        self.actions.append(action)
        if self._dry_run():
            return
        if self._send_foreground_key_raw(key):
            self._settle_after_key()
            return
        try:
            self.task.send_key(key, after_sleep=0)
            self._settle_after_key()
        except TaskDisabledException:
            raise
        except Exception:
            return

    def _send_foreground_key_raw(self, key):
        sender = getattr(self.task, "_send_foreground_key", None)
        if not callable(sender):
            return False
        try:
            sender(key, after_sleep=0)
            return True
        except TaskDisabledException:
            raise
        except Exception:
            return False

    def _settle_after_key(self):
        seconds = self._key_settle_seconds()
        if seconds > 0:
            time.sleep(seconds)

    def _key_settle_seconds(self):
        try:
            return max(
                0.0,
                float(
                    self._config_get("coffee_key_settle_seconds", self.COFFEE_KEY_SETTLE_SECONDS)
                    or 0.0
                ),
            )
        except (TypeError, ValueError):
            return self.COFFEE_KEY_SETTLE_SECONDS

    def _ui_point(self, x, y):
        return int(self._screen_width() * x), int(self._screen_height() * y)

    def _screen_width(self):
        return int(getattr(self.task, "width", 0) or 0)

    def _screen_height(self, default=1600):
        height = int(getattr(self.task, "height", 0) or 0)
        if height:
            return height
        frame = getattr(self.task, "frame", None)
        shape = getattr(frame, "shape", None)
        if shape is not None and len(shape) >= 2:
            return int(shape[0])
        return default

    def _sleep(self, seconds):
        sleep = getattr(self.task, "sleep", None)
        if callable(sleep):
            sleep(seconds)

    def _dry_run(self):
        return bool(self._config_get("coffee_dry_run", False))

    def _allow_pending_supply_completion(self):
        return bool(self._config_get("coffee_allow_pending_supply_completion", False))

    def _max_supply_slots(self):
        try:
            return max(0, int(self._config_get("coffee_max_supply_slots", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _product_scrolls(self):
        try:
            configured = self._config_get(
                "coffee_product_scrolls",
                self.COFFEE_PRODUCT_DEFAULT_SCAN_SCROLLS,
            )
            value = max(0, int(configured or 0))
            if value == 0:
                return 0
            return max(value, self.COFFEE_PRODUCT_DEFAULT_SCAN_SCROLLS)
        except (TypeError, ValueError):
            return self.COFFEE_PRODUCT_DEFAULT_SCAN_SCROLLS

    def _supply_duration(self):
        duration = self._config_get("coffee_supply_duration", "24小时") or "24小时"
        return normalize_duration(duration)

    @staticmethod
    def _supply_duration_attempts(duration):
        allowed = list(ALLOWED_DURATIONS)
        try:
            index = allowed.index(duration)
        except ValueError:
            return [duration]
        shorter = list(reversed(allowed[:index]))
        return [duration, *shorter]

    def _price_table_disabled(self):
        return (
            str(self._config_get("coffee_price_table", "auto") or "auto").strip().lower()
            == "disabled"
        )
