from src.char.BaseChar import BaseChar
from src.combat.planner import ActionIntent, Planner, RoleProfile
from src.Labels import Labels


class Akane(BaseChar):
    cn_name = "明音凛"
    element = BaseChar.ElementType.YELLOW

    ULT_DURATION = 20

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.in_ult = None

    def describe_role(self):
        return RoleProfile(
            role=Planner.Role.MAIN_DPS, field_preference=Planner.FieldPreference.MAIN_DPS
        )

    @property
    def skill_down_time(self):
        return 0.01 if self.task._boss_fight else 0.3

    def combat_plan(self, context):
        ultimate = self.click_ultimate_action()
        skill = self.click_skill_action(down_time=self.skill_down_time, post_sleep=0.2)

        def entry():
            if (yield ultimate):
                yield skill
                self.in_ult = True
                yield from self.perform_ult_combo(skill)

        return self.plan(skill, ultimate, entry=entry)

    def perform_ult_combo(self, skill: ActionIntent):
        skill_count = 0
        ult_2 = False
        start = self.now()
        while skill_count < 2:
            self.click()
            if self.skill_available():
                with self.task.skip_sleep_checks() as skip:
                    skip.sound_combat_context = True
                    if self.task.wait_until(
                        lambda: self.task.find_one(Labels.akane_skill_2), time_out=0.5
                    ):
                        if self.click_skill():
                            skill_count += 1
                            if skill_count < 2:
                                self.continues_normal_attack(1)
                    else:
                        yield skill.repeat_for_entry()

            if not ult_2 and (skill_count > 0 or self.now() - start > self.ULT_DURATION - 2):
                ult_2 = self.click_ult_2()
            self.sleep(0.1)

    def click_ult_2(self):
        if not self.ultimate_available():
            return False
        self.logger.info("click ult 2")

        start = self.now()
        clicked = False
        while self.ultimate_available():
            clicked = self.send_ultimate_key()
            self.click()
            self.sleep(0.1, checks=self.SleepChecks.NONE)

        if not clicked:
            return False

        self._wait_ultimate_unfreeze(start)
        self.in_ult = False
        return clicked

    def on_combat_end(self, chars):
        super().on_combat_end(chars)
        self.in_ult = None

    def _wait_ultimate_unfreeze(self, start, click=True):
        if self.in_ult:
            return super()._wait_ultimate_unfreeze(start, click)
        wait = 1.5
        if click:
            self.continues_normal_attack(wait)
        else:
            self.sleep(wait)
        duration = self.now() - start
        self.add_freeze_duration(start, duration)
        return duration
