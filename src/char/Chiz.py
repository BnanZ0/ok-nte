import re

from src.char.BaseChar import BaseChar
from src.combat.planner import Planner, RoleProfile


class Chiz(BaseChar):
    cn_name = "小吱"
    element = BaseChar.ElementType.WHITE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe_role(self):
        return RoleProfile(role=Planner.Role.MAIN_DPS)

    def combat_plan(self, context):
        plan = self.plan()
        ultimate = self.click_ultimate_action(plan)
        skill = self.click_skill_action(plan)

        def entry():
            if (yield ultimate):
                self.perform_in_ult()
            yield skill

        plan.entry = entry
        return plan

    def perform_in_ult(self):
        box = self.task.box_of_screen(0.487, 0.775, 0.514, 0.798, name="percentage")
        self.task.wait_ocr(box=box, match=re.compile(r"-?\d+%", re.IGNORECASE))
        timer = self.clock.timer(timeout=8)
        while not timer.expired():
            red_pct = self.task.calculate_color_percentage(red_pct_color, box)
            yellow_pct = self.task.calculate_color_percentage(yellow_pct_color, box)
            if yellow_pct > red_pct:
                self.send_skill_key()
            self.click_with_interval()
            self.sleep(0.1)


red_pct_color = {
    "r": (250, 255),
    "g": (115, 125),
    "b": (115, 120),
}

yellow_pct_color = {
    "r": (250, 255),
    "g": (230, 240),
    "b": (120, 125),
}
