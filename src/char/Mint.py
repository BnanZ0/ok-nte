from src.char.BaseChar import BaseChar
from src.combat.planner import Planner, RoleProfile


class Mint(BaseChar):
    cn_name = "薄荷"
    element = BaseChar.ElementType.GREEN

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe_role(self):
        return RoleProfile(
            role=Planner.Role.SUB_DPS,
            max_field_time=1.0,
        )

    def combat_plan(self):
        plan = self.plan()
        self.register_ultimate(plan)
        self.register_skill(plan)
        return plan
