from src.char.Support import Support
from src.combat.planner import Planner

SKILL_SHORT_TIMEOUT = 2.0


class Haniel(Support):
    """Haniel - BLUE support.

    SUB_DPS, SETUP_ONLY: Q to deploy the enhanced domain, then E to deploy the
    companion, then leave the field. Self-contained and independent of any specific
    team composition; higher-level coordination is handled outside this file.
    """

    cn_name = "哈妮娅"
    element = Support.ElementType.BLUE

    def combat_plan(self):
        plan = self.plan()
        self.register_ultimate(plan, add_tags=Planner.ActionTag.TEAM_BUFF)
        self.register_skill(plan)
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        if self.click_ultimate():
            self.sleep(0.3)
        if self.click_skill():
            self.sleep(0.3)
