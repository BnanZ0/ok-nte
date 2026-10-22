import unittest
from unittest.mock import patch

from src.tasks.CombatDetectionTestTask import (
    CombatDetectionTestStats,
    _Avx2BypassYOLO26OpenVINOAsyncDetector,
)
from src.utils import clock


class TestCombatDetectionTestStats(unittest.TestCase):
    def test_test_detector_always_bypasses_avx2_probe(self):
        self.assertTrue(_Avx2BypassYOLO26OpenVINOAsyncDetector._supports_avx2())

    @patch("src.utils.clock._read_monotonic", return_value=15.5)
    def test_report_includes_all_detection_results_and_errors(self, _monotonic):
        stats = CombatDetectionTestStats(started_at=clock.TimePoint(10.0))
        stats.record(lv=True, target=False, health_bar=True)
        stats.record(lv=False, target=True, health_bar=False)
        stats.errors.append("RuntimeError: test failure")

        report = stats.report("openvino(test)", using_avx2_bypass=True)

        self.assertIn("运行时长: 5.5s", report)
        self.assertIn("LV: 1/2 (50.0%)", report)
        self.assertIn("锁定目标白色菱形标记: 1/2 (50.0%)", report)
        self.assertIn("Health bar: 1/2 (50.0%)", report)
        self.assertIn("AVX2 测试绕过: 是", report)
        self.assertIn("采样错误: 1", report)
        self.assertIn("RuntimeError: test failure", report)

    @patch("src.utils.clock._read_monotonic", return_value=10.0)
    def test_report_handles_zero_samples(self, _monotonic):
        stats = CombatDetectionTestStats(started_at=clock.TimePoint(10.0))

        report = stats.report("openvino(test)", using_avx2_bypass=False)

        self.assertIn("LV: 0/0 (n/a)", report)
        self.assertIn("锁定目标白色菱形标记: 0/0 (n/a)", report)
        self.assertIn("Health bar: 0/0 (n/a)", report)
        self.assertIn("AVX2 测试绕过: 否", report)

    @patch("src.utils.clock._read_monotonic", return_value=15.5)
    def test_non_chinese_report_is_english(self, _monotonic):
        stats = CombatDetectionTestStats(started_at=clock.TimePoint(10.0))
        stats.record(lv=True, target=False, health_bar=True)
        stats.errors.append("RuntimeError: test failure")

        report = stats.report("openvino(test)", using_avx2_bypass=True, is_chinese=False)

        self.assertIn("CombatCheck Detection Test Report", report)
        self.assertIn("Duration: 5.5s", report)
        self.assertIn("Samples: 1", report)
        self.assertIn("AVX2 test bypass: Yes", report)
        self.assertIn("Sampling errors: 1", report)
        self.assertIn("Error details: RuntimeError: test failure", report)
        self.assertNotIn("自动战斗检测诊断报告", report)
