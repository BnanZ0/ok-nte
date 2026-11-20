"""消耗体力培养角色：按养成指南里的材料缺口自动刷体力的独立任务。

与异象界域(AnomalyTask)/异象追猎(AnomalyHunter)/异象巡礼(AnomalyPilgrimage)同级，
在 config.py 的 onetime_tasks 注册为一个单独功能。具体流程在 cultivation_goal/flow.py，
本类只负责界面配置与入口，并按需借用上游三个异象任务的战斗能力。
"""

from ok import TaskDisabledException

from src.tasks.AnomalyTask import AnomalyTask
from src.tasks.cultivation_goal.flow import CultivationGoalMixin


class CultivationGoalTask(CultivationGoalMixin, AnomalyTask):
    TASK_NAME = "消耗体力培养角色"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.TASK_NAME
        self.description = "按培养目标缺口自动消耗体力：周本 > 世界BOSS > 异能 > 弧盘"

    @classmethod
    def setup_config(cls, instance, daily=False):
        cls.setup_daily_stamina_config(instance, reward_cost=cls.TASK_COST)
        instance.default_config.update(
            {
                cls.CONF_CG_WEEKLY_FIRST: True,
                cls.CONF_CG_LOOT_STATS: True,
            }
        )
        instance.config_description.update(
            {
                cls.CONF_STAMINA_TARGET: (
                    "今日体力花到这个累计数就停；今日目标已达标或设为 0 时不受每日目标限制，"
                    "按当前体力一直刷到材料齐全；材料齐全后仍会切空幕继续消耗剩余体力。"
                ),
                cls.CONF_CG_WEEKLY_FIRST: (
                    "周本刷取开关：开=优先刷满本周异象巡礼（每周 3 次，每次 60 体力）再刷其它材料；"
                    "关=直接刷其它材料，周本只在异能第 2 行缺料时才打。"
                ),
                cls.CONF_CG_LOOT_STATS: (
                    "每局领奖后在日志统计各材料本次掉落多少、已有多少、还差多少。"
                ),
            }
        )

    def do_run(self) -> bool | None:
        return self.run_cultivation_goal()
