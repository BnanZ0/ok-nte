"""材料卡材质色判档：铜=绿 / 银=蓝 / 金=紫。

养成页格子数 1~3 不等且首格不一定是绿档，故按材质色而非列序判档。
阈值：warm_ratio 银白 0.000~0.023、其余 0.261~0.906；warm_sat 铜 75~119、金 139~187。
"""

import cv2
import numpy as np

TIER_ORDER = ("绿", "蓝", "紫")

# 橙金像素占比低于此值即银白底板，判蓝档
SILVER_MAX_WARM = 0.12
# 橙金像素饱和度高于此值即金，判紫档，否则铜判绿档
GOLD_MIN_SAT = 130

# 底色判定用的最低亮度, 低于此值算背景/卡外
_BODY_MIN_VAL = 70
_BODY_MIN_PIXELS = 100


def detect_tier(cell: np.ndarray) -> tuple[str, dict] | None:
    """按材质色判档, 返回 (档位, 证据); 判不出来返回 None。

    证据里带 warm_ratio / warm_sat, 便于日志复盘阈值是否还站得住。
    """
    if cell is None or cell.size == 0 or cell.shape[0] < 16 or cell.shape[1] < 16:
        return None
    hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.int16)
    sat = hsv[:, :, 1].astype(np.int16)
    val = hsv[:, :, 2].astype(np.int16)

    body = val > _BODY_MIN_VAL
    if int(body.sum()) < _BODY_MIN_PIXELS:
        return None

    body_h, body_s = hue[body], sat[body]
    warm = (body_h <= 40) & (body_s >= 50)
    warm_ratio = float(warm.mean())
    evidence = {"warm_ratio": round(warm_ratio, 3)}

    # 银白底板几乎没有橙金像素, 这一条最干净
    if warm_ratio < SILVER_MAX_WARM:
        return "蓝", evidence

    if int(warm.sum()) < 20:
        return None
    warm_sat = float(np.median(body_s[warm]))
    evidence["warm_sat"] = round(warm_sat, 1)
    return ("紫" if warm_sat >= GOLD_MIN_SAT else "绿"), evidence
