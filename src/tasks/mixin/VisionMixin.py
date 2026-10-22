import math
from typing import Callable, NamedTuple

import cv2
import numpy as np
from ok import BaseTask, Box
from ok.feature.Feature import Feature
from ok.feature.FeatureSet import read_from_json

from src.Labels import Labels
from src.utils import clock


class RotatedTemplateCacheKey(NamedTuple):
    cache_id: object
    mask_shape: tuple[int, ...]
    mask_non_zero: int
    mask_hash: int
    angles: tuple[int, ...]
    min_non_zero: int


type RotatedTemplateCacheValue = list[tuple[int, np.ndarray]]


class SiftTemplateCacheKey(NamedTuple):
    feature_name: object
    template_shape: tuple[int, ...]
    template_dtype: str
    template_hash: int
    nfeatures: int


type SiftTemplateCacheValue = tuple[tuple[cv2.KeyPoint, ...], np.ndarray | None]


class OriginalFeatureCacheKey(NamedTuple):
    coco_json: str
    feature_name: object


class SiftAttempt(NamedTuple):
    name: str
    scene_scale: float
    ratio: float
    min_match_count: int


SIFT_HOMOGRAPHY_MIN_MATCH_COUNT = 4


class VisionMixin(BaseTask):
    clock = clock

    # cache_id/mask fingerprint/angle options -> [(angle, rotated mask), ...]
    _rotated_template_cache: dict[RotatedTemplateCacheKey, RotatedTemplateCacheValue] = {}

    # feature/template fingerprint/SIFT options -> (template keypoints, template descriptors)
    _sift_template_cache: dict[SiftTemplateCacheKey, SiftTemplateCacheValue] = {}

    # coco json path/feature name -> original, unscaled feature
    _original_feature_cache: dict[OriginalFeatureCacheKey, Feature] = {}

    def find_sift_feature(
        self,
        feature_name,
        box: Box | str | None = None,
        frame: np.ndarray | None = None,
        threshold=0.5,
        min_match_count=8,
        ratio=0.75,
        nfeatures=0,
        small_target_retry=True,
        small_target_scene_scale=2.0,
        small_target_min_match_count=8,
        small_target_ratio=0.75,
        trim_template=-1.0,
    ) -> Box | None:
        """
        使用 SIFT 在指定区域内查找原始 COCO crop 特征，适合模板和画面存在缩放差异的场景。

        :param feature_name: ok feature 名称。
        :param box: 搜索区域；None 表示整张图，也可传 box 名称。
        :param frame: 目标画面；None 时使用当前帧。
        :param threshold: RANSAC 内点比例阈值，越高越严格。
        :param min_match_count: 通过 Lowe ratio 过滤后的最少匹配点数量。
        :param ratio: Lowe ratio test 阈值，越低越严格。
        :param nfeatures: 传给 cv2.SIFT_create 的最大特征数，0 表示不限。
        :param small_target_retry: 原尺寸失败后，放大搜索区域再尝试一次，适合小图标。
        :param small_target_scene_scale: 小目标重试时搜索区域的放大倍率。
        :param small_target_min_match_count: 小目标重试时的最少匹配点数量。
        :param small_target_ratio: 小目标重试时的 Lowe ratio 阈值。
        :param trim_template: >= 0 时按该比例裁掉模板外圈背景；-1 表示不裁剪。
        :return: 匹配到的 Box；未找到时返回 None。Box 上额外带有 scale/match_count/inlier_count。
        """
        start_time = self.clock.now()
        diagnostics = {}

        def finish(result: Box | None, reason=None):
            cost_ms = (self.clock.now() - start_time) * 1000
            if cost_ms > 100:
                detail = " ".join(f"{key}={value}" for key, value in diagnostics.items())
                if result is None:
                    self.log_debug(
                        f"find_sift_feature {feature_name} not found"
                        f" reason={reason} {detail} cost={cost_ms:.2f}ms"
                    )
                else:
                    self.log_debug(
                        f"find_sift_feature {feature_name} found {result}"
                        f" attempt={getattr(result, 'sift_attempt', None)}"
                        f" scale={getattr(result, 'scale', None)}"
                        f" scene_scale={getattr(result, 'sift_scene_scale', None)}"
                        f" matches={getattr(result, 'match_count', None)}"
                        f" inliers={getattr(result, 'inlier_count', None)}"
                        f" cost={cost_ms:.2f}ms"
                    )
            return result

        frame = self.frame if frame is None else frame
        if frame is None:
            return finish(None, "empty_frame")

        if isinstance(box, str):
            box = self.get_box_by_name(box)

        search_x, search_y = 0, 0
        if box is None:
            scene = frame
        else:
            search_x, search_y = box.x, box.y
            scene = box.crop_frame(frame)
            if isinstance(feature_name, Labels):
                feature_name = feature_name.name
            box.name = "search_" + feature_name
            self.draw_boxes(boxes=box, color="blue")

        if scene is None or scene.size == 0:
            return finish(None, "empty_scene")

        feature = self.get_original_feature_by_name(feature_name)
        if feature is None:
            return finish(None, "missing_original_template")

        template = feature.mat
        if trim_template >= 0:
            template = self._trim_sift_template(template, padding=trim_template)
        diagnostics["template_shape"] = template.shape[:2]
        diagnostics["scene_shape"] = scene.shape[:2]
        sift = cv2.SIFT_create(nfeatures=nfeatures)
        template_keypoints, template_descriptors = self._get_sift_template_data(
            feature_name, template, sift, nfeatures
        )

        def build_attempts():
            normal_min_match_count = max(min_match_count, SIFT_HOMOGRAPHY_MIN_MATCH_COUNT)
            attempts = [SiftAttempt("normal", 1.0, ratio, normal_min_match_count)]
            if (
                small_target_retry
                and not math.isclose(small_target_scene_scale, 0.0)
                and not math.isclose(small_target_scene_scale, 1.0)
            ):
                retry_min_match_count = max(
                    min(min_match_count, small_target_min_match_count),
                    SIFT_HOMOGRAPHY_MIN_MATCH_COUNT,
                )
                attempts.append(
                    SiftAttempt(
                        "small_target",
                        small_target_scene_scale,
                        max(ratio, small_target_ratio),
                        retry_min_match_count,
                    )
                )
            return attempts

        attempts = build_attempts()

        required_template_keypoints = min(attempt.min_match_count for attempt in attempts)
        if template_descriptors is None or len(template_keypoints) < required_template_keypoints:
            return finish(
                None,
                f"not_enough_template_keypoints "
                f"template_kp={len(template_keypoints)} required={required_template_keypoints}",
            )
        diagnostics["template_kp"] = len(template_keypoints)

        matcher = cv2.BFMatcher(cv2.NORM_L2)
        template_height, template_width = template.shape[:2]
        corners = np.float32(
            [[0, 0], [template_width, 0], [template_width, template_height], [0, template_height]]
        ).reshape(-1, 1, 2)

        def _get_attempt_scene(attempt: SiftAttempt):
            scene_for_sift = scene
            if not math.isclose(attempt.scene_scale, 1.0):
                scene_for_sift = cv2.resize(
                    scene,
                    None,
                    fx=attempt.scene_scale,
                    fy=attempt.scene_scale,
                    interpolation=cv2.INTER_CUBIC,
                )
            return scene_for_sift

        def _get_good_matches(raw_matches, attempt: SiftAttempt):
            good_matches = []
            for candidates in raw_matches:
                if len(candidates) < 2:
                    continue
                first, second = candidates
                if first.distance < attempt.ratio * second.distance:
                    good_matches.append(first)
            return good_matches

        def _make_box_from_homography(homography, attempt: SiftAttempt):
            projected = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
            if not math.isclose(attempt.scene_scale, 1.0):
                projected = projected / attempt.scene_scale
            if not np.isfinite(projected).all():
                return None, f"{attempt.name}:invalid_projection"

            x, y, width, height = cv2.boundingRect(projected.astype(np.float32))
            if width <= 0 or height <= 0:
                return None, f"{attempt.name}:invalid_projected_box"

            scene_height, scene_width = scene.shape[:2]
            x = max(0, min(x, scene_width - 1))
            y = max(0, min(y, scene_height - 1))
            width = min(width, scene_width - x)
            height = min(height, scene_height - y)
            if width <= 0 or height <= 0:
                return None, f"{attempt.name}:projected_box_outside_scene"

            matched_box = Box(
                x + search_x,
                y + search_y,
                width,
                height,
                name=feature_name,
            )
            matched_box.scale = round(
                (width * height / (template_width * template_height)) ** 0.5, 3
            )
            matched_box.sift_attempt = attempt.name
            matched_box.sift_scene_scale = attempt.scene_scale
            return matched_box, None

        def match_attempt(attempt: SiftAttempt):
            scene_for_sift = _get_attempt_scene(attempt)
            scene_gray = self._to_sift_gray(scene_for_sift)
            scene_keypoints, scene_descriptors = sift.detectAndCompute(scene_gray, None)
            diagnostics[f"{attempt.name}_scene_kp"] = len(scene_keypoints)
            if scene_descriptors is None or len(scene_keypoints) < attempt.min_match_count:
                return (
                    None,
                    f"{attempt.name}:not_enough_scene_keypoints "
                    f"scene_kp={len(scene_keypoints)} required={attempt.min_match_count}",
                )

            raw_matches = matcher.knnMatch(template_descriptors, scene_descriptors, k=2)
            good_matches = _get_good_matches(raw_matches, attempt)

            diagnostics[f"{attempt.name}_raw"] = len(raw_matches)
            diagnostics[f"{attempt.name}_good"] = len(good_matches)
            if len(good_matches) < attempt.min_match_count:
                return (
                    None,
                    f"{attempt.name}:not_enough_good_matches "
                    f"good={len(good_matches)} required={attempt.min_match_count}",
                )

            template_points = np.float32(
                [template_keypoints[match.queryIdx].pt for match in good_matches]
            ).reshape(-1, 1, 2)
            scene_points = np.float32(
                [scene_keypoints[match.trainIdx].pt for match in good_matches]
            ).reshape(-1, 1, 2)

            homography, inlier_mask = cv2.findHomography(
                template_points, scene_points, cv2.RANSAC, 5.0 * attempt.scene_scale
            )
            if homography is None or inlier_mask is None:
                return None, f"{attempt.name}:homography_failed good={len(good_matches)}"

            inlier_count = int(inlier_mask.ravel().sum())
            confidence = inlier_count / len(good_matches)
            diagnostics[f"{attempt.name}_inliers"] = inlier_count
            diagnostics[f"{attempt.name}_confidence"] = round(confidence, 3)
            if inlier_count < attempt.min_match_count or confidence < threshold:
                return (
                    None,
                    f"{attempt.name}:low_inlier_confidence "
                    f"inliers={inlier_count} good={len(good_matches)} conf={confidence:.3f}",
                )

            matched_box, reason = _make_box_from_homography(homography, attempt)
            if matched_box is None:
                return None, reason
            matched_box.confidence = round(confidence, 3)
            matched_box.match_count = len(good_matches)
            matched_box.inlier_count = inlier_count
            return matched_box, None

        last_reason = "no_attempts"
        for attempt in attempts:
            matched_box, reason = match_attempt(attempt)
            if matched_box is None:
                last_reason = reason
                continue

            self.draw_boxes(boxes=matched_box, color="red")
            return finish(matched_box)

        return finish(None, last_reason)

    def get_original_feature_by_name(self, feature_name) -> Feature | None:
        """读取 COCO 标注中的原始 crop，不按当前游戏分辨率缩放。"""
        feature_set = getattr(getattr(self, "executor", None), "feature_set", None)
        coco_json = getattr(feature_set, "coco_json", None)
        if not coco_json:
            return None

        cache_key = OriginalFeatureCacheKey(coco_json=coco_json, feature_name=feature_name)
        cached = self._original_feature_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            features, _, _, load_success, _ = read_from_json(
                coco_json,
                adjust=False,
                target_category_name=feature_name,
            )
        except Exception as e:
            self.log_debug(f"load original feature {feature_name} failed: {e}")
            return None

        if not load_success:
            return None

        feature = features.get(feature_name)
        if feature is None:
            return None

        self._original_feature_cache[cache_key] = feature
        return feature

    @staticmethod
    def _trim_sift_template(template: np.ndarray, padding=0.04):
        height, width = template.shape[:2]
        if height < 12 or width < 12:
            return template

        padding_px = max(1, round(min(width, height) * padding))
        sample = max(2, min(12, height // 8, width // 8))
        pixels = template[:, :, :3] if template.ndim == 3 else template[:, :, None]
        corners = np.concatenate(
            [
                pixels[:sample, :sample].reshape(-1, pixels.shape[2]),
                pixels[:sample, -sample:].reshape(-1, pixels.shape[2]),
                pixels[-sample:, :sample].reshape(-1, pixels.shape[2]),
                pixels[-sample:, -sample:].reshape(-1, pixels.shape[2]),
            ],
            axis=0,
        )
        background = np.median(corners.astype(np.float32), axis=0)
        diff = np.max(np.abs(pixels.astype(np.float32) - background), axis=2)
        foreground = diff > 18
        if np.count_nonzero(foreground) < 20:
            return template

        ys, xs = np.nonzero(foreground)
        x1 = max(0, int(xs.min()) - padding_px)
        y1 = max(0, int(ys.min()) - padding_px)
        x2 = min(width, int(xs.max()) + padding_px + 1)
        y2 = min(height, int(ys.max()) + padding_px + 1)
        trimmed_width = x2 - x1
        trimmed_height = y2 - y1
        if trimmed_width < 8 or trimmed_height < 8:
            return template
        if trimmed_width > width * 0.95 and trimmed_height > height * 0.95:
            return template
        return template[y1:y2, x1:x2]

    def _get_sift_template_data(self, feature_name, template: np.ndarray, sift, nfeatures=0):
        template_key = SiftTemplateCacheKey(
            feature_name=feature_name,
            template_shape=template.shape,
            template_dtype=template.dtype.str,
            template_hash=hash(template.tobytes()),
            nfeatures=nfeatures,
        )
        cached = self._sift_template_cache.get(template_key)
        if cached is not None:
            return cached

        gray = self._to_sift_gray(template)
        keypoints, descriptors = sift.detectAndCompute(gray, None)
        data = (tuple(keypoints), descriptors)
        self._sift_template_cache[template_key] = data
        return data

    @staticmethod
    def _to_sift_gray(mat: np.ndarray):
        if mat.ndim == 2:
            gray = mat
        elif mat.shape[2] == 4:
            bgr = mat[:, :, :3].copy()
            bgr[mat[:, :, 3] == 0] = 0
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        else:
            gray = cv2.cvtColor(mat[:, :, :3], cv2.COLOR_BGR2GRAY)

        if gray.dtype != np.uint8:
            gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        return gray

    def _find_rotated_template(
        self,
        feature_name,
        scene: np.ndarray,
        threshold=0.75,
        angle_range=range(-180, 180, 2),
        min_non_zero=20,
        template_angle=0,
    ):
        start_time = self.clock.now()
        template = self.get_feature_by_name(feature_name).mat
        scene_mask = self._first_channel_mask(scene)
        if cv2.countNonZero(scene_mask) < min_non_zero:
            return [], (self.clock.now() - start_time) * 1000

        best = None
        for angle, rotated_template in self._get_rotated_templates(
            template,
            angle_range=angle_range,
            min_non_zero=min_non_zero,
            cache_key=feature_name,
        ):
            th, tw = rotated_template.shape[:2]
            if th > scene_mask.shape[0] or tw > scene_mask.shape[1]:
                continue

            result = cv2.matchTemplate(scene_mask, rotated_template, cv2.TM_CCOEFF_NORMED)
            _, score, _, top_left = cv2.minMaxLoc(result)
            if best is None or score > best["score"]:
                best = {
                    "center": (top_left[0] + tw // 2, top_left[1] + th // 2),
                    "angle": self._normalize_angle(angle + template_angle),
                    "match_angle": angle,
                    "score": score,
                }

        if best is None or best["score"] < threshold:
            return [], (self.clock.now() - start_time) * 1000

        best["score"] = round(best["score"], 3)
        return [best], (self.clock.now() - start_time) * 1000

    def find_rotated_template(
        self,
        feature_name,
        box: Box | str,
        frame: np.ndarray | None = None,
        threshold=0.75,
        angle_range=range(-180, 180, 2),
        min_non_zero=20,
        template_angle=0,
        frame_processor: Callable[[np.ndarray], np.ndarray] | None = None,
        tolerance_px=2.0,
        max_distance_px=6.0,
        min_coverage=0.65,
        coverage_weight=0.7,
    ):
        """
        使用旋转模板的 chamfer/distance-transform 匹配查找小型二值形状。

        这个方法比 cv2.TM_CCOEFF_NORMED 更容忍局部遮挡：模板白色像素附近只要在
        ``tolerance_px`` 内有场景白色像素，就会被视为形状轮廓仍然命中。

        :param feature_name: ok feature 名称。
        :param box: 搜索区域，也可传 box 名称。
        :param frame: 目标画面；None 时使用当前帧。
        :param threshold: 综合分数阈值，越高越严格。
        :param angle_range: 模板旋转角度范围。
        :param min_non_zero: 场景/模板的最少非零像素。
        :param template_angle: 模板自身初始角度偏移。
        :param frame_processor: 在 box 裁剪 frame 后执行的预处理函数。
        :param tolerance_px: 模板像素允许偏离场景白色像素的距离。
        :param max_distance_px: 距离惩罚上限。
        :param min_coverage: 模板白色像素的最低近邻覆盖率。
        :param coverage_weight: 覆盖率在综合分数中的权重。
        :return: (results, cost_ms)，results 与 _find_rotated_template 保持同类结构。
        """
        start_time = self.clock.now()
        frame = self.frame if frame is None else frame
        if frame is None:
            return [], (self.clock.now() - start_time) * 1000

        if isinstance(box, str):
            box = self.get_box_by_name(box)

        scene = box.crop_frame(frame)
        box.name = f"search_{feature_name}"
        self.draw_boxes(boxes=box, color="blue")

        if scene is None or scene.size == 0:
            return [], (self.clock.now() - start_time) * 1000

        if frame_processor is not None:
            scene = frame_processor(scene)
            if scene is None or scene.size == 0:
                return [], (self.clock.now() - start_time) * 1000

        scene_mask = self._binary_mask(scene)
        if cv2.countNonZero(scene_mask) < min_non_zero:
            return [], (self.clock.now() - start_time) * 1000

        max_distance_px = max(float(max_distance_px), 1.0)
        tolerance_px = max(float(tolerance_px), 0.0)
        coverage_weight = min(max(float(coverage_weight), 0.0), 1.0)

        scene_distance = cv2.distanceTransform(255 - scene_mask, cv2.DIST_L2, 3)
        scene_distance = np.minimum(scene_distance, max_distance_px).astype(np.float32)
        scene_near = (scene_distance <= tolerance_px).astype(np.float32)

        template = self.get_feature_by_name(feature_name).mat
        best = None
        for angle, rotated_template in self._get_rotated_templates(
            template,
            angle_range=angle_range,
            min_non_zero=min_non_zero,
            cache_key=feature_name,
        ):
            template_mask = self._binary_mask(rotated_template)
            template_non_zero = cv2.countNonZero(template_mask)
            if template_non_zero < min_non_zero:
                continue

            th, tw = template_mask.shape[:2]
            if th > scene_mask.shape[0] or tw > scene_mask.shape[1]:
                continue

            template_float = (template_mask > 0).astype(np.float32)
            distance_sum = cv2.matchTemplate(scene_distance, template_float, cv2.TM_CCORR)
            coverage_sum = cv2.matchTemplate(scene_near, template_float, cv2.TM_CCORR)
            avg_distance = distance_sum / template_non_zero
            coverage = coverage_sum / template_non_zero
            distance_score = 1.0 - (avg_distance / max_distance_px)
            score_map = coverage_weight * coverage + (1.0 - coverage_weight) * distance_score

            _, score, _, top_left = cv2.minMaxLoc(score_map)
            candidate_coverage = float(coverage[top_left[1], top_left[0]])
            candidate_distance_score = float(distance_score[top_left[1], top_left[0]])
            if best is None or score > best["score"]:
                matched_box = Box(
                    box.x + top_left[0],
                    box.y + top_left[1],
                    tw,
                    th,
                    name=feature_name,
                )
                best = {
                    "center": (top_left[0] + tw // 2, top_left[1] + th // 2),
                    "angle": self._normalize_angle(angle + template_angle),
                    "match_angle": angle,
                    "score": float(score),
                    "coverage": candidate_coverage,
                    "distance_score": candidate_distance_score,
                    "box": matched_box,
                }

        if best is None or best["score"] < threshold or best["coverage"] < min_coverage:
            return [], (self.clock.now() - start_time) * 1000

        self.draw_boxes(boxes=best["box"], color="red")
        best["score"] = round(best["score"], 3)
        best["coverage"] = round(best["coverage"], 3)
        best["distance_score"] = round(best["distance_score"], 3)
        return [best], (self.clock.now() - start_time) * 1000

    def _get_rotated_templates(
        self,
        template: np.ndarray,
        angle_range=range(-180, 180, 5),
        min_non_zero=20,
        cache_key=None,
    ):
        template_mask = self._trim_mask(self._first_channel_mask(template))
        angles = tuple(angle_range)
        template_key = RotatedTemplateCacheKey(
            cache_id=cache_key or id(template),
            mask_shape=template_mask.shape,
            mask_non_zero=cv2.countNonZero(template_mask),
            mask_hash=hash(template_mask.tobytes()),
            angles=angles,
            min_non_zero=min_non_zero,
        )
        cached = self._rotated_template_cache.get(template_key)
        if cached is not None:
            return cached

        templates = []
        for angle in angles:
            rotated = self._rotate_mask(template_mask, angle)
            rotated = self._trim_mask(rotated)
            if cv2.countNonZero(rotated) >= min_non_zero:
                templates.append((angle, rotated))

        self._rotated_template_cache[template_key] = templates
        return templates

    @staticmethod
    def _first_channel_mask(mat: np.ndarray):
        if mat.ndim == 2:
            return mat
        return mat[:, :, 0]

    @staticmethod
    def _binary_mask(mat: np.ndarray):
        mask = VisionMixin._first_channel_mask(mat)
        if mask.dtype != np.uint8:
            mask = cv2.normalize(mask, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        return np.where(mask > 0, 255, 0).astype(np.uint8)

    @staticmethod
    def _normalize_angle(angle):
        return (angle + 180) % 360 - 180

    def _rotate_mask(self, mask: np.ndarray, angle):
        h, w = mask.shape[:2]
        center = (w / 2, h / 2)
        rotate_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        cos = abs(rotate_matrix[0, 0])
        sin = abs(rotate_matrix[0, 1])
        new_w = int(round(h * sin + w * cos))
        new_h = int(round(h * cos + w * sin))
        rotate_matrix[0, 2] += new_w / 2 - center[0]
        rotate_matrix[1, 2] += new_h / 2 - center[1]
        return cv2.warpAffine(
            mask,
            rotate_matrix,
            (new_w, new_h),
            flags=cv2.INTER_NEAREST,
            borderValue=0,
        )

    def _trim_mask(self, mask):
        points = cv2.findNonZero(mask)
        if points is None:
            return mask
        x, y, w, h = cv2.boundingRect(points)
        return mask[y : y + h, x : x + w]

    def _find_contours_from_first_channel(self, bgr):
        bin_mat = bgr[:, :, 0]
        contours, _ = cv2.findContours(bin_mat, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return contours

    def _find_rotated_shape(self, target_contour, scene_contours, score_threshold=0.1):
        """
        target_contour: 要匹配的目标轮廓。
        scene_contours: 在场景中找到的候选轮廓。
        score_threshold: 越小越严格。通常 0.05-0.2 之间。
        """
        start_time = self.clock.now()

        results = []
        for cnt in scene_contours:
            if cv2.contourArea(cnt) < 50:
                continue

            # 核心算法：比较两个形状的胡氏矩 (I1 模式最常用)
            # 返回值越小，匹配度越高（0 为完美匹配）
            score = cv2.matchShapes(target_contour, cnt, cv2.CONTOURS_MATCH_I1, 0.0)

            if score < score_threshold:
                # 计算重心和角度
                M = cv2.moments(cnt)
                if not math.isclose(M["m00"], 0.0):
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])

                    # 使用最小外接矩形获取角度
                    rect = cv2.minAreaRect(cnt)
                    angle = rect[2]  # 得到角度

                    results.append({"center": (cx, cy), "angle": angle, "score": round(score, 3)})

        # 按分数升序排列（得分越低越好）
        results = sorted(results, key=lambda x: x["score"])
        return results, (self.clock.now() - start_time) * 1000
