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
                    "今日最低消耗目标（用于满足日常活跃度），不作为截断条件：达标后仍按当前体力继续刷，"
                    "材料齐全后切空幕消耗剩余体力，直到体力不够一次副本为止。"
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

    @classmethod
    def register_daily_routine_entry(cls) -> bool:
        """插件形态下把本任务挂成日常子项; 宿主文件零改动, 升级不会被还原。"""
        from src.tasks.cultivation_goal.daily_hook import register_daily_routine_entry

        return register_daily_routine_entry(cls)

    def do_run(self) -> bool | None:
        return self.run_cultivation_goal()
