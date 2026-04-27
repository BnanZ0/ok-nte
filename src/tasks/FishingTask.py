import time

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from ok import TaskDisabledException
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.utils import image_utils as iu


class FishingTask(BaseNTETask):
    BAR_BOX = (0.3164, 0.0646, 0.6875, 0.0743)
    BITE_INDICATOR_BOX = (0.9023, 0.8562, 0.9488, 0.9403)
    START_FISHING_BOX = (0.9102, 0.8743, 0.9387, 0.9271)
    FISH_BAIT_BOX = (0.8395, 0.8736, 0.8691, 0.9243)
    SUCCESS_TEXT_BOX = (0.4434, 0.8938, 0.5566, 0.9181)
    ENTER_FISHING_PANEL_BOX = (0.7113, 0.8247, 0.8089, 0.9111)
    SUCCESS_CLOSE_POS = (0.12, 0.88)
    OPEN_PANEL_TIMEOUT = 5
    BITE_TIMEOUT = 20
    CONTROL_TIMEOUT = 30
    RESULT_TIMEOUT = 10

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动钓鱼"
        self.description = "自动完成一轮或多轮钓鱼"
        self.icon = FluentIcon.GAME
        self.support_schedule_task = True
        self.default_config.update(
            {
                "循环次数": 1,
            }
        )
        self._fishing_started = False
        self._last_bar_log_time = 0.0
        self._prev_bar_pointer = None
        self._prev_bar_time = 0.0
        self._held_control_key = None
        self._last_control_switch_time = 0.0

    def click(self, *args, **kwargs):
        kwargs.setdefault("move", True)
        return super().click(*args, **kwargs)

    def run(self):
        NTEOneTimeTask.run(self)
        try:
            return self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.log_error("FishingTask error", e)
            raise

    def do_run(self):
        self.reset_runtime_state()
        self.enter_fishing_scene()
        rounds = max(1, int(self.config.get("循环次数", 1)))
        self.log_info(f"开始自动钓鱼，共 {rounds} 轮")
        success_count = 0
        for index in range(rounds):
            self.log_info(f"开始第 {index + 1}/{rounds} 轮钓鱼")
            if self.run_once(index + 1):
                success_count += 1
            else:
                self.log_error(f"第 {index + 1} 轮钓鱼失败")
                self.reset_runtime_state()
        self.info_set("Fishing Success Count", success_count)
        self.log_info(f"自动钓鱼结束，成功 {success_count}/{rounds}", notify=True)

    def run_once(self, round_index: int) -> bool:
        self.clear_success_overlay_if_present()

        if not self.cast_rod():
            raise TaskDisabledException("未检测到进入抛竿状态")

        if not self.wait_bite():
            self.screenshot(f"fishing_bite_timeout_{round_index}")
            return False

        if self.control_until_finish():
            return True

        return False

    def enter_fishing_scene(self) -> bool:
        if self.find_interac():
            box = self.box_of_screen(*self.ENTER_FISHING_PANEL_BOX)
            self.wait_until(
                lambda: self.find_one(Labels.skip_quest_confirm, box=box) is not None,
                pre_action=lambda: self.send_key("f", interval=1.5),
                time_out=self.OPEN_PANEL_TIMEOUT,
            )
            self.click(box)
            self.sleep(1.5)

    def cast_rod(self) -> bool:
        self.log_info("执行抛竿操作")
        if not self.wait_until(
            lambda: not self.is_fish_bait_exist() and self.is_fish_start_exist(),
            pre_action=lambda: self.send_key("f", interval=2),
            time_out=10,
        ):
            self.log_error("未检测到进入抛竿状态", notify=True)
            return False
        return True

    def wait_bite(self) -> bool:
        self.log_info("等待鱼儿咬钩")
        if self.wait_until(self.is_fishing_bite, time_out=self.BITE_TIMEOUT):
            self.log_info("鱼儿咬钩")
            if not self.wait_until(
                lambda: not self.is_fish_start_exist(),
                pre_action=lambda: self.send_key("f", interval=2),
                time_out=10,
            ):
                self.log_error("未检测到进入溜鱼状态")
                return False
            self.log_info("进入溜鱼状态")
            return True
        self.log_error("等待鱼儿咬钩超时")
        return False

    def control_until_finish(self) -> bool:
        deadline = time.time() + self.CONTROL_TIMEOUT
        failed_time = 0
        try:
            while time.time() < deadline:
                state = self.get_bar_state()
                if self.is_valid_bar_state(state):
                    self.apply_bar_control(state)
                else:
                    self._release_control_key()

                if self.is_fish_bait_exist():
                    if failed_time == 0:
                        failed_time = time.time()
                else:
                    failed_time = 0

                if failed_time != 0 and time.time() - failed_time > 5:
                    self.log_error("疑似脱钩或失败")
                    return False

                if self.is_success_overlay():
                    return True

                self.next_frame()
            self.log_error("控条阶段超时")
            return False
        finally:
            self._release_control_key()

    def apply_bar_control(self, state: dict):
        now = time.time()
        pointer = int(state["pointer_center"])
        zone_left = int(state["zone_left"])
        zone_right = int(state["zone_right"])

        zone_center = (zone_left + zone_right) // 2
        zone_width = max(1, zone_right - zone_left)

        prev_pointer = self._prev_bar_pointer if self._prev_bar_pointer is not None else pointer
        dt = max(0.001, now - self._prev_bar_time) if self._prev_bar_time else 0.016
        velocity = (pointer - prev_pointer) / dt
        velocity = max(-1800.0, min(1800.0, velocity))

        # Adapt lead time to the actual frame interval. Slower machines need
        # more forward prediction, faster machines need less.
        lead_time = min(0.080, max(0.035, dt * 2.0))
        predicted_pointer = int(round(pointer + velocity * lead_time))
        error = predicted_pointer - zone_center
        abs_error = abs(error)

        hard_deadzone = max(5, int(zone_width * 0.08))
        soft_deadzone = max(hard_deadzone + 6, int(zone_width * 0.16))

        if abs_error <= hard_deadzone:
            if now - self._last_bar_log_time > 0.5:
                self.log_info(
                    f"控条稳定: pointer={pointer}, predict={predicted_pointer}, "
                    f"target={zone_center}, hard={hard_deadzone}"
                )
                self._last_bar_log_time = now
            self._release_control_key()
            self._prev_bar_pointer = pointer
            self._prev_bar_time = now
            return

        desired_key = "d" if error < 0 else "a"

        moving_toward_center = (error < 0 and velocity > 60) or (error > 0 and velocity < -60)
        moving_away = (error < 0 and velocity < -60) or (error > 0 and velocity > 60)

        # Hysteresis:
        # - once we are already holding the correct direction, keep holding it
        #   until we truly enter the hard deadzone
        # - only release early when we are very close to center and momentum is
        #   clearly carrying the pointer inward
        if abs_error <= soft_deadzone:
            if self._held_control_key == desired_key:
                desired_key = self._held_control_key
            elif moving_toward_center and abs_error <= max(hard_deadzone + 3, hard_deadzone * 2):
                desired_key = None

        # Direction-switch hysteresis:
        # when we already hold one direction, do not instantly flip on a small
        # opposite error near the boundary. Require either more error or a short
        # dwell time before reversing.
        if (
            desired_key is not None
            and self._held_control_key is not None
            and desired_key != self._held_control_key
        ):
            time_since_switch = now - self._last_control_switch_time
            switch_error_gate = max(soft_deadzone + 4, int(zone_width * 0.28))
            if abs_error < switch_error_gate and time_since_switch < 0.14:
                desired_key = self._held_control_key

        if now - self._last_bar_log_time > 0.2:
            self.log_info(
                f"控条输入: key={desired_key}, held={self._held_control_key}, pointer={pointer}, "
                f"predict={predicted_pointer}, target={zone_center}, error={abs_error}, "
                f"vel={velocity:.1f}, lead={lead_time:.3f}, hard={hard_deadzone}, "
                f"soft={soft_deadzone}, away={moving_away}"
            )
            self._last_bar_log_time = now

        self._set_control_key(desired_key)
        self._prev_bar_pointer = pointer
        self._prev_bar_time = now

    def _set_control_key(self, key):
        if key == self._held_control_key:
            return
        if self._held_control_key is not None:
            self.send_key_up(self._held_control_key)
            self._held_control_key = None
        if key is not None:
            self.send_key_down(key)
            self._held_control_key = key
            self._last_control_switch_time = time.time()

    def _release_control_key(self):
        if self._held_control_key is not None:
            self.send_key_up(self._held_control_key)
            self._held_control_key = None

    def get_bar_state(self):
        return self.detect_fishing_bar_state()

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
        edge_zone = zone_left <= 1 or zone_right >= image_width - 2
        if edge_zone and abs(pointer_center - int((zone_left + zone_right) / 2)) > int(
            image_width * 0.38
        ):
            return False
        return True

    def is_fishing_entry(self) -> bool:
        return False

    def is_start_panel(self) -> bool:
        return False

    def is_success_overlay(self) -> bool:
        return self.is_success_text_exist()

    def close_success_overlay(self):
        self.wait_until(
            lambda: not self.is_success_overlay(),
            pre_action=lambda: self.click(
                self.SUCCESS_CLOSE_POS[0],
                self.SUCCESS_CLOSE_POS[1],
            ),
            time_out=self.RESULT_TIMEOUT,
        )
        self.wait_until(self.is_fish_start_exist, time_out=5)
        self.sleep(0.5)

    def clear_success_overlay_if_present(self):
        if self.is_success_overlay():
            self.close_success_overlay()

    def reset_runtime_state(self):
        self._fishing_started = False
        self._last_bar_log_time = 0.0
        self._prev_bar_pointer = None
        self._prev_bar_time = 0.0
        self._held_control_key = None
        self._last_control_switch_time = 0.0

    def detect_fishing_bar_state(self):
        box = self.box_of_screen(*self.BAR_BOX, name="fishing_bar")
        image = box.crop_frame(self.frame)
        if image is None or image.size == 0:
            return None

        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        green_mask = cv2.inRange(
            hsv,
            np.array([50, 150, 160], dtype=np.uint8),
            np.array([160, 220, 255], dtype=np.uint8),
        )
        yellow_mask = cv2.inRange(
            hsv,
            np.array([20, 60, 195], dtype=np.uint8),
            np.array([55, 200, 255], dtype=np.uint8),
        )

        kernel = np.ones((3, 3), dtype=np.uint8)
        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, kernel)
        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, kernel)
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_OPEN, kernel)
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_CLOSE, kernel)

        yellow_contours, _ = cv2.findContours(
            yellow_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        if yellow_contours:
            yellow_max_contour = max(yellow_contours, key=cv2.contourArea)
            px, _, pw, _ = cv2.boundingRect(yellow_max_contour)
            pointer_center = px + pw // 2
        else:
            pointer_center = -1

        green_contours, _ = cv2.findContours(
            green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        green_candidates = []
        for contour in green_contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w >= 5 and h >= 5:
                green_candidates.append((x, y, w, h))

        if not green_candidates:
            return None

        green_candidates.sort(key=lambda item: item[0])

        if len(green_candidates) == 1:
            zone_x, _, zone_w, _ = green_candidates[0]
            zone_left = zone_x
            zone_right = zone_x + zone_w
        else:
            min_x = green_candidates[0][0]
            max_x_w = green_candidates[-1][0] + green_candidates[-1][2]
            zone_left = min_x
            zone_right = max_x_w
            zone_w = zone_right - zone_left

        return {
            "zone_left": zone_left,
            "zone_right": zone_right,
            "zone_center": zone_left + zone_w // 2,
            "zone_width": zone_w,
            "image_width": int(image.shape[1]),
            "pointer_center": pointer_center,
            "in_zone": zone_left <= pointer_center <= zone_right,
        }

    def is_fish_start_exist(self):
        return self.find_one(Labels.fish_start)

    def is_success_text_exist(self):
        box = self.box_of_screen(*self.SUCCESS_TEXT_BOX, name="success_text")
        return self.calculate_color_percentage(text_white_color, box) > 0.2

    def is_fish_bait_exist(self):
        return self.find_one(Labels.fish_bait)

    def is_fishing_bite(self):
        box = self.box_of_screen(*self.BITE_INDICATOR_BOX, name="fishing_bite_indicator")
        image = box.crop_frame(self.frame)

        blue_mask = iu.create_color_mask(image, fishing_bite_blue_color, gray=True)

        h, w = blue_mask.shape[:2]
        center = (w // 2, h // 2)
        max_radius = min(h, w) // 2
        target_radius = int(max_radius * 0.7)

        circle_mask = np.ones((h, w), dtype="uint8")
        cv2.circle(circle_mask, center, target_radius, 0, -1)

        masked_blue = cv2.bitwise_and(blue_mask, circle_mask)
        blue_pixels = int(cv2.countNonZero(masked_blue))
        total_circle_pixels = int(cv2.countNonZero(circle_mask))

        if total_circle_pixels == 0:
            return 0.0

        blue_pixels_ratio = blue_pixels / total_circle_pixels
        return blue_pixels_ratio > 0.07


fishing_bite_blue_color = {
    "r": (30, 35),
    "g": (120, 130),
    "b": (250, 255),
}

text_white_color = {
    "r": (210, 255),
    "g": (210, 255),
    "b": (210, 255),
}
