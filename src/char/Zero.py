import time

from src.char.BaseChar import BaseChar

class Zero(BaseChar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def do_perform(self):
        if self.has_intro:
            self.logger.debug('test wait intro')
            self.continues_normal_attack(duration=2.0)
        if self.click_ultimate():
            start = time.time()
            while time.time() - start < 12:
                if self.click_skill()[0]:
                    self.task.middle_click_relative(0.5, 0.5)
                    pass
                self.normal_attack()
            return
        i = 0
        while not self.is_con_full():
            if i % 4 == 0:
                self.heavy_attack()
                if self.skill_available():
                    self.task.middle_click_relative(0.5, 0.5)
                    break
                i = 0
            self.normal_attack()
            i += 1
        if self.skill_available():
            self.click_skill(post_sleep=1.0)