from dataclasses import dataclass, field

import numpy as np

from src.char.BaseChar import BaseChar
from src.combat.planner import (
    ActionReservation,
    CombatContext,
    FieldClaim,
    FollowupStep,
    Planner,
    RoleProfile,
)
from src.utils import image_utils as iu


@dataclass(slots=True)
class HotoriRecordPlan:
    """Hotori 队友记录机制的一次规划结果。

    字段按生命周期命名，避免用字串 key 隐含语义：
    - `record_window_holds`：E 开窗期间持续保留。
    - `after_ultimate_reservations`：Hotori Q 后，保护下一次 E 路线会用到的技能。
    - `combat_reservations`：队伍加载后整场常驻保留。
    """

    steps: list[FollowupStep] = field(default_factory=list)
    record_window_holds: list[ActionReservation] = field(default_factory=list)
    after_ultimate_reservations: list[ActionReservation] = field(default_factory=list)
    combat_reservations: list[ActionReservation] = field(default_factory=list)

    @property
    def has_required_record_step(self):
        return any(not step.optional for step in self.steps)


@dataclass(slots=True)
class HotoriRecordTeam:
    """Hotori 记录路线需要的队伍识别结果。"""

    teammates: list[BaseChar] = field(default_factory=list)
    zero: BaseChar | None = None
    nanally: BaseChar | None = None
    other: BaseChar | None = None

    @property
    def has_zero_nanally_route(self):
        return self.zero is not None and self.nanally is not None


class Hotori(BaseChar):
    cn_name = "浔"
    element = BaseChar.ElementType.WHITE

    TEAM_SKILL_WINDOW = 5 + 1.2
    MAX_TEAM_SKILL_RECORDS = 3
    ULT_ATTACK_DURATION = 6

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.record_window_start = self.clock.UNSET
        self.pending_team_record_setup_reservation = False
        self.records_ready: bool | None = None

    def describe_role(self):
        return RoleProfile(
            role=Planner.Role.SUB_DPS,
            field_preference=Planner.FieldPreference.SETUP_ONLY,
            combat_start_priority=100,
            max_field_time=0,
        )

    def combat_policies(self, context: CombatContext) -> None:
        if len(self.task.chars) <= 1:
            return

        plan = self._team_record_plan()
        context.reserve_actions(
            plan.combat_reservations,
            reason="hotori reserves Zero skill for combat",
            until=Planner.NEVER_EXPIRES,
        )

    def combat_plan(self, context):
        plan = self.plan()
        if self.max_records == 0:
            self.click_ultimate_action(plan)
            return plan

        if self.records_status() is None or self._should_record():
            plan.add(
                FieldClaim.high(
                    reason="check Hotori team records or try start record",
                )
            )

        ultimate = self.planner_action(
            plan=plan,
            name="hotori_ultimate_with_records",
            tags={Planner.ActionTag.ULTIMATE_ACTION, Planner.ActionTag.HIGH_PRIORITY},
            execute=self._execute_hotori_ultimate,
            can_execute=lambda _: self.ready_for_ultimate(),
            reason="team skill records ready",
            priority_ready=lambda _: self.ready_for_ultimate(),
        )
        setup = self.planner_action(
            plan=plan,
            name="hotori_team_record_setup",
            tags={Planner.ActionTag.SKILL_ACTION, Planner.ActionTag.HIGH_PRIORITY},
            execute=self._execute_hotori_setup,
            reason="open team skill record window",
            can_execute=lambda _: self._can_start_record_setup(),
            priority_ready=lambda _: self._should_record(),
        )

        def entry():
            if not (yield ultimate):
                yield setup

        plan.entry = entry
        return plan

    def _can_start_record_setup(self):
        return self.count_team_skill_records() < 1 and self._should_record()

    def _should_record(self):
        cond1 = self.time_elapsed_accounting_for_freeze(self.last_ultimate_time) > 5
        cond2 = self.records_status() is False
        return cond1 and cond2

    def _execute_hotori_setup(self, context: CombatContext = None):
        if self.click_skill(time_out=3):
            self.clear_reservation()
            self.start_records()
            if context is not None:
                plan = self._team_record_plan()
                route = context.request_route(
                    plan.steps,
                    reason="hotori team skill record window",
                    until=self.is_record_expire,
                    on_finish=self._record_route_finish,
                )
                if route:
                    context.reserve_actions(
                        plan.record_window_holds,
                        reason="hotori team skill record window",
                        until=route.when.expired,
                        on_finish=self._record_window_reservation_finish,
                    )
            return True
        return False

    def _record_route_finish(self):
        self.records_ready = None

    def _record_window_reservation_finish(self):
        self.record_window_start = self.clock.UNSET

    def is_record_expire(self):
        if not self.record_window_start:
            return True
        return self.record_window_elapsed() > self.TEAM_SKILL_WINDOW

    def _team_record_plan(self) -> HotoriRecordPlan:
        team = self._record_team()
        plan = HotoriRecordPlan()

        if team.has_zero_nanally_route:
            if team.other:
                self._add_skill_record_steps(
                    plan,
                    team.other,
                    "teammate Q before recording skill for Hotori",
                    "teammate E records skill for Hotori",
                    optional=True,
                )

            self._add_skill_record_steps(
                plan,
                team.zero,
                "Zero Q before recording skill for Hotori",
                "Zero E records skill for Hotori and prepares entry reaction",
            )

            plan.steps.append(
                FollowupStep.for_entry_reaction(
                    team.nanally,
                    reason="Nanally records entry reaction for Hotori after Zero E",
                )
            )
            plan.record_window_holds.append(
                ActionReservation.for_action(team.nanally, Planner.ActionSlot.SKILL)
            )
            plan.combat_reservations.append(
                ActionReservation.for_action(team.zero, Planner.ActionSlot.SKILL)
            )
        else:
            self._add_skill_record_steps(
                plan,
                team.teammates,
                "Q before recording skill for Hotori",
                "E records skill for Hotori",
                optional=True,
            )

        if self.max_records > 0 and not plan.has_required_record_step:
            plan.steps = []
        return plan

    def _record_team(self) -> HotoriRecordTeam:
        from src.char.Nanally import Nanally
        from src.char.Zero import Zero

        team = HotoriRecordTeam()
        for char in self.task.chars:
            if char is None or char.index == self.index:
                continue
            team.teammates.append(char)
            if isinstance(char, Zero):
                team.zero = char
            elif isinstance(char, Nanally):
                team.nanally = char
            else:
                team.other = char
        return team

    def _add_skill_record_steps(
        self,
        plan: HotoriRecordPlan,
        targets: list[BaseChar] | BaseChar,
        ultimate_reason: str,
        skill_reason: str,
        optional=False,
    ) -> None:
        if not isinstance(targets, list):
            targets = [targets]
        for target in targets:
            plan.steps.append(
                FollowupStep.for_action(
                    target,
                    Planner.ActionSlot.ULTIMATE,
                    reason=ultimate_reason,
                    optional=True,
                )
            )
            plan.steps.append(
                FollowupStep.for_action(
                    target,
                    Planner.ActionSlot.SKILL,
                    reason=skill_reason,
                    optional=optional,
                )
            )
            plan.after_ultimate_reservations.append(
                ActionReservation.for_action(target, Planner.ActionSlot.SKILL)
            )

    def _execute_hotori_ultimate(self, context: CombatContext = None):
        success = self.click_ultimate()
        if success:
            self.records_ready = False
            self.record_window_start = self.clock.UNSET
            should_publish_pending = not self.has_reservation()
            self.start_reservation()
            if context is not None:
                if should_publish_pending:
                    plan = self._team_record_plan()
                    context.reserve_actions(
                        plan.after_ultimate_reservations,
                        reason="hotori reserve route skills after ultimate",
                        until=lambda: not self.has_reservation(),
                    )
                team = self._record_team()
                if team.zero is not None:
                    context.request_switch(
                        team.zero,
                        reason="switch to Zero after Hotori ultimate",
                        until=lambda: bool(self.record_window_start),
                    )
        else:
            self.continues_normal_attack(0.2)
        return success

    def start_reservation(self):
        self.pending_team_record_setup_reservation = True

    def clear_reservation(self):
        self.pending_team_record_setup_reservation = False

    def has_reservation(self):
        return self.pending_team_record_setup_reservation

    def start_records(self):
        self.record_window_start = self.last_skill_time or self.clock.now()
        self.records_ready = False

    def clear_records(self):
        self.record_window_start = self.clock.UNSET
        self.records_ready = None

    @property
    def max_records(self):
        return min(self.MAX_TEAM_SKILL_RECORDS, max(0, len(self.task.chars) - 1))

    def record_window_elapsed(self):
        ret = self.time_elapsed_accounting_for_freeze(self.record_window_start)
        self.logger.info(f"record_window_elapsed {ret}")
        return ret

    def ready_for_ultimate(self):
        return self.records_status() and self.ultimate_available()

    def records_status(self):
        status = self.records_ready
        if self.is_current_char:
            status = self.count_team_skill_records() > 0
            self.records_ready = status
        return status

    def count_team_skill_records(self):

        def is_dark(img):
            white_count = np.sum(img == 255)
            black_count = np.sum(img == 0)
            # self.logger.info(f"white {white_count}, black {black_count}")
            return black_count > white_count

        # fmt: off
        box_1 = self.task.box_of_screen(
            0.430, 0.910, 0.435, 0.915,
            name="skill_record_1",
        )
        box_2 = self.task.box_of_screen(
            0.445, 0.903, 0.453, 0.908,
            name="skill_record_1",
        )
        box_3 = self.task.box_of_screen(
            0.464, 0.904, 0.471, 0.909,
            name="skill_record_1",
        )
        # fmt: on
        count = 3
        _frame = self.task.frame
        for box in [box_3, box_2, box_1]:
            roi = box.crop_frame(_frame)
            roi = iu.binarize_bgr_by_brightness(roi, 240, to_bgr=False)
            # iu.show_images(roi)
            if is_dark(roi):
                count -= 1
            else:
                break
        return count

    def reset_state(self):
        super().reset_state()
        self.clear_records()
        self.clear_reservation()

    def on_combat_end(self, chars):
        self.clear_records()
        self.clear_reservation()

    def _wait_ultimate_unfreeze(self, start, click=True):
        self.logger.debug("waiting for time unfrozen")
        self.task.in_animation = False
        self.task.wait_until(lambda: not self.has_cd("ultimate"), time_out=2)
        try:
            self.task.wait_until(
                lambda: not self.available("ultimate"),
                time_out=13,
                post_action=lambda: click and self.click_with_interval(),
                pre_action=self.check_combat,
            )
        finally:
            duration = self.clock.now() - start - 0.1
            self.add_freeze_duration(start, duration)
        return duration
