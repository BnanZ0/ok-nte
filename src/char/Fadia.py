from src.char.BaseChar import BaseChar
from src.combat.planner import Planner, RoleProfile


class Fadia(BaseChar):
    cn_name = "法帝娅"
    element = BaseChar.ElementType.BLUE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe_role(self):
        return RoleProfile(role=Planner.Role.SUB_DPS)

    def combat_plan(self, context):
        plan = self.plan()
        ultimate = self.click_ultimate_action(plan)
        skill = self.click_skill_action(plan)

        def entry():
            if not (yield ultimate):
                yield skill

        plan.entry = entry
        return plan
