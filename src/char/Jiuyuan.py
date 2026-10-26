from src.char.BaseChar import BaseChar
from src.combat.planner import (
    Planner,
    RoleProfile,
)


class Jiuyuan(BaseChar):
    cn_name = "九原"
    element = BaseChar.ElementType.GREEN

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe_role(self):
        return RoleProfile(
            role=Planner.Role.SUB_DPS,
            max_field_time=1.0,
        )

    def combat_plan(self):
        plan = self.plan()
        self.register_ultimate(plan)
        self.register_skill(plan)
        plan.register(self.fire_bullets, tags={Planner.ActionTag.DEFAULT_ACTION})
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        self.click_ultimate()
        if not self.click_skill():
            self.fire_bullets()

    def fire_bullets(self):
        if self.combat_context.has_strict_route():
            return
        box = self.task.box_of_screen(
            0.4191, 0.8799, 0.4348, 0.9076, name="jiuyuan_bullet", hcenter=True
        )
        if not self.has_bullets(box):
            return
        self.heavy_attack()
        return True

    def has_bullets(self, box):
        pct = self.task.calculate_color_percentage(bullet_color, box)
        # self.logger.debug(f"Jiuyuan has_bullets {pct}")
        return pct > 0.1


bullet_color = {
    "r": (97, 253),
    "g": (101, 181),
    "b": (168, 255),
}
