from src.char.BaseChar import BaseChar
from src.combat.planner import ActionIntent, FieldClaim, Planner, RoleProfile
from src.Labels import Labels


class Akane(BaseChar):
    cn_name = "明音凛"
    element = BaseChar.ElementType.YELLOW

    ULT_DURATION = 20

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.in_ult_start = self.clock.UNSET
        self.skill_2_count = 0

    def describe_role(self):
        return RoleProfile(role=Planner.Role.MAIN_DPS)

    @property
    def skill_down_time(self):
        return 0.01 if self.task._boss_fight else 0.3

    def combat_plan(self, context):
        plan = self.plan()
        if self.in_ult_start:
            plan.add(FieldClaim.strict(reason="in ultimate"))

        skill = self.click_skill_action()
        skill = plan.add(skill(down_time=self.skill_down_time, post_sleep=0.2))
        ultimate = self.click_ultimate_action(plan)

        def entry():
            if not self.in_ult_start:
                if (yield ultimate):
                    self.in_ult_start = self.clock.now()
                    yield skill
            if self.in_ult_start:
                yield from self.perform_ult_combo(skill)

        plan.entry = entry
        return plan

    def perform_ult_combo(self, skill: ActionIntent):
        timer = self.clock.timer(timeout=13)
        while not timer.expired():
            if self.in_ult_start and (
                self.skill_2_count > 0 or self.clock.now() - self.in_ult_start > self.ULT_DURATION - 2
            ):
                if self.click_ultimate():
                    self.in_ult_start = self.clock.UNSET

            self.click()

            if self.skill_available():
                with self.task.skip_sleep_checks() as skip:
                    skip.sound_combat_context = True
                    if self.task.wait_until(
                        lambda: self.task.find_one(Labels.akane_skill_2), time_out=0.5
                    ):
                        if self.click_skill():
                            self.skill_2_count += 1
                            if self.skill_2_count >= 2:
                                self.skill_2_count = 0
                            return
                    else:
                        yield skill.repeat_for_entry()

            self.sleep(0.1)

    def on_combat_end(self, chars):
        super().on_combat_end(chars)
        self.in_ult_start = self.clock.UNSET
        self.skill_2_count = 0

    @property
    def ultimate_freeze_wait(self):
        return None if self.in_ult_start else 1.5
