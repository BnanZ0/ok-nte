from __future__ import annotations

from src.Labels import Labels
from src.tasks.store.contracts import StoreFlow, StorePurchaseResult
from src.tasks.store.layout import STORE_LAYOUT


class NineNightsStoreFlow(StoreFlow):
    """九百九十九夜商店: F4 面板 -> 常驻活动 -> 九百九十九夜 -> 商店."""

    shop_id = "nine_nights"
    display_name = "九百九十九夜"
    PANEL_TIMEOUT = 10
    BUY_TIMEOUT = 5
    CONFIRM_TIMEOUT = 5
    REWARD_TIMEOUT = 5
    # 需要购买的商品模板, 商品只在 item_list_box 区域内出现
    ITEM_LABELS: tuple[str, ...] = (Labels.event_999nights_item_heterogeneous_unit,)

    def open(self, task) -> bool:
        for label, name in (
            (Labels.f4_permanent_events_button, "常驻活动"),
            (Labels.event_999nights, "九百九十九夜"),
            (Labels.event_999nights_store, "九百九十九夜商店"),
        ):
            if not self._click_entry(task, label, name):
                return False
        return bool(
            task.wait_panel(Labels.event_999nights_store_panel, time_out=self.PANEL_TIMEOUT)
        )

    def _click_entry(self, task, label, name) -> bool:
        entry = task.wait_until(
            lambda: task.find_one(label),
            time_out=self.PANEL_TIMEOUT,
            raise_if_not_found=False,
        )
        if not entry:
            task.log_error(f"无法找到{name}入口")
            return False
        task.operate_click(entry, after_sleep=1)
        return True

    def purchase(self, task) -> StorePurchaseResult | None:
        if not self.ITEM_LABELS:
            task.log_warning("未配置待购商品模板, 跳过购买")
            return None

        box = task.box_of_screen(*STORE_LAYOUT.item_list_box, name="event_999nights_store_items")
        result = StorePurchaseResult(self.shop_id)
        for label in self.ITEM_LABELS:
            item = task.find_one(label, box=box)
            if item is None:
                task.log_info(f"商品不在售卖区, 按售罄处理: {label}")
                result.sold_out.append(label)
                continue
            if self._buy_item(task, item):
                result.bought.append(label)
            else:
                result.failed.append(label)
        return result

    def _buy_item(self, task, item) -> bool:
        """选中商品, 拉满数量并购买, 以「获得物品」弹窗作为成功判据."""
        task.operate_click(item, after_sleep=1)
        panel_box = task.box_of_screen(*STORE_LAYOUT.buy_panel_box, name="buy_panel")
        if not task.wait_until(
            lambda: task.find_one(Labels.event_999nights_buy_panel, box=panel_box),
            time_out=self.BUY_TIMEOUT,
            raise_if_not_found=False,
        ):
            task.log_error("无法打开商品详情页")
            return False

        max_box = task.box_of_screen(*STORE_LAYOUT.buy_max_box, name="buy_max")
        max_button = task.find_one(Labels.event_999nights_buy_max, box=max_box)
        if max_button is None:
            task.log_warning("未找到数量拉满按钮, 按当前数量购买")
        else:
            task.operate_click(max_button, after_sleep=0.5)

        button_box = task.box_of_screen(*STORE_LAYOUT.buy_button_box, name="buy_button")
        buy_button = task.find_one(Labels.event_999nights_buy_button, box=button_box)
        if buy_button is None:
            task.log_error("未找到购买按钮")
            return False
        task.operate_click(buy_button, after_sleep=1)

        confirm_panel_box = task.box_of_screen(
            *STORE_LAYOUT.buy_confirm_panel_box, name="buy_confirm_panel"
        )
        confirm_box = task.box_of_screen(*STORE_LAYOUT.buy_confirm_box, name="buy_confirm")
        confirm = task.wait_until(
            lambda: task.find_one(Labels.buy_confirm, box=confirm_box),
            time_out=self.CONFIRM_TIMEOUT,
            raise_if_not_found=False,
        )
        if confirm:
            task.operate_click(confirm, after_sleep=1)
        elif task.find_one(Labels.buy_confirm_panel, box=confirm_panel_box):
            task.log_error("确认框已弹出但找不到确认按钮")
            return False
        else:
            task.log_info("未出现购买确认框, 按直接购买处理")

        reward_box = task.box_of_screen(*STORE_LAYOUT.reward_popup_box, name="reward_popup")
        if not task.wait_until(
            lambda: task.find_one(Labels.reward_popup, box=reward_box),
            time_out=self.REWARD_TIMEOUT,
            raise_if_not_found=False,
        ):
            task.log_error("购买后未出现获得物品弹窗")
            return False
        # 关掉获得物品弹窗, 留在商店页, 后续商品还能继续买
        task.send_key("esc", after_sleep=1)
        task.log_info("购买成功")
        return True
