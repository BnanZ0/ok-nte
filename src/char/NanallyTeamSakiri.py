from src.char.BaseChar import BaseChar


class NanallyTeamSakiri(BaseChar):
    """娜娜莉队中的早雾固定轮转脚本。"""

    HOLD_SKILL_DURATION = 2.0
    INTRO_WAIT_DURATION = 2.0
    NEXT_BUILTIN_KEY = "char_nanally_team_jiuyuan"

    def do_perform(self):
        """执行早雾的固定输出循环后切到九原。"""
        if self.has_intro:
            self.sleep(self.INTRO_WAIT_DURATION)
        self.click_ultimate()
        self.hold_skill(self.HOLD_SKILL_DURATION)
        self.queue_switch_to_builtin_char(self.NEXT_BUILTIN_KEY)
