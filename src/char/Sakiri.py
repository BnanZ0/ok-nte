from src.char.Support import Support
from src.combat.planner import Planner


class Sakiri(Support):
    cn_name = "早雾"
    element = Support.ElementType.RED

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def combat_plan(self):
        plan = self.plan()
        self.register_ultimate(plan, add_tags=Planner.ActionTag.TEAM_BUFF)
        self.register_skill(plan, add_tags=Planner.ActionTag.TEAM_BUFF)
        return plan

    def click_skill(
        self,
        down_time: float = 0.25,
        post_sleep: float = 0,
        send_click: bool = True,
        time_out: float = 0,
    ) -> bool:
        return super().click_skill(down_time, post_sleep, send_click, time_out)
