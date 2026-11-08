import numpy as np

from src.char.BaseChar import BaseChar
from src.combat.planner import Planner
from src.utils import image_utils as iu


class Elyms(BaseChar):
    cn_name = "曳尔沐"
    element = BaseChar.ElementType.WHITE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def combat_plan(self):
        plan = self.plan()
        self.register_ultimate(plan)
        self.register_skill(
            plan,
            add_tags=Planner.ActionTag.HIGH_PRIORITY,
        )
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        if self.click_skill():
            self.task.wait_until(self.is_cycle_full, time_out=1)
        stage = self.get_stage()
        self.logger.info(stage)
        if stage == 1:
            self.task.wait_until(
                self.skill_available, post_action=self.click_with_interval, time_out=1.5
            )
            if self.click_special_skill():
                self.add_freeze_duration(start=self.clock.now(), duration=1.5)
                self.continues_normal_attack(1.5)
                stage += 1
        if stage == 2:
            self.continues_normal_attack(3, until_skill_full=True)
            if self.click_special_skill():
                self.add_freeze_duration(start=self.clock.now(), duration=1.5)
                self.continues_normal_attack(1.5)
                stage += 1
        if stage == 3:
            self.click_ultimate()

    def click_special_skill(self):
        if self.skill_available() and self.task.wait_until(self.is_special_skill, time_out=0.5):
            self.logger.info("click special skill")
            return self.click_skill()

    def is_special_skill(self) -> bool:
        """E 图标外围存在紫色光圈时返回 True; 不代表技能已可释放。"""
        if not self.is_current_char:
            return False

        frame = self.task.frame
        if frame is None:
            return False
        box = self.task.box_of_screen(
            0.8418, 0.8438, 0.9043, 0.9549, name="elyms_special_skill", hcenter=True
        )
        roi = box.crop_frame(frame)
        if roi is None or roi.size == 0:
            return False

        mask = iu.filter_by_hsv(roi, iu.HSVRange((125, 25, 180), (165, 255, 255)), return_mask=True)
        height, width = mask.shape
        y, x = np.ogrid[:height, :width]
        radius_squared = ((x - (width - 1) / 2) / (width / 2)) ** 2 + (
            (y - (height - 1) / 2) / (height / 2)
        ) ** 2
        # 只检测外圈, 排除中心图案、E 按键文字和相邻 Q 图标。
        ring = (radius_squared >= 0.72**2) & (radius_squared <= 0.92**2)
        if not ring.any() or (mask[ring] != 0).mean() < 0.25:
            return False

        # 至少三个象限亮起, 避免单侧粒子或局部场景亮光误判。
        lit_quadrants = 0
        for rows in (slice(0, height // 2), slice(height // 2, height)):
            for columns in (slice(0, width // 2), slice(width // 2, width)):
                quadrant = mask[rows, columns][ring[rows, columns]]
                if quadrant.size and (quadrant != 0).mean() >= 0.15:
                    lit_quadrants += 1
        return lit_quadrants >= 3

    def get_stage(self) -> int:
        """读取柱状条的阶段 1~3; 未出场或未识别到亮条时返回 0。"""
        if not self.is_current_char:
            return 0

        frame = self.task.frame
        if frame is None:
            return 0

        # 各段末端的柱身, 避开底部常亮横线、上方粒子和下方图标。
        stage_regions = (
            (0.4785, 0.8910, 0.4863, 0.8979),
            (0.5078, 0.8819, 0.5156, 0.8958),
            (0.5352, 0.8681, 0.5430, 0.8958),
        )
        stage = 0
        for index, region in enumerate(stage_regions, start=1):
            box = self.task.box_of_screen(*region, name=f"elyms_stage_{index}", hcenter=True)
            roi = box.crop_frame(frame)
            if roi is None or roi.size == 0:
                break
            mask = iu.binarize_bgr_by_brightness(roi, threshold=180, to_bgr=False)
            if (mask != 0).mean() < 0.3:
                break
            stage = index
        return stage
