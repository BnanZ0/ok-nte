from src.char.Support import Support
from src.combat.planner import (
    Planner,
)


class Linko(Support):
    cn_name = "灵可"
    element = Support.ElementType.GREEN

    SKILL_POST_SLEEP = 2  # E 按下后额外等待（时停由框架动画检测处理；切不进时上调）
    ULT_POST_SLEEP = 0.3  # Q 按下后额外等待（同上）

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def combat_plan(self, context):
        ultimate = self.click_ultimate_action(add_tags=Planner.ActionTag.TEAM_BUFF)
        skill = self.click_skill_action(
            add_tags=Planner.ActionTag.TEAM_BUFF, has_animation=True, send_click=False
        )

        def entry():
            if (yield skill):
                self.sleep(self.SKILL_POST_SLEEP)
            if (yield ultimate):
                self.sleep(self.ULT_POST_SLEEP)

        return self.plan(skill, ultimate, entry=entry)
