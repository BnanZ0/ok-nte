import ctypes
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List

import cv2
import numpy as np
import win32api
import win32con
import win32gui
import win32process
from ok import BaseTask, Box, Logger, og, safe_get

from src.Labels import Labels
from src.scene.NTEScene import NTEScene
from src.scene.ScreenPosition import ScreenPosition
from src.utils import image_utils as iu

logger = Logger.get_logger(__name__)


class BaseNTETask(BaseTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scene: NTEScene | None = None
        self.key_config = self.get_global_config("Game Hotkey Config")
        self._logged_in = False
        self.arrow_contour = {"contours": None, "shape": None}
        self.default_box = ScreenPosition(self)
        self.char_ui_offset = False

    @property
    def thread_pool_executor(self) -> ThreadPoolExecutor:
        if og.my_app is None:
            return None
        return og.my_app.get_thread_pool_executor()

    @property
    def main_viewport(self):
        return self.box_of_screen(0.1543, 0.1021, 0.9070, 0.8458)

    def click(self, *args, **kwargs):
        kwargs.setdefault("move", False)
        return super().click(*args, **kwargs)

    def get_char_box(self, index: int):
        box = self.get_box_by_name(f"box_char_{index + 1}")
        if self.char_ui_offset:
            box = self.shift_char_ui_box(box)
        return box

    def get_char_text_box(self, index: int):
        box = self.get_box_by_name(f"char_{index + 1}_text")
        return box

    def get_base_char_element_box(self):
        box = self.box_of_screen_scaled(
            2560, 1440, 2438, 335, width_original=29, height_original=29
        )
        box = self.shift_char_ui_box(box, expend=True)
        return box

    def is_in_team(self):
        box = self.find_one(
            Labels.health_bar_slash,
            mask_function=iu.mask_corners,
            horizontal_variance=0.01,
            vertical_variance=0.005,
        )
        result = box is not None
        # self.log_debug(f"is_in_team {box}")
        return result

    def shift_char_ui_box(self, box: Box, expend=False):
        """
        针对角色UI偏移的box修正
        :param box: 
        :param expend: 是否扩展box
        :return: 
        """
        offset = -9 * self.width / 2560
        width_offset = 0
        if expend:
            width_offset = -offset
        box = box.copy(x_offset=offset, width_offset=width_offset)
        return box

    def in_team(self):
        if not self.is_in_team():
            return False, -1, 0

        def process_char_text(image):
            return iu.binarize_bgr_by_brightness(image, threshold=180)

        def find_char_text(index: int):
            return self.find_one(
                f"char_{index + 1}_text",
                threshold=0.7,
                frame_processor=process_char_text,
                mask_function=iu.mask_outside_white_rect,
                horizontal_variance=0.005,
            )

        c1 = find_char_text(0)
        c2 = find_char_text(1)
        c3 = find_char_text(2)
        c4 = find_char_text(3)
        arr: List[Box | None] = [c1, c2, c3, c4]
        results = [
            c.x < self.get_char_text_box(idx).x for idx, c in enumerate(arr) if c is not None
        ]

        if results:
            self.char_ui_offset = sum(results) > (len(results) / 2)
        else:
            self.char_ui_offset = False

        # self.log_debug(f"in_team {arr}")
        current = -1
        exist_count = 0
        for i in range(len(arr)):
            if arr[i] is None:
                if current == -1:
                    current = i
            else:
                exist_count += 1

        self._logged_in = True
        return True, current, exist_count + 1

    def in_world(self) -> bool:
        frame = self.frame
        if self.arrow_contour["shape"] != frame.shape[:2]:
            template_bgr = self.get_feature_by_name(Labels.mini_map_arrow).mat
            t_bin = template_bgr[:, :, 0]
            contours, _ = cv2.findContours(t_bin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                raise ValueError("contours is None")
            self.arrow_contour["contours"] = max(contours, key=cv2.contourArea)
            self.arrow_contour["shape"] = frame.shape[:2]

        mat = self.box_of_screen(0.0691, 0.1083, 0.0949, 0.1493, name="in_world").crop_frame(frame)
        mat = iu.binarize_bgr_by_brightness(mat, threshold=200)
        res, cost = self._find_rotated_shape(mat)
        # self.log_debug(f"in_world {res}, cost {cost} ms")
        return len(res) == 1

    def _find_rotated_shape(self, scene_bgr, score_threshold=0.1):
        """
        score_threshold: 越小越严格。通常 0.05-0.2 之间。
        """
        start_time = time.time()
        s_bin = scene_bgr[:, :, 0]
        scene_contours, _ = cv2.findContours(s_bin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        results = []
        for cnt in scene_contours:
            if cv2.contourArea(cnt) < 50:
                continue

            # 核心算法：比较两个形状的胡氏矩 (I1 模式最常用)
            # 返回值越小，匹配度越高（0 为完美匹配）
            score = cv2.matchShapes(self.arrow_contour["contours"], cnt, cv2.CONTOURS_MATCH_I1, 0.0)

            if score < score_threshold:
                # 计算重心和角度
                M = cv2.moments(cnt)
                if M["m00"] != 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])

                    # 使用最小外接矩形获取角度
                    rect = cv2.minAreaRect(cnt)
                    angle = rect[2]  # 得到角度

                    results.append({"center": (cx, cy), "angle": angle, "score": score})

        # 按分数升序排列（得分越低越好）
        results = sorted(results, key=lambda x: x["score"])
        return results, (time.time() - start_time) * 1000

    def in_team_and_world(self):
        in_team = self.in_team()[0]
        in_world = self.in_world()
        return in_team and in_world

    def wait_in_team_and_world(self, time_out=10, raise_if_not_found=True, esc=False):
        success = self.wait_until(
            self.in_team_and_world,
            time_out=time_out,
            raise_if_not_found=raise_if_not_found,
            post_action=lambda: self.back(after_sleep=2) if esc else None,
        )
        if success:
            self.sleep(0.1)
        return success

    def set_pynput_interaction(self):
        self.bring_to_front()
        self.set_interaction(1)

    def set_post_interaction(self):
        self.set_interaction(0)

    def set_interaction(self, idx=0):
        """
        通过索引 (idx) 设置交互方法。
        会从配置的交互列表中读取指定索引的方法。
        """

        def get_name(m):
            return getattr(m, "__name__", str(m))

        methods: list = og.device_manager.windows_capture_config.get("interaction", [])
        available_options = [get_name(m) for m in methods]

        m = safe_get(methods, idx)
        if m is None:
            self.log_error(
                f"无法设置交互方式：索引 {idx} 越界。当前可用选择有: {available_options}"
            )
            return
        og.device_manager.set_interaction(m)
        self.log_info(f"已切换交互式方式: {get_name(m)}")

    def bring_to_front(self):
        if not self.hwnd:
            return

        hwnd = self.hwnd
        current_thread_id = 0
        target_thread_id = 0
        foreground_thread_id = 0
        attached_target = False
        attached_foreground = False

        try:
            current_thread_id = win32api.GetCurrentThreadId()
            target_thread_id, _ = win32process.GetWindowThreadProcessId(hwnd)
            foreground_hwnd = win32gui.GetForegroundWindow()
            if foreground_hwnd:
                foreground_thread_id, _ = win32process.GetWindowThreadProcessId(foreground_hwnd)

            if target_thread_id and target_thread_id != current_thread_id:
                attached_target = bool(
                    ctypes.windll.user32.AttachThreadInput(
                        current_thread_id, target_thread_id, True
                    )
                )
            if (
                foreground_thread_id
                and foreground_thread_id != current_thread_id
                and foreground_thread_id != target_thread_id
            ):
                attached_foreground = bool(
                    ctypes.windll.user32.AttachThreadInput(
                        current_thread_id, foreground_thread_id, True
                    )
                )

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            logger.debug(f"bring_to_front failed: {e}")
        finally:
            if attached_foreground:
                ctypes.windll.user32.AttachThreadInput(
                    current_thread_id, foreground_thread_id, False
                )
            if attached_target:
                ctypes.windll.user32.AttachThreadInput(current_thread_id, target_thread_id, False)

    @property
    def interac_box(self):
        interac_box = self.get_box_by_name(Labels.interactable)
        interac_box = interac_box.copy(
            x_offset=-interac_box.width * 0.3,
            y_offset=-interac_box.height * 2.5,
            width_offset=interac_box.width * 0.6,
            height_offset=interac_box.height * 5,
            name="search_interac",
        )
        return interac_box

    def find_interac(self):
        return self.find_one(
            Labels.interactable,
            box=self.interac_box,
            threshold=0.7,
            mask_function=interactable_mask,
        )

    def walk_until_find_interac(self, time_out=10, raise_if_not_found=False):
        self.send_key_down("w")
        self.wait_until(
            self.find_interac,
            time_out=time_out,
            raise_if_not_found=raise_if_not_found,
        )
        self.send_key_up("w")


def interactable_mask(image):
    mask = iu.create_color_mask(image, interac_pink_color, gray=True)
    kernel = np.ones((3, 3), np.uint8)
    dilated_mask = cv2.dilate(mask, kernel, iterations=1)
    return dilated_mask

interac_pink_color = {
    "r": (197, 221),
    "g": (71, 78),
    "b": (119, 133),
}
