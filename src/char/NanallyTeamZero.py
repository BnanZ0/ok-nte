from src.char.BaseChar import BaseChar


class NanallyTeamZero(BaseChar):
    NEXT_BUILTIN_KEY = "char_nanally_team_nanally"

    def do_perform(self):
        self.click_ultimate()
        self.click_skill()
        self.queue_switch_to_builtin_char(self.NEXT_BUILTIN_KEY)
