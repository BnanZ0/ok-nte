from __future__ import annotations

from src.Labels import Labels
from src.tasks.daily.store.contracts import StoreFlow


class NineNightsStoreFlow(StoreFlow):
    """九百九十九夜商店: F4 面板 -> 常驻活动 -> 九百九十九夜 -> 商店."""

    shop_id = "nine_nights"
    display_name = "九百九十九夜"

    # 商品只出现在这个区域, 区域内找不到即视为售罄
    ITEM_LIST_BOX = (0.1750, 0.1287, 0.3286, 0.4583)
    ITEM_LABELS: tuple[str, ...] = (Labels.event_999nights_item_heterogeneous_unit,)

    def open(self) -> bool:
        def action():
            self.openF4panel()
            for label, name in (
                (Labels.f4_permanent_events_button, "常驻活动"),
                (Labels.event_999nights, "九百九十九夜"),
                (Labels.event_999nights_store, "九百九十九夜商店"),
            ):
                if not self._click_entry(label, name):
                    return False
            return True

        return bool(self.retry_on_action(action, self.ensure_main))

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
