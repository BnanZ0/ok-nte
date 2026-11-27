import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from ok import Box

from src.utils import image_utils as iu

vision_module = importlib.import_module("src.tasks.mixin.VisionMixin")
VisionMixin = vision_module.VisionMixin


class _FakeSift:
    def detectAndCompute(self, _gray, _mask):
        keypoints = [vision_module.cv2.KeyPoint(float(index), float(index), 1) for index in range(4)]
        descriptors = np.zeros((4, 128), dtype=np.float32)
        return keypoints, descriptors


class _FakeMatcher:
    def knnMatch(self, _template_descriptors, _scene_descriptors, k=2):
        return [
            [
                SimpleNamespace(queryIdx=index, trainIdx=index, distance=0.1),
                SimpleNamespace(queryIdx=index, trainIdx=index, distance=1.0),
            ]
            for index in range(3)
        ]


class _VisionTask(VisionMixin):
    @property
    def frame(self):
        return self._frame


class TestVisionMixin(unittest.TestCase):
    def test_rotated_template_chamfer_tolerates_small_occlusion(self):
        task = object.__new__(_VisionTask)
        template_mask = np.zeros((12, 10), dtype=np.uint8)
        template_mask[:, :3] = 255
        template_mask[-3:, :] = 255
        template_mask[:3, :8] = 255
        template = np.dstack([template_mask, template_mask, template_mask])

        frame = np.zeros((30, 30, 3), dtype=np.uint8)
        frame[10:22, 8:18] = template
        frame[15:17, 9:11] = 0

        drawn_boxes = []
        task._frame = frame
        task.get_feature_by_name = lambda _name: SimpleNamespace(mat=template)
        task.draw_boxes = lambda boxes, color: drawn_boxes.append((boxes, color))

        result, _ = task.find_rotated_template(
            "unit_chamfer",
            box=Box(3, 4, 24, 24),
            angle_range=range(0, 1),
            threshold=0.8,
            min_coverage=0.8,
            frame_processor=lambda cropped: cropped[:, :, 0],
        )

        self.assertEqual(1, len(result))
        self.assertGreaterEqual(result[0]["coverage"], 0.9)
        self.assertEqual((10, 12), result[0]["center"])
        self.assertEqual(2, len(drawn_boxes))
        self.assertEqual("blue", drawn_boxes[0][1])
        self.assertEqual("red", drawn_boxes[1][1])
        self.assertEqual((8, 10), (drawn_boxes[1][0].x, drawn_boxes[1][0].y))

    def test_rotated_template_processor_runs_after_box_crop(self):
        task = object.__new__(_VisionTask)
        template_mask = np.full((4, 4), 255, dtype=np.uint8)
        template = np.dstack([template_mask, template_mask, template_mask])
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        frame[3:7, 4:8] = template
        seen_shapes = []

        task._frame = frame
        task.get_feature_by_name = lambda _name: SimpleNamespace(mat=template)
        task.draw_boxes = lambda *args, **kwargs: None

        def processor(cropped):
            seen_shapes.append(cropped.shape[:2])
            return cropped[:, :, 0]

        result, _ = task.find_rotated_template(
            "unit_processor",
            box=Box(2, 1, 7, 8),
            angle_range=range(0, 1),
            min_non_zero=4,
            threshold=0.75,
            min_coverage=0.75,
            frame_processor=processor,
        )

        self.assertEqual([(8, 7)], seen_shapes)
        self.assertEqual(1, len(result))

    def test_sift_homography_requires_at_least_four_matches(self):
        task = object.__new__(_VisionTask)
        task._frame = np.zeros((32, 32, 3), dtype=np.uint8)
        task.get_original_feature_by_name = lambda _name: SimpleNamespace(
            mat=np.zeros((16, 16, 3), dtype=np.uint8)
        )
        task.draw_boxes = lambda *args, **kwargs: None
        task.log_debug = lambda _message: None

        with (
            patch.object(vision_module.cv2, "SIFT_create", return_value=_FakeSift()),
            patch.object(vision_module.cv2, "BFMatcher", return_value=_FakeMatcher()),
            patch.object(vision_module.cv2, "findHomography") as find_homography,
        ):
            result = task.find_sift_feature(
                "unit_sift_min_matches",
                min_match_count=3,
                small_target_retry=False,
            )

        self.assertIsNone(result)
        find_homography.assert_not_called()

    def test_scale_image_handles_resize_noop_and_validation(self):
        img = np.zeros((20, 40, 3), dtype=np.uint8)
        self.assertIs(iu.scale_image(img, 1.0), img)
        self.assertEqual((10, 20, 3), iu.scale_image(img, 0.5).shape)
        self.assertEqual((30, 60, 3), iu.scale_image(img, 1.5).shape)

        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        self.assertIs(iu.scale_image(empty, 0.5), empty)

        with self.assertRaises(ValueError):
            iu.scale_image(img, 0)
        with self.assertRaises(ValueError):
            iu.scale_image(img, -0.5)

    def test_find_one_scaled_matches_resized_target_and_skips_oversized(self):
        task = object.__new__(_VisionTask)
        base_template = np.zeros((20, 20, 3), dtype=np.uint8)
        cv2.circle(base_template, (10, 10), 7, (255, 255, 255), 2)
        cv2.line(base_template, (10, 3), (10, 17), (180, 180, 180), 2)

        shrunk_target = iu.scale_image(base_template, 0.7)
        th, tw = shrunk_target.shape[:2]

        frame = np.zeros((100, 60, 3), dtype=np.uint8)
        # Place target in top area and distractor shapes below it
        frame[10 : 10 + th, 20 : 20 + tw] = shrunk_target
        cv2.rectangle(frame, (18, 55), (38, 75), (200, 200, 200), 2)

        search_box = Box(15, 5, 24, 80)
        drawn_boxes = []
        seen_template_shapes = []

        def fake_find_one(
            feature_name=None,
            threshold=0,
            use_gray_scale=False,
            box=None,
            template=None,
            frame=None,
            match_method=cv2.TM_CCOEFF_NORMED,
            **kwargs,
        ):
            self.assertIsNone(feature_name)
            seen_template_shapes.append(template.shape[:2])
            roi = box.crop_frame(frame)
            if use_gray_scale:
                roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                tmpl = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            else:
                tmpl = template
            res = cv2.matchTemplate(roi, tmpl, match_method)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val < threshold:
                return None
            return Box(
                box.x + max_loc[0],
                box.y + max_loc[1],
                tmpl.shape[1],
                tmpl.shape[0],
                confidence=float(max_val),
            )

        task._frame = frame
        task.get_feature_by_name = lambda _name: SimpleNamespace(
            mat=base_template, x=0, y=0, width=20, height=20
        )
        task.draw_boxes = lambda boxes, color: drawn_boxes.append((boxes, color))
        task.find_one = fake_find_one

        result = task.find_one_scaled(
            "unit_scaled_icon",
            box=search_box,
            scale_range=(0.6, 1.4),
            scale_step=0.1,
            threshold=0.8,
            use_gray_scale=True,
        )

        self.assertIsNotNone(result)
        self.assertEqual("unit_scaled_icon", result.name)
        self.assertAlmostEqual(0.7, result.scale, places=3)
        self.assertEqual((20, 10), (result.x, result.y))
        self.assertGreater(result.confidence, 0.9)
        # Templates wider than search_box.width (24) must be skipped before calling find_one
        self.assertTrue(all(w <= search_box.width and h <= search_box.height for h, w in seen_template_shapes))
        # No duplicate template shapes tested
        self.assertEqual(len(seen_template_shapes), len(set(seen_template_shapes)))
        # Emits blue search box once and red matched box once
        self.assertEqual(2, len(drawn_boxes))
        self.assertEqual("blue", drawn_boxes[0][1])
        self.assertEqual("red", drawn_boxes[1][1])

    def test_find_one_scaled_returns_none_when_no_scale_matches(self):
        task = object.__new__(_VisionTask)
        task._frame = np.zeros((40, 40, 3), dtype=np.uint8)
        task.get_feature_by_name = lambda _name: SimpleNamespace(
            mat=np.full((10, 10, 3), 255, dtype=np.uint8), x=0, y=0, width=10, height=10
        )
        task.draw_boxes = lambda *args, **kwargs: None
        task.find_one = lambda **kwargs: None

        result = task.find_one_scaled(
            "unit_missing",
            box=Box(0, 0, 40, 40),
            scales=[0.8, 1.0, 1.2],
            threshold=0.7,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
