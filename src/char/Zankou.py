from src.char.BaseChar import BaseChar
from src.combat.planner import Planner, RoleProfile
from src.Labels import Labels


class Zankou(BaseChar):
    cn_name = "残虹"
    element = BaseChar.ElementType.RED

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def describe_role(self):
        return RoleProfile(role=Planner.Role.MAIN_DPS)

    def combat_plan(self):
        plan = self.plan()
        plan.register(
            self.perform_skill_combo,
            tags={Planner.ActionTag.DEFAULT_ACTION},
            slot=Planner.ActionSlot.SKILL,
            name="zankou_skill_combo",
            reason="skill action available",
            can_execute=self.skill_available,
            priority_ready=self.skill_available,
        )
        self.register_ultimate(plan)
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        if not self.find_ult_purple():
            if self.perform_skill_combo() and self.ultimate_available():
                self.task.wait_until(
                    self.find_ult_purple,
                    post_action=self.click_with_interval,
                    time_out=0.6,
                )
        if self.find_ult_purple():
            self.click_ultimate()
            self.task.wait_until(
                self.ultimate_available, post_action=self.click_with_interval, time_out=3
            )
            self.sync_check_combat()
            self.click_ultimate()

    def perform_skill_combo(self):
        timer = self.clock.timer(timeout=10)
        clicked_skill = False
        while not timer.expired():
            click_skill, purple_skill = self.click_zankou_skill()
            if not clicked_skill:
                clicked_skill = click_skill
            if purple_skill or self.find_ult_purple():
                break

            self.sleep(0.1)

            try:
                self.task.mouse_down()
                for _ in range(5):
                    if self.find_zankou_skill() or self.find_ult_purple():
                        break
                    self.sleep(0.1)
            finally:
                self.task.mouse_up()

        return clicked_skill

    def find_zankou_skill(self):
        to_find = [Labels.zankou_skill_gold, Labels.zankou_skill_purple]
        for feature_name in to_find:
            if self.task.find_one(feature_name):
                return feature_name

    def click_zankou_skill(self):
        click_skill = False
        purple_skill = False
        if feature_name := self.find_zankou_skill():
            if click_skill := self.click_skill():
                with self.task.skip_sleep_checks() as skip:
                    skip.sound_combat_context = True
                    if feature_name == Labels.zankou_skill_purple:
                        self.continues_normal_attack(2)
                        purple_skill = True
                    else:
                        self.continues_normal_attack(1)
        return click_skill, purple_skill

    def find_ult_purple(self):
        return self.task.find_one(Labels.zankou_ult_purple)
