import threading
from collections import deque
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from functools import cache
from typing import TYPE_CHECKING, Optional

import cv2
import numpy as np
from ok import Box, Logger, find_color_rectangles

from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.utils import game_filters as gf
from src.utils import image_utils as iu
from src.utils.clock import TimePoint
from src.utils.visual_template_cache import get_visual_template_cache

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar

logger = Logger.get_logger(__name__)


class CombatDetectPhase(Enum):
    IN_COMBAT = "in_combat"
    UNCERTAIN = "uncertain"
    VERIFY_TARGET = "verify_target"


@dataclass(frozen=True)
class CombatDetectResult:
    value: Optional[bool]
    source: str
    lv_ret: Optional[bool] = None
    target_ret: Optional[bool] = None
    target_pending: bool = False
    exhaustive: bool = False
    force: bool = False


@dataclass(slots=True)
class CombatDetectPolicy:
    miss_required: int = 1
    uncertain_seconds: float = 0.5
    sync: bool = False


@dataclass
class CombatDetectState:
    miss_count: int = 0
    uncertain_until: TimePoint | None = None

    def reset(self):
        self.miss_count = 0
        self.uncertain_until = None

    @property
    def uncertain(self) -> bool:
        return self.uncertain_until is not None


@dataclass(frozen=True)
class LvTemplateFeatures:
    l_fingerprint: tuple[float, float, float]
    l_aspect: float
    l_normalized: np.ndarray
    v_fingerprint: tuple[float, float, float]
    v_aspect: float
    v_normalized: np.ndarray

    def __post_init__(self):
        self.l_normalized.setflags(write=False)
        self.v_normalized.setflags(write=False)


class CombatCheck(BaseNTETask):
    _LV_NORM_SIZE = 32
    _TARGET_MASK_REGIONS = [(0.020, 0.017, 0.145, 0.240)]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_animation = False
        self._in_combat = False
        self.target_enemy_time_out = 3
        self.combat_end_condition = None
        self.target_enemy_error_notified = False
        self.freeze_durations: deque[tuple[TimePoint, float, float]] = deque()
        self.cds = {}
        self.find_lv_future = None
        self._lv_async = None
        self.combat_detect_policy = CombatDetectPolicy()
        self.combat_detect_state = CombatDetectState()
        self._target_template_cache_key = None
        self._target_match_templates = None
        self._bg_ocr_lock = threading.Lock()
        self._find_lv_latency = 0
        self._find_lv_async_started_at = self.clock.UNSET
        self._turn_on_retarget = False
        self._boss_fight = False

    @contextmanager
    def retarget_turn_policy(self, enable=True):
        old_val = self._turn_on_retarget
        self._turn_on_retarget = enable
        try:
            yield
        finally:
            self._turn_on_retarget = old_val

    @property
    def in_animation(self):
        return self._in_animation

    @in_animation.setter
    def in_animation(self, value):
        self._in_animation = value

    def on_combat_check(self):
        return True

    def reset_to_false(self):
        self.freeze_durations.clear()
        self.cds = {}
        self._in_combat = False
        self._boss_fight = False
        self.combat_detect_state.reset()
        self.find_lv_future = None
        self._lv_async = None
        self.openvino_clear_cache()
        self.scene.set_not_in_combat()
        return False

    def get_current_char(self, raise_exception=False) -> "BaseChar":
        """
        获取当前角色。
        此方法必须由子类实现。
        """
        raise NotImplementedError("子类必须实现 get_current_char 方法")

    def load_chars(self) -> bool:
        """
        加载队伍中的角色信息。
        此方法必须由子类实现。
        """
        raise NotImplementedError("子类必须实现 load_chars 方法")

    def check_health_bar(self):
        return self.has_health_bar()

    def is_boss(self):
        def filter(image):
            return iu.binarize_bgr_by_brightness(image, threshold=180)

        box = self.box_of_screen(0.3582, 0.0215, 0.5000, 0.0569)
        is_boss = self.find_one(Labels.boss_lv_text, box=box, frame_processor=filter)
        return bool(is_boss)

    def target_enemy(self, wait=True, lv=True, turn=False):
        if not wait:
            self.middle_click()
            return

        time_out = self.target_enemy_time_out
        if turn:
            # 引入了转向，需要额外增加耗时，原本的时间不足以完成
            time_out += 2.5
        logger.info(f"targeting enemy for {time_out}s")
        timer = self.clock.timer(timeout=time_out)
        while not timer.expired():
            if self.is_in_team():
                if self.wait_until(lambda: self.combat_detect(lv=lv), time_out=0.2):
                    return True
                if turn:
                    self.send_key("a", down_time=0.1)
                    self.sleep(0.3)
                    self.middle_click()
                    self.sleep(0.3)
                else:
                    self.middle_click()
                    self.sleep(0.3)
            self.next_frame()

    def has_health_bar(self):
        if self._find_red_health_bar():
            return True
        return False

    def _find_red_health_bar(self, width=100):
        min_height = self.height_of_screen(4 / 1440)
        min_width = self.width_of_screen(width / 2560)
        max_height = min_height * 3
        max_width = self.width_of_screen(200 / 2560)

        # 还原原始的颜色过滤
        _frame = iu.filter_by_hsv(self.frame, enemy_health_hsv)
        boxes = find_color_rectangles(
            _frame,
            enemy_health_color_red,
            min_width,
            min_height,
            max_width,
            max_height,
            box=self.box_of_screen(0.0984, 0, 0.8961, 0.8944, name="health_bar"),
        )

        if len(boxes) > 0:
            self.draw_boxes("enemy_health_bar_red", boxes, color="blue")
            return True
        return False

    @property
    def combat_detect_uncertain(self) -> bool:
        return self.combat_detect_state.uncertain

    def _update_combat_detect_state(self, combat_detect: CombatDetectResult) -> CombatDetectPhase:
        now = self.clock.now()
        if combat_detect.value is True:
            self.combat_detect_state.reset()
            return CombatDetectPhase.IN_COMBAT

        if self.combat_detect_state.uncertain_until is not None:
            if now < self.combat_detect_state.uncertain_until:
                return CombatDetectPhase.UNCERTAIN
            return CombatDetectPhase.VERIFY_TARGET

        if combat_detect.value is None:
            return CombatDetectPhase.IN_COMBAT

        policy = self.combat_detect_policy
        self.combat_detect_state.miss_count += 1
        if self.combat_detect_state.miss_count < policy.miss_required:
            return CombatDetectPhase.IN_COMBAT

        self.log_info("CombatDetect UNCERTAIN")
        self.combat_detect_state.uncertain_until = now + policy.uncertain_seconds
        self.middle_click()
        return CombatDetectPhase.UNCERTAIN

    def _log_combat_detect_hit(self, source: str):
        self.log_debug_gated(f"CombatDetect hit: source={source}", interval=1, changed=True)

    def _set_in_combat(self, source: str):
        self._log_combat_detect_hit(source)
        return self.scene.set_in_combat()

    def in_combat(self):
        if self.in_animation:
            self._log_combat_detect_hit("in_animation")
            return True
        if self._in_combat:
            return self._check_active_combat()
        return self._try_enter_combat()

    def _check_active_combat(self):
        if self.scene.in_combat() is not None:
            self._log_combat_detect_hit(f"scene_cache={self.scene.in_combat()}")
            return self.scene.in_combat()

        current_char = self.get_current_char(raise_exception=True)
        if current_char.skip_combat_check():
            return self._set_in_combat(f"skip_combat_check:{current_char}")

        if not self.on_combat_check():
            self.log_info("on_combat_check failed")
            return self.reset_to_false()

        if self.is_boss():
            self._boss_fight = True
            self.combat_detect_state.reset()
            return self._set_in_combat("boss")
        elif self._boss_fight:
            return self._recover_or_end_combat()

        if self.combat_end_condition is not None and self.combat_end_condition():
            return self.reset_to_false()

        combat_detect = (
            CombatDetectResult(self.combat_detect(), "sync")
            if self.combat_detect_policy.sync
            else self.async_combat_detect()
        )
        combat_phase = self._update_combat_detect_state(combat_detect)
        if combat_phase is CombatDetectPhase.IN_COMBAT:
            return self._set_in_combat(combat_detect.source)
        if combat_phase is CombatDetectPhase.UNCERTAIN:
            return self.scene.set_in_combat()

        return self._recover_or_end_combat()

    def _recover_or_end_combat(self):
        if self.target_enemy(wait=True, turn=self._turn_on_retarget):
            self.combat_detect_state.reset()
            self.find_lv_future = None
            self._lv_async = None
            self.openvino_clear_cache()
            logger.debug("retarget enemy succeeded")
            return self._set_in_combat("retarget_enemy")
        if self.check_monthly_card() and self.handle_monthly_card():
            return self._set_in_combat("monthly_card")
        logger.error("target_enemy failed, try recheck break out of combat")
        return self.reset_to_false()

    def _try_enter_combat(self):
        from src.tasks.trigger.AutoCombatTask import AutoCombatTask

        @cache
        def has_target():
            return self.find_target()

        @cache
        def has_lv():
            return bool(self.find_lv())

        @cache
        def has_health_bar():
            return self.has_health_bar()

        @cache
        def is_boss():
            return self.is_boss()

        skip_target = not self.openvino_available
        is_auto = self.config.get("自动目标") or not isinstance(self, AutoCombatTask)
        has_combat_signal = is_boss() or has_lv() or has_health_bar()
        has_target_signal = is_auto or has_target() or skip_target

        if not (has_combat_signal and has_target_signal):
            return None

        if is_boss():
            self.middle_click()
        elif not self.target_enemy(wait=True, lv=skip_target):
            return False

        self._in_combat = self.load_chars()
        return self._in_combat

    def combat_detect(self, frame=None, target=True, lv=True, force=False):
        if lv and self.find_lv(frame=frame):
            return True
        if target and self.find_target(frame=frame, sync=True, force=force):
            return True
        return False

    def find_target(self, sync=False, frame=None, force=False) -> Box | None | bool:
        if frame is None:
            frame = self.frame
        box = self.box_of_screen(0.0840, 0.1326, 0.9030, 0.8694)
        result = self.openvino_detect(
            frame=frame,
            sync=sync,
            box=box,
            threshold=0.65,
            force=force,
            mask_regions=self._TARGET_MASK_REGIONS,
        )

        if not result:
            return result

        max_conf = max(result, key=lambda x: x.confidence)
        if max_conf.confidence > 0.8:
            return max_conf

        detect_frame = self.get_last_openvino_image()
        if detect_frame is None:
            return result

        target = False
        for box in result:
            if not isinstance(box, Box):
                continue

            cropped = box.crop_frame(detect_frame)
            # 使用自适应亮度二值化提取可能的亮色 UI 标记
            mask = iu.binarize_bgr_by_adaptive_brightness(cropped, offset=20, to_bgr=False)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if not contours:
                continue

            cnt = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(cnt)
            if area < 10:  # 过滤极小的噪点
                continue

            _, _, w, h = cv2.boundingRect(cnt)
            aspect_ratio = w / float(max(h, 1))
            extent = area / float(max(w * h, 1))

            # 正菱形的宽高比应接近 1，面积填充率应接近 0.5
            if 0.75 < aspect_ratio < 1.33 and 0.35 < extent < 0.65:
                target = True
                break

        if not target:
            self.log_info("find_target is false cause contour analysis failed")

        return target

    def find_lv_async(self, frame=None, force=False):
        ret = self._lv_async
        if not force and self.find_lv_future is not None:
            return self._lv_async

        if self.find_lv_future is not None:
            old_future = self.find_lv_future
            self.find_lv_future = None
            old_future.cancel()
        if frame is None:
            frame = self.frame
        now = self.clock.now()
        self._find_lv_async_started_at = now
        self.find_lv_future = self.thread_pool_executor.submit(self.find_lv, frame=frame)

        def callback(f):
            self._find_lv_latency = self.clock.now() - now
            if self.find_lv_future is not f:
                return
            try:
                self._lv_async = bool(f.result())
            except CancelledError:
                return
            except Exception as e:
                logger.error("find_lv_async failed", e)
                self._lv_async = None

            if self.find_lv_future is f:
                self.find_lv_future = None

        self.find_lv_future.add_done_callback(callback)
        # latency = self._find_lv_latency if ret is not None else -1
        # logger.debug(
        #     f"find_lv: sync False, result {ret}, cost {latency:.3f}s"
        # )
        return ret

    def async_combat_detect(self, target=True, lv=True, exhaustive=False, force=False):
        lv_ret = None
        target_ret = None
        frame = self.frame

        if lv:
            lv_ret = self.find_lv_async(frame=frame, force=force)
            if lv_ret:
                return CombatDetectResult(
                    True,
                    "lv",
                    lv_ret=lv_ret,
                    exhaustive=exhaustive,
                    force=force,
                )

        is_lv_false = not lv or lv_ret is False

        if target and (exhaustive or is_lv_false):
            target_ret = self.find_target(frame=frame, force=force)
            if target_ret:
                return CombatDetectResult(
                    True,
                    "target",
                    lv_ret=lv_ret,
                    target_ret=bool(target_ret),
                    exhaustive=exhaustive,
                    force=force,
                )

        target_pending = target and (exhaustive or is_lv_false) and target_ret is None
        if lv_ret is None or target_pending:
            result = CombatDetectResult(
                None,
                "pending",
                lv_ret=lv_ret,
                target_ret=target_ret,
                target_pending=target_pending,
                exhaustive=exhaustive,
                force=force,
            )
            # self._log_async_combat_detect_pending(result)
            return result

        return CombatDetectResult(
            False,
            "miss",
            lv_ret=lv_ret,
            target_ret=bool(target_ret),
            target_pending=target_pending,
            exhaustive=exhaustive,
            force=force,
        )

    def _log_async_combat_detect_pending(self, result: CombatDetectResult):
        now = self.clock.now()
        self.log_warning_gated(
            "CombatDetect pending None: "
            f"lv_ret={result.lv_ret}, lv_future={self._lv_future_debug_state(now)}, "
            f"target_pending={result.target_pending}, target_ret={result.target_ret}, "
            f"exhaustive={result.exhaustive}, force={result.force}, "
            f"{self._openvino_debug_state()}",
            interval=1,
        )

    def _lv_future_debug_state(self, now: TimePoint):
        future = self.find_lv_future
        if future is None:
            return f"none(latency={self._find_lv_latency:.3f})"
        if future.cancelled():
            state = "cancelled"
        elif future.running():
            state = "running"
        elif future.done():
            state = "done"
        else:
            state = "pending"
        age = now - self._find_lv_async_started_at if self._find_lv_async_started_at else -1
        return f"{state}(age={age:.3f}, latency={self._find_lv_latency:.3f})"

    def _openvino_debug_state(self):
        try:
            from ok import og

            detector = getattr(getattr(og, "my_app", None), "_openvino_model_async", None)
            if detector is None:
                return "openvino=uninitialized"
            debug_state = getattr(detector, "debug_state", None)
            if callable(debug_state):
                return debug_state()
            return "openvino=debug_unavailable"
        except Exception as e:
            return f"openvino=debug_failed({e})"

    def find_lv(self, frame=None, threshold=0.7):
        lv_templates = self._get_lv_template_features()
        if lv_templates is None:
            return []

        if frame is None:
            frame = self.frame

        box = self.box_of_screen(0.1543, 0, 0.9070, 0.7, name="find_lv")
        self.draw_boxes(boxes=box, color="blue")
        roi = box.crop_frame(frame)
        binary = gf.isolate_lv_to_white(roi)

        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        scale = self.width / 2560.0
        min_area = (15 * scale) ** 2 * 0.8
        max_area = (20 * scale) ** 2 * 1.5

        l_candidates = []
        v_candidates = []

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area_bbox = w * h

            if not (min_area <= area_bbox <= max_area):
                continue

            # 提取实时特征
            solidity, cx, cy = self._extract_shape_fingerprint(cnt, x, y, w, h)
            aspect_ratio = w / float(h)

            # 匹配 L
            if (
                abs(solidity - lv_templates.l_fingerprint[0]) < 0.15
                and abs(cx - lv_templates.l_fingerprint[1]) < 0.15
                and abs(cy - lv_templates.l_fingerprint[2]) < 0.15
            ):
                iou = self._match_contour_iou(lv_templates.l_normalized, cnt, x, y, w, h)
                if (
                    lv_templates.l_aspect * 0.6 < aspect_ratio < lv_templates.l_aspect * 1.5
                    and iou > 0.5
                ):
                    area = cv2.countNonZero(binary[y : y + h, x : x + w])
                    l_candidates.append(
                        {"x": x, "y": y, "w": w, "h": h, "score": iou, "area": area}
                    )

            # 匹配 v
            elif (
                abs(solidity - lv_templates.v_fingerprint[0]) < 0.15
                and abs(cx - lv_templates.v_fingerprint[1]) < 0.15
                and abs(cy - lv_templates.v_fingerprint[2]) < 0.15
            ):
                iou = self._match_contour_iou(lv_templates.v_normalized, cnt, x, y, w, h)
                if (
                    lv_templates.v_aspect * 0.6 < aspect_ratio < lv_templates.v_aspect * 1.5
                    and iou > 0.5
                ):
                    area = cv2.countNonZero(binary[y : y + h, x : x + w])
                    v_candidates.append(
                        {"x": x, "y": y, "w": w, "h": h, "score": iou, "area": area}
                    )

        results: list[Box] = []
        for l_shape in l_candidates:
            best_v = None
            min_gap = float("inf")

            for v_shape in v_candidates:
                gap = v_shape["x"] - (l_shape["x"] + l_shape["w"])
                y_diff = abs(v_shape["y"] - l_shape["y"])

                # 逻辑核心：v 在 l_shape 的右侧，距离合理，且 Y 轴大致平齐
                if -(l_shape["w"] * 0.5) <= gap <= (l_shape["h"] * 1.5) and y_diff <= (
                    l_shape["h"] * 0.5
                ):
                    if gap < min_gap:
                        min_gap = gap
                        best_v = v_shape

            if best_v:
                conf = float((l_shape["score"] + best_v["score"]) / 2.0)
                if conf < threshold:
                    continue
                box_x = l_shape["x"]
                box_y = min(l_shape["y"], best_v["y"])
                box_w = (best_v["x"] + best_v["w"]) - l_shape["x"]
                box_h = max(l_shape["y"] + l_shape["h"], best_v["y"] + best_v["h"]) - box_y

                pair_crop = binary[box_y : box_y + box_h, box_x : box_x + box_w]
                pair_area = cv2.countNonZero(pair_crop)
                if pair_area <= 0 or (l_shape["area"] + best_v["area"]) / pair_area < 0.82:
                    continue

                results.append(
                    Box(
                        x=int(box.x + box_x),
                        y=int(box.y + box_y),
                        width=int(box_w),
                        height=int(box_h),
                        confidence=conf,
                        name="lv",
                    )
                )
        if results:
            self.draw_boxes(Labels.lv, results, color="red")
            # self.screenshot("lv", frame, True)
        return results

    def _extract_shape_fingerprint(self, cnt, x, y, w, h):
        """提取形状的物理指纹：填充率和相对重心位置"""
        m = cv2.moments(cnt)
        if m["m00"] == 0:
            return 0.0, 0.5, 0.5
        solidity = cv2.contourArea(cnt) / float(w * h)
        cx = (m["m10"] / m["m00"] - x) / float(w)
        cy = (m["m01"] / m["m00"] - y) / float(h)
        return solidity, cx, cy

    def _render_contour_normalized(self, cnt, x, y, w, h):
        """将轮廓渲染到归一化尺寸的二值图上"""
        sz = self._LV_NORM_SIZE
        img = np.zeros((sz, sz), dtype=np.uint8)
        shifted = cnt.copy()
        shifted[:, :, 0] = ((cnt[:, :, 0] - x) * (sz - 1) / max(w - 1, 1)).astype(np.int32)
        shifted[:, :, 1] = ((cnt[:, :, 1] - y) * (sz - 1) / max(h - 1, 1)).astype(np.int32)
        cv2.drawContours(img, [shifted], -1, 255, cv2.FILLED)
        return img

    def _match_contour_iou(self, tpl_norm, cnt, x, y, w, h):
        """计算归一化二值图的 IoU 作为形状相似度"""
        cand = self._render_contour_normalized(cnt, x, y, w, h)
        intersection = cv2.countNonZero(cv2.bitwise_and(tpl_norm, cand))
        union = cv2.countNonZero(cv2.bitwise_or(tpl_norm, cand))
        return intersection / union if union > 0 else 0.0

    def _build_lv_template_features(self):
        tpl_img = self.get_feature_by_name(Labels.lv).mat
        tpl_bin = gf.isolate_lv_to_white(tpl_img)
        contours, _ = cv2.findContours(tpl_bin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_cnts = [
            c for c in contours if cv2.boundingRect(c)[2] > 2 and cv2.boundingRect(c)[3] > 2
        ]
        valid_cnts.sort(key=lambda c: cv2.boundingRect(c)[0])
        if len(valid_cnts) < 2:
            self.log_error(f"[LV-Init] 模板切割失败，仅找到 {len(valid_cnts)} 个轮廓")
            return None

        l_contour, v_contour = valid_cnts[:2]
        xl, yl, wl, hl = cv2.boundingRect(l_contour)
        xv, yv, wv, hv = cv2.boundingRect(v_contour)
        return LvTemplateFeatures(
            l_fingerprint=self._extract_shape_fingerprint(l_contour, xl, yl, wl, hl),
            l_aspect=wl / float(hl),
            l_normalized=self._render_contour_normalized(l_contour, xl, yl, wl, hl),
            v_fingerprint=self._extract_shape_fingerprint(v_contour, xv, yv, wv, hv),
            v_aspect=wv / float(hv),
            v_normalized=self._render_contour_normalized(v_contour, xv, yv, wv, hv),
        )

    def _get_lv_template_features(self):
        """Return process-wide LV template features for this screen resolution."""
        cache_key = (Labels.lv, self.width, self.height)
        return get_visual_template_cache().get_or_build(cache_key, self._build_lv_template_features)


enemy_health_hsv = iu.HSVRange((0, 190, 175), (179, 255, 255))

enemy_health_color_red = {
    "r": (210, 255),
    "g": (20, 80),
    "b": (20, 100),
}

boss_health_color = {
    "r": (215, 240),
    "g": (30, 60),
    "b": (50, 75),
}


def merge_images_vertically(img_list, bg_color=(255, 255, 255)):
    # 1. 找到所有图片中的最大宽度
    max_width = max(img.shape[1] for img in img_list)

    processed_imgs = []
    for img in img_list:
        _, w = img.shape[:2]
        if w < max_width:
            # 计算需要填充的宽度
            pad_width = max_width - w
            # 使用 cv2.copyMakeBorder 进行填充 (常数填充)
            # 这里的 bg_color 如果是灰度图传一个值(0)，如果是彩色传 (0,0,0)
            img = cv2.copyMakeBorder(img, 0, 0, 0, pad_width, cv2.BORDER_CONSTANT, value=bg_color)
        processed_imgs.append(img)

    # 2. 垂直合并
    return cv2.vconcat(processed_imgs)
