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
    DEFAULT_MOVE = True
    BAR_BOX = (0.3164, 0.0646, 0.6875, 0.0743)
    BITE_INDICATOR_BOX = (0.9023, 0.8562, 0.9488, 0.9403)
    START_FISHING_BOX = (0.9102, 0.8743, 0.9387, 0.9271)
    FISH_BAIT_BOX = (0.8395, 0.8736, 0.8691, 0.9243)
    SUCCESS_TEXT_BOX = (0.4434, 0.8938, 0.5566, 0.9181)
    ENTER_FISHING_PANEL_BOX = (0.7113, 0.8247, 0.8089, 0.9111)
    SUCCESS_CLOSE_POS = (0.12, 0.88)

    # 抛竿失败后点击的按钮位置（相对坐标）
    CAST_RETRY_POS = (0.839, 0.875)

    # 卖鱼操作相对坐标序列
    SELL_FISH_STEP1 = (0.075, 0.391)   # 点击售鱼类别
    SELL_FISH_STEP2 = (0.559, 0.9)     # 选择鱼
    SELL_FISH_STEP3 = (0.609, 0.666)   # 确认数量
    SELL_FISH_CONFIRM = (0.5, 0.889)   # 确认出售

    # 买鱼饵操作相对坐标
    BAIT_BUY_CONFIRM1 = (0.9523, 0.8784)  # 点击购买
    BAIT_BUY_CONFIRM2 = (0.843, 0.951)    # 确认数量
    BAIT_BUY_ok = (0.604,0.666)
    BAIT_BUY_CONFIRM3 = (0.5, 0.908)      # 最终确认

    # 鱼饵商店 OCR 框（左侧商品列表区域）
    BAIT_SHOP_OCR_BOX = (0.0, 0.098, 0.35, 0.335)

    OPEN_PANEL_TIMEOUT = 5
    BITE_TIMEOUT = 20
    CONTROL_TIMEOUT = 30

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动钓鱼"
        self.description = "自动完成一轮或多轮钓鱼"
        self.icon = FluentIcon.GAME
        self.support_schedule_task = True
        self.default_config.update(
            {
                "循环次数": 1,
                "控条模式": "长按",
                "点按时长倍率": 1.0,
                "使用ESC": False,
                "自动卖鱼": False,
                "自动续鱼饵": False,
                "万能鱼饵位置": "1",      # 新增：1=第1个, 2=第2个, 3=第3个
                "买饵钓鱼循环次数": 0,   # 0 表示无限循环
            }
        )
        self.config_description.update(
            {
                "控条模式": "长按：平滑流畅, 易过冲\n点按: 安全较慢, 防过冲",
                "点按时长倍率": "点按模式专用。用于微调每次按键的持续时间",
                "使用ESC": "开启后优先通过 ESC 键关闭成功界面，避免后台抢占鼠标。\n"
                "若游戏运行不流畅，可能因按键响应延迟导致误退出钓鱼场景",
                "自动卖鱼": "每轮钓鱼结束后自动打开售鱼处并出售鱼获",
                "自动续鱼饵": "钓鱼任务结束或鱼饵耗尽时，自动开启商店购买万能鱼饵并重新钓鱼",
                "万能鱼饵位置": "商店中万能鱼饵所在的位置（从上往下数第几个）",
            }
        )
        self.config_type["控条模式"] = {
            "type": "drop_down",
            "options": ["长按", "点按"],
        }
        self.config_type["万能鱼饵位置"] = {
            "type": "drop_down",
            "options": ["1", "2", "3"],
        }
        self._fishing_started = False
        self._last_bar_log_time = 0.0
        self._morph_kernel = np.ones((3, 3), dtype=np.uint8)
        self._bar_active_key = None
        self._last_direction = None
        self.sleep_check_interval = 1
        self.add_exit_after_config()

    def run(self):
        NTEOneTimeTask.run(self)
        try:
            return self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.log_error("FishingTask error", e)
            raise

    def sleep_check(self):
        if self.should_check_monthly_card():
            self.handle_monthly_card()
        return

    def do_run(self):
        self.reset_runtime_state()
        self.enter_fishing_scene()
    
        rounds = max(1, int(self.config.get("循环次数", 1)))
        outer_limit = int(self.config.get("买饵钓鱼循环次数", 0))  # 0 = 无限
        outer_index = 0
    
        while True:
            outer_index += 1
            if outer_limit > 0:
                self.log_info(f"【外层循环 {outer_index}/{outer_limit}】开始，共钓 {rounds} 轮")
            else:
                self.log_info(f"【外层循环 第{outer_index}次】开始，共钓 {rounds} 轮")
    
            success_count = 0
            for index in range(rounds):
                self.log_info(f"  开始第 {index + 1}/{rounds} 轮钓鱼")
                if self.run_once(index + 1):
                    success_count += 1
                else:
                    self.log_error(f"  第 {index + 1} 轮钓鱼失败")
                    self.reset_runtime_state()
    
            self.info_set("Fishing Success Count", success_count)
            self.log_info(f"本轮结束，成功 {success_count}/{rounds}")
    
            # 本轮 x 次钓完后，执行卖鱼/续饵
            if self.config.get("自动卖鱼", False):
                self._do_sell_fish()
            if self.config.get("自动续鱼饵", False):
                self._do_buy_bait()
    
            # 判断是否退出外层循环
            if outer_limit > 0 and outer_index >= outer_limit:
                self.log_info(f"已完成 {outer_limit} 次完整循环，任务结束", notify=True)
                break
    
            # 继续下一轮前重新进入钓鱼场景
            self.reset_runtime_state()
            self.enter_fishing_scene()

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


    # ------------------------------------------------------------------ #
    #  抛竿
    # ------------------------------------------------------------------ #

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
        def post():
            if self.is_success_overlay():
                self.log_info("抛竿时检测到成功面板, 尝试关闭")
                self.do_close_success_overlay()
        self.log_info("执行抛竿操作")
        if not self.wait_until(
            lambda: not self.is_fish_bait_exist() and self.is_fish_start_exist(),
            pre_action=lambda: self.send_key("f", interval=1.5),
            post_action=post,
            time_out=20,
        ):
            self.send_key("f")
            frame = self.frame
            self.screenshot("fishing_cast_timeout", frame=frame)
            text = self.ocr(0.4090, 0.4778, 0.5914, 0.5188, frame=frame)
            self.log_error("未检测到进入抛竿状态", notify=True)
            if text:
                self.log_warning(f"检测到文字: {text}")
            return False
        return True

    # ------------------------------------------------------------------ #
    #  等待咬钩
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  控条
    # ------------------------------------------------------------------ #

    def control_until_finish(self) -> bool:
        start_check_time = time.time() + 1
        deadline = time.time() + self.CONTROL_TIMEOUT
        failed_time = 0
        try:
            while time.time() < deadline:
                state = self.get_bar_state()
                if self.is_valid_bar_state(state):
                    self.apply_bar_control(state)
                else:
                    # 只在长按模式下清理按键
                    mode = self.config.get("控条模式", "长按")
                    if mode == "长按":
                        self._set_bar_key(None)

                if time.time() > start_check_time:
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
            else:
                self.log_error("控条阶段超时")
            return False
        finally:
            # 只在长按模式下清理按键
            mode = self.config.get("控条模式", "长按")
            if mode == "长按":
                self._set_bar_key(None)

    def apply_bar_control(self, state: dict):
        mode = self.config.get("控条模式", "长按")
        if mode == "点按":
            self.apply_bar_control_discrete(state)
        else:
            self.apply_bar_control_hold(state)

    def apply_bar_control_hold(self, state: dict):
        """长按模式 (默认)"""
        now = time.time()
        pointer = int(state["pointer_center"])
        zone_left = int(state["zone_left"])
        zone_right = int(state["zone_right"])

        zone_center = (zone_left + zone_right) // 2
        zone_width = max(1, zone_right - zone_left)

        error = pointer - zone_center
        abs_error = abs(error)

        deadzone = max(2, int(zone_width * 0.06))

        if abs_error <= deadzone:
            self._set_bar_key(None)
            if now - self._last_bar_log_time > 1:
                self.log_debug(f"指针已锁定中心: pointer={pointer}, target={zone_center}")
                self._last_bar_log_time = now
            return

        key = "d" if error < 0 else "a"
        self._set_bar_key(key)

    def apply_bar_control_discrete(self, state: dict):
        """点按模式 (使用 send_key + down_time)"""
        now = time.time()
        pointer = int(state["pointer_center"])
        zone_left = int(state["zone_left"])
        zone_right = int(state["zone_right"])

        zone_center = (zone_left + zone_right) // 2
        zone_width = max(1, zone_right - zone_left)

        dist_from_center = pointer - zone_center
        abs_dist = abs(dist_from_center)

        deadzone = max(2, int(zone_width * 0.06))

        if abs_dist <= deadzone:
            if now - self._last_bar_log_time > 0.5:
                self.log_debug(f"指针已锁定中心: pointer={pointer}, target={zone_center}")
                self._last_bar_log_time = now
            return

        key = "d" if dist_from_center < 0 else "a"

        ratio = min(1.0, abs_dist / (zone_width / 2))

        # S 曲线
        curve = ratio * ratio * (3 - 2 * ratio)

        base_hold = 0.01
        max_hold_ext = 0.18

        hold = base_hold + curve * max_hold_ext

        # 死区
        deadzone = max(2, int(zone_width * 0.08))
        if abs_dist <= deadzone:
            return

        # 方向
        key = "d" if dist_from_center < 0 else "a"

        # 方向变化削弱
        if key != self._last_direction:
            hold *= 0.6

        self._last_direction = key

        # 倍率
        multiplier = float(self.config.get("点按时长倍率", 1.0))
        hold *= multiplier

        # 限制
        hold = min(0.2, max(0.01, hold))

        self.send_key(key, down_time=hold)

    def _set_bar_key(self, key):
        if key == self._bar_active_key:
            return

        if self._bar_active_key is not None:
            self.send_key_up(self._bar_active_key)
            self._bar_active_key = None

        if key is not None:
            self.send_key_down(key)
            self._bar_active_key = key

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

    # ------------------------------------------------------------------ #
    #  成功面板
    # ------------------------------------------------------------------ #

    def is_fishing_entry(self) -> bool:
        # TODO: 替换为非 OCR 方式检测"钓鱼"交互入口 (如图标匹配或特征颜色)
        return False

    def is_start_panel(self) -> bool:
        # TODO: 替换为非 OCR 方式检测"钓鱼准备面板"或"开始钓鱼"按钮
        return False

    def is_success_overlay(self) -> bool:
        return self.is_success_text_exist()

    def close_success_overlay(self):
        if self.wait_until(
            lambda: not self.is_success_overlay(),
            pre_action=self.do_close_success_overlay,
            time_out=10,
        ):
            self.log_info("关闭成功面板")
        else:
            self.log_error("关闭成功面板超时")
            return False
        if self.wait_until(self.is_fish_start_exist, time_out=5):
            self.log_info("进入可抛竿状态")
            self.sleep(0.5)
        else:
            self.log_error("未进入可抛竿状态")
            return False
        return True

    def do_close_success_overlay(self):
        if self.config.get("使用ESC"):
            self.send_key("esc", interval=2)
        else:
            x, y = self.SUCCESS_CLOSE_POS
            self.click(x, y, interval=2)

    def clear_success_overlay_if_present(self):
        if self.is_success_overlay():
            self.log_info("检测到成功面板")
            self.close_success_overlay()

    # ------------------------------------------------------------------ #
    #  钓鱼结束后处理（卖鱼 / 续饵）
    # ------------------------------------------------------------------ #

    def _handle_post_success(self):
        """钓鱼成功后只关闭成功面板，卖鱼/续饵由外层循环统一处理"""
        if not self.close_success_overlay():
            self.log_warning("关闭成功面板失败，后续操作可能受影响")
        self.sleep(1)
        # 注意：不在这里调用 _do_sell_fish / _do_buy_bait

    def _do_sell_fish(self):
        """
        自动卖鱼流程：
        按 Q 打开售鱼处 → 点击类别 → 选鱼 → 确认数量 → 确认出售 → ESC 关闭
        """
        self.log_info("开始自动卖鱼")
        # 确保成功面板已关闭（保险）
        self.clear_success_overlay_if_present()
        self.sleep(0.5)

        self.send_key("q", interval=0)
        self.sleep(1)
        self.click(*self.SELL_FISH_STEP1)  # 点击售鱼类别
        self.sleep(1)
        self.click(*self.SELL_FISH_STEP2)  # 选择鱼
        self.sleep(1)
        self.click(*self.SELL_FISH_STEP3)  # 确认数量
        self.sleep(2)
        self.click(*self.SELL_FISH_CONFIRM)  # 确认出售
        self.sleep(1)
        self.send_key("esc", interval=0)
        self.sleep(1)
        self.log_info("卖鱼完成")

    def _do_buy_bait(self):
        """修改后的方法，使用配置的位置坐标"""
        self.log_info("开始自动续鱼饵")
        self.clear_success_overlay_if_present()
        self.sleep(0.5)

        self.send_key("r", interval=0)
        self.sleep(1.5)

        bait_position_str = self.config.get("万能鱼饵位置", "1")
        try:
            bait_position = int(bait_position_str)
        except ValueError:
            bait_position = 1
            self.log_warning(f"无效的万能鱼饵位置配置: {bait_position_str}，使用默认位置1")
        bait_coords = {
            1: (0.0835, 0.211),
            2: (0.1828, 0.211),
            3: (0.291, 0.211),
        }
        click_pos = bait_coords.get(bait_position, (0.0835, 0.211))
        self.log_info(f"点击万能鱼饵位置 {bait_position}，坐标 {click_pos}")
        self.click(*click_pos)
        self.sleep(1)

        self.click(*self.BAIT_BUY_CONFIRM1)
        self.sleep(1)
        self.click(*self.BAIT_BUY_CONFIRM2)
        self.sleep(1)
        self.click(*self.BAIT_BUY_ok)
        self.sleep(2)
        self.click(*self.BAIT_BUY_CONFIRM3)
        self.sleep(1)
        self.send_key("esc", interval=0)
        self.sleep(1)
        self.log_info("续鱼饵完成")


    # ------------------------------------------------------------------ #
    #  状态重置
    # ------------------------------------------------------------------ #

    def reset_runtime_state(self):
        self._set_bar_key(None)
        self._fishing_started = False
        self._last_bar_log_time = 0.0
        self._last_direction = None
        self._bar_active_key = None

    # ------------------------------------------------------------------ #
    #  拉力条检测
    # ------------------------------------------------------------------ #

    def detect_fishing_bar_state(self):
        """
        Detect the fishing control bar state from a cropped top-bar image using
        contour analysis to filter noise.
        """
        box = self.box_of_screen(*self.BAR_BOX, name="fishing_bar")
        image = box.crop_frame(self.frame)
        if image is None or image.size == 0:
            return None

        green_mask = iu.filter_by_hsv(
            image, iu.HSVRange((50, 150, 160), (160, 220, 255)), return_mask=True
        )
        yellow_mask = iu.filter_by_hsv(
            image, iu.HSVRange((20, 60, 195), (55, 200, 255)), return_mask=True
        )

        # iu.show_images([green_mask, yellow_mask], names=["green_mask", "yellow_mask"], wait_key=1)

        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, self._morph_kernel)
        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, self._morph_kernel)
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_OPEN, self._morph_kernel)
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_CLOSE, self._morph_kernel)
        # iu.show_images([green_mask, yellow_mask], names=["green_mask", "yellow_mask"])

        yellow_contours, _ = cv2.findContours(
            yellow_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        if yellow_contours:
            yellow_max_contour = max(yellow_contours, key=cv2.contourArea)
            px, _, pw, _ = cv2.boundingRect(yellow_max_contour)
            pointer_center = px + pw // 2
        else:
            pointer_center = -1

        green_contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        green_candidates = []
        for contour in green_contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w >= 5 and h >= 5:
                area = w * h
                green_candidates.append((x, y, w, h, area))

        if not green_candidates:
            return None

        green_candidates.sort(key=lambda item: item[4], reverse=True)

        top_2_candidates = green_candidates[:2]

        top_2_candidates.sort(key=lambda item: item[0])

        if len(top_2_candidates) == 1:
            zone_left = top_2_candidates[0][0]
            zone_right = top_2_candidates[0][0] + top_2_candidates[0][2]
        else:
            zone_left = top_2_candidates[0][0]
            zone_right = max(
                top_2_candidates[0][0] + top_2_candidates[0][2],
                top_2_candidates[1][0] + top_2_candidates[1][2],
            )
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

    # ------------------------------------------------------------------ #
    #  UI 检测辅助
    # ------------------------------------------------------------------ #

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
        white_text = self.calculate_color_percentage(text_white_color, box)
        black_border = self.calculate_color_percentage(text_black_color, box)
        # self.log_debug(f"white_text: {white_text}, black_border: {black_border}")
        return white_text > 0.2 and black_border > 0.2

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

        blue_mask = iu.create_color_mask(image, fishing_bite_blue_color, to_bgr=False)

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

text_black_color = {
    "r": (0, 10),
    "g": (0, 10),
    "b": (0, 10),
}