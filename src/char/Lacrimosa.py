from src.char.BaseChar import BaseChar
from src.combat.planner import FieldClaim, Planner, RoleProfile


class Lacrimosa(BaseChar):
    cn_name = "安魂曲"
    element = BaseChar.ElementType.PURPLE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe_role(self):
        return RoleProfile(
            role=Planner.Role.MAIN_DPS,
            max_field_time=1.5,
        )

    def combat_plan(self, context):
        claims = []
        if self.time_elapsed_accounting_for_freeze(self.last_switch_time) > 2.5:
            claims.append(FieldClaim.normal("Lacrimosa wants short field time"))
        return self.plan(
            self.click_ultimate_action(),
            self.click_skill_action(),
            claims=claims,
        )

    def on_combat_end(self, chars):
        self.switch_other_char()
