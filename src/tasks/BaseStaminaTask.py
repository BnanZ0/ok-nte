import re
from dataclasses import dataclass

from ok import CannotFindException

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

    def open_activity_panel(self) -> bool:
        def action():
            self.openF1panel()
            self.operate_click(*self.pos.panels.f1.activity)
            self.sleep(0.5)
            return self.wait_panel(Labels.f1_activity_panel)

        self.log_info("开启活跃度面板")
        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("无法找到活跃度面板")
            return False
        return True

    def read_daily_activity(self) -> DailyActivity:
        if not self.open_activity_panel():
            raise CannotFindException("无法打开活跃度面板")

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

        self.info_set("used stamina", used_stamina)
        self.info_set("daily activity", daily_activity)
        return DailyActivity(used_stamina, daily_activity)

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
