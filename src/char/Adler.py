from src.char.BaseChar import BaseChar
from src.combat.planner import (
    CombatContext,
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

    def combat_plan(self, context: CombatContext):
        plan = self.plan()
        skill = self.click_skill_action(plan)
        ultimate = self.click_ultimate_action(plan)

        def entry():
            if self.skill_available() and context.is_action_allowed(self, skill):
                self.continues_normal_attack(1.5)
            if (yield skill):
                self.logger.info("shield deployed")
                self.sleep(0.5)
                yield ultimate
            else:
                self.logger.info("setup skill failed, skipping ultimate")

        plan.entry = entry
        return plan
