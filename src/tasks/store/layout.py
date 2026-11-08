from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StoreLayout:
    """商店页面共用的区域比例, 采集自 1920x1080 截图."""

    # 九百九十九夜商店左侧商品列表: X 336 Y 139 宽 295 高 356
    # 需要购买的商品只在这个区域内出现, 区域内找不到即视为售罄
    item_list_box: tuple[float, float, float, float] = (0.1750, 0.1287, 0.3286, 0.4583)

    # 购买弹窗: 顶部标题和底部按钮都是「购买」, 用区域把两者分开
    # 区域按导出后模板的实际 bbox 放宽得到, 见 assets/coco_annotations.json
    buy_panel_box: tuple[float, float, float, float] = (0.39, 0.16, 0.61, 0.36)
    buy_max_box: tuple[float, float, float, float] = (0.76, 0.57, 0.89, 0.72)
    buy_button_box: tuple[float, float, float, float] = (0.58, 0.67, 0.74, 0.81)
    # 「获得物品」弹窗会随上下文上下浮动, 搜索框放宽
    reward_popup_box: tuple[float, float, float, float] = (0.35, 0.20, 0.65, 0.55)

    # 购买数量较大时弹出的「提示」确认框
    buy_confirm_panel_box: tuple[float, float, float, float] = (0.40, 0.26, 0.60, 0.40)
    buy_confirm_box: tuple[float, float, float, float] = (0.50, 0.60, 0.72, 0.72)


STORE_LAYOUT = StoreLayout()
