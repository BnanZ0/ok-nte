from src.char.BaseChar import BaseChar
from src.combat.planner import Planner, RoleProfile


class Zero(BaseChar):
    cn_name = "零"
    element = BaseChar.ElementType.WHITE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe_role(self):
        return RoleProfile(
            role=Planner.Role.SUB_DPS,
            max_field_time=1.0,
        )

    def combat_plan(self, context):
        plan = self.plan()
        self.click_ultimate_action(plan)
        self.click_skill_action(
            plan,
            add_tags=Planner.ActionTag.HIGH_PRIORITY,
            can_execute=self.should_instant_cycle,
        )
        return plan

    def click_skill(self, down_time = 0.05, post_sleep = 0, send_click = True, time_out = 0):
        ret = super().click_skill(down_time, post_sleep, send_click, time_out)
        self.task.wait_until(self.is_cycle_full, time_out=1)
        return ret