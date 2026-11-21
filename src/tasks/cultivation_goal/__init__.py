"""消耗体力培养角色：按养成指南材料缺口自动刷体力。"""

from .version import VERSION

__version__ = VERSION

from .core import (
    Action,
    Decision,
    MaterialRow,
    PlanConfig,
    PlanState,
    Ratio,
    decide,
    effective_stamina,
    next_weekly_remaining,
    parse_ratios,
    parse_weekly_remaining,
)
from .positions import POS, TARGET_ROWS, CultivationPositions

__all__ = [
    "Action",
    "CultivationPositions",
    "Decision",
    "MaterialRow",
    "POS",
    "PlanConfig",
    "PlanState",
    "Ratio",
    "TARGET_ROWS",
    "decide",
    "effective_stamina",
    "next_weekly_remaining",
    "parse_ratios",
    "parse_weekly_remaining",
]
