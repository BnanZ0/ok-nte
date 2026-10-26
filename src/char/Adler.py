from src.char.BaseChar import BaseChar
from src.combat.planner import (
    ActionSlot,
    FieldPreference,
    Role,
    RoleProfile,
)

SKILL_SHORT_TIMEOUT = 2.0


class Adler(BaseChar):
    """Adler - RED survival support.

    SUB_DPS, SETUP_ONLY: stack YE on entry, cast E (shield), then Q and leave the field.
    YE stacking and E are merged into a single SKILL action so the planner checks
    reservations before executing. Game mechanics are self-contained; no team coupling.
    """

    cn_name = "阿德勒"
    element = BaseChar.ElementType.RED

    def describe_role(self):
        return RoleProfile(
            role=Role.SUB_DPS,
            field_preference=FieldPreference.SETUP_ONLY,
            max_field_time=0,
        )

    def combat_plan(self):
        plan = self.plan()
        self.register_skill(plan)
        self.register_ultimate(plan)
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        if self.skill_available() and self.combat_context.is_slot_available(
            self, slot=ActionSlot.SKILL
        ):
            self.continues_normal_attack(1.5)
        if self.click_skill():
            self.logger.info("shield deployed")
            self.sleep(0.5)
            self.click_ultimate()
        else:
            self.logger.info("setup skill failed, skipping ultimate")
