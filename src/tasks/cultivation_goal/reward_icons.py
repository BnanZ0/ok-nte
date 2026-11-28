"""奖励弹窗材料识别：底板模板 + SIFT 几何一致点 + 图标本体判档。

主库 assets/game/reward_icon/（41 张）, 养成页 40 张兜底。档位由 tier_color 判图标本体, 
不靠 SIFT 命中哪张模板（同副本三档图案几乎一样）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from ok import Logger

from src.tasks.cultivation_goal.asset_paths import resolve_asset_dir
from src.tasks.cultivation_goal.guide_icons import GUIDE_ICON, load_info
from src.tasks.cultivation_goal.tier_color import detect_tier

logger = Logger.get_logger(__name__)

_REWARD_DIR = resolve_asset_dir("reward_icon")

CARD_Y = (0.489, 0.591)
CARD_X0 = 0.405
CARD_W = 0.0667
MAX_CARDS = 5
# 正确材料通常 ≥9 内点, 库外材料（金币/银卷）误认约 4 内点。
MIN_INLIERS = 8
LOWE_RATIO = 0.72
RANSAC_PIXELS = 4.0
# 图标本体判档区：卡片中上部, 避开描边和数量。
ICON_Y = (0.12, 0.78)
ICON_X = (0.12, 0.88)


@dataclass(frozen=True)
class RewardMatch:
    key: str
    kind: str
    dungeon: str
    tier: str
    inliers: int
    x: int
    count_box: tuple[float, float, float, float]
    # 副本结算页的数量印在卡片下沿之外, 单靠 count_box 会读空。
    count_box_below: tuple[float, float, float, float] | None = None

    def as_dict(self) -> dict:
        return {
            "key": self.key, "kind": self.kind, "dungeon": self.dungeon,
            "tier": self.tier, "inliers": self.inliers, "x": self.x,
        }

    @property
    def slot_key(self) -> str:
        """与养成页 identify_row 的 key 同格式, 用来对上行里的槽位。"""
        return self.key


def _normalize_key(name: str) -> str:
    """relation 的 _材料 后缀、泡影罐头- 跟养成页 key 不一致, 统一过来。"""
    key = name[:-4] if name.endswith(".png") else name
    key = key.replace("泡影罐头-", "泡影罐头·")
    if key.endswith("_材料"):
        key = key[:-3]
    return key


def load_reward_info() -> dict[str, dict]:
    """把 reward_info.json 的列表格式转成 {归一化 key: 元数据}。

    养成页 guide_info.json 是 dict 且缺贵腐巢母; 这份列表页元数据覆盖 41 张主库, 
    两者合并后所有命中都能拿到 dungeon 名。
    """
    merged = dict(load_info())
    target = _REWARD_DIR / "reward_info.json"
    try:
        with open(target, encoding="utf-8") as fp:
            rows = json.load(fp)
    except (OSError, ValueError) as error:
        # 只记类型与文件名: 异常串里带完整路径, 会泄露安装目录。
        logger.warning(f"奖励页元数据读取失败: {type(error).__name__} ({target.name})")
        return merged
    for row in rows:
        if not isinstance(row, dict):
            continue
        for tier, filename in row.get("tiers", {}).items():
            key = _normalize_key(filename)
            merged.setdefault(key, {
                "kind": row.get("kind", ""),
                "dungeon": row.get("name", key),
                "tier": "" if tier == "材料" else tier,
                "material": row.get("material", ""),
            })
    return merged


def _sift():
    if hasattr(cv2, "SIFT_create"):
        return cv2.SIFT_create(nfeatures=400), cv2.NORM_L2
    return cv2.ORB_create(nfeatures=800), cv2.NORM_HAMMING


def _gray(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        return None
    if image.ndim == 3:
        return cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    return image


def _imread(path: Path) -> np.ndarray:
    """Windows 中文路径 cv2.imread 返回 None, 必须走 fromfile+imdecode。"""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    except OSError:
        return None


class RewardRecognizer:
    """缓存所有模板的 SIFT 描述子, 避免每局重算。"""

    def __init__(self, height: int = 1080, info: dict | None = None):
        self.height = int(height)
        self.info = info if info is not None else load_reward_info()
        self.detector, norm = _sift()
        self.matcher = cv2.BFMatcher(norm)
        self._templates: list[tuple[str, object, object]] = []
        self._currency_templates: list[tuple[str, object, object]] = []
        self._build()

    def _build(self):
        # 主库：列表页带底板 41 张（含贵腐巢母, 养成页库缺它）。货币模板单独放,
        # 只在材料模板全没命中时才参与匹配, 防止金罐头形状抢走罐头材料的命中。
        for path in sorted(_REWARD_DIR.glob("*.png")):
            image = _imread(path)
            if image is None:
                continue
            gray = _gray(image)
            kp, desc = self.detector.detectAndCompute(gray, None)
            if desc is None or len(desc) < 2:
                continue
            key = _normalize_key(path.name)
            target = (self._currency_templates
                      if self.info.get(key, {}).get("kind") == "currency"
                      else self._templates)
            target.append((key, kp, desc))
        # 兜底：养成页 40 张, 裁掉底部 0/12 数字条。
        for key in GUIDE_ICON.list_names():
            prepared = GUIDE_ICON.get_template(key, self.height)
            if prepared is None:
                continue
            image = prepared[0] if isinstance(prepared, tuple) else prepared
            body = _gray(image[:max(8, int(image.shape[0] * 0.72))])
            if body is None:
                continue
            kp, desc = self.detector.detectAndCompute(body, None)
            if desc is not None and len(desc) >= 2:
                self._templates.append((key, kp, desc))

    def identify_card(self, card: np.ndarray, x1: float, x2: float,
                      center_x: int,
                      card_y: tuple[float, float] | None = None) -> RewardMatch | None:
        """认一张材料卡; 认不出或分数不够就返回 None, 绝不猜。

        card_y 是投影切卡得到的真实 (上, 下) 比例边界, 数量框必须跟着它走;
        不传时回退固定 CARD_Y, 兼容直接调用与货币卡。
        """
        return self._match_card(card, x1, x2, center_x, self._templates, card_y)

    def _identify_currency(self, card: np.ndarray, x1: float = 0.0, x2: float = 0.0,
                           center_x: int = 0,
                           card_y: tuple[float, float] | None = None) -> RewardMatch | None:
        """只在材料模板全没命中后兜底认货币卡, 保证材料永远优先。

        x1/x2 必须传真实卡边界: 货币卡也要读数量(甲硬币入账), 边界给 0 会裁出空图。
        """
        return self._match_card(card, x1, x2, center_x, self._currency_templates, card_y)

    def _match_card(self, card: np.ndarray, x1: float, x2: float, center_x: int,
                    templates: list,
                    card_y: tuple[float, float] | None = None) -> RewardMatch | None:
        gray = _gray(card)
        if gray is None or gray.shape[0] < 20 or gray.shape[1] < 20:
            return None
        kp1, desc1 = self.detector.detectAndCompute(gray, None)
        if desc1 is None or len(desc1) < 4:
            return None
        best: tuple[int, str] | None = None
        for key, kp2, desc2 in templates:
            pairs = self.matcher.knnMatch(desc1, desc2, k=2)
            good = [
                pair[0] for pair in pairs
                if isinstance(pair, (tuple, list)) and len(pair) == 2
                and pair[0].distance < LOWE_RATIO * pair[1].distance
            ]
            if len(good) < MIN_INLIERS:
                continue
            src = np.float32([kp1[m.queryIdx].pt for m in good])
            dst = np.float32([kp2[m.trainIdx].pt for m in good])
            _, mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_PIXELS)
            inliers = int(mask.sum()) if mask is not None else 0
            if inliers < MIN_INLIERS or (best and inliers <= best[0]):
                continue
            best = (inliers, key)
        if best is None:
            return None
        inliers, key = best
        entry = self.info.get(key, {})
        kind = entry.get("kind", "")
        # 三档副本按图标本体判色, 单格材料(boss/weekly)沿用模板 key 的空档。
        tier = entry.get("tier", "")
        if tier:
            h, w = card.shape[:2]
            icon = card[int(h * ICON_Y[0]):int(h * ICON_Y[1]),
                        int(w * ICON_X[0]):int(w * ICON_X[1])]
            detected = detect_tier(icon)
            if detected is None:
                # 判色失败就不能拿模板档位顶上: 同副本三档图案几乎一样, 记错档比漏记更糟。
                return None
            tier = detected[0]
            # 判色档位与模板档位不一致时, key 同步换成判色档位, 避免掉落入账记到低档槽位。
            if tier != entry.get("tier", ""):
                key = f"{kind}_{entry.get('dungeon', key)}_{tier}"
        # 数量框的 y 必须来自投影切出的真实卡边界: 固定 CARD_Y 与投影 y 错位时,
        # 数量 OCR 会读到卡片外, 导致「数量读不出 → 整轮账本作废回页复核」。
        top, bottom = card_y if card_y is not None else CARD_Y
        return RewardMatch(
            key=key,
            kind=kind,
            dungeon=entry.get("dungeon", key),
            tier=tier,
            inliers=inliers,
            x=int(center_x),
            # 卡内数量框: 数字右对齐压在卡片右下角。
            # - 左边界留 60% 卡宽: 45% 会把"12000"的"1"切掉读成 2000;
            # - 下沿要越过卡底边 8%: "7"这种贴底数字切到 bottom-0.05h 只剩上半截;
            # - 上沿 60%: 再往上会把图标边缘算进来, 读出"37"这类噪声。
            count_box=(x2 - (x2 - x1) * 0.6,
                       top + (bottom - top) * 0.6,
                       x2, min(1.0, bottom + (bottom - top) * 0.08)),
            # 副本结算页: 卡下沿下方 0~42% 卡高的窄条, 数量就印在这里。
            # 取 0.42 而不是 0.3: 单数字("1")贴得更靠下, 0.3 会切掉它的下半截读不出。
            count_box_below=(x1, bottom,
                             x2, min(1.0, bottom + (bottom - top) * 0.42)),
        )

    @staticmethod
    def _detect_cards(frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        """投影找奖励卡的行高和每张卡的 x 段, 返回 (x1, x2, y1, y2) 像素框。

        两种底色各试一路:
        - 亮卡: 副本结算/领奖弹窗, 深色背景上的几张亮卡;
        - 暗卡: 野外清单, 浅灰面板上的几张深卡(整页都是亮的, 亮卡投影只会切出面板本身)。
        两路都切不出就退回固定比例。
        """
        height, width = frame.shape[:2]
        # 行高/卡宽阈值按 1080 高标定, 其它分辨率等比缩放, 否则高分辨率下切不出卡。
        scale = height / 1080
        gray = cv2.cvtColor(frame[:, :, :3], cv2.COLOR_BGR2GRAY)
        for dark in (False, True):
            boxes = RewardRecognizer._project_cards(gray, height, width, scale, dark)
            if boxes:
                return boxes
        return []

    @staticmethod
    def _project_cards(gray: np.ndarray, height: int, width: int, scale: float,
                       dark: bool) -> list[tuple[int, int, int, int]]:
        top0 = int(height * 0.38)
        strip = gray[top0:int(height * 0.66), int(width * 0.24):int(width * 0.76)]
        rows = (strip < 160) if dark else (strip > 50)
        proj = rows.sum(axis=1)
        # 亮卡: 只有 1~2 张卡时亮像素远不到半屏（两卡 ≈ 33%）, 门限按「两张最窄卡」定,
        # 不能用 strip 宽的 50%——那样单卡/双卡页永远切不出来, 只能退回固定格而错位。
        # 暗卡: 面板整体是亮的, 门限只需压过噪声。
        min_lit = strip.shape[1] * 0.06 if dark else max(2 * 40 * scale, strip.shape[1] * 0.05)
        # 取最长连续带: 「获得道具」横幅、底部水印也会亮, 首末行相减会把它们算进行高。
        spans: list[tuple[int, int]] = []
        start: int | None = None
        for i, v in enumerate(proj):
            if v > min_lit and start is None:
                start = i
            elif v <= min_lit and start is not None:
                spans.append((start, i))
                start = None
        if start is not None:
            spans.append((start, len(proj)))
        if not spans:
            return []
        top, bottom = max(spans, key=lambda span: span[1] - span[0])
        y1, y2 = top + top0, bottom + top0
        # 卡片行高度约 140px; 野外清单整屏连片会拉到 290px, 直接判投影失效。
        if y2 - y1 < 60 * scale or y2 - y1 > 200 * scale:
            return []
        band = gray[y1:y2, :]
        cols = (band < 160) if dark else (band > 50)
        # 暗卡列门限要低: 金色/亮色卡只有部分列算暗, 0.5 会把中间那张漏掉。
        min_col = (y2 - y1) * (0.35 if dark else 0.4)
        xp = cols.sum(axis=0)
        segs: list[tuple[int, int]] = []
        start = None
        for x, v in enumerate(xp):
            if v > min_col and start is None:
                start = x
            elif v <= min_col and start is not None:
                if 40 * scale < x - start < 200 * scale:
                    segs.append((start, x))
                start = None
        if start is not None and 40 * scale < width - start < 200 * scale:
            segs.append((start, width))
        # 卡太多（野外整屏连片）说明投影失效, 交给固定切格。
        if not segs or len(segs) > MAX_CARDS:
            return []
        return [(x1, x2, y1, y2) for x1, x2 in segs]

    def identify_strip(self, frame: np.ndarray) -> list[RewardMatch]:
        """在奖励弹窗卡片带上逐格识别, 按 x 排序。"""
        return self.identify_strip_detailed(frame)[0]

    def identify_strip_detailed(self, frame: np.ndarray) -> tuple[list[RewardMatch], int | None]:
        """识别并返回 (材料匹配列表, 待识别卡数)。

        待识别卡数只在投影切卡成功时给出, 且已排除认出的货币卡: 货币不算漏识别。
        调用方拿「材料张数 < 待识别卡数」判定有材料卡没认出, 整轮按残缺处理。
        """
        if frame is None or getattr(frame, "size", 0) == 0:
            return [], None
        height, width = frame.shape[:2]
        boxes = self._detect_cards(frame)
        projected = bool(boxes)
        if not boxes:
            y1, y2 = int(height * CARD_Y[0]), int(height * CARD_Y[1])
            boxes = []
            for index in range(MAX_CARDS):
                x1 = CARD_X0 + index * CARD_W
                x2 = x1 + CARD_W
                if x2 > 1.0:
                    break
                boxes.append((int(width * x1), int(width * x2), y1, y2))
        matches: list[RewardMatch] = []
        currencies = 0
        self.currencies = []
        for px1, px2, py1, py2 in boxes:
            card = frame[py1:py2, px1:px2]
            # 数量框必须跟随投影切出的真实卡边界, 不能用固定 CARD_Y (Major #20)。
            card_y = (py1 / height, py2 / height)
            found = self.identify_card(card, px1 / width, px2 / width, (px1 + px2) // 2,
                                       card_y=card_y)
            if found is None:
                # 材料模板没命中再试货币; 材料永远优先, 防止货币模板抢材料卡。
                currency = self._identify_currency(card, px1 / width, px2 / width,
                                                   (px1 + px2) // 2, card_y)
                if currency is not None:
                    currencies += 1
                    # 甲硬币入账要用币种卡的数量, 所以单独留下来; 不混进 matches,
                    # 否则待识别卡数与材料槽位映射都会被货币卡带偏。
                    self.currencies.append(currency)
                continue
            matches.append(found)
        expected = len(boxes) - currencies if projected else None
        return matches, expected


_recognizer: RewardRecognizer | None = None


def last_currencies() -> list[RewardMatch]:
    """上一次识别里认出的币种卡(金币/银卷); 甲硬币入账按币种卡取数量。"""
    return list(getattr(_recognizer, "currencies", ()) or ())


def identify_rewards(frame: np.ndarray, height: int = 1080,
                     info: dict | None = None) -> list[RewardMatch]:
    """模块级入口; 高度变了重建缓存。带自定义 info 时不走缓存, 避免元数据串用。"""
    return identify_rewards_detailed(frame, height, info)[0]


def identify_rewards_detailed(frame: np.ndarray, height: int = 1080,
                              info: dict | None = None) -> tuple[list[RewardMatch], int | None]:
    """同 identify_rewards, 额外返回投影切出的实际卡片数(投影失败为 None)。"""
    if info is not None:
        return RewardRecognizer(height, info).identify_strip_detailed(frame)
    global _recognizer
    if _recognizer is None or _recognizer.height != int(height):
        _recognizer = RewardRecognizer(height)
    return _recognizer.identify_strip_detailed(frame)


def summarize(matches: list[RewardMatch]) -> str:
    if not matches:
        return "未识别到材料"
    return " | ".join(
        f"{match.dungeon}{match.tier}×{match.inliers}点" for match in matches
    )
