"""Conservative visual fallback for the bonfire refresh button."""

from enum import Enum
from functools import lru_cache

import cv2
from ok import get_path_relative_to_exe


class RefreshIcon(Enum):
    UNKNOWN = "unknown"
    READY = "ready"
    COOLDOWN = "cooldown"


def normalize_refresh_icon(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)


@lru_cache(maxsize=1)
def refresh_templates():
    templates = {}
    for state in (RefreshIcon.READY, RefreshIcon.COOLDOWN):
        path = get_path_relative_to_exe("assets", "dsd_refresh", f"{state.value}.png")
        image = cv2.imread(path)
        if image is None:
            raise FileNotFoundError(f"Missing bonfire refresh template: {state.value}")
        templates[state] = normalize_refresh_icon(image)[6:-6, 6:-6]
    return templates


def classify_refresh_icon(image):
    if image is None or image.size == 0:
        return RefreshIcon.UNKNOWN
    normalized = normalize_refresh_icon(image)
    scores = sorted(
        (
            (float(cv2.matchTemplate(normalized, template, cv2.TM_CCOEFF_NORMED).max()), state)
            for state, template in refresh_templates().items()
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    # Require a positive match, not merely the disappearance of the ready icon.
    if scores[0][0] >= 0.90 and scores[0][0] - scores[1][0] >= 0.08:
        return scores[0][1]
    return RefreshIcon.UNKNOWN
