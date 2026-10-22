import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from ok import Box

from src.utils import clock
from src.vision.openvino_detector import YOLO26OpenVINOAsyncDetector

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class FakeOutputTensor:
    def __init__(self, data):
        self.data = data


class FakeInferRequest:
    def __init__(self, complete_immediately=False, detections=None):
        self.cancelled = False
        self.complete_immediately = complete_immediately
        self.detections = detections or []
        self.callback = None
        self.user_data = None
        self.started = []
        self.waited = False

    def cancel(self):
        self.cancelled = True

    def set_callback(self, callback, user_data=None):
        self.callback = callback
        self.user_data = user_data

    def start_async(self, inputs, user_data=None):
        user_data = self.user_data if user_data is None else user_data
        self.started.append((inputs, user_data))
        if self.complete_immediately and self.callback is not None:
            self.callback(user_data)

    def wait(self):
        self.waited = True

    def get_output_tensor(self):
        return FakeOutputTensor(np.array([self.detections], dtype=np.float32))


class TestYOLO26OpenVINOAsyncDetector(unittest.TestCase):
    def _detector(self, requests):
        detector = YOLO26OpenVINOAsyncDetector.__new__(YOLO26OpenVINOAsyncDetector)
        detector.num_requests = 1
        detector._openvino_available = True
        detector._openvino_unavailable_reason = None
        detector._state_lock = threading.RLock()
        detector._retired_infer_requests = []
        detector._active_request_jobs = {}
        detector._active_requests = {}
        detector.latest_results = ["old"]
        detector.latest_image = None
        detector.class_names = ["target"]
        detector.latency = 0.0
        detector.job_id = 0
        detector._force_next_submit = False
        detector.model_h = 896
        detector.model_w = 1536
        detector.model_ratio = detector.model_w / detector.model_h
        detector.infer_request = requests.pop(0)
        detector.infer_request.set_callback(detector._callback)

        def create_request():
            request = requests.pop(0)
            request.set_callback(detector._callback)
            return request

        detector._create_infer_request = create_request
        return detector

    @patch.object(YOLO26OpenVINOAsyncDetector, "_supports_avx2", return_value=False)
    @patch("src.vision.openvino_detector.communicate")
    @patch("src.vision.openvino_detector.og.app")
    def test_detector_notifies_and_returns_false_without_avx2(
        self, app, communicate, _supports_avx2
    ):
        app.tr.side_effect = lambda message: message

        detector = YOLO26OpenVINOAsyncDetector("unused.xml")
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        self.assertFalse(detector._openvino_available)
        self.assertIs(detector.detect(image), False)
        self.assertIs(detector.detect_sync(image), False)
        self.assertIn("unavailable", detector.debug_state())
        detector.wait()
        detector.clear_cache()
        communicate.notification.emit.assert_called_once()

    @patch.object(YOLO26OpenVINOAsyncDetector, "_numpy_supports_avx2", return_value=True)
    @patch.object(YOLO26OpenVINOAsyncDetector, "_windows_supports_avx2", return_value=False)
    def test_numpy_fallback_accepts_avx2_on_old_windows(self, _windows_supports, _numpy_supports):
        self.assertTrue(YOLO26OpenVINOAsyncDetector._supports_avx2())

    @patch.object(YOLO26OpenVINOAsyncDetector, "_numpy_supports_avx2", return_value=False)
    @patch.object(YOLO26OpenVINOAsyncDetector, "_windows_supports_avx2", return_value=False)
    def test_avx2_requires_both_probes_to_report_unavailable(
        self, _windows_supports, _numpy_supports
    ):
        self.assertFalse(YOLO26OpenVINOAsyncDetector._supports_avx2())

    def test_detect_sync_cancels_busy_request_and_waits_for_latest_frame(self):
        old_request = FakeInferRequest()
        new_request = FakeInferRequest(
            complete_immediately=True,
            detections=[[0, 0, 1536, 896, 0.99, 0]],
        )
        detector = self._detector([old_request, new_request])
        detector._mark_request_job_started(old_request)
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        result = detector.detect_sync(image)

        self.assertTrue(old_request.cancelled)
        self.assertIs(detector.infer_request, new_request)
        self.assertEqual(len(new_request.started), 1)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Box)
        self.assertEqual(result[0].name, "target")
        self.assertIs(detector.latest_image, image)

    def test_force_cancels_busy_request_and_submits_latest_frame(self):
        old_request = FakeInferRequest()
        new_request = FakeInferRequest()
        detector = self._detector([old_request, new_request])
        detector._mark_request_job_started(old_request)
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        result = detector.detect(image, force=True)

        self.assertEqual(result, ["old"])
        self.assertTrue(old_request.cancelled)
        self.assertIs(detector.infer_request, new_request)
        self.assertEqual(len(new_request.started), 1)
        self.assertEqual(detector._get_active_retired_count(), 0)

    def test_busy_background_detect_does_not_replace_current_request(self):
        active_request = FakeInferRequest()
        detector = self._detector([active_request])
        detector._mark_request_job_started(active_request)
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        result = detector.detect(image)

        self.assertEqual(result, ["old"])
        self.assertEqual(active_request.started, [])
        self.assertFalse(active_request.cancelled)

    def test_clear_cache_retires_active_request_and_next_detect_submits(self):
        old_request = FakeInferRequest()
        new_request = FakeInferRequest()
        detector = self._detector([old_request, new_request])
        detector._mark_request_job_started(old_request)
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        detector.clear_cache()
        result = detector.detect(image)

        self.assertIsNone(result)
        self.assertTrue(old_request.cancelled)
        self.assertIs(detector.infer_request, new_request)
        self.assertEqual(len(new_request.started), 1)
        self.assertFalse(detector._force_next_submit)

    def test_stale_callback_cannot_overwrite_latest_results(self):
        old_request = FakeInferRequest()
        new_request = FakeInferRequest()
        detector = self._detector([old_request, new_request])
        detector._mark_request_job_started(old_request)
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        detector.detect(image, force=True)
        current_job_id = detector.job_id

        detector.latest_results = ["newer"]
        detector._callback(
            {
                "box": Box(x=0, y=0, width=20, height=20),
                "threshold": 0.5,
                "label": "target",
                "start_time": clock.now(),
                "pad_x": 0,
                "pad_y": 0,
                "target_w": detector.model_w,
                "job_id": current_job_id - 1,
                "request_id": id(old_request),
                "image": image,
            }
        )

        self.assertEqual(detector.latest_results, ["newer"])
        self.assertEqual(detector._get_active_retired_count(), 0)

    def test_retired_request_releases_job_when_cancel_has_no_callback(self):
        old_request = FakeInferRequest()
        detector = self._detector([old_request])
        detector._mark_request_job_started(old_request)

        detector._retire_request(old_request, cancel=True)

        self.assertEqual(detector._get_active_retired_count(), 0)
        self.assertNotIn(id(old_request), detector._active_request_jobs)
        self.assertTrue(old_request.cancelled)
        self.assertEqual(detector._retired_infer_requests[0]["request"], old_request)

    @patch("src.vision.openvino_detector.communicate")
    @patch("src.vision.openvino_detector.og.app")
    def test_sync_timeout_disables_openvino_and_cancels_request(self, app, communicate):
        app.tr.side_effect = lambda message: message
        request = FakeInferRequest()
        detector = self._detector([request])
        detector._SYNC_WAIT_TIMEOUT = 0
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        result = detector.detect_sync(image)

        self.assertFalse(result)
        self.assertFalse(detector._openvino_available)
        self.assertEqual(
            detector._openvino_unavailable_reason,
            "OpenVINO 单次推理超时，已自动关闭目标检测；重启程序后会重新尝试启用。",
        )
        self.assertTrue(request.cancelled)
        self.assertNotIn(id(request), detector._active_request_jobs)
        self.assertFalse(detector.detect(image))
        communicate.notification.emit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
