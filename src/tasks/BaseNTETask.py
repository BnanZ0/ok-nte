import ctypes
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, List, overload

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
from src.utils import game_filters as gf
from src.utils import image_utils as iu

logger = Logger.get_logger(__name__)


class BaseNTETask(BaseTask):
    DEFAULT_MOVE = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scene: NTEScene | None = None
        self.key_config = self.get_global_config("Game Hotkey Config")
        self.monthly_card_config = self.get_global_config("Monthly Card Config")
        self._logged_in = False
        self.arrow_contour = {"contours": None, "shape": None}
        self.default_box = ScreenPosition(self)
        self.char_ui_offset = False
        self.next_monthly_card_start = 0

    @property
    def thread_pool_executor(self) -> ThreadPoolExecutor:
        if og.my_app is None:
            return None
        return og.my_app.get_thread_pool_executor()

    def _openvino_detect(self, sync, box, threshold):
        if og.my_app is None:
            return []
        if box is None:
            box = self.box_of_screen(0.0840, 0.1326, 0.9176, 0.8694, name="openvino_box")
        self.draw_boxes(boxes=box, color="blue")
        if sync:
            results = og.my_app.openvino_detect_sync(image=self.frame, box=box, threshold=threshold)
        else:
            results = og.my_app.openvino_detect_async(
                image=self.frame, box=box, threshold=threshold
            )
        if results:
            self.draw_boxes(boxes=results, color="red")
        return results

    def openvino_detect_async(self, box: Box = None, threshold=0.5) -> List[Box]:
        return self._openvino_detect(False, box, threshold)

    def openvino_detect_sync(self, box: Box = None, threshold=0.5) -> List[Box]:
        return self._openvino_detect(True, box, threshold)

    @property
    def main_viewport(self):
        return self.box_of_screen(0.1543, 0.1021, 0.9070, 0.6389)

    # fmt: off
    @overload
    def click(self, x: int | Box | List[Box] = -1, y=-1, move_back=False, name=None, interval=-1,
              move=False, down_time=0.02, after_sleep=0, key='left', hcenter=False,
              vcenter=False) -> Any:
        ...
    # fmt: on

    def click(self, *args, **kwargs):
        is_top_level = not hasattr(self, "_current_move")

        if is_top_level:
            self._current_move = kwargs.get("move", self.DEFAULT_MOVE)
        else:
            kwargs["move"] = self._current_move

        try:
            return super().click(*args, **kwargs)
        finally:
            if is_top_level:
                delattr(self, "_current_move")

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

        if self.scene is not None:
            state, timestamp = self.scene.get_is_in_team_record()
            if state and (to_sleep := 0.2 - (time.time() - timestamp)) > 0:
                self.sleep(to_sleep)

        arr = self.update_char_ui_offset()

        # self.log_debug(f"in_team {arr}")
        current = self.get_current_char_index()
        exist_count = 0
        for i in range(len(arr)):
            if arr[i] is not None:
                exist_count += 1
            elif current == -1:
                current = i

        if current != -1 and arr[current] is None:
            exist_count += 1

        self._logged_in = True
        return True, current, exist_count

    def update_char_ui_offset(self):
        # now = time.time()
        arr = self.multi_stage_char_match()
        results = [
            c.x < self.get_char_text_box(idx).x for idx, c in enumerate(arr) if c is not None
        ]

        if results:
            self.char_ui_offset = sum(results) > (len(results) / 2)
        else:
            self.char_ui_offset = False
        # logger.debug(f"update_char_ui_offset cost {time.time() - now:.3f}")
        return arr

    @property
    def char_vertical_spacing(self):
        return int(self.height * 176 / 1440)

    def get_box_by_char_spacing(self, box: Box, index: int):
        return box.copy(y_offset=index * self.char_vertical_spacing, name=f"{box.name}_{index}")

    def _get_char_template_data(self):
        """延迟加载并缓存模板掩码和覆盖面积"""
        if (
            not hasattr(self, "_char_template_cache")
            or self._char_template_cache.get("width") != self.width
            or self._char_template_cache.get("height") != self.height
        ):
            feature = self.get_feature_by_name(Labels.is_current_char)
            mat = feature.mat  # 原始二值化模板
            white_pixels = cv2.countNonZero(mat)

            # 仍然保留膨胀掩码用于过滤
            kernel = np.ones((5, 5), np.uint8)
            mask = cv2.dilate(feature.mat, kernel, iterations=1)

            self._char_template_cache = {
                "width": self.width,
                "height": self.height,
                "mat": mat,
                "mask": mask,
                "white_pixels": white_pixels,
            }

        cache = self._char_template_cache
        return cache["mat"], cache["mask"], cache["white_pixels"]

    def get_char_match_score(self, index):
        """获取指定位置的匹配得分（基于像素覆盖率），分值越小越匹配"""
        template_mat, template_mask, template_white_count = self._get_char_template_data()
        if template_white_count == 0:
            return 1.0

        base_box = self.get_box_by_name(Labels.is_current_char)
        if self.char_ui_offset:
            base_box = self.shift_char_ui_box(base_box)
        box = self.get_box_by_char_spacing(base_box, index)
        # self.draw_boxes(boxes=box, color="blue")

        current_mat = gf.current_char_filter(box.crop_frame(self.frame), blur=True)

        # 1. 掩码过滤并计算交集
        if current_mat.shape == template_mask.shape:
            current_mat = cv2.bitwise_and(current_mat, template_mask)

            if current_mat.shape == template_mat.shape:
                intersection = cv2.bitwise_and(current_mat, template_mat)
                coverage = cv2.countNonZero(intersection) / template_white_count
                return 1.0 - coverage

        return 1.0

    def is_char_at_index(self, index, threshold=0.3):
        """判断指定索引是否为当前角色"""
        self.update_char_ui_offset()
        score = self.get_char_match_score(index)
        new = f"idx {index} conf {score:.3f}"
        if self.info_get("current char") != new:
            self.info_set("current char", new)
        if score < threshold:
            return True

    def get_current_char_index(self):
        """扫描所有槽位，返回匹配度最高的索引"""
        best_score = 999
        best_idx = -1

        for i in range(4):
            score = self.get_char_match_score(i)
            if score < best_score:
                best_score = score
                best_idx = i

        if best_idx != -1:
            self.log_debug(f"current_char found at {best_idx} with score {best_score:.4f}")
        return best_idx

    def multi_stage_char_match(self):
        # 初始化 4 个结果为 None
        results = [None, None, None, None]

        # 定义对比度阶梯（从低到高）
        # 低对比度下匹配到的置信度通常更高
        contrast_steps = [0, 30, 60, 90]

        for c_val in contrast_steps:
            # 如果 4 个都找齐了，直接跳出大循环，节省计算时间
            if all(res is not None for res in results):
                break

            for i in range(4):
                # 只有还没找到的位置才进行匹配
                if results[i] is None:
                    # 构造处理函数
                    def process(image, current_c=c_val):
                        return iu.adjust_lightness_contrast_lab(
                            image, brightness=0, contrast=current_c
                        )

                    res = self.find_one(
                        f"char_{i + 1}_text",
                        threshold=0.7,
                        frame_processor=process,
                        mask_function=iu.mask_outside_white_rect,
                        horizontal_variance=0.005,
                    )

                    # 只要找到了，就存入结果，后续对比度级别不再处理这个索引
                    if res:
                        results[i] = res

        return results

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
        in_team = self.is_in_team()
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
            mask_function=interac_mask,
        )

    def walk_until_find_interac(self, time_out=10, raise_if_not_found=False):
        self.send_key_down("w")
        self.wait_until(
            self.find_interac,
            time_out=time_out,
            raise_if_not_found=raise_if_not_found,
        )
        self.send_key_up("w")

    def find_traval_button(self):
        box = self.get_box_by_name(Labels.teleport)
        w = box.width - (box.x - self.width_of_screen(0.99))
        y = -box.width * 0.2
        box = box.copy(y_offset=y, width_offset=w, height_offset=-y)
        return self.find_one(Labels.teleport, box=box)

    def click_traval_button(self, travel_btn=None):
        if not travel_btn:
            travel_btn = self.find_traval_button()
        if travel_btn:
            self.sleep(0.1)
            self.click(travel_btn, after_sleep=1, move=True, down_time=0.01)
            return True
        return False

    def openF1panel(self):
        if hasattr(self, "reset_to_false"):
            self.reset_to_false("opening f1 panel")
        if self.in_team_and_world():
            self.send_key("f1", after_sleep=1)
            self.log_info("send f1 key to open the panel")

        result = self.wait_panel(Labels.f1_panel)
        if not result:
            self.log_error("can't find panel, make sure f1 is the hotkey for panel", notify=True)
            raise Exception("can't find panel, make sure f1 is the hotkey for panel")
        return result

    def openF2panel(self):
        if hasattr(self, "reset_to_false"):
            self.reset_to_false("opening f2 panel")
        if self.in_team_and_world():
            self.send_key("f2", after_sleep=1)
            self.log_info("send f2 key to open the panel")

        result = self.wait_panel(Labels.f2_panel)
        if not result:
            self.log_error("can't find panel, make sure f2 is the hotkey for panel", notify=True)
            raise Exception("can't find panel, make sure f2 is the hotkey for panel")
        return result

    def wait_panel(self, feature, box=None, threshold=0.8, time_out=4.5):
        result = self.wait_until(
            lambda: self.find_one(feature, box=box, threshold=threshold),
            time_out=time_out,
            settle_time=0.5,
        )
        logger.info(f"found {feature} {result}")
        return result

    def openESCpanel(self):
        if hasattr(self, "reset_to_false"):
            self.reset_to_false("opening esc panel")
        if self.in_team_and_world():
            self.send_key("esc", after_sleep=1)
            self.log_info("send esc key to open the panel")

        result = self.wait_panel(Labels.esc_option, box=Labels.box_all_esc_options, threshold=0.3)
        if not result:
            self.log_error("can't find panel, make sure esc is the hotkey for panel", notify=True)
            raise Exception("can't find panel, make sure esc is the hotkey for panel")
        return result

    def ensure_main(self, esc=True, time_out=30):
        self.info_set("current task", f"wait main esc={esc}")
        if not self._logged_in:
            time_out = 600
        if not self.wait_until(
            lambda: self.is_main(esc=esc), time_out=time_out, raise_if_not_found=False
        ):
            raise Exception("Please start in game world and in team!")
        self.sleep(0.5)
        self.info_set("current task", f"in main esc={esc}")

    def is_main(self, esc=True):
        if self.in_team_and_world():
            self._logged_in = True
            return True
        if self.handle_monthly_card():
            return True
        if self.wait_login():
            return True
        if esc:
            self.back(after_sleep=2)

    def find_monthly_card(self):
        return self.find_one(Labels.monthly_card)

    def should_check_monthly_card(self):
        if self.next_monthly_card_start > 0:
            if 0 < time.time() - self.next_monthly_card_start < 120:
                return True
        return False

    def handle_monthly_card(self):
        monthly_card = self.find_monthly_card()
        # self.screenshot('monthly_card1')
        if monthly_card is not None:
            # self.screenshot('monthly_card1')
            self.log_info("monthly_card found click")
            self.click(0.50, 0.89)
            self.sleep(2)
            # self.screenshot('monthly_card2')
            self.click(0.50, 0.89)
            self.sleep(2)
            self.wait_until(
                self.in_team_and_world,
                time_out=10,
                post_action=lambda: self.click(0.50, 0.89, after_sleep=1),
            )
            # self.screenshot('monthly_card3')
            self.set_check_monthly_card(next_day=True)
        # logger.debug(f'check_monthly_card {monthly_card}')
        return monthly_card is not None

    def set_check_monthly_card(self, next_day=False):
        if self.monthly_card_config.get("Check Monthly Card"):
            now = datetime.now()
            hour = self.monthly_card_config.get("Monthly Card Time")
            # Calculate the next 4 o'clock in the morning
            next_four_am = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if now >= next_four_am or next_day:
                next_four_am += timedelta(days=1)
            next_monthly_card_start_date_time = next_four_am - timedelta(seconds=30)
            # Subtract 1 minute from the next 4 o'clock in the morning
            self.next_monthly_card_start = next_monthly_card_start_date_time.timestamp()
            logger.info(
                "set next monthly card start time to {}".format(next_monthly_card_start_date_time)
            )
        else:
            self.next_monthly_card_start = 0

    def wait_login(self):
        if not self._logged_in:
            if self.in_team_and_world():
                return True
            self.handle_monthly_card()
            texts = self.ocr(log=self.debug)
            if login := self.find_boxes(
                texts, boundary=self.box_of_screen(0.3, 0.3, 0.7, 0.7), match="登录"
            ):
                if not self.find_boxes(
                    texts, boundary=self.box_of_screen(0.3, 0.3, 0.7, 0.7), match="+86"
                ):
                    self.click(login, after_sleep=1)
                    self.log_info("点击登录按钮!")
                return False
            if agree := self.find_boxes(
                texts, boundary=self.box_of_screen(0.3, 0.3, 0.7, 0.7), match="同意"
            ):
                self.log_debug(f"found agree {agree}")
                if self.find_boxes(
                    texts, boundary=self.box_of_screen(0.3, 0.3, 0.7, 0.7), match=re.compile("隐私")
                ):
                    self.click(agree, after_sleep=1)
                    self.log_info("点击同意按钮!")
                return False
            # if self.find_boxes(texts, match=re.compile("游戏即将重启")):
            #     self.log_info("游戏更新成功, 游戏即将重启")
            #     self.click(self.find_boxes(texts, match="确认"), after_sleep=60)
            #     result = self.start_device()
            #     self.log_info(f"start_device end {result}")
            #     self.sleep(30)
            #     return False

            if start := self.find_boxes(
                texts, boundary="bottom_right", match=["开始游戏", re.compile("进入游戏")]
            ):
                if not self.find_boxes(texts, boundary="bottom_right", match="登录"):
                    self.click(start)
                    self.log_info(f"点击开始游戏! {start}")
                    return False

            if login_account := self.find_boxes(
                texts, match=re.compile("Windows.{0,3}Product", re.IGNORECASE)
            ):
                self.log_info(f"wait_login {login_account}")
                self.click(0.5, 0.5, after_sleep=3)
                return False


def interac_mask(image):
    mask = iu.create_color_mask(image, interac_pink_color, to_bgr=False)
    kernel = np.ones((3, 3), np.uint8)
    dilated_mask = cv2.dilate(mask, kernel, iterations=1)
    return dilated_mask


interac_pink_color = {
    "r": (197, 221),
    "g": (71, 78),
    "b": (119, 133),
}
