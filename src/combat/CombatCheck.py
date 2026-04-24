import re
import time
from typing import TYPE_CHECKING

import cv2
import numpy as np
from ok import Box, Logger, color_range_to_bound, find_color_rectangles

from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.utils import image_utils as iu

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar

logger = Logger.get_logger(__name__)


class CombatCheck(BaseNTETask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_ultimate = False
        self._in_combat = False
        self.skip_combat_check = False
        self.sleep_check_interval = 0.4
        self.last_out_of_combat_time = 0
        self.out_of_combat_reason = ""
        self.target_enemy_time_out = 3
        self.switch_char_time_out = 5
        self.combat_end_condition = None
        self.target_enemy_error_notified = False
        self.cds = {}
        self.combat_detect_future = None

    @property
    def in_ultimate(self):
        return self._in_ultimate

    @in_ultimate.setter
    def in_ultimate(self, value):
        self._in_ultimate = value
        if value:
            self._last_ultimate = time.time()

    def on_combat_check(self):
        return True

    def reset_to_false(self, reason=""):
        self.out_of_combat_reason = reason
        self.do_reset_to_false()
        return False

    def do_reset_to_false(self):
        self.cds = {}
        self._in_combat = False
        self.scene.set_not_in_combat()
        return False

    def get_current_char(self) -> "BaseChar":
        """
        获取当前角色。
        此方法必须由子类实现。
        """
        raise NotImplementedError("子类必须实现 get_current_char 方法")

    def load_chars(self) -> bool:
        """
        加载队伍中的角色信息。
        此方法必须由子类实现。
        """
        raise NotImplementedError("子类必须实现 load_chars 方法")

    def check_health_bar(self):
        return self.has_health_bar() or self.is_boss()

    def is_boss(self):
        def filter(image):
            return iu.binarize_bgr_by_brightness(image, threshold=180)

        box = self.box_of_screen(0.3582, 0.0215, 0.4808, 0.0569)
        is_boss = self.find_one(Labels.boss_lv_text, box=box, frame_processor=filter)
        return bool(is_boss)

    def target_enemy(self, wait=True):
        if not wait:
            self.middle_click()
        else:
            if self.has_target():
                return True
            else:
                logger.info(f"target lost try retarget {self.target_enemy_time_out}")
                start = time.time()
                while time.time() - start < self.target_enemy_time_out:
                    self.middle_click(interval=0.3)
                    if self.combat_detect() is True:
                        return True
                    self.next_frame()

    def has_target(self, frame=None):
        ret = self.find_diamond_target(frame=frame)[0] is not None
        return ret

    def has_health_bar(self):
        if self._find_red_health_bar() or self._find_boss_health_bar():
            return True
        return False

    def _find_red_health_bar(self):
        min_height = self.height_of_screen(5 / 1440)
        min_width = self.width_of_screen(100 / 2560)
        max_height = min_height * 2.5
        max_width = self.width_of_screen(200 / 2560)

        boxes = find_color_rectangles(
            self.frame,
            enemy_health_color_red,
            min_width,
            min_height,
            max_width,
            max_height,
            box=self.main_viewport,
        )

        if len(boxes) > 0:
            self.draw_boxes("enemy_health_bar_red", boxes, color="blue")
            return True
        return False

    def _find_boss_health_bar(self):
        min_height = self.height_of_screen(9 / 2160)
        min_width = self.width_of_screen(100 / 3840)

        boxes = find_color_rectangles(
            self.frame,
            boss_health_color,
            min_width,
            min_height,
            box=self.box_of_screen(0.3277, 0.0507, 0.4980, 0.0701),
        )
        if len(boxes) == 1:
            self.draw_boxes("boss_health", boxes, color="blue")
            return True
        return False

    def in_combat(self, target=False):
        self.in_sleep_check = True
        try:
            return self.do_check_in_combat(target)
        except Exception as e:
            logger.error("do_check_in_combat", e)
        finally:
            self.in_sleep_check = False

    def do_check_in_combat(self, target):
        if self.in_ultimate:
            return True
        if self._in_combat:
            if self.scene.in_combat() is not None:
                return self.scene.in_combat()
            if current_char := self.get_current_char():
                if current_char.skip_combat_check():
                    return self.scene.set_in_combat()
            if not self.on_combat_check():
                self.log_info("on_combat_check failed")
                return self.reset_to_false(reason="on_combat_check failed")
            if self.is_boss():
                return self.scene.set_in_combat()
            # else:
            #     frame = getattr(self, 'cache_frame', None)
            #     if frame is not None:
            #         cv2.imwrite(f"cache_frame_{int(time.time())}.png", frame)
            # if self.has_target():
            #     self.last_in_realm_not_combat = 0
            #     return self.scene.set_in_combat()
            if self.combat_end_condition is not None and self.combat_end_condition():
                return self.reset_to_false(reason="end condition reached")
            combat_detect = self.combat_detect()
            if combat_detect is None or combat_detect is True:
                return self.scene.set_in_combat()
            if self.target_enemy(wait=True):
                logger.debug("retarget enemy succeeded")
                return self.scene.set_in_combat()
            # if self.should_check_monthly_card() and self.handle_monthly_card():
            #     return self.scene.set_in_combat()
            logger.error("target_enemy failed, try recheck break out of combat")
            return self.reset_to_false(reason="target enemy failed")
        else:
            from src.tasks.trigger.AutoCombatTask import AutoCombatTask

            has_target = self.has_target()
            if not has_target and target:
                self.log_debug("try target")
                self.middle_click(after_sleep=0.1)
            in_combat = (
                self.config.get("自动目标") or not isinstance(self, AutoCombatTask)
            ) and self.check_health_bar()
            if not in_combat:
                in_combat = has_target and self.ocr(
                    box=self.main_viewport,
                    frame_processor=iu.isolate_lv_to_black,
                    match=re.compile(r"lv", re.IGNORECASE),
                    target_height=720,
                )
            if in_combat:
                if not has_target and not self.target_enemy(wait=True):
                    return False
                self.log_info("enter combat")
                self._in_combat = self.load_chars()
                return self._in_combat

    def create_rhombus_template(self, size):
        """
        版模板生成器：支持奇数(如9x9)的尖顶，也完美支持偶数(如10x10)的平顶
        """
        template = np.zeros((size, size), dtype=np.uint8)
        mask = np.zeros((size, size), dtype=np.uint8)

        # 浮点数中心点，完美解决偶数尺寸没有绝对中心的问题
        cy, cx = (size - 1) / 2.0, (size - 1) / 2.0

        for i in range(size):
            for j in range(size):
                # 计算到中心的精确曼哈顿距离
                dist = abs(i - cy) + abs(j - cx)

                # 动态划分内核与外框的范围
                if dist < (size / 2.0) - 1.5:
                    template[i, j] = 255
                    mask[i, j] = 255
                elif dist < (size / 2.0) + 0.5:
                    template[i, j] = 0
                    mask[i, j] = 255

        return template, mask

    def find_diamond_target(self, scales=range(10, 15), threshold=0.8, frame=None):
        """
        在图像中心 50% 范围内寻找黑框白底菱形
        利用屏幕比例动态收缩范围 + 智能颜色遮罩剔除背景杂讯
        """
        if frame is None:
            frame = self.frame
        ratio = self.height / 1440.0

        dynamic_scales = set()
        for base_size in scales:
            new_size = max(5, int(round(base_size * ratio)))
            dynamic_scales.add(new_size)
        dynamic_scales = sorted(list(dynamic_scales))

        box = self.box_of_screen(0.25, 0.25, 0.75, 0.75)
        roi = box.crop_frame(frame)

        # ==========================================
        # 【核心优化】：智能颜色过滤 + 保留灰度特征
        # ==========================================
        # 1. 严格过滤白内核 (建议范围放宽一点点如240-255，防止抗锯齿导致核心不是纯白)
        color = {
            "r": (255, 255),
            "g": (255, 255),
            "b": (255, 255),
        }
        lower_bound, upper_bound = color_range_to_bound(color)  # 假设你有这个辅助函数
        white_mask = cv2.inRange(roi, lower_bound, upper_bound)

        # 2. 【极大提升性能】初步检查
        if cv2.countNonZero(white_mask) == 0:
            return None, None, None

        # ==========================================================
        # 【新增】：剔除过大的白块 (Size Filtering)
        # ==========================================================
        # 获取当前最大的预期尺寸
        max_allowed_size = max(dynamic_scales) * 1.2  # 允许 20% 的冗余误差

        # 查找连通区域
        # connectivity=8 表示 8 邻域插件；stats 包含 [x, y, w, h, area]
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(white_mask, connectivity=8)

        # 遍历所有找到的“块”（索引 0 是背景，从 1 开始）
        for i in range(1, num_labels):
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]
            area = stats[i, cv2.CC_STAT_AREA]

            # 过滤条件：如果宽度或高度明显超过了最大模板尺寸
            # 或者你可以根据面积过滤：area > max_allowed_size**2
            if w > max_allowed_size or h > max_allowed_size:
                white_mask[labels == i] = 0  # 将过大的块抹黑

        # 再次检查抹除后是否还有剩余有效区域
        if cv2.countNonZero(white_mask) == 0:
            return None, None, None
        # ==========================================================

        # 4. 提取原灰度图，应用过滤后的掩码
        roi_gray_original = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if len(roi.shape) == 3 else roi
        # 将无效区域设为 0 (黑色)，有效区域保留原图细节
        roi_gray = np.where(white_mask == 255, roi_gray_original, 0)

        # iu.show_images(roi_gray, name="roi_gray")
        # ==========================================

        best_match_val = -1.0
        best_loc = None
        best_scale = None

        for size in dynamic_scales:
            template, mask = self.create_rhombus_template(size)
            # iu.show_images(template, name="template")

            res = cv2.matchTemplate(roi_gray, template, cv2.TM_CCOEFF_NORMED)

            _, max_val, _, max_loc = cv2.minMaxLoc(res)

            if max_val > best_match_val:
                best_match_val = max_val
                best_loc = max_loc
                best_scale = size

        if best_match_val >= threshold:
            center_x = box.x + best_loc[0] + best_scale // 2
            center_y = box.y + best_loc[1] + best_scale // 2

            result_box = Box(
                center_x - best_scale // 2,
                center_y - best_scale // 2,
                width=best_scale,
                height=best_scale,
                confidence=best_match_val,
            )
            self.draw_boxes("target", result_box, color="blue")

            return (center_x, center_y), best_scale, best_match_val
        else:
            return None, None, None

    def _async_combat_detect(self, frame):
        if self.has_target(frame=frame):
            return True, "target"
        if self.ocr(
            frame=frame,
            box=self.main_viewport,
            frame_processor=iu.isolate_lv_to_black,
            match=re.compile(r"lv", re.IGNORECASE),
            target_height=720,
            lib="bg_onnx_ocr",
        ):
            return True, "lv"
        return False, None

    def combat_detect(self):
        if self.combat_detect_future and self.combat_detect_future.done():
            ret, reason = self.combat_detect_future.result()
            self.combat_detect_future = None
            self.logger.info(f"combat_detect_future result: {ret}, reason: {reason}")
            return ret
        if self.combat_detect_future is None:
            self.logger.info("combat_detect_future submit")
            frame = self.frame.copy()
            self.combat_detect_future = self.thread_pool_executor.submit(
                self._async_combat_detect, frame=frame
            )
        return None


enemy_health_color_red = {
    "r": (210, 255),
    "g": (20, 80),
    "b": (20, 100),
}

boss_health_color = {
    "r": (215, 240),
    "g": (30, 60),
    "b": (50, 75),
}


def merge_images_vertically(img_list, bg_color=(255, 255, 255)):
    # 1. 找到所有图片中的最大宽度
    max_width = max(img.shape[1] for img in img_list)

    processed_imgs = []
    for img in img_list:
        _, w = img.shape[:2]
        if w < max_width:
            # 计算需要填充的宽度
            pad_width = max_width - w
            # 使用 cv2.copyMakeBorder 进行填充 (常数填充)
            # 这里的 bg_color 如果是灰度图传一个值(0)，如果是彩色传 (0,0,0)
            img = cv2.copyMakeBorder(img, 0, 0, 0, pad_width, cv2.BORDER_CONSTANT, value=bg_color)
        processed_imgs.append(img)

    # 2. 垂直合并
    return cv2.vconcat(processed_imgs)
