from src.char.BaseChar import BaseChar
from src.combat.planner import (
    ActionIntent,
    CombatContext,
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

    def combat_plan(self, context):
        plan = self.plan()
        skill = self.click_skill_action(plan)
        ultimate = self.click_ultimate_action(plan)

        def entry():
            yield skill
            if (yield ultimate):
                self.perform_in_ult(context, skill)

        plan.entry = entry
        return plan

    def perform_in_ult(self, context: CombatContext, skill: ActionIntent):
        start = self.clock.now()
        skill_used = False
        while (elapsed := self.clock.now() - start) < 6:
            if elapsed > 1 and not self.ultimate_available(False):
                break
            if not skill_used and context.is_action_allowed(self, skill):
                skill_used = self.click_skill()
            self.normal_attack()
            self.sleep(0.2)
        return skill_used

    def on_combat_end(self, chars):
        self.switch_other_char()
