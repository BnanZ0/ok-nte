import re
import time
from typing import TYPE_CHECKING

import cv2
from ok import Logger, find_color_rectangles

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

        box = self.box_of_screen(0.3582, 0.0215, 0.4508, 0.0569)
        is_boss = self.find_one(Labels.boss_lv_text, box=box, frame_processor=filter)
        return bool(is_boss)

    def test_ocr_lv(self, boxes):
        lv_text_boxes = []
        lv_text_imgs = []
        width = self.width
        height = self.height
        frame = self.frame
        lv_regex = re.compile(r"lv", flags=re.IGNORECASE)
        self.ocr(box=boxes[0], frame=frame, frame_processor=iu.binarize_bgr_by_brightness)
        for box in boxes:
            box_xr = box.x / width + 0.01
            box_yr = box.y / height - 0.035

            lv_text_box = self.box_of_screen(box_xr, box_yr, width=0.03, height=0.038)
            lv_text_imgs.append(lv_text_box.crop_frame(frame))
            lv_text_boxes.append(lv_text_box)
        img = merge_images_vertically(lv_text_imgs)
        start = time.perf_counter()
        texts = self.ocr(match=lv_regex, frame=img, frame_processor=iu.binarize_bgr_by_brightness)
        end = time.perf_counter()
        logger.info(f"ocr: {end - start}")
        if len(texts) > 0:
            return True
        self.draw_boxes("search_lv", lv_text_boxes, color="blue")

    def has_health_bar(self):
        if self._in_combat:
            min_height = self.height_of_screen(7 / 2160)
            max_height = min_height * 3
            min_width = self.width_of_screen(12 / 3840)
        else:
            min_height = self.height_of_screen(7 / 2160)
            max_height = min_height * 3
            min_width = self.width_of_screen(100 / 3840)

        boxes = find_color_rectangles(
            self.frame, enemy_health_color_red, min_width, min_height, max_height=max_height
        )

        if len(boxes) > 0:
            self.draw_boxes("enemy_health_bar_red", boxes, color="blue")
            return True
        else:
            boxes = find_color_rectangles(
                self.frame,
                boss_health_color,
                min_width,
                min_height * 1.3,
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
            if self.is_boss() or self.check_health_bar():
                return self.scene.set_in_combat()
            # if self.has_target():
            #     self.last_in_realm_not_combat = 0
            #     return self.scene.set_in_combat()
            if self.combat_end_condition is not None and self.combat_end_condition():
                return self.reset_to_false(reason="end condition reached")
            # if self.target_enemy(wait=True):
            #     logger.debug('retarget enemy succeeded')
            #     return self.scene.set_in_combat()
            # if self.should_check_monthly_card() and self.handle_monthly_card():
            #     return self.scene.set_in_combat()
            logger.error("target_enemy failed, try recheck break out of combat")
            return self.reset_to_false(reason="target enemy failed")
        else:
            in_combat = self.check_health_bar()
            if in_combat:
                self.log_info("enter combat")
                self._in_combat = self.load_chars()
                return self._in_combat


enemy_health_color_red = {
    "r": (215, 255),  # Red range
    "g": (40, 70),  # Green range
    "b": (20, 55),  # Blue range
}

boss_health_color = {
    "r": (235, 255),  # Red range
    "g": (50, 95),  # Green range
    "b": (40, 75),  # Blue range
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
