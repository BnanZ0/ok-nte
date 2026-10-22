from src.char.BaseChar import BaseChar
from src.combat.planner import (
    CombatContext,
    FieldPreference,
    Role,
    RoleProfile,
)

SKILL_SHORT_TIMEOUT = 2.0


class Skia(BaseChar):
    """Skia - YELLOW sub DPS setup.

    Casts Q then E to complete the aspect setup, then leaves the field.
    Self-contained and independent of any specific team composition.
    """

    cn_name = "翳"
    element = BaseChar.ElementType.YELLOW

    def describe_role(self):
        return RoleProfile(
            role=Role.SUB_DPS,
            field_preference=FieldPreference.SETUP_ONLY,
        )

    def combat_plan(self, context: CombatContext):
        plan = self.plan()
        skill = self.click_skill_action(plan)
        ultimate = self.click_ultimate_action(plan)

        def entry():
            if (yield skill):
                self.sleep(0.4)
            yield ultimate

        plan.entry = entry
        return plan
