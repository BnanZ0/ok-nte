from src.char.BaseChar import BaseChar
from src.combat.planner import (
    Planner,
    RoleProfile,
)


class Nanally(BaseChar):
    cn_name = "娜娜莉"
    element = BaseChar.ElementType.GREEN

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe_role(self):
        return RoleProfile(
            role=Planner.Role.MAIN_DPS,
            max_field_time=1.5,
        )

    def combat_plan(self):
        plan = self.plan()
        self.register_skill(plan)
        self.register_ultimate(plan)
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        self.click_skill()
        if self.click_ultimate():
            self.perform_in_ult()

    def perform_in_ult(self):
        start = self.clock.now()
        skill_used = False
        while (elapsed := self.clock.now() - start) < 6:
            if elapsed > 1 and not self.ultimate_available(False):
                break
            if not skill_used:
                skill_used = self.click_skill()
            self.normal_attack()
            self.sleep(0.2)
        return skill_used

    def on_combat_end(self, chars):
        self.switch_other_char()
