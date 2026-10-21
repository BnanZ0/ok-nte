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
        ultimate = self.click_ultimate_action()
        skill = self.click_skill_action()

        def entry():
            if not (yield ultimate):
                yield skill

        return self.plan(ultimate, skill, entry=entry)
