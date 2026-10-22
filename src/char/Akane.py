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
        return RoleProfile(role=Planner.Role.MAIN_DPS, max_field_time=1.8)

    @property
    def skill_down_time(self):
        return 0.01 if self.task._boss_fight else 0.3

    def combat_plan(self, context):
        plan = self.plan()
        if self.in_ult():
            plan.add(FieldClaim.strict(reason="in ultimate"))

        skill = self.click_skill_action()
        skill = plan.add(skill(down_time=self.skill_down_time, post_sleep=0.2))
        skill_2 = self.click_skill_action(
            plan, can_execute=lambda _: self.skill_available() and self.find_skill_2()
        )
        ultimate = self.click_ultimate_action(plan)

        def entry():
            if not self.in_ult():
                if (yield ultimate):
                    self.in_ult_start = self.clock.now()
                    yield skill
            if self.in_ult():
                yield from self.perform_ult_combo(skill)
            else:
                yield skill_2

        plan.entry = entry
        return plan

    def perform_ult_combo(self, skill: ActionIntent):
        timer = self.clock.timer(timeout=13)
        while not timer.expired():
            if self.skill_2_count > 0 and self.in_ult(time_offset=2):
                if self.click_ultimate():
                    self.in_ult_start = self.clock.UNSET
                    self.skill_2_count = 0
                    return

            self.click()

            if self.skill_available():
                with self.task.skip_sleep_checks() as skip:
                    skip.sound_combat_context = True
                    if self.task.wait_until(self.find_skill_2, time_out=0.5):
                        if self.click_skill():
                            if not self.task._boss_fight:
                                self.continues_normal_attack(1)
                            self.skill_2_count += 1
                    else:
                        yield skill.repeat_for_entry()

            self.sleep(0.1)

    def find_skill_2(self):
        return self.task.find_one(Labels.akane_skill_2)

    def on_combat_end(self, chars):
        super().on_combat_end(chars)
        self.in_ult_start = self.clock.UNSET
        self.skill_2_count = 0

    @property
    def ultimate_freeze_wait(self):
        return None if self.in_ult_start else 1.5

    def in_ult(self, time_offset: float = 0) -> bool:
        if not self.in_ult_start:
            return False
        elif (
            self.time_elapsed_accounting_for_freeze(self.in_ult_start)
            > self.ULT_DURATION - time_offset
        ):
            self.logger.info("in ult timeout")
            self.in_ult_start = self.clock.UNSET
            return False
        else:
            return True
