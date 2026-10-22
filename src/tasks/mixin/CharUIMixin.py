from dataclasses import dataclass
from types import MappingProxyType

import cv2
import numpy as np
from ok import BaseTask, Box, get_path_relative_to_exe

from src.char.BaseChar import Element
from src.Labels import Labels
from src.utils import clock
from src.utils import image_utils as iu
from src.utils.current_char_detector import (
    CurrentCharConfig,
    CurrentCharDetection,
    build_current_char_scores,
    detect_current_char,
    normalize_char_count,
)
from src.utils.visual_template_cache import get_visual_template_cache


@dataclass(frozen=True)
class ElementTemplate:
    image: np.ndarray
    mask: np.ndarray
    color_signature: tuple[float, float, float] | None

    def __post_init__(self):
        self.image.setflags(write=False)
        self.mask.setflags(write=False)


class CharUIMixin(BaseTask):
    clock = clock

    _CURRENT_CHAR = CurrentCharConfig()

    def _init_char_ui_state(self):
        self._char_ui_offset = False
        self._current_char_tracker = {
            "index": -1,
            "score": 1.0,
            "time": self.clock.UNSET,
            "reason": "",
        }

    def _get_char_text_box(self, index: int):
        box = self.get_box_by_name(f"char_{index + 1}_text")
        return box

    def get_base_char_element_box(self):
        box = self.box_of_screen_scaled(
            2560, 1440, 2438, 335, width_original=29, height_original=29, name="element"
        )
        box = self._shift_char_ui_box(box, expend=True)
        return box

    def _shift_char_ui_box(self, box: Box, expend=False):
        offset = -9 * self.width / 2560
        width_offset = 0
        if expend:
            width_offset = -offset
        box = box.copy(x_offset=offset, width_offset=width_offset)
        return box

    @property
    def _char_vertical_spacing(self):
        return int(self.height * 176 / 1440)

    def get_box_by_char_spacing(self, box: Box, index: int):
        return box.copy(y_offset=index * self._char_vertical_spacing, name=f"{box.name}_{index}")

    def _get_current_char_template(self):
        if (
            not hasattr(self, "_char_template_cache")
            or self._char_template_cache.get("width") != self.width
            or self._char_template_cache.get("height") != self.height
        ):
            feature = self.get_feature_by_name(Labels.is_current_char)
            self._char_template_cache = {
                "width": self.width,
                "height": self.height,
                "mat": feature.mat,
            }

        return self._char_template_cache["mat"]

    def _build_current_char_scores(self, index, score, accepted):
        return build_current_char_scores(index, score, accepted, config=self._CURRENT_CHAR)

    def _get_current_char_boxes(self):
        base_box = self.get_box_by_name(Labels.is_current_char)
        base_box = self._shift_char_ui_box(base_box, expend=True)
        return base_box, [self.get_box_by_char_spacing(base_box, i) for i in range(4)]

    def _detect_current_char_once(self, frame=None, char_count=None):
        candidate_count = normalize_char_count(char_count)
        if frame is None:
            frame = self.frame
        if frame is None or candidate_count <= 0:
            return CurrentCharDetection(
                index=-1,
                score=1.0,
                scores=[self._CURRENT_CHAR.reject_score] * 4,
                accepted=False,
                strong=False,
                reason="empty_frame" if frame is None else "empty_char_count",
                active_scores=[0.0] * 4,
            )

        _, boxes = self._get_current_char_boxes()
        slot_images = [box.crop_frame(frame) for box in boxes[:candidate_count]]
        detection = detect_current_char(
            slot_images=slot_images,
            template_mat=self._get_current_char_template(),
            char_count=candidate_count,
            config=self._CURRENT_CHAR,
        )

        if 0 <= detection.index < len(boxes):
            self.draw_boxes(boxes=boxes[detection.index], color="red")

        return detection

    def _apply_current_char_tracker(self, detection: CurrentCharDetection, char_count=None):
        now = self.clock.now()
        tracker = self._current_char_tracker
        candidate_count = normalize_char_count(char_count)
        if detection.accepted:
            tracker["index"] = detection.index
            tracker["score"] = detection.score
            tracker["time"] = now
            tracker["reason"] = detection.reason
            return detection

        if (
            tracker["index"] != -1
            and tracker["index"] < candidate_count
            and now - tracker["time"] <= self._CURRENT_CHAR.sticky_seconds
        ):
            index = tracker["index"]
            score = max(tracker["score"], self._CURRENT_CHAR.accept_score)
            scores = self._build_current_char_scores(index, score, accepted=True)
            return CurrentCharDetection(
                index=index,
                score=scores[index],
                scores=scores,
                accepted=True,
                strong=False,
                reason=f"sticky:{tracker['reason']}",
                active_scores=detection.active_scores,
            )

        return detection

    def _get_current_char_detection(self, frame=None, char_count=None):
        detection = self._detect_current_char_once(frame=frame, char_count=char_count)
        if frame is None:
            return self._apply_current_char_tracker(detection, char_count=char_count)
        return detection

    def _get_char_match_scores(self, frame=None, char_count=None):
        """Return four slot scores; lower means the slot is the current char."""
        return self._get_current_char_detection(frame=frame, char_count=char_count).scores

    def get_current_char_index(self, char_count=None):
        # frame = self.frame
        detection = self._get_current_char_detection(char_count=char_count)
        if detection.accepted:
            self.log_debug(
                f"current_char found at {detection.index} "
                f"with score {detection.score:.4f} ({detection.reason})"
            )
            # if detection.score > 0.5:
            #     self.screenshot("low_conf", frame)
            return detection.index

        self.log_debug(
            f"current_char rejected ({detection.reason}) active={detection.active_scores}"
        )
        return -1

    def _multi_stage_char_match(self):
        results = [None, None, None, None]
        contrast_steps = [0, 30, 60, 90]

        for c_val in contrast_steps:
            if all(res is not None for res in results):
                break

            for i in range(4):
                if results[i] is not None:
                    continue

                def process(image, current_c=c_val):
                    return iu.adjust_lightness_contrast_lab(image, brightness=0, contrast=current_c)

                res = self.find_one(
                    f"char_{i + 1}_text",
                    threshold=0.7,
                    frame_processor=process,
                    mask_function=iu.mask_outside_white_rect,
                    horizontal_variance=0.005,
                )
                if res:
                    results[i] = res

        return results

    def _update_char_ui_offset(self):
        # now = self.clock.now()
        arr = self._multi_stage_char_match()
        results = [
            c.x < self._get_char_text_box(idx).x for idx, c in enumerate(arr) if c is not None
        ]

        if results:
            self._char_ui_offset = sum(results) > (len(results) / 2)
        else:
            self._char_ui_offset = False
        # logger.debug(f"update_char_ui_offset cost {self.clock.now() - now:.3f}")
        return arr


class CharElementUIMixin(CharUIMixin):
    _ELEMENT_TEMPLATE_CACHE_KEY = "element_templates"
    _element_match_weight = 0.6
    _element_color_weight = 0.4
    _element_min_score = 0.45

    @staticmethod
    def _process_template_transparency(img):
        if img is None:
            return None
        if len(img.shape) == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if img.shape[2] == 4:
            b, g, r, a = cv2.split(img)
            black_bg = np.zeros_like(img[:, :, :3])
            alpha_factor = a.astype(float) / 255.0
            alpha_factor = cv2.merge([alpha_factor, alpha_factor, alpha_factor])

            foreground = cv2.merge([b, g, r]).astype(float)
            background = black_bg.astype(float)

            final_img = cv2.add(
                cv2.multiply(foreground, alpha_factor),
                cv2.multiply(background, 1.0 - alpha_factor),
            )
            return final_img.astype(np.uint8)
        return img

    @staticmethod
    def _preprocess_element_template_image(image):
        return iu.binarize_bgr_by_adaptive_center(image)

    @staticmethod
    def _get_element_color_signature(image):
        if image is None or image.size == 0:
            return None

        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        value = hsv[:, :, 2]
        visible = value > 25
        if not np.any(visible):
            return None

        visible_values = value[visible]
        threshold = max(45, float(np.percentile(visible_values, 70)))
        foreground = visible & (value >= threshold)
        min_pixels = max(12, int(image.shape[0] * image.shape[1] * 0.03))
        if np.count_nonzero(foreground) < min_pixels:
            threshold = max(35, float(np.percentile(visible_values, 55)))
            foreground = visible & (value >= threshold)
        if np.count_nonzero(foreground) < min_pixels:
            foreground = visible

        samples = hsv[foreground].astype(np.float32)
        return tuple(float(v) for v in np.median(samples, axis=0))

    @staticmethod
    def _element_color_similarity(signature, template_signature):
        if signature is None or template_signature is None:
            return 0.0

        hue_delta = abs(signature[0] - template_signature[0])
        hue_delta = min(hue_delta, 180 - hue_delta) / 90
        saturation_delta = abs(signature[1] - template_signature[1]) / 255
        value_delta = abs(signature[2] - template_signature[2]) / 255
        if signature[1] < 80 or template_signature[1] < 80:
            distance = hue_delta * 0.1 + saturation_delta * 0.85 + value_delta * 0.05
        else:
            distance = hue_delta * 0.45 + saturation_delta * 0.45 + value_delta * 0.1
        return max(0.0, 1.0 - min(distance, 1.0))

    @classmethod
    def _load_element_template(cls, element):
        path = get_path_relative_to_exe("assets", "esper_icons", f"{element.value}.png")
        raw_template = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if raw_template is None:
            return None

        h, w = raw_template.shape[:2]
        raw_template = cls._process_template_transparency(raw_template)
        if raw_template is None:
            return None

        element_scale = 0.5
        raw_template = cv2.resize(
            raw_template,
            (int(w * element_scale), int(h * element_scale)),
            interpolation=cv2.INTER_NEAREST,
        )
        template_bin = cls._preprocess_element_template_image(raw_template)
        _, mask = cv2.threshold(template_bin, 127, 255, cv2.THRESH_BINARY)
        kernel = np.ones((30, 30), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)
        return ElementTemplate(
            image=raw_template,
            mask=mask,
            color_signature=cls._get_element_color_signature(raw_template),
        )

    @classmethod
    def _build_element_templates(cls):
        templates = {}
        for element in cls.element_ring:
            template_data = cls._load_element_template(element)
            if template_data is not None:
                templates[element] = template_data
        return MappingProxyType(templates) if templates else None

    @classmethod
    def _get_element_templates(cls):
        template_cache = get_visual_template_cache().get_or_build(
            cls._ELEMENT_TEMPLATE_CACHE_KEY, cls._build_element_templates
        )
        return template_cache if template_cache is not None else MappingProxyType({})

    @classmethod
    def preheat_element_template_cache_async(cls):
        get_visual_template_cache().preheat_async(
            cls._ELEMENT_TEMPLATE_CACHE_KEY,
            cls._build_element_templates,
            thread_name="element-template-cache-preheat",
        )

    def load_chars_element(self, indices: list[int]) -> dict:
        results = {}
        template_cache = self._get_element_templates()

        base_box = self.get_base_char_element_box()

        _frame = self.frame
        # self.screenshot("load_chars_element", _frame)

        for i in indices:
            base_scale = 8
            scale = base_scale * 1440 / self.height
            current_box = self.get_box_by_char_spacing(base_box, i)
            crop_img = current_box.crop_frame(_frame)
            crop_h, crop_w = crop_img.shape[:2]
            crop_resized = cv2.resize(
                crop_img,
                (int(crop_w * scale), int(crop_h * scale)),
                interpolation=cv2.INTER_NEAREST,
            )
            crop_color_signature = self._get_element_color_signature(crop_resized)
            # iu.show_images([crop_resized, crop_img], [f"crop_resized_{i}", f"crop_img_{i}"])

            best_element = Element.DEFAULT
            max_score = -1.0
            best_template_score = 0.0
            best_color_score = 0.0

            for element in self.element_ring:
                template_data = template_cache.get(element)
                if template_data is None:
                    continue
                template_img = template_data.image
                template_mask = template_data.mask

                match_score = 0
                color_score = 0.0
                if (
                    crop_resized is not None
                    and template_img is not None
                    and crop_resized.shape[0] >= template_img.shape[0]
                    and crop_resized.shape[1] >= template_img.shape[1]
                ):
                    res = cv2.matchTemplate(
                        crop_resized, template_img, cv2.TM_CCOEFF_NORMED, mask=template_mask
                    )
                    np.nan_to_num(res, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
                    _, match_score, _, _ = cv2.minMaxLoc(res)
                    color_score = self._element_color_similarity(
                        crop_color_signature, template_data.color_signature
                    )

                combined_score = (
                    match_score * self._element_match_weight
                    + color_score * self._element_color_weight
                )
                if combined_score > max_score:
                    max_score = combined_score
                    best_template_score = match_score
                    best_color_score = color_score
                    best_element = element

            if max_score < self._element_min_score:
                best_element = Element.DEFAULT

            current_box.confidence = max_score
            current_box.name = f"char_{i}_" + best_element.name
            results[i] = best_element
            self.draw_boxes(boxes=current_box, color="red")
            self.log_debug(
                f"char_{i + 1} identified as {best_element.name} "
                f"(score: {max_score:.4f}, template: {best_template_score:.4f}, "
                f"color: {best_color_score:.4f})"
            )

        return results
