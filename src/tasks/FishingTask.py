import re
import time
import ctypes
from ctypes import wintypes

import win32api
import win32con
import win32gui
from win32api import GetCursorPos, SetCursorPos
from qfluentwidgets import FluentIcon

from src.tasks.BaseNTETask import BaseNTETask
from src.utils import image_utils as iu


INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


class FishingTask(BaseNTETask):
    # 1080p 固定参数（仅“循环次数”开放配置）
    ENTRY_BOX = (0.54, 0.50, 0.82, 0.60)
    START_PANEL_BOX = (0.63, 0.08, 0.98, 0.95)
    BITE_BOX = (0.30, 0.66, 0.72, 0.88)
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
    BITE_BLUE_THRESHOLD = 260
    CONTROL_TAP_HOLD = 0.05
    CONTROL_USE_LONG_PRESS = True
    CONTROL_LONG_PRESS_THRESHOLD = 10
    CONTROL_LONG_PRESS_HOLD = 0.18
    DIRECTION_INVERT = True

    ENTRY_PATTERN = re.compile(r"钓鱼", re.IGNORECASE)
    START_PATTERN = re.compile(r"开始钓鱼|钓鱼准备", re.IGNORECASE)
    START_BUTTON_PATTERN = re.compile(r"开始钓鱼", re.IGNORECASE)
    BITE_PATTERN = re.compile(r"上钩|钩了", re.IGNORECASE)
    FAIL_PATTERN = re.compile(r"溜走|跑掉|失败", re.IGNORECASE)
    SUCCESS_PATTERN = re.compile(r"钓鱼经验|点击空白区域关闭|垂钓等级", re.IGNORECASE)
    VK_CODE = {
        "a": 0x41,
        "d": 0x44,
        "f": 0x46,
        "enter": 0x0D,
        "space": 0x20,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动钓鱼"
        self.description = "自动完成一轮或多轮钓鱼"
        self.icon = FluentIcon.GAME
        self.support_schedule_task = True
        self.default_config.update({"循环次数": 1})
        self._fishing_started = False
        self._last_bite_log_time = 0.0
        self._last_bar_log_time = 0.0

    def run(self):
        self.reset_runtime_state()
        rounds = max(1, int(self.config.get("循环次数", 1)))
        self.log_info(f"开始自动钓鱼，共 {rounds} 轮", notify=True)
        success_count = 0
        try:
            for index in range(rounds):
                self.log_info(f"开始第 {index + 1}/{rounds} 轮钓鱼")
                if self.run_once(index + 1):
                    success_count += 1
                else:
                    self.log_error(f"第 {index + 1} 轮钓鱼失败", notify=True)
                    break
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

        if not self.cast_rod():
            self.screenshot(f"fishing_cast_failed_{round_index}")
            return False

        if not self.wait_bite():
            self.screenshot(f"fishing_bite_timeout_{round_index}")
            return False

        if not self.control_until_finish():
            self.screenshot(f"fishing_control_failed_{round_index}")
            return False

        return True

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

        self.send_key("f", after_sleep=0.2)
        return self.wait_until(
            self.is_start_panel,
            time_out=self.OPEN_PANEL_TIMEOUT,
            raise_if_not_found=False,
        )

    def start_fishing(self) -> bool:
        if not self.is_start_panel():
            return False

        strategies = [
            ("OCR中心坐标物理单击", lambda: self.click_start_button(mode="center")),
            ("OCR原始坐标物理单击", lambda: self.click_start_button(mode="raw")),
            ("固定坐标物理单击", lambda: self.click_start_button(mode="fallback")),
        ]

        for strategy_name, action in strategies:
            self.log_info(f"尝试开始钓鱼策略: {strategy_name}")
            action()
            if self.wait_until(
                self.has_left_start_phase,
                time_out=3,
                raise_if_not_found=False,
            ):
                self.log_info(f"开始钓鱼成功，策略: {strategy_name}")
                return True

        return False

    def cast_rod(self) -> bool:
        self.log_info("执行抛竿操作")
        self.physical_key_press("f", hold=0.05, after_sleep=0.25)
        start = time.time()
        while time.time() - start < 4:
            if self.is_bite_signal() or self.get_bar_state() is not None:
                return True
            self.next_frame()
        return True

    def wait_bite(self) -> bool:
        start = time.time()
        timeout = float(self.BITE_TIMEOUT)
        while time.time() - start < timeout:
            bite_indicator = self.get_bite_indicator_state()
            blue_pixels = bite_indicator["blue_pixels"] if bite_indicator is not None else 0
            white_pixels = bite_indicator["white_pixels"] if bite_indicator is not None else 0
            threshold = int(self.BITE_BLUE_THRESHOLD)
            now = time.time()
            if now - self._last_bite_log_time > 0.6:
                self.log_info(
                    f"检测咬钩蓝环: blue_pixels={blue_pixels}, "
                    f"white_pixels={white_pixels}, threshold={threshold}"
                )
                self._last_bite_log_time = now

            if blue_pixels >= threshold:
                self.log_info("检测到右下角蓝色收杆环，立即按 F 收杆")
                if self.reel_hook():
                    return True

            if self.is_bite_signal():
                self.log_info(
                    "检测到文字上钩提示，立即按 F 收杆"
                )
                if self.reel_hook():
                    return True
            if self.is_fail_signal():
                self.log_error("检测到鱼已溜走，本轮钓鱼失败")
                return False
            if self.get_bar_state() is not None:
                self.log_info("未捕获到文字提示，但已进入控条阶段")
                return True
            self.next_frame()
        return False

    def control_until_finish(self) -> bool:
        start = time.time()
        timeout = float(self.CONTROL_TIMEOUT)
        while time.time() - start < timeout:
            if self.is_success_overlay():
                self.close_success_overlay()
                return True

            state = self.get_bar_state()
            if state is not None:
                self.apply_bar_control(state)
            elif time.time() - self._last_bar_log_time > 0.5:
                self.log_info("控条阶段: 未识别到有效指针/绿区，等待下一帧")
                self._last_bar_log_time = time.time()
            elif time.time() - start > 1.0 and self.is_fishing_entry():
                self.log_error("钓鱼条阶段提前结束，疑似脱钩或失败")
                return False

            self.next_frame()

        return False

    def apply_bar_control(self, state: dict):
        tolerance = max(0, int(self.BAR_TOLERANCE))
        pointer_center = state["pointer_center"]
        zone_left = state["zone_left"]
        zone_right = state["zone_right"]
        invert = bool(self.DIRECTION_INVERT)
        use_long_press = bool(self.CONTROL_USE_LONG_PRESS)
        long_press_threshold = max(1, int(self.CONTROL_LONG_PRESS_THRESHOLD))
        tap_hold = float(self.CONTROL_TAP_HOLD)
        long_hold = float(self.CONTROL_LONG_PRESS_HOLD)
        now = time.time()
        distance = 0

        if pointer_center < zone_left - tolerance:
            distance = (zone_left - tolerance) - pointer_center
            key = "d" if invert else "a"
        elif pointer_center > zone_right + tolerance:
            distance = pointer_center - (zone_right + tolerance)
            key = "a" if invert else "d"
        else:
            if now - self._last_bar_log_time > 0.5:
                self.log_info(
                    f"控条稳定区: pointer={pointer_center}, zone=({zone_left},{zone_right}), tolerance={tolerance}"
                )
                self._last_bar_log_time = now
            return

        press_hold = tap_hold
        if use_long_press and distance >= long_press_threshold:
            # 偏差越大，按住越久，提高追条速度
            ratio = min(distance, 120) / 120.0
            press_hold = long_hold + 0.12 * ratio

        burst = 2 if distance >= 90 else 1

        if now - self._last_bar_log_time > 0.2:
            self.log_info(
                f"控条输入: key={key}, hold={press_hold:.3f}, burst={burst}, dist={distance}, "
                f"pointer={pointer_center}, zone=({zone_left},{zone_right}), tolerance={tolerance}"
            )
            self._last_bar_log_time = now

        for _ in range(burst):
            self.send_control_key(key, hold=press_hold)

    def get_bar_state(self):
        box = self.box_of_screen(*self.BAR_BOX, name="fishing_bar")
        bar_image = box.crop_frame(self.frame)
        return iu.detect_fishing_bar_state(bar_image)

    def get_bite_indicator_state(self):
        box = self.box_of_screen(*self.BITE_INDICATOR_BOX, name="fishing_bite_indicator")
        indicator_image = box.crop_frame(self.frame)
        return iu.detect_fishing_bite_indicator(indicator_image)

    def is_fishing_entry(self) -> bool:
        return bool(self.ocr(*self.ENTRY_BOX, match=self.ENTRY_PATTERN))

    def is_start_panel(self) -> bool:
        return bool(self.ocr(*self.START_PANEL_BOX, match=self.START_PATTERN))

    def is_start_button_visible(self) -> bool:
        return bool(self.ocr(*self.START_PANEL_BOX, match=self.START_BUTTON_PATTERN))

    def is_bite_signal(self) -> bool:
        return bool(self.ocr(*self.BITE_BOX, match=self.BITE_PATTERN))

    def is_fail_signal(self) -> bool:
        return bool(self.ocr(*self.BITE_BOX, match=self.FAIL_PATTERN))

    def is_success_overlay(self) -> bool:
        if self.ocr(*self.SUCCESS_BOX, match=self.SUCCESS_PATTERN):
            return True
        return bool(self.ocr(*self.SUCCESS_TITLE_BOX, match=self.SUCCESS_PATTERN))

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
        if not self.is_start_button_visible():
            return True
        if self.get_bar_state() is not None:
            return True
        if self.is_bite_signal():
            return True
        return False

    def click_start_button(self, mode: str = "center") -> bool:
        texts = self.ocr(*self.START_PANEL_BOX, match=self.START_BUTTON_PATTERN)
        for text in texts:
            name = str(getattr(text, "name", ""))
            if "开始钓鱼" not in name:
                continue

            x = getattr(text, "x", None)
            y = getattr(text, "y", None)
            width = getattr(text, "width", 0) or 0
            height = getattr(text, "height", 0) or 0
            if x is None or y is None:
                continue

            if mode == "raw":
                click_x = int(round(x))
                click_y = int(round(y))
            elif mode == "center":
                click_x = int(round(x + width / 2)) if width else int(round(x))
                click_y = int(round(y + height / 2)) if height else int(round(y))
            else:
                click_x = self.START_BUTTON_POS[0]
                click_y = self.START_BUTTON_POS[1]

            self.log_info(
                f"点击开始钓鱼 OCR 位置 mode={mode}: ({click_x}, {click_y}), "
                f"raw=({x}, {y}), size=({width}, {height})"
            )
            self.physical_click(click_x, click_y)
            return True

        self.log_info(f"未定位到开始钓鱼文本，改用兜底坐标点击 mode={mode}")
        self.physical_click(self.START_BUTTON_POS[0], self.START_BUTTON_POS[1])
        return True

    def reset_runtime_state(self):
        self._fishing_started = False
        self._last_bite_log_time = 0.0
        self._last_bar_log_time = 0.0

    def reel_hook(self) -> bool:
        # 咬钩后短时间重试几次 F，避免单次按键被吞
        for attempt in range(3):
            self.physical_key_press("f", hold=0.05, after_sleep=0.08)
            if self.wait_until(
                lambda: self.get_bar_state() is not None or self.is_success_overlay() or self.is_fail_signal(),
                time_out=0.7,
                raise_if_not_found=False,
            ):
                return True
            self.log_info(f"收杆未生效，重试按 F ({attempt + 1}/3)")
        return False

    def physical_click(self, x, y, down_time: float = 0.03):
        original_position = GetCursorPos()
        interaction = self.executor.interaction
        try:
            interaction.try_activate()
        except Exception:
            pass

        self._focus_game_window()

        time.sleep(0.08)
        abs_x, abs_y = interaction.capture.get_abs_cords(x, y)
        abs_x = int(round(abs_x))
        abs_y = int(round(abs_y))
        SetCursorPos((abs_x, abs_y))
        time.sleep(0.06)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(down_time)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.08)
        SetCursorPos(original_position)

    def _focus_game_window(self):
        interaction = self.executor.interaction
        try:
            interaction.try_activate()
        except Exception:
            pass

        hwnd = getattr(getattr(interaction, "capture", None), "hwnd", None)
        if hwnd:
            try:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
                win32gui.SetActiveWindow(hwnd)
            except Exception:
                pass

    def physical_key_press(self, key: str, hold: float = 0.05, after_sleep: float = 0.0, focus: bool = True):
        vk = self.VK_CODE.get(str(key).lower())
        if vk is None:
            self.log_error(f"不支持的物理按键: {key}")
            return

        if focus:
            self._focus_game_window()
            time.sleep(0.03)
        self.log_info(f"发送物理按键: {key.upper()}")
        sent = False
        try:
            sent = self._send_key_sendinput(vk, hold)
        except Exception as e:
            self.log_info(f"SendInput 发送失败，回退 keybd_event: {e}")
        if not sent:
            # 兜底回退到 keybd_event
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(hold)
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        if after_sleep > 0:
            time.sleep(after_sleep)

    def send_control_key(self, key: str, hold: float):
        # 控条专用：真实键 + 框架键双通道，提高 A/D 生效概率
        self.physical_key_press(key, hold=hold, after_sleep=0.0, focus=False)
        try:
            self.send_key(key, down_time=min(0.02, hold), after_sleep=0.0)
        except Exception as e:
            self.log_info(f"控条框架按键发送失败: {e}")

    def _send_key_sendinput(self, vk: int, hold: float) -> bool:
        user32 = ctypes.windll.user32
        scan = user32.MapVirtualKeyW(vk, 0)
        if scan == 0:
            return False

        down = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUTUNION(
                ki=KEYBDINPUT(
                    wVk=0,
                    wScan=scan,
                    dwFlags=KEYEVENTF_SCANCODE,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
        up = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUTUNION(
                ki=KEYBDINPUT(
                    wVk=0,
                    wScan=scan,
                    dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )

        user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        user32.SendInput.restype = wintypes.UINT

        down_ok = user32.SendInput(1, ctypes.pointer(down), ctypes.sizeof(INPUT)) == 1
        time.sleep(hold)
        up_ok = user32.SendInput(1, ctypes.pointer(up), ctypes.sizeof(INPUT)) == 1
        return down_ok and up_ok
