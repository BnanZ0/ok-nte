import time

import cv2
import numpy as np
from ok import TaskDisabledException
from qfluentwidgets import FluentIcon

from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.utils import image_utils as iu


class FishingTask(BaseNTETask):
    # 1080p 固定参数（“循环次数”“方向反转”开放配置）
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
    BAR_TOLERANCE = 15
    CONTROL_TAP_HOLD = 0.05

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
        self._last_control_failed_escape = False

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
                self.log_error(f"第 {index + 1} 轮钓鱼失败", notify=True)
                # 失败后重置状态继续下一轮，避免“设置2轮只跑1轮”
                self.reset_runtime_state()
        self.info_set("Fishing Success Count", success_count)
        self.log_info(f"自动钓鱼结束，成功 {success_count}/{rounds}", notify=True)

    def run_once(self, round_index: int) -> bool:
        self.clear_success_overlay_if_present()
        round_deadline = time.time() + max(
            30.0, float(self.BITE_TIMEOUT) + float(self.CONTROL_TIMEOUT) + 12.0
        )
        round_attempt = 0
        while time.time() < round_deadline:
            round_attempt += 1
            if round_attempt > 1:
                self.log_info(f"第 {round_index} 轮自动重试抛竿: 第 {round_attempt} 次")

            if not self.cast_rod():
                raise TaskDisabledException("未检测到进入抛竿状态")

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
            lambda: not self.is_fish_bait_exist(),
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
        else:
            self.log_error("等待鱼儿咬钩超时")
            return False

    def control_until_finish(self) -> bool:
        self._last_control_failed_escape = False
        deadline = time.time() + self.CONTROL_TIMEOUT
        while time.time() < deadline:
            state = self.get_bar_state()
            if self.is_valid_bar_state(state):
                self.apply_bar_control(state)

            if self.is_fish_bait_exist():
                if self.wait_until(lambda: not self.is_fish_bait_exist(), time_out=5):
                    if self.wait_until(self.is_success_overlay, time_out=5):
                        return True
                self.log_error("疑似脱钩或失败")
                self._last_control_failed_escape = True
                break

            if self.is_success_overlay():
                return True

            self.next_frame()
        else:
            self.log_error("控条阶段超时")
        return False

    def apply_bar_control(self, state: dict):
        now = time.time()
        pointer = int(state["pointer_center"])
        zone_left = int(state["zone_left"])
        zone_right = int(state["zone_right"])
        zone_center = int(state.get("zone_center", (zone_left + zone_right) // 2))
        image_width = int(state.get("image_width", None))
        zone_width = max(1, zone_right - zone_left)

        # 容差范围内认为稳定
        tolerance = max(0, int(self.BAR_TOLERANCE))
        if image_width is not None:
            tolerance = int(tolerance * image_width / 934)
        if zone_left + tolerance <= pointer <= zone_right - tolerance:
            if now - self._last_bar_log_time > 0.5:
                self.log_info(f"控条稳定区: pointer={pointer}, zone=({zone_left},{zone_right})")
                self._last_bar_log_time = now
            return

        key = "d" if pointer < zone_center else "a"

        ratio = abs(pointer - zone_center) / zone_width

        base_hold = float(self.CONTROL_TAP_HOLD)
        hold = min(0.12, max(0.02, base_hold + ratio * 0.05))

        burst = 2 if ratio > 0.5 else 1

        if now - self._last_bar_log_time > 0.2:
            self.log_info(f"控条输入: key={key}, hold={hold:.3f}, burst={burst}, ratio={ratio:.2f}")
            self._last_bar_log_time = now

        for _ in range(burst):
            self.send_key(key, down_time=hold)

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
        # 过滤明显误检：绿区贴边且指针又远离，通常不是有效拉力条
        edge_zone = zone_left <= 1 or zone_right >= image_width - 2
        if edge_zone and abs(pointer_center - int((zone_left + zone_right) / 2)) > int(
            image_width * 0.38
        ):
            return False
        return True

    def is_fishing_entry(self) -> bool:
        # TODO: 替换为非 OCR 方式检测“钓鱼”交互入口 (如图标匹配或特征颜色)
        return False

    def is_start_panel(self) -> bool:
        # TODO: 替换为非 OCR 方式检测“钓鱼准备面板”或“开始钓鱼”按钮
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
        self._last_control_failed_escape = False

    def detect_fishing_bar_state(self):
        """
        Detect the fishing control bar state from a cropped top-bar image using
        contour analysis to filter noise.
        """
        box = self.box_of_screen(*self.BAR_BOX, name="fishing_bar")
        image = box.crop_frame(self.frame)
        if image is None or image.size == 0:
            return None

        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        green_mask = cv2.inRange(
            hsv, np.array([50, 150, 160], dtype=np.uint8), np.array([160, 220, 255], dtype=np.uint8)
        )
        yellow_mask = cv2.inRange(
            hsv, np.array([20, 60, 195], dtype=np.uint8), np.array([55, 200, 255], dtype=np.uint8)
        )

        kernel = np.ones((3, 3), dtype=np.uint8)
        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, kernel)
        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, kernel)
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_OPEN, kernel)
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_CLOSE, kernel)
        # iu.show_images([green_mask, yellow_mask], names=["green_mask", "yellow_mask"])

        green_contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        green_candidates = []
        for contour in green_contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w >= 20 and h >= 5:
                green_candidates.append((x, y, w, h))
        if not green_candidates:
            return None

        zone_x, _, zone_w, zone_h = max(green_candidates, key=lambda item: item[2] * item[3])
        zone_left = zone_x
        zone_right = zone_x + zone_w

        yellow_contours, _ = cv2.findContours(
            yellow_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        pointer_candidates = []
        vertical_min = max(4, int(zone_h * 0.5))
        for contour in yellow_contours:
            x, y, w, h = cv2.boundingRect(contour)
            if h >= vertical_min and w <= 30:
                pointer_candidates.append((x, y, w, h))

        if pointer_candidates:
            pointer_x, _, pointer_w, _ = max(
                pointer_candidates, key=lambda item: item[3] * max(1, item[2])
            )
            pointer_center = pointer_x + pointer_w // 2
        else:
            # 兜底：按黄线列投影找最亮竖线（避免轮廓断裂时丢指针）
            col_sum = np.sum(yellow_mask > 0, axis=0)
            idx = int(np.argmax(col_sum))
            if col_sum[idx] < vertical_min:
                return None
            pointer_center = idx

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
        """
        检测开始钓鱼按钮是否存在
        """
        # box = self.box_of_screen(*self.START_FISHING_BOX, name="start_fishing")
        # return self.calculate_color_percentage(text_white_color, box) > 0.09
        return self.find_one(Labels.fish_start)

    def is_success_text_exist(self):
        """
        检测成功文本是否存在
        """
        box = self.box_of_screen(*self.SUCCESS_TEXT_BOX, name="success_text")
        return self.calculate_color_percentage(text_white_color, box) > 0.2

    def is_fish_bait_exist(self):
        """
        检测鱼饵是否存在
        """
        # box = self.box_of_screen(*self.FISH_BAIT_BOX, name="fish_bait")
        # return self.calculate_color_percentage(text_white_color, box) > 0.06
        return self.find_one(Labels.fish_bait)

    def is_fishing_bite(self):
        """
        Detect the blue bite/reel indicator shown at the bottom-right of the fishing UI.
        聚焦于中心 70% 半径区域以提高识别精度。
        """
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
