from src.char.Support import Support
from src.combat.planner import Planner


class Linko(Support):
    """灵可 Linko - GREEN 纯增益辅助。

    与其它辅助的差别在于 E/Q 都是时停动画(E 约 2s, Q 约 4s):
    框架的 click_skill / click_ultimate 会自动检测脱队动画并记账冻结时长,
    所以这里不 sleep 固定时长, 也不做输出连段。

    爆发窗口的释放顺序是 "有 E 放 E -> 有 Q 放 Q -> 切人前复检一次":
    时停动画期间能量与冷却仍在推进, 放完 E 之后 Q 可能刚好攒满,
    因此切人前再确认一遍, 避免空转浪费增益窗口。
    """

    cn_name = "灵可"
    element = Support.ElementType.GREEN

    def combat_plan(self):
        plan = self.plan()
        self.register_skill(plan, add_tags=Planner.ActionTag.TEAM_BUFF)
        self.register_ultimate(plan, add_tags=Planner.ActionTag.TEAM_BUFF)
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        if self.click_skill():
            self.sleep(0.3)
        if self.click_ultimate():
            self.sleep(0.2)
        # 时停动画期间能量与冷却会变化, 切人前复检一次
        if self.ultimate_available():
            self.click_ultimate()
        elif self.skill_available():
            self.click_skill()
