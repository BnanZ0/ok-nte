import time

from src.char.BaseChar import BaseChar
from src.combat.planner import CombatContext, Planner, RoleProfile


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

    def combat_plan(self, context):
        skill = self.click_skill_action()
        ultimate = self.click_ultimate_action()

        def entry():
            yield skill
            if (yield ultimate):
                self.perform_in_ult(context)

        return self.plan(skill, ultimate, entry=entry)

    def perform_in_ult(self, context: CombatContext = None):
        start = time.time()
        while (elapsed := time.time() - start) < 12:
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
