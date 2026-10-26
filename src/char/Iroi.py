from src.char.Support import Support
from src.combat.planner import Planner


class Iroi(Support):
    cn_name = "伊洛伊"
    element = Support.ElementType.GREEN

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mouse_pressed = False

    def combat_plan(self):
        plan = self.plan()
        self.register_skill(plan, add_tags=Planner.ActionTag.TEAM_BUFF)
        self.register_ultimate(plan)
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        if self.click_skill() and self.ultimate_available():
            self.sleep(0.8)
        self.click_ultimate()

    def click_ultimate(self, send_click=True, wait_if_no_cd=0):
        try:
            if ret := super().click_ultimate(send_click=send_click, wait_if_no_cd=wait_if_no_cd):
                self.sleep(0.7)
            return ret
        finally:
            if self._mouse_pressed:
                self.task.mouse_up()
            self._mouse_pressed = False

    def _wait_ultimate_unfreeze(self, start, click=False):
        self.task.mouse_down()
        self._mouse_pressed = True
        return super()._wait_ultimate_unfreeze(start=start, click=click)
