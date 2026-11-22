"""识别层：养成页材料行/图标槽位、奖励清单、掉落数字的 OCR 与切图。"""

from __future__ import annotations

import cv2
import unicodedata

import cv2

from src.tasks.cultivation_goal.core import (
    MaterialRow,
    RATIO_RE,
    Ratio,
    parse_ratios,
    repair_swallowed_slash,
)
from src.tasks.cultivation_goal.flow_const import (
    CG_BURST_CONFIRM_RANGE,
    CG_FINAL_CONFIRM_RANGE,
)
from src.tasks.cultivation_goal.positions import POS
from src.tasks.cultivation_goal.positions import TARGET_ROWS


class ScanMixin:
    def _cg_scan_reward_list(self, tag: str):
        """认奖励卡材料与数量, 存入 _cg_reward_loot; 每次扫描都登记一次完整性结果。"""

        flags = getattr(self, "_cg_scan_flags", None)
        if flags is None:
            flags = self._cg_scan_flags = []

        def mark(complete: bool) -> bool:
            flags.append(bool(complete))
            return complete

        try:
            from src.tasks.cultivation_goal.reward_icons import identify_rewards_detailed, summarize

            frame = getattr(self, "frame", None)
            if frame is None or getattr(frame, "size", 0) == 0:
                # 没画面也是一次没读到的清单, 必须登记, 否则漏扫会被当成整轮完整。
                self._cg_note("warning", f"奖励清单识别({tag}): 无可用画面")
                mark(False)
                return []
            matches, card_total = identify_rewards_detailed(frame, self.height)
            loot = []
            partial = False
            for match in matches:
                # 副本结算页数量印在卡片下沿外, 野外清单在卡内右下: 各自优先, 读不出再换另一个框。
                below = getattr(match, "count_box_below", None)
                if tag == "副本" and below:
                    count = self._cg_read_reward_count(below, match.count_box)
                else:
                    count = self._cg_read_reward_count(match.count_box)
                if count is None or count <= 0:
                    self._cg_note("warning", f"奖励卡 {match.dungeon} 数量读不出, 该格跳过")
                    partial = True
                    continue
                loot.append({"key": match.key, "count": count, "dungeon": match.dungeon,
                             "inliers": match.inliers})
            if card_total is not None and len(matches) < card_total:
                # 投影切出了卡但有的没认出模板: 缺的那张不能按零入账, 整轮按残缺处理。
                self._cg_note(
                    "warning",
                    f"奖励清单切出 {card_total} 张卡只认出 {len(matches)} 张, 本轮标记残缺",
                )
                partial = True
            # 有奖励卡认出模板但数量 OCR 失败时视为本轮读数残缺, 主循环不入账。
            self._cg_loot_partial = partial or getattr(self, "_cg_loot_partial", False)
            # 连打多局时逐次累计, 同材料数量相加; 每轮开打前由主循环清零。
            merged = list(getattr(self, "_cg_reward_loot", None) or [])
            by_key = {item["key"]: i for i, item in enumerate(merged)}
            for item in loot:
                idx = by_key.get(item["key"])
                if idx is not None:
                    merged[idx] = {**merged[idx], "count": merged[idx]["count"] + item["count"]}
                else:
                    merged.append(item)
                    by_key[item["key"]] = len(merged) - 1
            self._cg_reward_loot = merged
            # 逐次记下这份清单是否读全: 同一轮里有清单没读全, 整轮不能入账。
            mark(bool(loot) and not partial)
            self._cg_note("info", f"奖励清单识别({tag}): {summarize(matches)}, 累计 {merged}")
            if loot:
                self._cg_report().emit("loot", tag=tag, items=loot)
            return loot
        except Exception as error:
            mark(False)
            self._cg_note("warning", f"奖励清单识别失败: {error}")
            return []

    def _cg_read_reward_count(self, box_ratio: tuple[float, float, float, float],
                              alt_box_ratio: tuple[float, float, float, float] | None = None
                              ) -> int | None:
        """读奖励卡右下角数量; 只接受唯一一个数字段, 多组数字视为读不出。

        主框读不出(空/多组数字)时换备选框再试一次, 两框都失败才判读不出。
        """
        for candidate in (box_ratio, alt_box_ratio):
            if not candidate:
                continue
            try:
                box = self.box_of_screen(*candidate, name="cg_reward_count", hcenter=True)
                results = self.ocr(box=box, match=None) or []
                import re as _re
                for result in results:
                    text = (result.name or "").strip()
                    if not text:
                        continue
                    # "×4"这类取 4; "4张5个"把两组数字拼起来就是错账, 宁可重读也不入账。
                    groups = _re.findall(r"\d+", text)
                    if len(groups) > 1:
                        self._cg_note("warning", f"奖励数量出现多组数字({text}), 换框重读")
                        break
                    if groups:
                        count = int(groups[0])
                        if count > 0:
                            return count
            except Exception as error:
                self._cg_note("warning", f"奖励数量 OCR 失败: {error}")
        return None

    def _cg_arm_reward_capture(self, limit=6):
        """在点掉确认框前识别奖励清单（on_found 回调是唯一时机），逐次累计掉落。"""
        try:
            raw = self.wait_click_confirm
            state = {"n": 0, "skipped": 0, "limit": limit}
            self._cg_scan_state = state
            loot_ranges = self._cg_loot_confirm_ranges()

            def patched(*args, **kwargs):
                confirm_range = kwargs.get("range")
                if confirm_range is None and len(args) >= 2:
                    confirm_range = args[1]
                # 只扫白名单确认框(连打继续/收场/周本领奖): ESC 离场、团灭、入场等确认没有奖励清单。
                if not isinstance(confirm_range, tuple) or confirm_range not in loot_ranges:
                    return raw(*args, **kwargs)
                if state["n"] >= state["limit"]:
                    # 额度用完仍要照常确认, 但要记下有领取没能扫描, 主循环据此回页复核。
                    state["skipped"] += 1
                    return raw(*args, **kwargs)
                found = kwargs.get("on_found")
                if found is None and len(args) >= 3:
                    found = args[2]

                def on_found():
                    state["n"] += 1
                    self._cg_scan_reward_list("副本")
                    if callable(found):
                        found()

                if len(args) >= 3:
                    args = args[:2] + (on_found,) + args[3:]
                else:
                    kwargs["on_found"] = on_found
                return raw(*args, **kwargs)

            self._cg_shadow("wait_click_confirm", patched)
            return True
        except Exception as error:
            self._cg_note("warning", f"奖励清单识别未挂上: {error}")
            return False

    def _cg_loot_confirm_ranges(self):
        """带奖励清单的确认框白名单: 连打继续、收场、周本领奖; 其余确认一律不扫。"""
        ranges = {CG_BURST_CONFIRM_RANGE, CG_FINAL_CONFIRM_RANGE}
        try:
            from src.tasks.AnomalyPilgrimage import AnomalyPilgrimage

            box = getattr(AnomalyPilgrimage, "REWARD_CLAIM_BOX", None)
            if isinstance(box, tuple):
                ranges.add(box)
        except Exception:
            pass
        return tuple(ranges)

    def _cg_read_rows(self):
        """按 tab 分组读完所有目标行，返回值按 TARGET_ROWS 优先级排。"""
        if not self._cg_ensure_guide():
            return None
        texts = {}
        self._cg_row_dungeon = {}
        # 槽位映射同样重建, 只认本轮确认的 key, 防止行内容变化后用旧槽位入账。
        self._cg_row_slots = {}
        self._cg_unverified_labels = set()
        # 档位异常标记只对本次读数有效: 上轮不连续不代表这轮还不可信。
        self._cg_inconsistent_labels = set()
        # 本轮沿用旧值的行: 决策照用, 但不参与收益/零产出统计, 免得把"没读到"算成"没涨"。
        self._cg_stale_labels = set()
        read: dict[tuple[str, int], tuple | None] = {}
        grouped: dict[str, list] = {}
        for tab, row_index, cost, landing in TARGET_ROWS:
            grouped.setdefault(tab, []).append((row_index, cost, landing))
        for tab, specs in grouped.items():
            self._cg_click(POS.tab_x[tab], POS.overview_tabs_y, f"{tab} tab", after_sleep=0.6)
            for row_index, cost, landing in specs:
                x1, x2 = POS.ratio_box_x
                center_y = POS.ratio_row_y[tab][row_index]
                y1 = center_y - POS.ratio_half_height
                y2 = center_y + POS.ratio_half_height
                box = self.box_of_screen(x1, y1, x2, y2, name=f"cg_{tab}_{row_index}", hcenter=True)
                slot_text, slot_ratios = self._cg_read_row_ratios(tab, row_index)
                slot_mode = bool(slot_text) or slot_ratios is not None
                if slot_mode:
                    # 分框读数接管: 单槽解析, 斜杠误读不会再跨槽拼接成假比例。
                    text = slot_text
                    ratios = slot_ratios
                else:
                    text = self._cg_read_line(box)
                    ratios = self._cg_usable_ratios(text)
                self._cg_note(
                    "info", f"{tab} 第 {row_index + 1} 行 (y {y1:.3f}-{y2:.3f}): {text or '<空>'}"
                )
                label = f"{tab}-第{row_index + 1}行"
                if not slot_mode:
                    # 只有整行模式才做单串猜出重试与分级重试; 分框模式在槽内已重试,
                    # 且槽残缺时不能拿整行拼接串回填, 否则 8140/13 这类假比例又回来了。
                    if ratios is None and parse_ratios(text):
                        # 单串猜出来的比例(如 3150→3/50)切法不唯一, 先重试一轮再放弃。
                        self._cg_note("warning", f"{label} 比例由单串猜出({text}), 重试识别")
                    if ratios is None:
                        retry = self._cg_read_retry(box)
                        retry_ratios = self._cg_usable_ratios(retry)
                        if retry_ratios is not None:
                            self._cg_note("info", f"{tab} 第 {row_index + 1} 行 重试成功: {retry}")
                            text = retry
                            ratios = retry_ratios
                texts[f"{tab}-第{row_index + 1}行"] = text
                if ratios is None:
                    # 读不出时沿用上一轮值, 避免一次识别抖动就收摊; 槽位对不上会在下方统一作废。
                    ratios = getattr(self, "_cg_prev_owned", {}).get(label)
                    if ratios:
                        self._cg_stale_labels.add(label)
                        shown = " ".join(f"{r.owned}/{r.required}" for r in ratios)
                        self._cg_note("warning", f"{label} 读不出, 本轮沿用上一轮 {shown}")
                if ratios is None:
                    self._cg_capture(f"row_empty_{tab}_{row_index + 1}")
                read[(tab, row_index)] = ratios
                dungeon = self._cg_identify_row(tab, row_index)
                if label in getattr(self, "_cg_inconsistent_labels", ()):
                    self._cg_capture(f"row_inconsistent_{tab}_{row_index + 1}")
                    read[(tab, row_index)] = None
                    dungeon = None
                if dungeon is None and ratios is not None:
                    # 图标本轮没认出: 仍可用历史槽位数核对读数完整性, 完全没历史才放弃核验。
                    self._cg_unverified_labels.add(label)
                if ratios is not None:
                    # 沿用旧值的行同样要过这道检查: 槽位对不上说明行内容换过, 旧值对新行无效。
                    slots = (getattr(self, "_cg_row_slots", {}) or {}).get(label) \
                        or (getattr(self, "_cg_last_slots", {}) or {}).get(label) or ()
                    if slots and len(ratios) != len(slots):
                        self._cg_note(
                            "warning",
                            f"{label} 比例读数 {len(ratios)} 项与材料槽位 {len(slots)} 个不一致, 视为读数残缺回页复核",
                        )
                        self._cg_capture(f"row_ratio_mismatch_{tab}_{row_index + 1}")
                        read[(tab, row_index)] = None
        self._cg_last_texts = texts
        # 记住最近一次确认的槽位映射, 供后续图标本轮没认出时核对读数完整性。
        self._cg_last_slots = {**(getattr(self, "_cg_last_slots", {}) or {}), **self._cg_row_slots}
        return tuple(
            MaterialRow(tab, row_index, cost, landing, read.get((tab, row_index)))
            for tab, row_index, cost, landing in TARGET_ROWS
        )

    def _cg_identify_row(self, tab, row_index):
        """认这一行材料图标所属副本，并记录槽位 key 供掉落入账。"""
        try:
            from src.tasks.cultivation_goal.guide_icons import (
                identify_row, infer_missing, summarize, vote_dungeon)

            frame = getattr(self, "frame", None)
            if frame is None or getattr(frame, "size", 0) == 0:
                return None
            height, width = frame.shape[:2]
            center_y = POS.ratio_row_y[tab][row_index] - POS.icon_row_offset
            half = POS.icon_half_height
            strip = frame[
                max(0, int(height * (center_y - half))):int(height * (center_y + half)),
                int(width * 0.30):int(width * 0.85),
            ]
            matches = identify_row(strip, height)
            kind, dungeon, votes = vote_dungeon(matches)
            label = f"{tab}-第{row_index + 1}行"
            if not dungeon:
                self._cg_note("warning", f"{label} 材料图标未识别, 该行缺口无法规划")
                # 图标识别失败单独留图: 区分是这一行图标变了/页面错位, 还是整页没加载。
                self._cg_capture(f"row_icon_unknown_{tab}_{row_index + 1}")
                return None
            # 缺口档位须是连续高档区间；不连续说明漏识别, 本行标记未读, 不写槽位与副本。
            plan = infer_missing(match.tier for match in matches)
            if not plan.consistent:
                self._cg_note("warning",
                              f"{label} 缺口档位不连续, 疑似漏识别, 本行标记未读回页复核: 缺 {''.join(plan.gaps)}")
                inconsistent = getattr(self, "_cg_inconsistent_labels", set())
                inconsistent.add(label)
                self._cg_inconsistent_labels = inconsistent
                return None
            # 存下从左到右的材料 key（与 ratios 同序），掉落识别据此摊到槽位。
            slots = getattr(self, "_cg_row_slots", {})
            slots[label] = tuple(match.key for match in matches)
            self._cg_row_slots = slots
            self._cg_row_dungeon[label] = dungeon
            self._cg_note("info", f"{label} = {dungeon} ({votes} 票, {summarize(matches)})"
                                  f" 缺 {''.join(plan.observed) or '无'}")
            self._cg_report().emit("material", row=label, dungeon=dungeon, kind=kind,
                                   items=[m.as_dict() for m in matches],
                                   missing=plan.as_dict())
            return dungeon
        except Exception as error:
            self._cg_note("warning", f"材料图标识别失败: {error}")
            return None

    def _cg_read_line(self, box, frame_processor=None, threshold=0):
        """OCR 一行数字，异常按读空处理。"""
        try:
            results = self.ocr(
                box=box, match=None, threshold=threshold, frame_processor=frame_processor
            ) or []
            return " ".join(result.name for result in results)
        except Exception as error:
            self._cg_note("warning", f"OCR 异常(按读空处理): {error}")
            return ""

    def _cg_read_retry(self, box):
        """分级重试：原样 → 二值化 → 灰度降阈值。

        斜杠常被 OCR 认成 `1`，二值化 200 可还原；不能用 use_grayscale（转单通道会让
        onnxocr 检测算子 ValueError 带崩任务）。
        """
        gray3 = lambda img: cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        for delay, processor, threshold in (
            (0.6, None, 0),
            (0.2, self._cg_binarize_frame, 0),
            (0.2, gray3, 0.3),
        ):
            self.sleep(delay)
            text = self._cg_read_line(box, frame_processor=processor, threshold=threshold)
            if parse_ratios(text):
                return text
        return ""

    @staticmethod
    def _cg_binarize_frame(img):
        """二值化 200：斜杠被 OCR 认成 `1` 时还原字形。"""
        return cv2.cvtColor(
            cv2.threshold(
                cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 200, 255, cv2.THRESH_BINARY
            )[1],
            cv2.COLOR_GRAY2BGR,
        )

    def _cg_split_icon_slots(self, frame, tab, row_index):
        """亮度投影切出本行材料图标的 x 段(比例坐标)，失效返回空列表。

        图标卡片在深色背景上是亮块，列投影即可分开；空槽位整列全暗。
        """
        try:
            if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim < 2:
                return []
            height, width = frame.shape[:2]
            center_y = POS.ratio_row_y[tab][row_index] - POS.icon_row_offset
            y1 = int(height * (center_y - POS.icon_half_height))
            y2 = int(height * (center_y + POS.icon_half_height))
            x1 = int(width * 0.28)
            x2 = int(width * 0.88)
            strip = cv2.cvtColor(frame[y1:y2, x1:x2, :3], cv2.COLOR_BGR2GRAY)
            proj = (strip > 50).sum(axis=0)
            lit = max(1, y2 - y1)
            segs = []
            start = None
            for i, v in enumerate(proj):
                on = v > lit * 0.3
                if on and start is None:
                    start = i
                elif not on and start is not None:
                    segs.append((start, i))
                    start = None
            if start is not None:
                segs.append((start, len(proj)))
            # 过滤窄噪声并转比例坐标; 一行最多 3 个材料, 切出更多说明投影被污染。
            slots = []
            for sx1, sx2 in segs:
                if sx2 - sx1 < width * 0.015:
                    continue
                slots.append(((x1 + sx1) / width, (x1 + sx2) / width))
            return slots if 1 <= len(slots) <= 3 else []
        except Exception:
            return []

    @staticmethod
    def _cg_parse_slot(text):
        """单槽解析：正常 xx/xx 直接收；纯数字单串走斜杠吞字修复；其余 None。"""
        normalized = unicodedata.normalize("NFKC", (text or "").strip())
        if not normalized:
            return None
        matches = RATIO_RE.findall(normalized)
        if len(matches) == 1:
            left, right = matches[0]
            return Ratio(int(left), int(right))
        if len(matches) > 1:
            return None  # 单槽出现多个比例说明切槽串了, 判残缺不猜
        return (repair_swallowed_slash(normalized) or (None,))[0]

    def _cg_read_row_ratios(self, tab, row_index):
        """按图标槽位分框读比例，防 OCR 把 `/` 读成 `1` 后跨槽拼接。

        整行连读时 `8/40 4/13` 的斜杠被认成 1 会拼出 `8140/13`，把缺料判成满。
        分框后单槽串错只污染一格：纯数字单串交给 repair_swallowed_slash，
        失败槽二值化重试一次；任何槽解析不出则整行返回 None，绝不把缺当满。
        返回 (槽文本, ratios)；切槽失效返回 ("", None) 由调用方退回整行读数。
        """
        frame = getattr(self, "frame", None)
        slots = self._cg_split_icon_slots(frame, tab, row_index)
        if not slots:
            return "", None
        texts = []
        ratios = []
        cy = POS.ratio_row_y[tab][row_index]
        ny1, ny2 = cy - POS.ratio_half_height, cy + POS.ratio_half_height
        for i, (sx1, sx2) in enumerate(slots):
            # 数字比图标窄但可能溢出边缘, 以槽中心 ±0.028 取框, 相邻槽间距 0.09 不会串。
            cx = (sx1 + sx2) / 2
            box = self.box_of_screen(
                cx - 0.028, ny1, cx + 0.028, ny2,
                name=f"cg_slot_{tab}_{row_index}_{i}", hcenter=True,
            )
            text = self._cg_read_line(box)
            ratio = self._cg_parse_slot(text)
            if ratio is None or ratio.guessed:
                # 斜杠被读成 1 的槽: 二值化重试一次, 还原不出就整行判残缺。
                retry = self._cg_read_line(box, frame_processor=self._cg_binarize_frame)
                retry_ratio = self._cg_parse_slot(retry)
                if retry_ratio is not None and not retry_ratio.guessed:
                    text, ratio = retry, retry_ratio
                else:
                    ratio = None
            texts.append(text or "<空>")
            ratios.append(ratio)
        joined = " | ".join(texts)
        if any(r is None for r in ratios):
            self._cg_note(
                "warning", f"{tab} 第 {row_index + 1} 行 槽读数残缺({joined}), 判读不出防误判满"
            )
            return joined, None
        return joined, tuple(ratios)

