"""奖励清单材料图标识别（assets/game/material_icon 逐档模板匹配）。"""

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from ok import Logger

from src.tasks.cultivation_goal.asset_paths import resolve_asset_dir
from src.utils.game_templates import GameTemplateSet

logger = Logger.get_logger(__name__)

_ASSET_DIR = resolve_asset_dir("material_icon")

# 模板按 1:1 存档, reference_height 对齐常用运行高度, 1080 下不重采样(实测高 0.012 分)。
MATERIAL_ICON = GameTemplateSet(_ASSET_DIR, reference_scale=1.0, reference_height=1080)

# 列表页图标 -> 奖励清单图标约 1.8 倍, 留余量做多尺度; 换分辨率后比例不变。
SCALE_MIN = 1.3
SCALE_MAX = 2.5
SCALE_STEP = 0.2

# 场景先缩一半再匹配: 实测 1800ms -> 240ms, 且分数反而从 0.92 升到 0.96
# (降采样把 JPEG 块噪声和插值噪声一起去掉了)。
DOWNSCALE = 0.5

MATCH_MIN_SCORE = 0.85


@dataclass(frozen=True)
class MaterialMatch:
    key: str
    name: str
    quality: str
    score: float
    center_x: int

    def as_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "quality": self.quality,
                "score": round(self.score, 4), "x": self.center_x}


def load_info(path: Path | None = None) -> dict[str, dict]:
    """读材料元数据; 缺文件就当没有名字(只用文件名当名字)。"""
    target = path or (_ASSET_DIR / "material_info.json")
    try:
        with open(target, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError) as error:
        logger.warning(f"材料元数据读取失败: {type(error).__name__}: {Path(target).name}")
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def available_keys() -> tuple[str, ...]:
    return MATERIAL_ICON.list_names()


def _scaled(template: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-6:
        return template
    return cv2.resize(template, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)


def _best_in_region(template: np.ndarray, scene: np.ndarray) -> tuple[float, int]:
    """多尺度在(已降采样的) scene 里找 template, 返回 (最高分, 命中中心 x)。"""
    best = (0.0, 0)
    if template.shape[0] < 8 or template.shape[1] < 8:
        return best
    scale = SCALE_MIN
    while scale <= SCALE_MAX + 1e-6:
        probe = _scaled(template, scale * DOWNSCALE)
        if probe.shape[0] <= scene.shape[0] and probe.shape[1] <= scene.shape[1]:
            result = cv2.matchTemplate(scene, probe, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, loc = cv2.minMaxLoc(result)
            if max_val > best[0]:
                best = (float(max_val), loc[0] + probe.shape[1] // 2)
        scale += SCALE_STEP
    return best


def identify_strip(strip: np.ndarray, height: int, *, threshold: float = MATCH_MIN_SCORE,
                   info: dict[str, dict] | None = None) -> list[MaterialMatch]:
    """在一条清单横带里认出所有材料图标, 按横坐标排序。

    strip: BGR 图, 只需包含图标那一行(高度比图标略大即可)。
    同一材料可能重复出现, 本函数按 key 去重, 只保留分数最高的一处。
    """
    if strip is None or strip.size == 0:
        return []
    if strip.shape[0] < 20 or strip.shape[1] < 20:
        return []
    scene = cv2.resize(strip, None, fx=DOWNSCALE, fy=DOWNSCALE, interpolation=cv2.INTER_AREA)
    meta = info if info is not None else load_info()
    found: dict[str, MaterialMatch] = {}
    for key in available_keys():
        prepared = MATERIAL_ICON.get_template(key, height)
        if prepared is None:
            continue
        template = prepared[0] if isinstance(prepared, tuple) else prepared
        score, center_x = _best_in_region(template, scene)
        if score < threshold:
            continue
        entry = meta.get(key, {})
        found[key] = MaterialMatch(
            key=key,
            name=entry.get("name", key),
            quality=entry.get("quality", ""),
            score=score,
            center_x=int(center_x / DOWNSCALE),
        )
    return sorted(found.values(), key=lambda m: m.center_x)


def identify_cell(cell: np.ndarray, height: int, *, info: dict[str, dict] | None = None
                  ) -> MaterialMatch | None:
    """认单个格子, 返回分数最高的材料; 低于阈值返回 None。

    格子里若什么都没有(空格), 所有模板都会拿低分, 自然返回 None。
    """
    matches = identify_strip(cell, height, info=info)
    if not matches:
        return None
    return max(matches, key=lambda m: m.score)


def summarize(matches: list[MaterialMatch]) -> str:
    if not matches:
        return "未识别到材料图标"
    return " | ".join(f"{m.name}({m.quality}) {m.score:.3f}" for m in matches)
