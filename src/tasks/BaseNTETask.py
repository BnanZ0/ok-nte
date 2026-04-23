import time
from typing import List

import cv2
from ok import BaseTask, Box

from src.Labels import Labels
from src.scene.NTEScene import NTEScene
from src.scene.ScreenPosition import ScreenPosition
from src.utils import image_utils as iu


class BaseNTETask(BaseTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scene: NTEScene | None = None
        self.key_config = self.get_global_config("Game Hotkey Config")
        self._logged_in = False
        self.arrow_contour = {"contours": None, "shape": None}
        self.box= ScreenPosition(self)

    def is_in_team(self):
        find_box = self.get_box_by_name(Labels.health_bar_slash).scale(1.5)
        box = self.find_one(Labels.health_bar_slash, box=find_box, mask_function=iu.mask_corners)
        result = box is not None
        # self.log_debug(f"is_in_team {box}")
        return result

    def in_team(self):
        if not self.is_in_team():
            return False, -1, 0

        def process_char_text(image):
            return iu.binarize_bgr_by_brightness(image, threshold=180)
        
        c1 = self.find_one(Labels.char_1_text, threshold=0.7, frame_processor=process_char_text,
                           mask_function=iu.mask_outside_white_rect)
        c2 = self.find_one(Labels.char_2_text, threshold=0.7, frame_processor=process_char_text,
                           mask_function=iu.mask_outside_white_rect)
        c3 = self.find_one(Labels.char_3_text, threshold=0.7, frame_processor=process_char_text,
                           mask_function=iu.mask_outside_white_rect)
        c4 = self.find_one(Labels.char_4_text, threshold=0.7, frame_processor=process_char_text,
                           mask_function=iu.mask_outside_white_rect)
        arr: List[Box | None] = [c1, c2, c3, c4]
        self.log_debug(f"in_team {arr}")
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
