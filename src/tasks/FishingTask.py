import re
import time
import win32api
import win32con
import win32gui
import win32process
from qfluentwidgets import FluentIcon

from src.tasks.BaseNTETask import BaseNTETask
from src.utils import image_utils as iu

class FishingTask(BaseNTETask):
    # 1080p 固定参数（“循环次数”“方向反转”开放配置）
    ENTRY_BOX = (0.54, 0.50, 0.82, 0.60)
    START_PANEL_BOX = (0.63, 0.08, 0.98, 0.95)
    BITE_BOX = (0.20, 0.56, 0.82, 0.92)
    FAIL_BOX = (0.12, 0.48, 0.88, 0.92)
    SUCCESS_BOX = (0.30, 0.70, 0.71, 0.92)
    SUCCESS_TITLE_BOX = (0.34, 0.08, 0.68, 0.17)
    BAR_BOX = (0.30, 0.025, 0.70, 0.085)
    BITE_INDICATOR_BOX = (0.84, 0.79, 0.96, 0.95)
    START_BUTTON_POS = (0.844, 0.866)
    SUCCESS_CLOSE_POS = (0.12, 0.88)
    OPEN_PANEL_TIMEOUT = 5
    BITE_TIMEOUT = 20
    CONTROL_TIMEOUT = 30
    RESULT_TIMEOUT = 10
    BAR_TOLERANCE = 4
    CONTROL_TAP_HOLD = 0.05
    CONTROL_USE_LONG_PRESS = True
    CONTROL_LONG_PRESS_THRESHOLD = 10
    CONTROL_LONG_PRESS_HOLD = 0.18
    CONTROL_OCR_CHECK_INTERVAL = 0.45
    DIRECTION_INVERT = True
    CONTROL_SPEED_PERCENT = 100
    START_PHASE_STABLE_CHECKS = 3
    START_CLICK_USE_PHYSICAL_FALLBACK = True

    ENTRY_PATTERN = re.compile(r"钓鱼", re.IGNORECASE)
    START_PATTERN = re.compile(r"开始钓鱼|钓鱼准备", re.IGNORECASE)
    START_BUTTON_PATTERN = re.compile(r"开始钓鱼", re.IGNORECASE)
    BITE_PATTERN = re.compile(r"上钩|Clinch|钩了", re.IGNORECASE)
    FAIL_PATTERN = re.compile(
        r"鱼儿溜走了|鱼.?溜走|溜走了|溜走|跑掉|脱钩|失败",
        re.IGNORECASE,
    )
    SUCCESS_PATTERN = re.compile(r"钓鱼经验|点击空白区域关闭|垂钓等级", re.IGNORECASE)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动钓鱼"
        self.description = "自动完成一轮或多轮钓鱼"
        self.icon = FluentIcon.GAME
        self.support_schedule_task = True
        self.default_config.update(
            {
                "循环次数": 1,
                "方向反转": bool(self.DIRECTION_INVERT),
                "拉力速度": int(self.CONTROL_SPEED_PERCENT),
            }
        )
        self._fishing_started = False
        self._last_bar_log_time = 0.0
        self._prev_pointer_center = None
        self._prev_error = 0.0
        self._prev_control_time = 0.0
        self._last_control_key = None
        self._same_key_streak = 0
        self._last_control_failed_escape = False

    def run(self):
        self.reset_runtime_state()
        rounds = max(1, int(self.config.get("循环次数", 1)))
        self.log_info(f"控条方向反转: {self.is_direction_invert()}")
        self.log_info(f"拉力速度倍率: x{self.get_control_speed_factor():.2f}")
        self.log_info(f"开始自动钓鱼，共 {rounds} 轮", notify=True)
        success_count = 0
        try:
            for index in range(rounds):
                self.log_info(f"开始第 {index + 1}/{rounds} 轮钓鱼")
                if self.run_once(index + 1):
                    success_count += 1
                else:
                    self.log_error(f"第 {index + 1} 轮钓鱼失败", notify=True)
                    # 失败后重置状态继续下一轮，避免“设置2轮只跑1轮”
                    self.reset_runtime_state()
                    self.clear_success_overlay_if_present()
            self.info_set("Fishing Success Count", success_count)
            self.log_info(f"自动钓鱼结束，成功 {success_count}/{rounds}", notify=True)
        finally:
            # Task 实例会复用，结束后必须清掉运行态，避免下次从中间步骤继续
            self.reset_runtime_state()

    def run_once(self, round_index: int) -> bool:
        self.clear_success_overlay_if_present()
        self.wait_until(
            lambda: not self.is_success_overlay(),
            time_out=3,
            raise_if_not_found=False,
        )

        if not self._fishing_started:
            if not self.open_fishing_panel():
                self.screenshot(f"fishing_open_panel_failed_{round_index}")
                return False

            if not self.start_fishing():
                self.screenshot(f"fishing_start_failed_{round_index}")
                return False

            self._fishing_started = True

        round_deadline = time.time() + max(30.0, float(self.BITE_TIMEOUT) + float(self.CONTROL_TIMEOUT) + 12.0)
        round_attempt = 0
        while time.time() < round_deadline:
            round_attempt += 1
            if round_attempt > 1:
                self.log_info(f"第 {round_index} 轮自动重试抛竿: 第 {round_attempt} 次")

            if not self.cast_rod():
                self.screenshot(f"fishing_cast_failed_{round_index}")
                return False

            if not self.wait_bite():
                self.screenshot(f"fishing_bite_timeout_{round_index}")
                return False

            if self.control_until_finish():
                return True

            if self._last_control_failed_escape:
                self._last_control_failed_escape = False
                self.log_info("检测到“鱼儿溜走了”，本轮自动重新抛竿继续")
                continue

            self.screenshot(f"fishing_control_failed_{round_index}")
            return False

        self.log_error(f"第 {round_index} 轮重试超时，结束本轮")
        self.screenshot(f"fishing_round_timeout_{round_index}")
        return False

    def open_fishing_panel(self) -> bool:
        if self.is_start_panel():
            return True
        if not self.wait_until(
            self.is_fishing_entry,
            time_out=self.OPEN_PANEL_TIMEOUT,
            raise_if_not_found=False,
        ):
            self.log_error("未检测到钓鱼入口，请先站在钓点旁")
            return False

        self.send_game_key("f", after_sleep=0.2)
        return self.wait_until(
            self.is_start_panel,
            time_out=self.OPEN_PANEL_TIMEOUT,
            raise_if_not_found=False,
        )

    def start_fishing(self) -> bool:
        if not self.is_start_panel():
            return False

        strategies = [
            ("OCR中心坐标点击", lambda: self.click_start_button(mode="center")),
            ("OCR原始坐标点击", lambda: self.click_start_button(mode="raw")),
            ("固定坐标点击", lambda: self.click_start_button(mode="fallback")),
        ]

        for strategy_name, action in strategies:
            self.log_info(f"尝试开始钓鱼策略: {strategy_name}")
            if not action():
                continue
            if self.wait_until(
                self.has_left_start_phase,
                time_out=3,
                raise_if_not_found=False,
            ):
                self.log_info(f"开始钓鱼成功，策略: {strategy_name}")
                return True

        return False

    def cast_rod(self) -> bool:
        if not self.wait_until(
            self.has_left_start_phase,
            time_out=1.5,
            raise_if_not_found=False,
        ):
            self.log_error("未稳定离开钓鱼准备面板，抛竿取消")
            return False

        for attempt in range(2):
            if attempt == 0:
                self.log_info("执行抛竿操作")
            else:
                self.log_info(f"抛竿未生效，重试按 F ({attempt + 1}/2)")
            self.send_game_key("f", down_time=0.05, after_sleep=0.16)
            start = time.time()
            while time.time() - start < 2.2:
                state = self.get_bar_state()
                if self.is_bite_signal() or self.is_valid_bar_state(state):
                    return True
                if self.is_fail_signal():
                    return False
                self.next_frame()
        self.log_error("抛竿后未进入咬钩/控条状态")
        return False

    def wait_bite(self) -> bool:
        start = time.time()
        timeout = float(self.BITE_TIMEOUT)
        next_reel_tap = 0.0
        tap_interval = 0.08
        bar_confirm_count = 0
        bar_confirm_required = 3
        while time.time() - start < timeout:
            if self._is_start_panel_stuck():
                return False

            bar_state = self.get_bar_state()
            if self.is_valid_bar_state(bar_state):
                bar_confirm_count += 1
                if bar_confirm_count >= bar_confirm_required:
                    self.log_info(f"已连续识别到拉力条 {bar_confirm_count} 帧，进入控条阶段")
                    return True
            else:
                bar_confirm_count = 0

            now = time.time()
            if now >= next_reel_tap:
                self.log_info("等待上钩中：点按 F 尝试收杆")
                self.send_game_key("f", down_time=0.03, after_sleep=0.0)
                next_reel_tap = now + tap_interval

            self.next_frame()
        return False

    def _is_start_panel_stuck(self) -> bool:
        if not self.is_start_panel_stable(expected=True, checks=2):
            return False
        self.log_error("仍处于钓鱼准备面板，判定开始钓鱼未生效")
        return True

    def control_until_finish(self) -> bool:
        self._last_control_failed_escape = False
        start = time.time()
        timeout = float(self.CONTROL_TIMEOUT)
        next_ocr_check = 0.0
        while time.time() - start < timeout:
            state = self.get_bar_state()
            if self.is_valid_bar_state(state):
                self.apply_bar_control(state)
            elif time.time() - self._last_bar_log_time > 0.5:
                self.log_info("控条阶段: 未识别到有效指针/绿区，等待下一帧")
                self._last_bar_log_time = time.time()
            elif time.time() - start > 1.0 and self.is_fishing_entry():
                self.log_error("钓鱼条阶段提前结束，疑似脱钩或失败")
                return False

            now = time.time()
            if now >= next_ocr_check:
                # OCR 判定降频，避免拖慢控条按键节奏
                if self.is_control_fail_signal():
                    self.log_error("控条阶段检测到失败文案（鱼儿溜走）")
                    self._last_control_failed_escape = True
                    return False
                if self.is_success_overlay():
                    self.close_success_overlay()
                    return True
                next_ocr_check = now + float(self.CONTROL_OCR_CHECK_INTERVAL)

            self.next_frame()

        return False

    def apply_bar_control(self, state: dict):
        now = time.time()
        tolerance = max(0, int(self.BAR_TOLERANCE))
        decision = self._make_bar_decision(state, tolerance, now)
        if decision["stable"]:
            self._log_bar_stable(decision, now)
            self._update_bar_runtime_state(decision["pointer_center"], decision["zone_center"], now)
            self._same_key_streak = max(0, self._same_key_streak - 1)
            return

        press_hold, burst = self._resolve_press_profile(decision)
        burst = self._apply_key_streak(decision["key"], decision["ratio"], burst)

        if now - self._last_bar_log_time > 0.2:
            self.log_info(
                f"控条输入: key={decision['key']}, hold={press_hold:.3f}, burst={burst}, dist={decision['distance']}, "
                f"ratio={decision['ratio']:.2f}, vel={decision['velocity']:.1f}, drift_wrong={decision['drift_wrong']}, "
                f"pointer={decision['pointer_center']}, zone=({decision['zone_left']},{decision['zone_right']}), "
                f"soft=({decision['soft_left']},{decision['soft_right']})"
            )
            self._last_bar_log_time = now

        for _ in range(burst):
            self.send_control_key(decision["key"], hold=press_hold)

        self._update_bar_runtime_state(decision["pointer_center"], decision["zone_center"], now)

    def _make_bar_decision(self, state: dict, tolerance: int, now: float) -> dict:
        invert = self.is_direction_invert()
        pointer_center = int(state["pointer_center"])
        zone_left = int(state["zone_left"])
        zone_right = int(state["zone_right"])
        zone_center = int(state.get("zone_center", (zone_left + zone_right) // 2))
        zone_width = max(1, zone_right - zone_left)
        prev_pointer = self._prev_pointer_center if self._prev_pointer_center is not None else pointer_center
        dt = max(0.01, now - self._prev_control_time) if self._prev_control_time > 0 else 0.01
        velocity = (pointer_center - prev_pointer) / dt

        soft_margin = max(tolerance + 1, int(zone_width * 0.18))
        soft_left = zone_left + soft_margin
        soft_right = zone_right - soft_margin
        if soft_left >= soft_right:
            soft_left = zone_left + tolerance
            soft_right = zone_right - tolerance

        capped_velocity = max(-260.0, min(260.0, velocity))
        predict_gain = 0.015
        predicted_pointer = int(round(pointer_center + capped_velocity * predict_gain))

        if predicted_pointer < soft_left:
            return {
                "stable": False,
                "key": "d" if invert else "a",
                "distance": soft_left - predicted_pointer,
                "drift_wrong": velocity < -35,
                "pointer_center": pointer_center,
                "predicted_pointer": predicted_pointer,
                "zone_left": zone_left,
                "zone_right": zone_right,
                "zone_center": zone_center,
                "zone_width": zone_width,
                "velocity": velocity,
                "soft_left": soft_left,
                "soft_right": soft_right,
                "ratio": min(1.0, (soft_left - predicted_pointer) / max(1, zone_width)),
            }

        if predicted_pointer > soft_right:
            return {
                "stable": False,
                "key": "a" if invert else "d",
                "distance": predicted_pointer - soft_right,
                "drift_wrong": velocity > 35,
                "pointer_center": pointer_center,
                "predicted_pointer": predicted_pointer,
                "zone_left": zone_left,
                "zone_right": zone_right,
                "zone_center": zone_center,
                "zone_width": zone_width,
                "velocity": velocity,
                "soft_left": soft_left,
                "soft_right": soft_right,
                "ratio": min(1.0, (predicted_pointer - soft_right) / max(1, zone_width)),
            }

        return {
            "stable": True,
            "pointer_center": pointer_center,
            "predicted_pointer": predicted_pointer,
            "zone_left": zone_left,
            "zone_right": zone_right,
            "zone_center": zone_center,
            "velocity": velocity,
            "soft_left": soft_left,
            "soft_right": soft_right,
        }

    def _resolve_press_profile(self, decision: dict) -> tuple[float, int]:
        tap_hold = float(self.CONTROL_TAP_HOLD)
        ratio = decision["ratio"]
        distance = decision["distance"]
        zone_width = max(1, int(decision.get("zone_width", 1)))
        speed = self.get_control_speed_factor()

        # 提高追条力度：偏差大时更长按、更高 burst
        distance_factor = min(1.0, distance / max(1, zone_width * 2))
        press_hold = max(0.035, tap_hold * 0.70 + 0.022 + ratio * 0.050 + distance_factor * 0.030)
        if decision["drift_wrong"]:
            press_hold += 0.012
        press_hold *= speed
        press_hold = min(0.120, max(0.032, press_hold))

        burst = 2
        if ratio < 0.12 and distance < zone_width * 0.12:
            burst = 1
        if ratio > 0.55:
            burst = 2
        if ratio > 0.75:
            burst = 3
        if distance > zone_width * 1.6:
            burst = max(burst, 4)
        if decision["drift_wrong"] and burst < 3:
            burst += 1
        if speed >= 1.20:
            burst = min(4, burst + 1)
        elif speed <= 0.85:
            burst = max(1, burst - 1)
        return press_hold, burst

    def _apply_key_streak(self, key: str, ratio: float, burst: int) -> int:
        if key == self._last_control_key:
            self._same_key_streak += 1
        else:
            self._same_key_streak = 1
        self._last_control_key = key
        if self._same_key_streak >= 3 and ratio > 0.60:
            return min(4, burst + 1)
        return burst

    def _log_bar_stable(self, decision: dict, now: float):
        if now - self._last_bar_log_time <= 0.5:
            return
        self.log_info(
            f"控条稳定区: pointer={decision['pointer_center']}, zone=({decision['zone_left']},{decision['zone_right']}), "
            f"soft=({decision['soft_left']},{decision['soft_right']}), vel={decision['velocity']:.1f}"
        )
        self._last_bar_log_time = now

    def _update_bar_runtime_state(self, pointer_center: int, zone_center: int, now: float):
        self._prev_pointer_center = pointer_center
        self._prev_error = pointer_center - zone_center
        self._prev_control_time = now

    def get_bar_state(self):
        box = self.box_of_screen(*self.BAR_BOX, name="fishing_bar")
        bar_image = box.crop_frame(self.frame)
        return iu.detect_fishing_bar_state(bar_image)

    def get_bite_indicator_state(self):
        box = self.box_of_screen(*self.BITE_INDICATOR_BOX, name="fishing_bite_indicator")
        indicator_image = box.crop_frame(self.frame)
        return iu.detect_fishing_bite_indicator(indicator_image)

    def is_valid_bar_state(self, state) -> bool:
        if state is None:
            return False
        zone_left = int(state.get("zone_left", 0))
        zone_right = int(state.get("zone_right", 0))
        pointer_center = int(state.get("pointer_center", -1))
        image_width = max(1, int(state.get("image_width", 1)))
        zone_width = max(0, int(state.get("zone_width", zone_right - zone_left)))
        ratio = zone_width / image_width
        if not (0.05 <= ratio <= 0.55):
            return False
        if not (0 <= pointer_center < image_width):
            return False
        # 过滤明显误检：绿区贴边且指针又远离，通常不是有效拉力条
        edge_zone = zone_left <= 1 or zone_right >= image_width - 2
        if edge_zone and abs(pointer_center - int((zone_left + zone_right) / 2)) > int(image_width * 0.38):
            return False
        return True

    def ocr_safe(self, *box, match=None, retry: int = 3):
        """
        OCR with retry for OpenVINO busy errors.
        Prefer bg_onnx_ocr channel to reduce contention with default OCR.
        """
        for attempt in range(retry):
            try:
                return self.ocr(*box, match=match, lib="bg_onnx_ocr")
            except RuntimeError as e:
                if "Infer Request is busy" not in str(e):
                    raise
                if attempt == 0:
                    self.log_info("OCR busy，开始重试")
                time.sleep(0.03 * (attempt + 1))

        # fallback to default channel once
        try:
            return self.ocr(*box, match=match)
        except RuntimeError as e:
            if "Infer Request is busy" in str(e):
                self.log_info("OCR busy，跳过当前帧")
                return []
            raise

    def is_fishing_entry(self) -> bool:
        return bool(self.ocr_safe(*self.ENTRY_BOX, match=self.ENTRY_PATTERN))

    def is_start_panel(self) -> bool:
        return bool(self.ocr_safe(*self.START_PANEL_BOX, match=self.START_PATTERN))

    def is_start_button_visible(self) -> bool:
        return bool(self.ocr_safe(*self.START_PANEL_BOX, match=self.START_BUTTON_PATTERN))

    def is_bite_signal(self) -> bool:
        return bool(self.ocr_safe(*self.BITE_BOX, match=self.BITE_PATTERN))

    def is_fail_signal(self) -> bool:
        if self.ocr_safe(*self.BITE_BOX, match=self.FAIL_PATTERN):
            return True
        return bool(self.ocr_safe(*self.FAIL_BOX, match=self.FAIL_PATTERN))

    def is_control_fail_signal(self) -> bool:
        # “鱼儿溜走了”只在拉力条阶段出现，控条阶段仅检查该区域，减轻 OCR 开销
        return bool(self.ocr_safe(*self.FAIL_BOX, match=self.FAIL_PATTERN))

    def is_success_overlay(self) -> bool:
        if self.ocr_safe(*self.SUCCESS_BOX, match=self.SUCCESS_PATTERN):
            return True
        return bool(self.ocr_safe(*self.SUCCESS_TITLE_BOX, match=self.SUCCESS_PATTERN))

    def close_success_overlay(self):
        self.click(
            self.SUCCESS_CLOSE_POS[0],
            self.SUCCESS_CLOSE_POS[1],
            move=True,
            down_time=0.01,
            after_sleep=0.2,
        )
        self.wait_until(
            lambda: not self.is_success_overlay(),
            time_out=self.RESULT_TIMEOUT,
            raise_if_not_found=False,
        )

    def clear_success_overlay_if_present(self):
        if self.is_success_overlay():
            self.close_success_overlay()

    def has_left_start_phase(self) -> bool:
        # 以“面板连续多帧消失”为准，避免 OCR 单帧漏检导致假阳性
        return self.is_start_panel_stable(
            expected=False,
            checks=self.START_PHASE_STABLE_CHECKS,
        )

    def is_start_panel_stable(self, expected: bool, checks: int = 2, interval: float = 0.06) -> bool:
        checks = max(1, int(checks))
        for i in range(checks):
            if self.is_start_panel() != expected:
                return False
            if i < checks - 1:
                self.sleep(interval)
                self.next_frame()
        return True

    def click_start_button(self, mode: str = "center") -> bool:
        texts = self.ocr_safe(*self.START_PANEL_BOX, match=self.START_BUTTON_PATTERN)
        target = self._resolve_start_button_target(texts, mode)
        if target is not None:
            click_x, click_y, x, y, width, height = target
            self.log_info(
                f"点击开始钓鱼 OCR 位置 mode={mode}: ({click_x}, {click_y}), "
                f"raw=({x}, {y}), size=({width}, {height})"
            )
            return self.dispatch_start_click(click_x, click_y)

        click_x, click_y = self.resolve_fallback_start_pos()
        self.log_info(f"未定位到开始钓鱼文本，改用兜底坐标点击 mode={mode}: ({click_x}, {click_y})")
        return self.dispatch_start_click(click_x, click_y)

    def _resolve_start_button_target(self, texts, mode: str):
        for text in texts:
            x = getattr(text, "x", None)
            y = getattr(text, "y", None)
            width = getattr(text, "width", 0) or 0
            height = getattr(text, "height", 0) or 0
            if x is None or y is None:
                continue
            click_x, click_y = self._calc_start_click_position(mode, x, y, width, height)
            return click_x, click_y, x, y, width, height
        return None

    def _calc_start_click_position(self, mode: str, x: float, y: float, width: float, height: float):
        if mode == "raw":
            return int(round(x)), int(round(y))
        if mode == "center":
            click_x = int(round(x + width / 2)) if width else int(round(x))
            click_y = int(round(y + height / 2)) if height else int(round(y))
            return click_x, click_y
        return self.resolve_fallback_start_pos()

    def resolve_fallback_start_pos(self):
        x, y = self.START_BUTTON_POS
        return int(round(self.width * x)), int(round(self.height * y))

    def dispatch_start_click(self, click_x: int, click_y: int) -> bool:
        # 按用户要求：开始钓鱼按钮只使用实体点击，不走虚拟点击
        self.log_info("开始钓鱼按钮使用实体点击")
        if not self.physical_click(click_x, click_y, down_time=0.04):
            return False
        return bool(self.wait_until(
            lambda: not self.is_start_panel(),
            time_out=0.8,
            raise_if_not_found=False,
        ))

    def resolve_click_target(self, x: int, y: int):
        interaction = self.executor.interaction
        hwnd_window = getattr(interaction, "hwnd_window", None)
        if hwnd_window is not None:
            hwnd = int((getattr(hwnd_window, "top_hwnd", 0) or getattr(hwnd_window, "hwnd", 0) or 0))
            tx, ty = hwnd_window.get_top_window_cords(int(round(x)), int(round(y)))
            return hwnd, int(round(tx)), int(round(ty))
        hwnd = int(getattr(interaction, "hwnd", 0) or 0)
        return hwnd, int(round(x)), int(round(y))

    def sendmessage_click(self, x: int, y: int, down_time: float = 0.03) -> bool:
        try:
            hwnd, tx, ty = self.resolve_click_target(x, y)
            if hwnd <= 0:
                return False
            self.log_info(f"SendMessage 点击坐标: hwnd={hwnd}, client=({tx},{ty})")
            lparam = win32api.MAKELONG(int(tx), int(ty))
            win32gui.SendMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
            win32gui.SendMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
            win32gui.SendMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
            time.sleep(max(0.01, float(down_time)))
            win32gui.SendMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
            return True
        except Exception as e:
            self.log_info(f"SendMessage 点击失败: {e}")
            return False

    def physical_click(self, x: int, y: int, down_time: float = 0.03) -> bool:
        try:
            hwnd, tx, ty = self.resolve_click_target(x, y)
            if hwnd <= 0:
                self.log_info("物理点击失败: hwnd 无效")
                return False
            # 确保点击坐标在游戏窗口 client 区内
            _, _, right, bottom = win32gui.GetClientRect(hwnd)
            max_x = max(0, int(right) - 1)
            max_y = max(0, int(bottom) - 1)
            tx = min(max(0, int(tx)), max_x)
            ty = min(max(0, int(ty)), max_y)
            self.log_info(f"物理点击坐标: hwnd={hwnd}, client=({tx},{ty}), client_size=({right},{bottom})")

            # 物理点击只能作用于前台窗口，先确保目标窗口前置
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass
            try:
                foreground = win32gui.GetForegroundWindow()
                if foreground != hwnd:
                    current_tid = win32api.GetCurrentThreadId()
                    target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
                    win32process.AttachThreadInput(current_tid, target_tid, True)
                    win32gui.SetForegroundWindow(hwnd)
                    win32process.AttachThreadInput(current_tid, target_tid, False)
            except Exception:
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass

            abs_x, abs_y = win32gui.ClientToScreen(hwnd, (int(tx), int(ty)))

            current_x, current_y = win32api.GetCursorPos()
            win32api.SetCursorPos((int(abs_x), int(abs_y)))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(max(0.01, float(down_time)))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            win32api.SetCursorPos((current_x, current_y))
            return True
        except Exception as e:
            self.log_info(f"物理点击失败: {e}")
            return False

    def reset_runtime_state(self):
        self._fishing_started = False
        self._last_bar_log_time = 0.0
        self._prev_pointer_center = None
        self._prev_error = 0.0
        self._prev_control_time = 0.0
        self._last_control_key = None
        self._same_key_streak = 0
        self._last_control_failed_escape = False

    def reel_hook(self) -> bool:
        # 咬钩后短时间重试几次 F，避免单次按键被吞
        for attempt in range(4):
            self.send_game_key("f", down_time=0.05, after_sleep=0.05)
            if self.wait_until(
                lambda: self.is_valid_bar_state(self.get_bar_state()) or self.is_success_overlay() or self.is_fail_signal(),
                time_out=0.9,
                raise_if_not_found=False,
            ):
                return True
            self.log_info(f"收杆未生效，重试按 F ({attempt + 1}/4)")
        return False

    def send_control_key(self, key: str, hold: float):
        # 纯后台按键（PostMessage）
        self.send_game_key(key, down_time=hold, after_sleep=0.0)

    def send_game_key(self, key: str, down_time: float = 0.02, after_sleep: float = 0.0):
        """
        F/A/D 走实体按键（前台窗口），其余保持原有按键通道。
        """
        interaction = self.executor.interaction
        if hasattr(interaction, "_dynamic_target_hwnd"):
            interaction._dynamic_target_hwnd = 0
        low_key = str(key).lower()
        if low_key in {"f", "a", "d"} and self.physical_key_press(low_key, hold=down_time):
            if after_sleep > 0:
                self.sleep(after_sleep)
            return True
        self.send_key(key, down_time=down_time, after_sleep=after_sleep)
        return True

    def physical_key_press(self, key: str, hold: float = 0.03) -> bool:
        try:
            hwnd = self.get_game_top_hwnd()
            if hwnd <= 0:
                self.log_info(f"实体按键失败: hwnd无效 key={key}")
                return False
            self._ensure_foreground(hwnd)

            vk = self._vk_code(key)
            scan = win32api.MapVirtualKey(vk, 0)
            self.log_info(f"发送实体按键: {str(key).upper()} (hwnd={hwnd}, vk={vk})")
            win32api.keybd_event(vk, scan, 0, 0)
            time.sleep(max(0.015, float(hold)))
            win32api.keybd_event(vk, scan, win32con.KEYEVENTF_KEYUP, 0)
            return True
        except Exception as e:
            self.log_info(f"实体按键失败 key={key}: {e}")
            return False

    def get_game_top_hwnd(self) -> int:
        interaction = self.executor.interaction
        hwnd_window = getattr(interaction, "hwnd_window", None)
        if hwnd_window is not None:
            return int(getattr(hwnd_window, "top_hwnd", 0) or getattr(hwnd_window, "hwnd", 0) or 0)
        return int(getattr(interaction, "hwnd", 0) or 0)

    def _ensure_foreground(self, hwnd: int):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass
        try:
            if win32gui.GetForegroundWindow() != hwnd:
                current_tid = win32api.GetCurrentThreadId()
                target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
                win32process.AttachThreadInput(current_tid, target_tid, True)
                win32gui.SetForegroundWindow(hwnd)
                win32process.AttachThreadInput(current_tid, target_tid, False)
        except Exception:
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass

    @staticmethod
    def _vk_code(key: str) -> int:
        key = str(key).lower()
        if key == "a":
            return 0x41
        if key == "d":
            return 0x44
        if key == "f":
            return 0x46
        vk = win32api.VkKeyScan(key)
        if isinstance(vk, int) and vk >= 0:
            return vk & 0xFF
        return ord(key.upper())

    def is_direction_invert(self) -> bool:
        value = self.config.get("方向反转", self.DIRECTION_INVERT)
        if isinstance(value, str):
            value = value.strip().lower()
            return value in {"1", "true", "yes", "on", "是"}
        return bool(value)

    def get_control_speed_factor(self) -> float:
        value = self.config.get("拉力速度", self.CONTROL_SPEED_PERCENT)
        try:
            percent = float(value)
        except Exception:
            percent = float(self.CONTROL_SPEED_PERCENT)
        percent = max(50.0, min(220.0, percent))
        return percent / 100.0
