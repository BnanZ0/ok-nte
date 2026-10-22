from src.char.BaseChar import BaseChar
from src.combat.planner import CombatContext, FieldClaim, Planner, RoleProfile


class Zero(BaseChar):
    cn_name = "零"
    element = BaseChar.ElementType.WHITE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe_role(self):
        return RoleProfile(
            role=Planner.Role.SUB_DPS,
            max_field_time=1.0,
        )

    def combat_plan(self, context):
        plan = self.plan()
        if self.skill_available():
            plan.add(FieldClaim.normal(reason="Zero skill instant cycle"))
        self.click_ultimate_action(plan)
        self.click_skill_action(
            plan,
            can_execute=self.should_use_skill,
            add_tags=Planner.ActionTag.HIGH_PRIORITY,
        )
        return plan

    def should_use_skill(self, context: CombatContext = None):
        return (
            not self.has_element_reaction_teammate()
            or not self.is_cycle_full()
            or (
                context is not None
                and context.strict_route_wants_action(self, slot=Planner.ActionSlot.SKILL)
            )
        )
