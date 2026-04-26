from src.char.BaseChar import BaseChar


class NanallyTeamJiuyuan(BaseChar):
    """娜娜莉队中的九原固定轮转脚本。"""

    HEAVY_ATTACK_DURATION = 2.5
    NEXT_BUILTIN_KEY = "char_nanally_team_zero"

    def do_perform(self):
        """执行九原的固定输出循环后切到零。"""
        self.click_ultimate()
        self.click_skill()
        self.heavy_attack(self.HEAVY_ATTACK_DURATION)
        self.queue_switch_to_builtin_char(self.NEXT_BUILTIN_KEY)
