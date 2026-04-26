from src.char.BaseChar import BaseChar


class NanallyTeamZero(BaseChar):
    """娜娜莉队中的零固定轮转脚本。"""

    NEXT_BUILTIN_KEY = "char_nanally_team_nanally"

    def do_perform(self):
        """执行零的固定输出循环后切到娜娜莉。"""
        self.click_ultimate()
        self.click_skill()
        self.queue_switch_to_builtin_char(self.NEXT_BUILTIN_KEY)
