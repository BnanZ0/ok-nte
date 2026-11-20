"""养成指南页坐标（1920x1080 相对比例）。

弧盘页布局比等级/异能页低约 26px，行坐标按 tab 分开定义。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CultivationPositions:
    guide_sidebar_x: float = 0.056
    guide_goal_icon_y: float = 0.435
    goal_goto_cultivate: tuple[float, float] = (0.89, 0.23)
    # 详情页还需再点「养成指南」才到材料页。
    detail_guide_button: tuple[float, float] = (0.826, 0.048)
    overview_back_to_goal: tuple[float, float] = (0.89, 0.16)
    overview_tabs_y: float = 0.069
    tab_x: dict[str, float] | None = None

    material_button_x: float = 0.890
    ratio_row_y: dict[str, tuple[float, float, float]] | None = None
    material_row_y: dict[str, tuple[float, float, float]] | None = None
    # box 半高约 ±26px，小于行间距，不会串到相邻行。
    ratio_half_height: float = 0.024
    # 图标中心比数字行高约 23px，按卡片中心裁才不会切掉顶部。
    icon_row_offset: float = 0.021
    icon_half_height: float = 0.05
    # 数字区横向范围，再宽会读进按钮。
    ratio_box_x: tuple[float, float] = (0.34, 0.64)

    map_travel_button: tuple[float, float, float, float] = (0.80, 0.84, 0.92, 0.94)
    # 传送按钮固定中心，供上游裁剪失准时补点兜底。
    map_travel_button_center: tuple[float, float] = (0.870, 0.911)
    domain_entry_button: tuple[float, float] = (0.8008, 0.9042)
    domain_stamina_box: tuple[float, float, float, float] = (0.814, 0.029, 0.898, 0.083)
    weekly_remaining_box: tuple[float, float, float, float] = (0.409, 0.206, 0.668, 0.254)
    close_button: tuple[float, float] = (0.95, 0.07)
    # 追猎页目标「前往」位置，OCR 失败时兜底。
    hunter_first_row: tuple[float, float] = (0.867, 0.262)

    def __post_init__(self):
        if self.tab_x is None:
            object.__setattr__(
                self,
                "tab_x",
                {"等级": 0.515, "弧盘": 0.628, "异能": 0.740, "空幕": 0.850},
            )
        if self.ratio_row_y is None:
            object.__setattr__(
                self,
                "ratio_row_y",
                {
                    "等级": (0.579, 0.719, 0.860),
                    "弧盘": (0.602, 0.743, 0.883),
                    "异能": (0.579, 0.719, 0.860),
                },
            )
        if self.material_row_y is None:
            object.__setattr__(
                self,
                "material_row_y",
                {
                    "等级": (0.544, 0.685, 0.826),
                    "弧盘": (0.568, 0.708, 0.849),
                    "异能": (0.544, 0.685, 0.826),
                },
            )


POS = CultivationPositions()

# 异象追猎（世界 BOSS）目标名；出现任一个说明进的是追猎界面（非周本）。
HUNTER_BOSS_NAMES = (
    "音霸魔王",
    "无首铁驭",
    "塞润尼缇",
    "黑之书",
    "海囚",
    "囿巢鸟",
    "斑蝶",
)

# 刷取优先级：周本 > 等级 > 异能 > 弧盘；decide() 按序取第一条未达标行。
TARGET_ROWS = (
    # 周本材料在异能第2行，走异象巡礼。
    ("异能", 1, 60, "weekly"),
    # 等级第2行是世界BOSS材料，走异象追猎。
    ("等级", 1, 60, "hunter_map"),
    # 异能第1行是副本材料。
    ("异能", 0, 40, "domain"),
    # 弧盘第2行是弧盘副本（泡影罐头）；第1行是通用升级材料，非副本产物。
    ("弧盘", 1, 40, "domain"),
)
