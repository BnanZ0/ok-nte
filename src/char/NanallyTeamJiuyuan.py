from src.char.BaseChar import BaseChar


class NanallyTeamJiuyuan(BaseChar):
    HEAVY_ATTACK_DURATION = 3
    NEXT_BUILTIN_KEY = "char_nanally_team_zero"

    def do_perform(self):
        self.click_ultimate()
        self.click_skill()
        self.heavy_attack(self.HEAVY_ATTACK_DURATION)
        self.queue_switch_to_builtin_char(self.NEXT_BUILTIN_KEY)
