from src.char.BaseChar import BaseChar
from src.combat.planner import Planner, RoleProfile


class Fadia(BaseChar):
    cn_name = "法帝娅"
    element = BaseChar.ElementType.BLUE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe_role(self):
        return RoleProfile(role=Planner.Role.SUB_DPS)

    def combat_plan(self):
        plan = self.plan()
        self.register_ultimate(plan)
        self.register_skill(plan)
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        if not self.click_ultimate():
            self.click_skill()
