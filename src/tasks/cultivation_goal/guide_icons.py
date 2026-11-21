"""养成指南页「所需材料」图标识别。模板裁自同页（assets/game/guide_icon），与奖励页那套分开。"""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, cast

import cv2
import numpy as np
from ok import Logger

from src.tasks.cultivation_goal.asset_paths import resolve_asset_dir
from src.tasks.cultivation_goal.tier_color import TIER_ORDER, detect_tier
from src.utils.game_templates import GameTemplateSet

logger = Logger.get_logger(__name__)

_ASSET_DIR = resolve_asset_dir("guide_icon")
GUIDE_ICON = GameTemplateSet(_ASSET_DIR, reference_scale=1.0, reference_height=1080)

SCALES = (0.88, 0.94, 1.0, 1.06, 1.12)
MATCH_MIN_SCORE = 0.72

CELL_MIN = 36
CELL_MAX = 130
# 识别时同样外扩，否则卡片边框对不上会掉到阈值以下。
CELL_PAD = 8

# ability_/arc_ 模板当年连比例数字一起切了进来（如 0/5、86/42），数字随材料变化，
# 直接比会把分数压到阈值以下。比对前统一切掉底部数字带（模板高 92，数字在 71 行以下）。
DIGIT_BAND_PREFIXES = ("ability_", "arc_")
DIGIT_BAND_PX = 24
# 格子 bbox 一旦被条带边缘切掉，正确缩放的模板就放不进去。窗口至少留这么大。
MIN_WINDOW = 100


@dataclass(frozen=True)
class GuideMatch:
    key: str
    kind: str
    dungeon: str
    tier: str
    quality: str
    score: float
    center_x: int
    # 档位来源与冲突标记：两者不一致说明库标签或判色有误。
    tier_source: str = "template"
    tier_conflict: bool = False

    def as_dict(self) -> dict:
        return {"key": self.key, "dungeon": self.dungeon, "tier": self.tier,
                "score": round(self.score, 4), "x": self.center_x,
                "tier_source": self.tier_source}


@dataclass(frozen=True)
class MissingPlan:
    """一行材料格推断出的缺口档位。

    observed 是实际认出来的档位, expected 是按「缺低档必缺高档」补全后的档位,
    gaps 是补全出来却没认到的档位 —— 非空说明中间某格漏识别了。
    """

    observed: tuple[str, ...]
    expected: tuple[str, ...]
    gaps: tuple[str, ...]

    @property
    def consistent(self) -> bool:
        return not self.gaps

    def as_dict(self) -> dict:
        return {"observed": list(self.observed), "expected": list(self.expected),
                "gaps": list(self.gaps), "consistent": self.consistent}


def available_keys() -> tuple[str, ...]:
    return GUIDE_ICON.list_names()


def load_info(path: Path | None = None) -> dict[str, dict]:
    target = path or (_ASSET_DIR / "guide_info.json")
    try:
        with open(target, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError) as error:
        logger.warning(f"养成指南材料元数据读取失败: {type(error).__name__}: {Path(target).name}")
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def find_cells(image: np.ndarray, cell_min: float = CELL_MIN,
               cell_max: float = CELL_MAX) -> list[tuple[int, int, int, int]]:
    """按亮度切出材料卡片, 返回按 x 排序的 (x, y, w, h)。

    材料卡底板是铜/银/金三色, 亮度都高于深灰行底; 右侧「获取材料」按钮
    靠尺寸上限和宽高比排掉。阈值默认按 1080 高标定。
    """
    if image is None or image.size == 0:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray > 72).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cells = []
    for i in range(1, count):
        x, y, w, h, _ = stats[i]
        if not (cell_min <= w <= cell_max and cell_min <= h <= cell_max):
            continue
        if not 0.45 < w / h < 1.9:
            continue
        cells.append((int(x), int(y), int(w), int(h)))
    return sorted(cells, key=lambda c: c[0])


def _score(scene: np.ndarray, template: np.ndarray) -> float:
    best = 0.0
    sh, sw = scene.shape[:2]
    for scale in SCALES:
        w, h = int(template.shape[1] * scale), int(template.shape[0] * scale)
        if w < 8 or h < 8 or w > sw or h > sh:
            continue
        probe = cv2.resize(template, (w, h), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(scene, probe, cv2.TM_CCOEFF_NORMED)
        best = max(best, float(result.max()))
    return best


def identify_cell(cell: np.ndarray, height: int = 1080, *,
                  info: dict[str, dict] | None = None) -> GuideMatch | None:
    """认单张材料卡: 模板匹配定副本, 材质色定档位; 空格子返回 None。"""
    if cell is None or cell.size == 0 or cell.shape[0] < 20 or cell.shape[1] < 20:
        return None
    meta = info if info is not None else load_info()
    best: GuideMatch | None = None
    for key in GUIDE_ICON.list_names():
        prepared = GUIDE_ICON.get_template(key, height)
        if prepared is None:
            continue
        template = prepared[0] if isinstance(prepared, tuple) else prepared
        if key.startswith(DIGIT_BAND_PREFIXES):
            template = template[: max(16, template.shape[0] - DIGIT_BAND_PX)]
        score = _score(cell, template)
        if score < MATCH_MIN_SCORE:
            continue
        if best is not None and score <= best.score:
            continue
        entry = meta.get(key, {})
        best = GuideMatch(
            key=key,
            kind=entry.get("kind", ""),
            dungeon=entry.get("dungeon", key),
            tier=entry.get("tier", ""),
            quality=entry.get("quality", ""),
            score=score,
            center_x=cell.shape[1] // 2,
        )
    if best is None:
        return None
    # 周本/世界BOSS 是单格材料(每副本一种, 无三档), 图标色跟品质无关,
    # 套判色会乱标成蓝/紫/绿。这类模板 tier 为空, 直接按模板结果返回。
    if not best.tier:
        return best
    # 档位以材质色为准。同一副本三档形状几乎一致, 模板匹配最容易错的就是档位;
    # 材质色是一维单调量(实测 20/20), 拿它覆盖, 不一致时留痕便于回查。
    detected = detect_tier(cell)
    if detected is None:
        return best
    tier, evidence = detected
    if tier == best.tier:
        return cast(GuideMatch, replace(best, tier_source="color"))
    logger.debug(f"档位以材质色为准: 模板={best.tier} 材质={tier} {evidence}")
    return cast(GuideMatch, replace(
        best, key=f"{best.kind}_{best.dungeon}_{tier}", tier=tier, quality=tier,
        tier_source="color", tier_conflict=True,
    ))


def infer_missing(tiers: Iterable[str]) -> MissingPlan:
    """按「缺低档必缺高档, 缺高档不必缺低档」补全缺口档位。

    合法形态只有 {} / {紫} / {蓝,紫} / {绿,蓝,紫} 四种: 最低档既然缺,
    更高的档次一定也缺。出现别的组合只可能是中间某格没认出来, 用 gaps 暴露出来,
    让调用方决定重拍还是跳过, 而不是照着残缺结果去刷。
    """
    seen = {tier for tier in tiers if tier in TIER_ORDER}
    if not seen:
        return MissingPlan((), (), ())
    lowest = min(TIER_ORDER.index(tier) for tier in seen)
    expected = TIER_ORDER[lowest:]
    gaps = tuple(tier for tier in expected if tier not in seen)
    observed = tuple(tier for tier in TIER_ORDER if tier in seen)
    return MissingPlan(observed, expected, gaps)


def _window(x: int, y: int, cw: int, ch: int, width: int, height: int,
            min_window: int) -> tuple[int, int, int, int]:
    """把格子 bbox 扩成不小于 min_window 的窗口。

    bbox 被条带上下边切掉时光靠 CELL_PAD 补不回来，正确缩放的模板会放不进去，
    分数会从 0.9 掉到 0.7 以下，所以这里按中心撑开窗口。
    """
    cx, cy = x + cw // 2, y + ch // 2
    half_w = max(cw // 2 + CELL_PAD, min_window // 2)
    half_h = max(ch // 2 + CELL_PAD, min_window // 2)
    return (max(0, cx - half_w), max(0, cy - half_h),
            min(width, cx + half_w), min(height, cy + half_h))


def identify_row(row_image: np.ndarray, height: int = 1080, *,
                 info: dict[str, dict] | None = None) -> list[GuideMatch]:
    """认一整行材料格(含 1~3 格), 返回按 x 排序的识别结果。

    弧盘那一行只列未满足的档位, 格子数不定且不保证从绿档起排,
    所以档位一律以模板的材质色为准, 不看列序。
    """
    meta = info if info is not None else load_info()
    # 卡片尺寸阈值按 1080 高标定; 其它分辨率下行带里卡片实际像素随画面高度放大,
    # 阈值必须按同一比例放宽, 否则高分辨率下有效材料会在模板匹配前被筛掉。
    scale = max(1, int(height or 1080)) / 1080
    cell_min, cell_max = CELL_MIN * scale, CELL_MAX * scale
    min_window = int(MIN_WINDOW * scale)
    matches = []
    h, w = row_image.shape[:2]
    for x, y, cw, chh in find_cells(row_image, cell_min, cell_max):
        x1, y1, x2, y2 = _window(x, y, cw, chh, w, h, min_window)
        match = identify_cell(row_image[y1:y2, x1:x2], height, info=meta)
        if match is None:
            continue
        matches.append(replace(match, center_x=x + cw // 2))
    return sorted(matches, key=lambda m: m.center_x)


def vote_dungeon(matches: list[GuideMatch]) -> tuple[str, str, int]:
    """三格联合投票, 返回 (kind, 副本名, 票数); 空输入返回 ("", "", 0)。"""
    tally: dict[tuple[str, str], int] = {}
    for match in matches:
        key = (match.kind, match.dungeon)
        tally[key] = tally.get(key, 0) + 1
    if not tally:
        return "", "", 0
    (kind, dungeon), votes = max(tally.items(), key=lambda item: item[1])
    return kind, dungeon, votes


def summarize(matches: list[GuideMatch]) -> str:
    if not matches:
        return "未识别到材料"
    parts = []
    for match in matches:
        flag = "!" if match.tier_conflict else ""
        parts.append(f"{match.dungeon}·{match.tier}{flag} {match.score:.3f}")
    return " | ".join(parts)
