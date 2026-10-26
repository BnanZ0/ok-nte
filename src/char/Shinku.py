from src.char.BaseChar import BaseChar
from src.combat.planner import Planner, RoleProfile


class Shinku(BaseChar):
    cn_name = "真红"
    element = BaseChar.ElementType.WHITE

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
        while (elapsed := self.clock.now() - start) < 12:
            if elapsed > 9 and self.ultimate_available():
                if self.click_ultimate():
                    return True
            self.click_skill()
            self.sleep(0.1)
            self.normal_attack()
            self.sleep(0.1)
        return False

    def on_combat_end(self, chars):
        self.switch_other_char()
