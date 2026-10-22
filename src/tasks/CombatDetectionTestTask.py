from dataclasses import dataclass, field

from ok import Logger, TaskDisabledException, get_path_relative_to_exe, og

from src.combat.CombatCheck import CombatCheck
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.utils import clock
from src.vision.openvino_detector import YOLO26OpenVINOAsyncDetector

logger = Logger.get_logger(__name__)


class _Avx2BypassYOLO26OpenVINOAsyncDetector(YOLO26OpenVINOAsyncDetector):
    """测试专用检测器：只跳过 AVX2 的前置探测。"""

    @classmethod
    def _supports_avx2(cls) -> bool:
        return True


@dataclass
class CombatDetectionTestStats:
    started_at: clock.TimePoint = field(default_factory=clock.now)
    rounds: int = 0
    lv_hits: int = 0
    target_hits: int = 0
    health_bar_hits: int = 0
    errors: list[str] = field(default_factory=list)

    def record(self, lv: bool, target: bool, health_bar: bool) -> None:
        self.rounds += 1
        self.lv_hits += int(lv)
        self.target_hits += int(target)
        self.health_bar_hits += int(health_bar)

    @staticmethod
    def _rate(hits: int, rounds: int) -> str:
        return "n/a" if rounds == 0 else f"{hits / rounds:.1%}"

    def report(self, detector_state: str, using_avx2_bypass: bool, is_chinese: bool = True) -> str:
        elapsed = clock.now() - self.started_at
        if not is_chinese:
            lines = [
                "CombatCheck Detection Test Report",
                f"Duration: {elapsed:.1f}s",
                f"Samples: {self.rounds}",
                f"LV: {self.lv_hits}/{self.rounds} ({self._rate(self.lv_hits, self.rounds)})",
                (
                    f"Locked target white diamond marker: {self.target_hits}/{self.rounds} "
                    f"({self._rate(self.target_hits, self.rounds)})"
                ),
                (
                    f"Health bar: {self.health_bar_hits}/{self.rounds} "
                    f"({self._rate(self.health_bar_hits, self.rounds)})"
                ),
                f"AVX2 test bypass: {'Yes' if using_avx2_bypass else 'No'}",
                f"OpenVINO: {detector_state}",
                f"Sampling errors: {len(self.errors)}",
            ]
            if self.errors:
                lines.append("Error details: " + " | ".join(self.errors))
            return "\n".join(lines)

        lines = [
            "自动战斗检测诊断报告",
            f"运行时长: {elapsed:.1f}s",
            f"采样轮次: {self.rounds}",
            f"LV: {self.lv_hits}/{self.rounds} ({self._rate(self.lv_hits, self.rounds)})",
            (
                f"锁定目标白色菱形标记: {self.target_hits}/{self.rounds} "
                f"({self._rate(self.target_hits, self.rounds)})"
            ),
            (
                f"Health bar: {self.health_bar_hits}/{self.rounds} "
                f"({self._rate(self.health_bar_hits, self.rounds)})"
            ),
            f"AVX2 测试绕过: {'是' if using_avx2_bypass else '否'}",
            f"OpenVINO: {detector_state}",
            f"采样错误: {len(self.errors)}",
        ]
        if self.errors:
            lines.append("错误详情: " + " | ".join(self.errors))
        return "\n".join(lines)


class CombatDetectionTestTask(NTEOneTimeTask, CombatCheck):
    """持续采样 CombatCheck 的 LV, 锁定目标白色菱形标记与红色血条, 直到用户手动停止。"""

    SAMPLE_INTERVAL = 0.25
    MAX_REPORTED_ERRORS = 10

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动战斗检测诊断"
        self.description = (
            "持续检测诊断 Lv, 锁定目标标记(白色菱形)和红色血条; 请手动停止以查看完整报告"
        )
        self._stats: CombatDetectionTestStats | None = None
        self._original_detector = None
        self._using_avx2_bypass = False
        self._test_detector_state = "uninitialized"
        self._test_detectors = []

    def run(self):
        super().run()
        self._stats = CombatDetectionTestStats()
        try:
            self._prepare_detector()
            self.log_info("自动战斗检测诊断已开始; 请手动停止任务以生成完整报告", notify=True)
            self._run_samples()
        except TaskDisabledException:
            self.log_info("自动战斗检测诊断已由用户停止")
        except Exception as error:
            self._record_error(error)
            logger.exception("CombatCheck detection test failed")
        finally:
            self._restore_detector()
            self._show_report()

    def _prepare_detector(self) -> None:
        app = og.my_app
        if app is None:
            raise RuntimeError("应用尚未初始化, 无法使用 OpenVINO 检测器")

        detector = app.openvino_model_async
        if detector._openvino_available:
            return

        self._original_detector = detector
        message = (
            "检测到 OpenVINO 因 AVX2 探测不可用; 将仅在本次 CombatCheck 测试中创建"
            "绕过 AVX2 前置检查的检测器. 该测试不保证 CPU 实际兼容 OpenVINO."
        )
        self.log_warning(message, notify=True)
        test_detector = _Avx2BypassYOLO26OpenVINOAsyncDetector(
            xml_path=get_path_relative_to_exe("assets", "openvino", "best.xml")
        )
        app._openvino_model_async = test_detector
        self._test_detectors.append(test_detector)
        self._using_avx2_bypass = True
        self.log_info("已启用本次测试专用 OpenVINO 检测器", notify=True)

    def _run_samples(self) -> None:
        while True:
            if not self.scene.is_in_team(self.is_in_team):
                self.sleep(0.1)
                continue
            started_at = self.clock.now()
            try:
                frame = self.frame
                lv = bool(self.find_lv(frame=frame))
                target = bool(self.find_target(frame=frame, sync=True, force=True))
                health_bar = bool(self.has_health_bar())
                self._stats.record(lv, target, health_bar)  # type: ignore[union-attr]
                self._update_live_info(lv, target, health_bar)
            except TaskDisabledException:
                raise
            except Exception as error:
                self._record_error(error)

            self.next_frame()
            remaining = self.SAMPLE_INTERVAL - (self.clock.now() - started_at)
            if remaining > 0:
                self.sleep(remaining)

    def _update_live_info(self, lv: bool, target: bool, health_bar: bool) -> None:
        stats = self._stats
        if stats is None:
            return
        self.info_set("自动战斗检测诊断", f"第 {stats.rounds} 轮")
        self.info_set(
            "当前检测",
            f"LV={lv}, 锁定目标白色菱形标记={target}, 红色血条={health_bar}",
        )
        self.info_set(
            "命中统计",
            f"LV {stats.lv_hits}/{stats.rounds}; 锁定目标白色菱形标记 "
            f"{stats.target_hits}/{stats.rounds}; 红色血条 {stats.health_bar_hits}/{stats.rounds}",
        )

    def _record_error(self, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"
        logger.error("CombatCheck detection test sample failed: %s", message)
        if self._stats is not None and len(self._stats.errors) < self.MAX_REPORTED_ERRORS:
            self._stats.errors.append(message)

    def _restore_detector(self) -> None:
        if not self._using_avx2_bypass or self._original_detector is None or og.my_app is None:
            return
        try:
            current_detector = og.my_app._openvino_model_async
            if current_detector is not None:
                self._test_detector_state = current_detector.debug_state()
                current_detector.clear_cache()
            og.my_app._openvino_model_async = self._original_detector
            self.log_info("已恢复原 OpenVINO 检测器")
        except Exception:
            logger.exception("Failed to restore the original OpenVINO detector")

    def _show_report(self) -> None:
        if self._stats is None:
            return
        detector = getattr(getattr(og, "my_app", None), "_openvino_model_async", None)
        detector_state = self._test_detector_state
        if detector_state == "uninitialized" and detector is not None:
            detector_state = detector.debug_state()
        report = self._stats.report(
            detector_state,
            self._using_avx2_bypass,
            is_chinese=self.is_chinese(),
        )
        self.info_set("自动战斗检测诊断报告", report)
        self.log_info(report, notify=True)
