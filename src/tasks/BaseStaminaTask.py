import re
import time
from dataclasses import dataclass

from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.utils.i18n_format import register_i18n_format


@dataclass(frozen=True)
class DailyActivity:
    used_stamina: int | None
    daily_activity: int | None


class BaseStaminaTask(NTEOneTimeTask, BaseNTETask):
    CONF_STAMINA_TARGET = "目标消耗体力"
    DAILY_STAMINA_REQUIREMENT = 180
    DAILY_ACTIVITY_REQUIREMENT = 100
    DESC_STAMINA_TARGET_FMT = (
        "按今日累计消耗计算, 扣除已消耗体力; 每份奖励{cost}体力, 剩余目标向上取整。\n"
        "今日已消耗180体力或活跃度达100时跳过; 体力不足时仅刷可领取的次数。"
    )

    def open_f1_activity(self):
        from src.utils import image_utils as iu

        def action():
            self.openF1panel()

            feature = self.get_feature_by_name(Labels.f1_activity).mat
            timer = self.clock.timer(timeout=5)
            invert = False
            while not timer.expired():
                template = iu.binarize_bgr_by_brightness(feature, threshold=20, invert=invert)
                if entry := self.find_one(
                    Labels.f1_activity,
                    template=template,
                    box=self.pos.panels.f1.entry_area.to_box(),
                ):
                    break
                invert = not invert
            else:
                return False

            self.operate_click(entry)
            return self.wait_panel(Labels.f1_activity_panel)

        return self.retry_on_action(
            action=action, reset_action=self.ensure_main, raise_if_failed=True
        )

    def read_daily_activity(self) -> DailyActivity:
        self.open_f1_activity()

        activity_re = re.compile(r"(\d+)")
        mission_re = re.compile(r"^(\d+)\s*/\s*180$")
        used_stamina = None
        daily_activity = None
        mission_box = self.box_of_screen(0.184, 0.652, 0.781, 0.710, name="mission", hcenter=True)
        activity_box = self.box_of_screen(0.184, 0.188, 0.256, 0.255, name="activity", hcenter=True)

        activity = self.ocr(box=activity_box, match=activity_re)
        for attempt in range(2):
            mission = self.ocr(box=mission_box, match=mission_re)
            match = next(
                (match for box in mission or () if (match := mission_re.search(box.name.strip()))),
                None,
            )
            if match:
                used_stamina = int(match.group(1))
                self.log_info(f"ocr found used stamina {used_stamina}")
                break
            if attempt == 0:
                self.operate(
                    lambda: self.scroll_relative(0.2379, 0.7285, -42),
                    block=True,
                )
                self.sleep(0.25)

        if activity:
            match = activity_re.search(activity[0].name)
            if match:
                daily_activity = int(match.group(1))
                self.log_info(f"ocr found daily activity {daily_activity}")

        if used_stamina is None:
            self.log_info("未识别到今日已消耗体力, 按0计算")
            used_stamina = 0

        self.info_set("used stamina", used_stamina)
        self.info_set("daily activity", daily_activity)
        return DailyActivity(used_stamina, daily_activity)

    def find_stamina_icon(self):
        return self.find_one(Labels.stamina_icon, horizontal_variance=0.1)

    def open_stamina_panel(self, direction="w") -> None:
        """Open the nearby challenge panel, raising if it cannot be confirmed."""
        def merged_action(interac):
            try:
                self.send_key_down("lalt")
                time.sleep(0.1)
                self.click(interac, move=True)
                time.sleep(0.1)
            finally:
                self.send_key_up("lalt")

        def click_interac():
            if interacs := self.find_interac():
                interac = min(interacs, key=lambda x: x.y)
                self.operate(lambda: merged_action(interac), block=True)
                self.sleep(1)

        if self.find_stamina_icon():
            return

        self.walk_until_interac(direction=direction, raise_if_not_found=True)
        self.sleep(1)
        self.wait_until(
            lambda: not self.find_interac(),
            post_action=click_interac,
            time_out=10,
            settle_time=0.5,
            raise_if_not_found=True,
        )
        self.wait_until(
            self.find_stamina_icon,
            settle_time=0.5,
            time_out=10,
            raise_if_not_found=True,
        )

    @classmethod
    def setup_daily_stamina_config(cls, instance: "BaseNTETask", *, reward_cost: int):
        instance.default_config[cls.CONF_STAMINA_TARGET] = cls.DAILY_STAMINA_REQUIREMENT
        instance.config_description[cls.CONF_STAMINA_TARGET] = cls.DESC_STAMINA_TARGET_FMT.format(
            cost=reward_cost
        )

    @classmethod
    def daily_activity_complete(cls, activity: DailyActivity) -> bool:
        if (
            activity.used_stamina is not None
            and activity.used_stamina >= cls.DAILY_STAMINA_REQUIREMENT
        ) or (
            activity.daily_activity is not None
            and activity.daily_activity >= cls.DAILY_ACTIVITY_REQUIREMENT
        ):
            return True
        if activity.used_stamina is None or activity.daily_activity is None:
            raise ValueError("无法识别每日体力消耗或活跃度")
        return False

    @classmethod
    def calculate_remaining_stamina(cls, activity: DailyActivity, target: int) -> int:
        if target <= 0 or (activity.used_stamina is not None and activity.used_stamina >= target):
            return 0
        if cls.daily_activity_complete(activity):
            return 0
        assert activity.used_stamina is not None
        return max(0, target - activity.used_stamina)

    def check_activity(self) -> bool:
        return self.daily_activity_complete(self.read_daily_activity())

    def resolve_stamina_target(self) -> int | None:
        """Return the remaining daily target, or None for reward-count mode."""
        if self.CONF_STAMINA_TARGET not in self.config:
            return None
        target = self.config.get(self.CONF_STAMINA_TARGET)
        must_use = 0
        if target > 0:
            activity = self.read_daily_activity()
            must_use = self.calculate_remaining_stamina(activity, target)
        self.info_set("must use stamina", must_use)
        if must_use == 0:
            self.log_info("体力消耗目标或每日活跃度已达标, 跳过每日体力任务")
        return must_use


register_i18n_format(BaseStaminaTask.DESC_STAMINA_TARGET_FMT)
