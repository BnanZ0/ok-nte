from src.char.BaseChar import BaseChar
from src.combat.planner import (
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

    def combat_plan(self):
        plan = self.plan()
        self.register_skill(plan)
        self.register_ultimate(plan)
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        if self.click_skill():
            self.sleep(0.4)
        self.click_ultimate()
