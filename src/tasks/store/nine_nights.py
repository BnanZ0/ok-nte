from __future__ import annotations

from src.Labels import Labels
from src.tasks.store.contracts import StoreFlow, StorePurchaseResult


class NineNightsStoreFlow(StoreFlow):
    """九百九十九夜商店: F4 面板 -> 常驻活动 -> 九百九十九夜 -> 商店."""

    shop_id = "nine_nights"
    display_name = "九百九十九夜"

    PANEL_TIMEOUT = 10
    BUY_TIMEOUT = 5
    CONFIRM_TIMEOUT = 1
    REWARD_TIMEOUT = 5

    # 商品只出现在这个区域, 区域内找不到即视为售罄
    ITEM_LIST_BOX = (0.1750, 0.1287, 0.3286, 0.4583)
    BUY_BUTTON = (0.660, 0.740)
    # 只覆盖确认按钮, 避开左侧取消按钮
    CONFIRM_BOX = (0.649, 0.608, 0.707, 0.711)
    # 通用「获得物品」弹窗
    REWARD_BOX = (0.453, 0.248, 0.548, 0.424)

    ITEM_LABELS: tuple[str, ...] = (Labels.event_999nights_item_heterogeneous_unit,)

    def open(self) -> bool:
        for label, name in (
            (Labels.f4_permanent_events_button, "常驻活动"),
            (Labels.event_999nights, "九百九十九夜"),
            (Labels.event_999nights_store, "九百九十九夜商店"),
        ):
            if not self._click_entry(label, name):
                return False
        return bool(
            self.wait_panel(Labels.event_999nights_store_panel, time_out=self.PANEL_TIMEOUT)
        )

    def _click_entry(self, label, name) -> bool:
        entry = self.wait_until(
            lambda: self.find_one(label),
            time_out=self.PANEL_TIMEOUT,
            raise_if_not_found=False,
        )
        if not entry:
            self.log_error(f"无法找到{name}入口")
            return False
        self.operate_click(entry, after_sleep=1)
        return True

    def purchase(self) -> StorePurchaseResult | None:
        if not self.ITEM_LABELS:
            self.log_warning("未配置待购商品模板, 跳过购买")
            return None

        box = self.box_of_screen(*self.ITEM_LIST_BOX, name="event_999nights_store_items")
        result = StorePurchaseResult(self.shop_id)
        for label in self.ITEM_LABELS:
            item = self.find_one(label, box=box)
            if item is None:
                self.log_info(f"商品不在售卖区, 按售罄处理: {label}")
                result.sold_out.append(label)
                continue
            if self._buy_item(item):
                result.bought.append(label)
            else:
                result.failed.append(label)
        return result

    def _buy_item(self, item) -> bool:
        """选中商品并拉满数量购买, 以「获得物品」弹窗判成功."""
        self.operate_click(item, after_sleep=1)
        max_button = self.wait_panel(Labels.event_999nights_buy_max, time_out=self.BUY_TIMEOUT)
        if not max_button:
            self.log_error("无法打开商品详情页")
            return False
        self.operate_click(max_button, after_sleep=0.5)

        self.operate_click(*self.BUY_BUTTON, after_sleep=1)
        if self.wait_click_confirm(
            range=self.CONFIRM_BOX,
            time_out=self.CONFIRM_TIMEOUT,
            raise_if_not_found=False,
        ):
            self.log_info("已确认购买弹窗")

        reward_box = self.box_of_screen(*self.REWARD_BOX, name="reward_popup")
        if not self.wait_until(
            lambda: self.find_one(Labels.reward_popup, box=reward_box),
            time_out=self.REWARD_TIMEOUT,
            raise_if_not_found=False,
        ):
            self.log_error("购买后未出现获得物品弹窗")
            return False
        # 关掉弹窗留在商店页, 便于继续买其它商品
        self.send_key("esc", after_sleep=1)
        self.log_info("购买成功")
        return True
