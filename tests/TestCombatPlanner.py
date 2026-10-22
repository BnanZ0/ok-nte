import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.char.Akane import Akane
from src.char.BaseChar import BaseChar
from src.char.Nanally import Nanally
from src.char.Sakiri import Sakiri
from src.char.Support import Support
from src.combat.BaseCombatTask import BaseCombatTask
from src.combat.planner import (
    NEVER_EXPIRES,
    ActionIntent,
    ActionReservation,
    ActionResult,
    ActionSlot,
    ActionTag,
    CombatPlan,
    CombatPlanner,
    ExpectedEntry,
    FieldClaim,
    FieldPreference,
    FollowupStep,
    Planner,
    Role,
    RoleProfile,
    SwitchDecision,
    SwitchInGuard,
)
from src.utils import clock


class FakeTask:
    clock = clock

    def __init__(self):
        self.chars = []
        self.reaction_target = None

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return 999

    def find_element_reaction_target(self, source_char):
        return self.reaction_target


class FakeChar:
    def __init__(
        self,
        index,
        name,
        field_preference=FieldPreference.SUB_DPS,
        tags=None,
        plan_items=None,
        policies=None,
        claims=None,
        can_execute=None,
        priority_ready=None,
        switch_in_guard=None,
        max_field_time=1.5,
        elapsed=0,
        combat_start_priority=0,
        cycle_full=False,
        role=None,
    ):
        self.index = index
        self.name = name
        self.char_name = name
        self.last_perform = clock.UNSET
        self.last_switch_time = clock.UNSET
        self.is_current_char = False
        self._field_preference = field_preference
        self._role = role
        self._tags = set(tags or {ActionTag.DAMAGE})
        self._plan_items = plan_items
        self._policies = policies
        self._claims = claims or []
        self._can_execute = can_execute
        self._priority_ready = priority_ready
        self._switch_in_guard = switch_in_guard
        self._max_field_time = max_field_time
        self._elapsed = elapsed
        self._combat_start_priority = combat_start_priority
        self._cycle_full = cycle_full
        self.is_dead = False
        self.has_intro = False
        self.waited = 0
        self.plan_calls = 0

    def __repr__(self):
        return self.name

    def __eq__(self, other):
        return isinstance(other, FakeChar) and self.index == other.index

    def describe_role(self):
        role = self._role
        if role is None:
            role = {
                FieldPreference.MAIN_DPS: Role.MAIN_DPS,
                FieldPreference.SUPPORT: Role.SUPPORT,
            }.get(self._field_preference, Role.SUB_DPS)
        return RoleProfile(
            role=role,
            field_preference=self._field_preference,
            max_field_time=self._max_field_time,
            combat_start_priority=self._combat_start_priority,
        )

    def combat_plan(self, context):
        self.plan_calls += 1
        if callable(self._plan_items):
            plan_items = self._plan_items(context)
            if isinstance(plan_items, CombatPlan):
                return plan_items
            return self._plan_from_plan_items(plan_items)
        if self._plan_items is not None:
            return self._plan_from_plan_items(self._plan_items)

        claims = self._claims(context) if callable(self._claims) else self._claims
        slot = None
        if ActionTag.SKILL_ACTION in self._tags:
            slot = ActionSlot.SKILL
        elif ActionTag.ULTIMATE_ACTION in self._tags:
            slot = ActionSlot.ULTIMATE
        return self._plan_from_plan_items(
            [
                ActionIntent(
                    name=f"{self.name}_action",
                    tags=set(self._tags),
                    execute=lambda _: ActionResult(
                        name=f"{self.name}_action",
                        success=True,
                        tags=set(self._tags),
                        slot=slot,
                    ),
                    slot=slot,
                    reason=f"{self.name} available",
                    can_execute=self._can_execute,
                    priority_ready=self._priority_ready,
                )
            ]
            + list(claims)
        )

    def _plan_from_plan_items(self, plan_items):
        actions = []
        claims = []
        for item in plan_items:
            if isinstance(item, ActionIntent):
                actions.append(item)
            elif isinstance(item, FieldClaim):
                claims.append(item)
        return CombatPlan(actions=actions, claims=claims)

    def combat_policies(self, context):
        if self._policies is not None:
            self._policies(context)

    def is_cycle_full(self):
        return self._cycle_full

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return self._elapsed

    def continues_normal_attack(self, duration):
        self.waited = duration

    def switch_in_guard(self, context, from_char, has_intro):
        if self._switch_in_guard is None:
            return SwitchInGuard.allow()
        return self._switch_in_guard(context, from_char, has_intro)


class PublicApiChar(BaseChar):
    def __init__(self, task, index, name, plan_factory, max_field_time=0):
        super().__init__(task, index, char_id=name)
        self.char_name = name
        self._plan_factory = plan_factory
        self._max_field_time = max_field_time
        self.skill_clicked = 0
        self.ultimate_clicked = 0
        self.arc_clicked = 0
        self.normal_attack_time = 0

    def __repr__(self):
        return self.char_name

    def __str__(self):
        return self.char_name

    def combat_plan(self, context):
        return self._plan_factory(self, context)

    def describe_role(self):
        return RoleProfile(max_field_time=self._max_field_time)

    def skill_available(self, wait_if_cd_ready=0):
        return True

    def ultimate_available(self, wait_if_cd_ready=0):
        return True

    def click_skill(self, *args, **kwargs):
        self.skill_clicked += 1
        return True

    def click_ultimate(self, *args, **kwargs):
        self.ultimate_clicked += 1
        return True

    def click_arc(self, *args, **kwargs):
        self.arc_clicked += 1
        return True

    def continues_normal_attack(self, duration):
        self.normal_attack_time = duration


class TestCombatPlanner(unittest.TestCase):
    def test_planner_namespace_exports_existing_enums(self):
        self.assertIs(Planner.NEVER_EXPIRES, NEVER_EXPIRES)

    def test_role_profile_defaults_follow_role(self):
        self.assertEqual(RoleProfile().field_preference, FieldPreference.SUB_DPS)
        for role, preference in (
            (Role.MAIN_DPS, FieldPreference.MAIN_DPS),
            (Role.SUB_DPS, FieldPreference.SUB_DPS),
            (Role.SUPPORT, FieldPreference.SUPPORT),
        ):
            with self.subTest(role=role):
                profile = RoleProfile(role=role)
                self.assertEqual(profile.field_preference, preference)

    def test_role_profile_preserves_explicit_field_preference(self):
        for role in Role:
            for preference in FieldPreference:
                with self.subTest(role=role, preference=preference):
                    profile = RoleProfile(role=role, field_preference=preference)
                    self.assertEqual(profile.field_preference, preference)

    def _char(self, index, name, **kwargs):
        return FakeChar(index, name, **kwargs)

    def _main_dps(self, index, name="dps", **kwargs):
        return self._char(
            index,
            name,
            field_preference=FieldPreference.MAIN_DPS,
            **kwargs,
        )

    def _support(self, index, name="support", **kwargs):
        return self._char(
            index,
            name,
            field_preference=FieldPreference.SUPPORT,
            **kwargs,
        )

    def _setup_char(self, index, name="setup", **kwargs):
        return self._char(
            index,
            name,
            field_preference=FieldPreference.SETUP_ONLY,
            **kwargs,
        )

    def _planner(self, chars):
        task = FakeTask()
        task.chars = chars
        planner = CombatPlanner(task)
        planner.reset(chars)
        return planner

    def _publish(self, planner, source, publish):
        original_combat_plan = source.combat_plan

        def execute(context):
            publish(context)
            return True

        def combat_plan(_):
            return CombatPlan(
                actions=[
                    ActionIntent(
                        name="publish_test_request",
                        tags={ActionTag.DEFAULT_ACTION},
                        execute=execute,
                        reason="publish test request",
                    )
                ]
            )

        source.combat_plan = combat_plan
        try:
            planner.perform_current_char(source)
        finally:
            source.combat_plan = original_combat_plan

    def _action(
        self,
        name,
        tags,
        slot=None,
        calls=None,
        success=True,
        priority_ready=None,
    ):
        def execute(_):
            if calls is not None:
                calls.append(name)
            return ActionResult(
                name=name,
                success=success,
                tags=set(tags),
                slot=slot,
            )

        return ActionIntent(
            name=name,
            tags=set(tags),
            slot=slot,
            execute=execute,
            reason=f"{name} ready",
            priority_ready=priority_ready,
        )

    def test_main_dps_field_preference_boosts_entry(self):
        current = self._support(0, "current")
        dps = self._main_dps(1)
        support = self._support(2)
        planner = self._planner([current, dps, support])

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, dps)
        self.assertIn("dps", decision.reason)

    def test_inferred_and_explicit_field_preferences_affect_switch_selection(self):
        for profile, expected in (
            (RoleProfile(role=Role.MAIN_DPS), "dps"),
            (
                RoleProfile(role=Role.MAIN_DPS, field_preference=FieldPreference.SETUP_ONLY),
                "sub_dps",
            ),
        ):
            with self.subTest(preference=profile.field_preference):
                current = self._support(0, "current")
                dps = self._char(1, "dps")
                dps.describe_role = Mock(return_value=profile)
                sub_dps = self._char(2, "sub_dps")
                sub_dps.describe_role = Mock(return_value=RoleProfile(role=Role.SUB_DPS))
                planner = self._planner([current, dps, sub_dps])

                decision = planner.decide_switch(current)

                self.assertIs(decision.target, dps if expected == "dps" else sub_dps)

    def test_team_buff_skill_outranks_main_dps_ultimate(self):
        current = self._setup_char(0, "current", max_field_time=0)
        dps = self._main_dps(1, "dps", tags={ActionTag.ULTIMATE_ACTION})
        support = self._support(
            2,
            "support",
            tags={ActionTag.SKILL_ACTION, ActionTag.SUPPORT, ActionTag.TEAM_BUFF},
        )
        planner = self._planner([current, dps, support])

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, support)
        self.assertGreater(decision.priority, 320)

    def test_normal_switch_excludes_current_from_scoring(self):
        dps = self._main_dps(0)
        support = self._support(1)
        planner = self._planner([dps, support])

        decision = planner.decide_switch(dps)

        self.assertEqual(decision.target, support)

    def test_decide_switch_caches_combat_plan_per_char(self):
        current = FakeChar(0, "current")
        claimed = FakeChar(
            1,
            "claimed",
            claims=lambda _: [FieldClaim.high("claim also scans plan")],
        )
        planner = self._planner([current, claimed])

        planner.decide_switch(current)

        self.assertEqual(claimed.plan_calls, 1)

    def test_field_claim_can_request_entry_without_ready_action(self):
        current = FakeChar(0, "current")
        claimed = FakeChar(
            1,
            "claimed",
            priority_ready=lambda _: False,
            max_field_time=0,
            claims=lambda _: [FieldClaim.critical("check mechanism")],
        )
        planner = self._planner([current, claimed])

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, claimed)
        self.assertIn("field claim", decision.reason)

    def test_field_claim_expected_entry_replays_into_entry_flow(self):
        calls = []
        current = FakeChar(0, "current")
        ultimate = self._action(
            "claimed_ultimate",
            {ActionTag.ULTIMATE_ACTION},
            ActionSlot.ULTIMATE,
            calls,
        )
        skill = self._action(
            "claimed_skill",
            {ActionTag.SKILL_ACTION},
            ActionSlot.SKILL,
            calls,
        )

        def entry():
            ultimate_result = yield ultimate
            if not ultimate_result:
                yield skill

        claimed = FakeChar(
            1,
            "claimed",
            priority_ready=lambda _: False,
            max_field_time=0,
            plan_items=lambda _: CombatPlan(
                [ultimate, skill],
                claims=[
                    FieldClaim.critical(
                        "claim burst",
                        expected_entry=ExpectedEntry(slot=ActionSlot.ULTIMATE),
                    )
                ],
                entry=entry,
            ),
        )
        planner = self._planner([current, claimed])

        decision = planner.decide_switch(current)
        planner.prepare_switch(decision)
        planner.complete_switch(decision, previous_char=current)
        result = planner.perform_current_char(claimed)

        self.assertEqual(decision.target, claimed)
        self.assertEqual(calls, ["claimed_ultimate"])
        self.assertEqual(result.name, "claimed_ultimate")

    def test_strict_field_claim_beats_ordinary_score_and_element_reaction(self):
        current = FakeChar(0, "current")
        ordinary = FakeChar(
            1,
            "ordinary",
            field_preference=FieldPreference.MAIN_DPS,
            tags={ActionTag.ULTIMATE_ACTION},
        )
        claim_active = {"value": False}
        claimed = FakeChar(
            2,
            "claimed",
            priority_ready=lambda _: False,
            max_field_time=0,
            claims=lambda _: [FieldClaim.strict("return now")] if claim_active["value"] else [],
        )
        planner = self._planner([current, ordinary, claimed])

        ordinary_decision = planner.decide_switch(current)
        self.assertEqual(ordinary_decision.target, ordinary)
        self.assertFalse(ordinary_decision.skip_switch_waits)
        claim_active["value"] = True
        decision = planner.decide_switch(current)
        self.assertEqual(decision.target, claimed)
        self.assertTrue(decision.strict)
        self.assertTrue(decision.skip_switch_waits)

        current._cycle_full = True
        planner.task.reaction_target = ordinary
        decision = planner.decide_switch(current)
        self.assertEqual(decision.target, claimed)
        self.assertTrue(decision.strict)
        self.assertTrue(decision.skip_switch_waits)

    def test_strict_field_claim_preempts_switch_request(self):
        current = FakeChar(0, "current")
        requested = FakeChar(1, "requested")
        claimed = FakeChar(2, "claimed", claims=[FieldClaim.strict("return now")])
        planner = self._planner([current, requested, claimed])
        self._publish(
            planner,
            current,
            lambda context: context.request_switch(requested, reason="switch request"),
        )

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, claimed)
        self.assertTrue(decision.strict)
        self.assertIn("strict field claim", decision.reason)

    def test_locked_strict_route_precedes_strict_field_claim(self):
        current = FakeChar(0, "current")
        route_target = FakeChar(1, "route target")
        claimed = FakeChar(2, "claimed", claims=[FieldClaim.strict("return now")])
        planner = self._planner([current, route_target, claimed])
        self._publish(
            planner,
            current,
            lambda context: context.request_route([FollowupStep.for_switch(route_target)]),
        )

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, route_target)
        self.assertIn("strict route", decision.reason)
        self.assertTrue(decision.skip_switch_waits)

    def test_strict_field_claim_uses_ordinary_entry_flow(self):
        calls = []
        current = FakeChar(0, "current")
        skill = self._action("skill", {ActionTag.SKILL_ACTION}, ActionSlot.SKILL, calls)
        ultimate = self._action("ultimate", {ActionTag.ULTIMATE_ACTION}, ActionSlot.ULTIMATE, calls)

        def entry():
            yield skill

        claimed = FakeChar(
            1,
            "claimed",
            plan_items=lambda _: CombatPlan(
                actions=[skill, ultimate],
                claims=[FieldClaim.strict("return now")],
                entry=entry,
            ),
        )
        planner = self._planner([current, claimed])

        decision = planner.decide_switch(current)
        planner.prepare_switch(decision)
        planner.complete_switch(decision, previous_char=current)
        result = planner.perform_current_char(claimed)

        self.assertEqual(decision.target, claimed)
        self.assertIsNone(decision.expected_entry)
        self.assertEqual(calls, ["skill"])
        self.assertEqual(result.name, "skill")

    def test_combat_start_uses_role_profile_priority(self):
        current = FakeChar(0, "current")
        low = FakeChar(1, "low", combat_start_priority=20)
        high = FakeChar(2, "high", combat_start_priority=100)
        planner = self._planner([current, low, high])

        decision = planner.decide_combat_start_char(current)

        self.assertEqual(decision.target, high)
        self.assertEqual(decision.reason, "combat start priority")

    def test_combat_start_skips_dead_characters(self):
        current = FakeChar(0, "current")
        dead_high = FakeChar(1, "dead_high", combat_start_priority=100)
        alive_low = FakeChar(2, "alive_low", combat_start_priority=20)
        dead_high.is_dead = True
        planner = self._planner([current, dead_high, alive_low])

        decision = planner.decide_combat_start_char(current)

        self.assertEqual(decision.target, alive_low)

    def test_action_intent_auto_builds_result(self):
        action = ActionIntent(
            name="simple_skill",
            tags={ActionTag.SKILL_ACTION},
            slot=ActionSlot.SKILL,
            execute=lambda _: True,
        )

        result = action.run(None)

        self.assertEqual(result.name, "simple_skill")
        self.assertTrue(result.success)
        self.assertEqual(result.tags, {ActionTag.SKILL_ACTION})
        self.assertEqual(result.slot, ActionSlot.SKILL)

    def test_anonymous_action_keeps_empty_name_but_is_not_repeated(self):
        calls = []
        char = FakeChar(
            0,
            "anonymous",
            plan_items=lambda _: [
                ActionIntent(
                    tags={ActionTag.SKILL_ACTION},
                    slot=ActionSlot.CUSTOM,
                    execute=lambda _: calls.append("ran") or True,
                    reason="anonymous action",
                )
            ],
        )
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(result.name, "")
        self.assertEqual(calls, ["ran"])

    def test_action_intent_none_result_is_not_success(self):
        action = ActionIntent(
            name="forgot_return",
            tags={ActionTag.SKILL_ACTION},
            execute=lambda _: None,
        )

        result = action.run(None)

        self.assertFalse(result.success)

    def test_planner_falls_back_to_field_time_when_actions_fail(self):
        char = FakeChar(
            0,
            "lacrimosa",
            field_preference=FieldPreference.MAIN_DPS,
            plan_items=lambda _: [
                ActionIntent(
                    name="lacrimosa_skill",
                    tags={ActionTag.SKILL_ACTION},
                    execute=lambda _: False,
                    reason="skill declared",
                    priority_ready=lambda _: True,
                )
            ],
            max_field_time=1.25,
        )
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(result.name, "planner_field_time")
        self.assertEqual(result.tags, {ActionTag.FIELD_TIME, ActionTag.DAMAGE})
        self.assertEqual(char.waited, 1.25)

    def test_field_time_remaining_is_calculated_when_fallback_executes(self):
        char = FakeChar(
            0,
            "main",
            field_preference=FieldPreference.MAIN_DPS,
            plan_items=lambda _: [
                ActionIntent(
                    name="main_skill",
                    tags={ActionTag.SKILL_ACTION},
                    execute=lambda _: False,
                    reason="skill declared",
                    priority_ready=lambda _: True,
                )
            ],
            max_field_time=10,
            elapsed=5,
        )
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(result.name, "planner_field_time")
        self.assertTrue(result.success)
        self.assertEqual(char.waited, 5)

    def test_default_entry_runs_ultimate_and_skill(self):
        calls = []
        char = FakeChar(
            0,
            "combo",
            plan_items=lambda _: [
                self._action(
                    "combo_ultimate",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                    calls,
                ),
                self._action(
                    "combo_skill",
                    {ActionTag.SKILL_ACTION},
                    ActionSlot.SKILL,
                    calls,
                ),
            ],
        )
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["combo_ultimate", "combo_skill"])
        self.assertEqual(result.name, "combo_skill")

    def test_reservation_does_not_stop_entry_flow(self):
        calls = []
        source = FakeChar(0, "hotori")
        reserved = FakeChar(1, "zero")
        char = FakeChar(
            2,
            "nanally",
            plan_items=lambda _: [
                self._action("nanally_skill", {ActionTag.SKILL_ACTION}, ActionSlot.SKILL, calls),
                self._action(
                    "nanally_ultimate",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                    calls,
                ),
            ],
        )
        planner = self._planner([source, reserved, char])
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [ActionReservation.for_action(reserved, ActionSlot.SKILL)],
                reason="reserve teammate skill",
                until=NEVER_EXPIRES,
            ),
        )

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["nanally_skill", "nanally_ultimate"])
        self.assertEqual(result.name, "nanally_ultimate")

    def test_reservation_does_not_stop_failed_entry_flow(self):
        calls = []
        source = FakeChar(0, "hotori")
        reserved = FakeChar(1, "zero")
        char = FakeChar(
            2,
            "jiuyuan",
            plan_items=lambda _: [
                ActionIntent(
                    name="jiuyuan_ultimate",
                    tags={ActionTag.ULTIMATE_ACTION},
                    slot=ActionSlot.ULTIMATE,
                    execute=lambda _: calls.append("ultimate") or False,
                ),
                self._action(
                    "jiuyuan_skill",
                    {ActionTag.SKILL_ACTION},
                    ActionSlot.SKILL,
                    calls,
                ),
                self._action(
                    "jiuyuan_bullets",
                    {ActionTag.DEFAULT_ACTION},
                    None,
                    calls,
                ),
            ],
        )
        planner = self._planner([source, reserved, char])
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [ActionReservation.for_action(reserved, ActionSlot.SKILL)],
                reason="reserve teammate skill",
                until=NEVER_EXPIRES,
            ),
        )

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["ultimate", "jiuyuan_skill", "jiuyuan_bullets"])
        self.assertEqual(result.name, "jiuyuan_bullets")

    def test_switch_request_does_not_stop_failed_entry_flow(self):
        calls = []
        source = FakeChar(0, "hotori")
        target = FakeChar(1, "zero")
        current = FakeChar(
            2,
            "jiuyuan",
            plan_items=lambda _: [
                ActionIntent(
                    name="jiuyuan_ultimate",
                    tags={ActionTag.ULTIMATE_ACTION},
                    slot=ActionSlot.ULTIMATE,
                    execute=lambda _: calls.append("ultimate") or False,
                ),
                self._action(
                    "jiuyuan_skill",
                    {ActionTag.SKILL_ACTION},
                    ActionSlot.SKILL,
                    calls,
                ),
            ],
        )
        planner = self._planner([source, target, current])
        self._publish(
            planner,
            source,
            lambda context: context.request_switch(target, reason="switch later"),
        )

        result = planner.perform_current_char(current)

        self.assertEqual(calls, ["ultimate", "jiuyuan_skill"])
        self.assertEqual(result.name, "jiuyuan_skill")

    def test_tag_request_stops_failed_entry_flow(self):
        calls = []
        source = FakeChar(0, "source")
        current = FakeChar(
            1,
            "jiuyuan",
            plan_items=lambda _: [
                ActionIntent(
                    name="jiuyuan_ultimate",
                    tags={ActionTag.ULTIMATE_ACTION},
                    slot=ActionSlot.ULTIMATE,
                    execute=lambda _: calls.append("ultimate") or False,
                ),
                self._action(
                    "jiuyuan_skill",
                    {ActionTag.SKILL_ACTION},
                    ActionSlot.SKILL,
                    calls,
                ),
            ],
        )
        planner = self._planner([source, current])
        self._publish(
            planner,
            source,
            lambda context: context.request_tags(
                {ActionTag.SUPPORT},
                reason="needs support",
            ),
        )

        result = planner.perform_current_char(current)

        self.assertEqual(calls, ["ultimate"])
        self.assertEqual(result.name, "jiuyuan_ultimate")

    def test_normal_entry_executes_declared_order_not_highest_score_first(self):
        calls = []
        char = FakeChar(
            0,
            "nanally",
            plan_items=lambda _: [
                self._action(
                    "nanally_skill",
                    {ActionTag.SKILL_ACTION},
                    ActionSlot.SKILL,
                    calls,
                ),
                self._action(
                    "nanally_ultimate",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                    calls,
                ),
            ],
        )
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["nanally_skill", "nanally_ultimate"])
        self.assertEqual(result.name, "nanally_ultimate")

    def test_normal_switch_does_not_force_best_scoring_action_as_expected_entry(self):
        current = FakeChar(0, "current", field_preference=FieldPreference.SUPPORT)
        nanally = FakeChar(
            1,
            "nanally",
            plan_items=lambda _: [
                self._action("nanally_skill", {ActionTag.SKILL_ACTION}, ActionSlot.SKILL),
                self._action(
                    "nanally_ultimate",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                ),
            ],
        )
        planner = self._planner([current, nanally])

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, nanally)
        self.assertIn("nanally_ultimate", decision.reason)
        self.assertIsNone(decision.expected_entry)

    def test_normal_switch_skips_dead_characters(self):
        current = self._support(0, "current")
        dead_dps = self._main_dps(1, "dead_dps")
        alive_support = self._support(2, "alive_support")
        dead_dps.is_dead = True
        planner = self._planner([current, dead_dps, alive_support])

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, alive_support)

    def test_switch_decision_includes_debug_score_explanation(self):
        current = self._support(0, "current")
        target = self._char(1, "target", tags={ActionTag.SKILL_ACTION})
        planner = self._planner([current, target])

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, target)
        self.assertEqual(decision.priority, 115)
        self.assertTrue(decision.score_breakdown)

    def test_request_bonus_raises_switch_priority(self):
        source = self._setup_char(0, "source")
        support = self._char(1, "support", tags={ActionTag.SUPPORT})
        baseline = self._planner([source, support]).decide_switch(source)
        planner = self._planner([source, support])
        self._publish(
            planner,
            source,
            lambda context: context.request_tags(
                {ActionTag.SUPPORT},
                reason="needs support",
            ),
        )

        decision = planner.decide_switch(source)

        self.assertEqual(decision.target, support)
        self.assertGreater(decision.priority, baseline.priority)
        self.assertIn("fulfill request", decision.reason)

    def test_active_request_result_replays_into_entry_flow(self):
        calls = []
        source = FakeChar(0, "source")
        support = self._action(
            "support_action",
            {ActionTag.SUPPORT},
            ActionSlot.SKILL,
            calls,
        )
        followup = self._action(
            "support_followup",
            {ActionTag.DEFAULT_ACTION},
            None,
            calls,
        )

        def entry():
            support_result = yield support
            if support_result:
                yield followup

        target = FakeChar(
            1,
            "support",
            plan_items=lambda _: CombatPlan([support, followup], entry=entry),
        )
        planner = self._planner([source, target])
        self._publish(
            planner,
            source,
            lambda context: context.request_tags(
                {ActionTag.SUPPORT},
                reason="needs support",
            ),
        )

        result = planner.perform_current_char(target)

        self.assertEqual(calls, ["support_action", "support_followup"])
        self.assertEqual(result.name, "support_followup")

    def test_active_request_still_blocks_entry_flow_when_unfulfilled(self):
        calls = []
        source = FakeChar(0, "source")
        support = self._action(
            "support_action",
            {ActionTag.SUPPORT},
            ActionSlot.SKILL,
            calls,
        )
        followup = self._action(
            "support_followup",
            {ActionTag.DEFAULT_ACTION},
            None,
            calls,
        )

        def entry():
            support_result = yield support
            if support_result:
                yield followup

        target = FakeChar(
            1,
            "support",
            plan_items=lambda _: CombatPlan([support, followup], entry=entry),
        )
        planner = self._planner([source, target])
        self._publish(
            planner,
            source,
            lambda context: context.request_tags(
                {ActionTag.SUPPORT},
                count=2,
                reason="needs two supports",
            ),
        )

        result = planner.perform_current_char(target)

        self.assertEqual(calls, ["support_action"])
        self.assertEqual(result.name, "support_action")

    def test_entry_flow_can_stop_after_success(self):
        calls = []
        ultimate = self._action(
            "fadia_ultimate",
            {ActionTag.ULTIMATE_ACTION},
            ActionSlot.ULTIMATE,
            calls,
        )
        skill = self._action(
            "fadia_skill",
            {ActionTag.SKILL_ACTION},
            ActionSlot.SKILL,
            calls,
        )

        def entry():
            ultimate_result = yield ultimate
            if not ultimate_result:
                yield skill

        char = FakeChar(0, "fadia", plan_items=lambda _: CombatPlan([ultimate, skill], entry=entry))
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["fadia_ultimate"])
        self.assertEqual(result.name, "fadia_ultimate")

    def test_entry_flow_publishes_route_before_finishing(self):
        calls = []
        ultimate = self._action(
            "source_ultimate",
            {ActionTag.ULTIMATE_ACTION},
            ActionSlot.ULTIMATE,
            calls,
        )
        target = FakeChar(1, "target")

        def plan(context):
            def entry():
                ultimate_result = yield ultimate
                if ultimate_result:
                    context.request_route(
                        [FollowupStep.for_switch(target, reason="switch to target")],
                        reason="source finished ultimate",
                    )

            return CombatPlan([ultimate], entry=entry)

        source = FakeChar(0, "source", plan_items=plan)
        planner = self._planner([source, target])

        planner.perform_current_char(source)
        decision = planner.decide_switch(source)

        self.assertEqual(calls, ["source_ultimate"])
        self.assertIs(decision.target, target)
        self.assertIn("strict route switch", decision.reason)

    def test_entry_flow_can_continue_after_failure(self):
        calls = []
        ultimate = self._action(
            "fadia_ultimate",
            {ActionTag.ULTIMATE_ACTION},
            ActionSlot.ULTIMATE,
            calls,
            success=False,
        )
        skill = self._action(
            "fadia_skill",
            {ActionTag.SKILL_ACTION},
            ActionSlot.SKILL,
            calls,
        )

        def entry():
            ultimate_result = yield ultimate
            if not ultimate_result:
                yield skill

        char = FakeChar(0, "fadia", plan_items=lambda _: CombatPlan([ultimate, skill], entry=entry))
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["fadia_ultimate", "fadia_skill"])
        self.assertEqual(result.name, "fadia_skill")

    def test_expected_entry_result_replays_into_entry_flow(self):
        calls = []
        ultimate = self._action(
            "fadia_ultimate",
            {ActionTag.ULTIMATE_ACTION},
            ActionSlot.ULTIMATE,
            calls,
        )
        skill = self._action(
            "fadia_skill",
            {ActionTag.SKILL_ACTION},
            ActionSlot.SKILL,
            calls,
        )

        def entry():
            ultimate_result = yield ultimate
            if not ultimate_result:
                yield skill

        char = FakeChar(0, "fadia", plan_items=lambda _: CombatPlan([ultimate, skill], entry=entry))
        planner = self._planner([char])
        decision = SwitchDecision(
            target=char,
            reason="test expected entry",
            priority=1,
            expected_entry=ExpectedEntry(slot=ActionSlot.ULTIMATE),
        )
        planner.prepare_switch(decision)
        planner.complete_switch(decision)

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["fadia_ultimate"])
        self.assertEqual(result.name, "fadia_ultimate")

    def test_entry_flow_supports_python_boolean_logic(self):
        scenarios = [
            (
                "and",
                True,
                True,
                lambda first_result, second_result: first_result and second_result,
            ),
            (
                "or",
                False,
                True,
                lambda first_result, second_result: first_result or second_result,
            ),
            (
                "and_not",
                True,
                False,
                lambda first_result, second_result: first_result and not second_result,
            ),
        ]

        for name, first_success, second_success, should_continue in scenarios:
            with self.subTest(name=name):
                calls = []
                first = self._action(
                    f"{name}_first",
                    {ActionTag.DEFAULT_ACTION},
                    None,
                    calls,
                    success=first_success,
                )
                second = self._action(
                    f"{name}_second",
                    {ActionTag.DEFAULT_ACTION},
                    None,
                    calls,
                    success=second_success,
                )
                followup = self._action(
                    f"{name}_followup",
                    {ActionTag.DEFAULT_ACTION},
                    None,
                    calls,
                )

                def entry():
                    first_result = yield first
                    second_result = yield second
                    if should_continue(first_result, second_result):
                        yield followup

                char = FakeChar(
                    0,
                    f"logic_{name}",
                    plan_items=lambda _: CombatPlan([first, second, followup], entry=entry),
                )
                planner = self._planner([char])

                result = planner.perform_current_char(char)

                self.assertEqual(
                    calls,
                    [f"{name}_first", f"{name}_second", f"{name}_followup"],
                )
                self.assertEqual(result.name, f"{name}_followup")

    def test_action_result_tags_do_not_control_entry_flow(self):
        calls = []
        char = FakeChar(
            0,
            "entry_flow",
            plan_items=lambda _: [
                ActionIntent(
                    name="first",
                    tags={ActionTag.SKILL_ACTION},
                    slot=ActionSlot.SKILL,
                    execute=lambda _: (
                        calls.append("first")
                        or ActionResult(
                            name="first",
                            success=True,
                            tags={ActionTag.DEFAULT_ACTION},
                            slot=ActionSlot.SKILL,
                        )
                    ),
                ),
                self._action(
                    "second",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                    calls,
                ),
            ],
        )
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(result.name, "second")

    def test_basechar_helpers_are_public_planner_api(self):
        calls = []
        task = FakeTask()
        char = PublicApiChar(
            task,
            0,
            "api_char",
            lambda source, _: source.plan(
                source.click_skill_action(
                    reason="skill first",
                    can_execute=lambda context: calls.append("skill_allowed") or True,
                ),
                source.click_ultimate_action(reason="ultimate second"),
            ),
        )
        task.chars = [char]
        planner = CombatPlanner(task)
        planner.reset([char])

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["skill_allowed"])
        self.assertEqual(char.skill_clicked, 1)
        self.assertEqual(char.ultimate_clicked, 1)
        self.assertEqual(result.name, "api_char_ultimate")

    def test_plan_add_keeps_objects_and_plans_independent(self):
        plan = CombatPlan()
        other = CombatPlan()
        action = self._action("skill", {ActionTag.SKILL_ACTION}, ActionSlot.SKILL, [])
        claim = FieldClaim.strict(reason="return now")

        self.assertIs(plan.add(action), action)
        self.assertIs(plan.add(claim), claim)
        self.assertIsNone(plan.add(None))

        self.assertEqual(plan.actions, [action])
        self.assertEqual(plan.claims, [claim])
        self.assertEqual(other.actions, [])
        self.assertEqual(other.claims, [])
        with self.assertRaises(TypeError):
            plan.add("skill")

    def test_plan_add_preserves_seeded_and_added_default_action_order(self):
        calls = []
        first = self._action("first", {ActionTag.SKILL_ACTION}, ActionSlot.SKILL, calls)
        second = self._action("second", {ActionTag.ULTIMATE_ACTION}, ActionSlot.ULTIMATE, calls)
        plan = CombatPlan(iter([first]), claims=())
        plan.add(FieldClaim.normal(reason="ordinary entry"))
        plan.add(second)
        char = FakeChar(0, "builder", plan_items=lambda _: plan)
        planner = self._planner([char])

        self.assertEqual(calls, [])
        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(result.name, "second")

    def test_plan_add_claim_selects_char_and_entry_receives_action_results(self):
        task = FakeTask()
        current = FakeChar(0, "current")
        results = []

        def build_plan(source, context):
            plan = source.plan()
            skill = plan.add(source.click_skill_action())
            ultimate = plan.add(source.click_ultimate_action())
            skill.priority_ready = ultimate.priority_ready = lambda _: False
            plan.add(FieldClaim.strict(reason="entry needed without ready actions"))

            def entry():
                result = yield skill
                results.append(bool(result))
                if result:
                    yield ultimate

            plan.entry = entry
            return plan

        char = PublicApiChar(task, 1, "builder", build_plan)
        task.chars = [current, char]
        planner = CombatPlanner(task)
        planner.reset(task.chars)

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, char)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (0, 0))
        planner.prepare_switch(decision)
        planner.complete_switch(decision, previous_char=current)
        result = planner.perform_current_char(char)

        self.assertEqual(results, [True])
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 1))
        self.assertEqual(result.name, "builder_ultimate")

    def test_basechar_arc_helper_is_zero_priority_arc_slot(self):
        task = FakeTask()
        char = PublicApiChar(
            task,
            0,
            "api_char",
            lambda source, _: source.plan(source.click_arc_action()),
        )
        context = CombatPlanner(task).context_for(char)

        action = char.combat_plan(context).actions[0]
        result = action.run(context)

        self.assertEqual(action.slot, ActionSlot.ARC)
        self.assertEqual(action.tags, {ActionTag.ARC_ACTION})
        self.assertFalse(action.is_priority_ready(context))
        self.assertTrue(result.success)
        self.assertEqual(result.slot, ActionSlot.ARC)
        self.assertEqual(result.tags, {ActionTag.ARC_ACTION})
        self.assertEqual(char.arc_clicked, 1)

    def test_basechar_registration_keeps_support_role_tags(self):
        char = Support(FakeTask(), 0)
        plan = char.plan()

        skill = char.click_skill_action(plan, add_tags=ActionTag.HIGH_PRIORITY)
        ultimate = char.click_ultimate_action(plan)

        self.assertEqual(plan.actions, [skill, ultimate])
        self.assertEqual(skill.slot, ActionSlot.SKILL)
        self.assertEqual(
            skill.tags, {ActionTag.SKILL_ACTION, ActionTag.SUPPORT, ActionTag.HIGH_PRIORITY}
        )
        self.assertEqual(ultimate.slot, ActionSlot.ULTIMATE)
        self.assertEqual(ultimate.tags, {ActionTag.ULTIMATE_ACTION, ActionTag.SUPPORT})
        self.assertIsInstance(skill, ActionIntent)
        self.assertIsInstance(ultimate, ActionIntent)
        with self.assertRaises(TypeError):
            char.click_skill_action(plan, down_tme=0.3)
        self.assertEqual(plan.actions, [skill, ultimate])

    def test_entry_parameters_preserve_registered_defaults_and_explicit_repeats(self):
        task = FakeTask()
        char = PublicApiChar(task, 0, "params", lambda *_: plan)
        char.click_skill = Mock(return_value=True)
        char.click_ultimate = Mock(return_value=True)
        plan = char.plan()
        skill = char.click_skill_action()
        skill = plan.add(skill(down_time=0.4, post_sleep=0.2))
        ultimate = char.click_ultimate_action(plan)

        def entry():
            if (yield skill(down_time=0.3, post_sleep=0, send_click=False)):
                yield ultimate(send_click=False, wait_if_no_cd=0)
            yield skill.repeat_for_entry()
            yield skill(down_time=0.1, time_out=0.5).repeat_for_entry()

        plan.entry = entry
        task.chars = [char]
        planner = CombatPlanner(task)
        planner.reset(task.chars)
        char.click_skill.assert_not_called()
        char.click_ultimate.assert_not_called()

        result = planner.perform_current_char(char)

        self.assertEqual(
            [call.kwargs for call in char.click_skill.call_args_list],
            [
                dict(down_time=0.3, post_sleep=0, send_click=False),
                dict(down_time=0.4, post_sleep=0.2),
                dict(down_time=0.1, time_out=0.5),
            ],
        )
        char.click_ultimate.assert_called_once_with(send_click=False, wait_if_no_cd=0)
        self.assertEqual(plan.actions, [skill, ultimate])
        self.assertEqual(result.name, "params_skill")

    def test_bound_skill_keeps_default_duration_and_accepts_positional_overrides(self):
        task = FakeTask()
        char = PublicApiChar(task, 0, "params", lambda *_: None)
        calls = []

        def click_skill(down_time=0.05, *, post_sleep=0):
            calls.append((down_time, post_sleep))
            return True

        char.click_skill = click_skill
        skill = char.click_skill_action()
        context = CombatPlanner(task).context_for(char)

        skill.run(context)
        skill(post_sleep=0.2).run(context)
        skill(0.3, post_sleep=0.4).run(context)

        self.assertEqual(calls, [(0.01, 0), (0.01, 0.2), (0.3, 0.4)])

    def test_different_parameters_do_not_bypass_entry_deduplication(self):
        task = FakeTask()
        char = PublicApiChar(task, 0, "params", lambda *_: plan)
        char.click_skill = Mock(return_value=True)
        char.click_ultimate = Mock(return_value=True)
        plan = char.plan()
        skill = char.click_skill_action(plan)
        ultimate = char.click_ultimate_action(plan)
        results = []

        def entry():
            results.append(bool((yield skill(down_time=0.1))))
            results.append(bool((yield skill(down_time=0.5))))
            results.append(bool((yield ultimate(send_click=False))))
            results.append(bool((yield ultimate(send_click=True))))

        plan.entry = entry
        task.chars = [char]
        planner = CombatPlanner(task)
        planner.reset(task.chars)

        planner.perform_current_char(char)

        char.click_skill.assert_called_once_with(down_time=0.1)
        char.click_ultimate.assert_called_once_with(send_click=False)
        self.assertEqual(results, [True, True, True, True])

    def test_entry_arguments_use_state_after_previous_action(self):
        task = FakeTask()
        current = FakeChar(0, "current")
        char = PublicApiChar(task, 1, "params", lambda *_: plan)
        char.click_skill = Mock(return_value=True)
        char.click_ultimate = Mock(return_value=True)
        state = {"down_time": 0.01, "send_click": True}
        plan = char.plan()
        skill = char.click_skill_action(plan)
        ultimate = char.click_ultimate_action(plan)

        def entry():
            state["send_click"] = False
            if (yield ultimate(send_click=state["send_click"])):
                state["down_time"] = 0.3
                yield skill(down_time=state["down_time"])

        plan.entry = entry
        task.chars = [current, char]
        planner = CombatPlanner(task)
        planner.reset(task.chars)

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, char)
        char.click_skill.assert_not_called()
        char.click_ultimate.assert_not_called()
        planner.prepare_switch(decision)
        planner.complete_switch(decision, previous_char=current)
        planner.perform_current_char(char)

        char.click_skill.assert_called_once_with(down_time=0.3)
        char.click_ultimate.assert_called_once_with(send_click=False)

    def test_strict_route_uses_registered_parameters_before_entry_arguments(self):
        task = FakeTask()
        source = FakeChar(0, "source")
        char = PublicApiChar(task, 1, "params", lambda *_: plan)
        char.click_skill = Mock(return_value=True)
        char.click_ultimate = Mock(return_value=True)
        state = {"down_time": 0.4}
        plan = char.plan()
        skill = char.click_skill_action()
        registered_skill = plan.add(skill(down_time=state["down_time"]))
        ultimate = char.click_ultimate_action(plan)
        results = []

        def entry():
            results.append(bool((yield skill(down_time=0.9))))
            yield ultimate(send_click=False)

        plan.entry = entry
        task.chars = [source, char]
        planner = CombatPlanner(task)
        planner.reset(task.chars)
        self._publish(
            planner,
            source,
            lambda context: context.request_route(
                [FollowupStep.for_action(char, ActionSlot.SKILL)], reason="need skill"
            ),
        )
        decision = planner.decide_switch(source)
        char.click_skill.assert_not_called()
        state["down_time"] = 0.8
        planner.prepare_switch(decision)
        planner.complete_switch(decision, previous_char=source)

        planner.perform_current_char(char)

        char.click_skill.assert_called_once_with(down_time=0.4)
        char.click_ultimate.assert_called_once_with(send_click=False)
        self.assertEqual(results, [True])
        self.assertEqual(plan.actions, [registered_skill, ultimate])
        self.assertFalse(planner.context_for(char).has_active_request())

    def test_parameter_binding_keeps_reservations_permissions_and_cooldowns(self):
        for kind, slot, params in (
            ("skill", ActionSlot.SKILL, {"down_time": 0.2}),
            ("ultimate", ActionSlot.ULTIMATE, {"send_click": False}),
        ):
            for restriction in ("reservation", "can_execute", "cooldown"):
                with self.subTest(kind=kind, restriction=restriction):
                    task = FakeTask()
                    source = FakeChar(0, "source")
                    char = PublicApiChar(task, 1, "params", lambda *_: plan)
                    char.click_skill = Mock(return_value=True)
                    char.click_ultimate = Mock(return_value=True)
                    setattr(char, f"{kind}_available", Mock(return_value=restriction != "cooldown"))
                    plan = char.plan()
                    action = getattr(char, f"click_{kind}_action")(
                        plan, can_execute=lambda _: restriction != "can_execute"
                    )

                    def entry():
                        yield action(**params)

                    plan.entry = entry
                    task.chars = [source, char]
                    planner = CombatPlanner(task)
                    planner.reset(task.chars)
                    if restriction == "reservation":
                        self._publish(
                            planner,
                            source,
                            lambda context: context.reserve_actions(
                                [ActionReservation.for_action(char, slot)],
                                reason="hold action",
                                until=NEVER_EXPIRES,
                            ),
                        )

                    result = planner.perform_current_char(char)

                    self.assertFalse(result.success)
                    char.click_skill.assert_not_called()
                    char.click_ultimate.assert_not_called()

    def test_qe_helpers_reject_execute_replacement(self):
        for char_type in (BaseChar, Support):
            char = char_type(FakeTask(), 0)
            plan = char.plan()
            execute = Mock(return_value=True)
            for kind in ("skill", "ultimate"):
                with self.subTest(char=char_type.__name__, kind=kind), self.assertRaises(TypeError):
                    getattr(char, f"click_{kind}_action")(plan, execute=execute)
            self.assertEqual(plan.actions, [])
            execute.assert_not_called()

    def test_akane_binds_skill_parameters_and_passes_action_to_combo(self):
        for boss_fight, down_time in ((False, 0.3), (True, 0.01)):
            with self.subTest(boss_fight=boss_fight):
                task = FakeTask()
                task._boss_fight = boss_fight
                task.time_elapsed_accounting_for_freeze = Mock(return_value=0)
                char = Akane(task, 0)
                char.skill_available = Mock(return_value=True)
                char.ultimate_available = Mock(return_value=True)
                char.click_skill = Mock(return_value=True)
                char.click_ultimate = Mock(return_value=True)
                char.perform_ult_combo = Mock(return_value=iter(()))
                task.chars = [char]
                planner = CombatPlanner(task)
                planner.reset(task.chars)

                planner.perform_current_char(char)

                char.click_skill.assert_called_once_with(down_time=down_time, post_sleep=0.2)
                char.click_ultimate.assert_called_once_with()
                char.perform_ult_combo.assert_called_once()
                action = char.perform_ult_combo.call_args.args[0]
                self.assertIsInstance(action, ActionIntent)
                self.assertEqual(action.slot, ActionSlot.SKILL)

    def test_sakiri_keeps_registered_skill_duration(self):
        task = FakeTask()
        char = Sakiri(task, 0)
        char.skill_available = Mock(return_value=True)
        char.ultimate_available = Mock(return_value=False)
        char.click_skill = Mock(return_value=True)
        task.chars = [char]
        planner = CombatPlanner(task)
        planner.reset(task.chars)

        result = planner.perform_current_char(char)

        char.click_skill.assert_called_once_with(down_time=0.25)
        self.assertTrue(result.success)
        self.assertEqual(result.slot, ActionSlot.SKILL)

    def test_action_calls_reject_metadata_overrides(self):
        char = BaseChar(FakeTask(), 0)
        plan = char.plan()
        for action in (char.click_skill_action(plan), char.click_ultimate_action(plan)):
            for params in ({"can_execute": lambda _: True}, {"slot": ActionSlot.CUSTOM}):
                with self.subTest(action=action.slot, params=params), self.assertRaises(TypeError):
                    action(**params)

    def test_custom_action_parameters_preserve_defaults_results_and_repeats(self):
        task = FakeTask()
        char = PublicApiChar(task, 0, "custom", lambda *_: plan)
        plan = char.plan()
        calls = []
        results = []

        def perform(context, *, duration=0.5, send_click=True):
            calls.append((context.current_char, duration, send_click))
            return duration > 0

        burst = char.planner_action(
            plan=plan,
            tags={ActionTag.DAMAGE},
            slot=ActionSlot.CUSTOM,
            execute=perform,
        )

        def entry():
            results.append(bool((yield burst(duration=0))))
            # Changing parameters alone must replay the failed attempt's result.
            results.append(bool((yield burst(duration=0.8))))
            results.append(bool((yield burst(duration=0.8, send_click=False).repeat_for_entry())))
            yield burst.repeat_for_entry()

        plan.entry = entry
        task.chars = [char]
        planner = CombatPlanner(task)
        planner.reset(task.chars)

        self.assertIsInstance(burst, ActionIntent)
        self.assertEqual(calls, [])
        result = planner.perform_current_char(char)

        self.assertEqual(calls, [(char, 0, True), (char, 0.8, False), (char, 0.5, True)])
        self.assertEqual(results, [False, False, True])
        self.assertTrue(result.success)
        self.assertEqual(result.slot, ActionSlot.CUSTOM)
        self.assertEqual(result.tags, {ActionTag.DAMAGE})
        self.assertEqual(plan.actions, [burst])

    def test_custom_action_parameters_keep_permissions_and_reservations(self):
        for restriction in ("reservation", "can_execute"):
            with self.subTest(restriction=restriction):
                task = FakeTask()
                source = FakeChar(0, "source")
                char = PublicApiChar(task, 1, "custom", lambda *_: plan)
                plan = char.plan()
                calls = []

                def perform(context, *, duration=0.5):
                    calls.append(duration)
                    return True

                burst = char.planner_action(
                    plan=plan,
                    tags=ActionTag.DAMAGE,
                    slot=ActionSlot.CUSTOM,
                    can_execute=lambda _: restriction != "can_execute",
                    execute=perform,
                )

                def entry():
                    yield burst(duration=0.8)

                plan.entry = entry
                task.chars = [source, char]
                planner = CombatPlanner(task)
                planner.reset(task.chars)
                if restriction == "reservation":
                    self._publish(
                        planner,
                        source,
                        lambda context: context.reserve_actions(
                            [ActionReservation.for_action(char, ActionSlot.CUSTOM)],
                            reason="hold custom action",
                            until=NEVER_EXPIRES,
                        ),
                    )

                result = planner.perform_current_char(char)

                self.assertFalse(result.success)
                self.assertEqual(calls, [])

    def test_custom_action_route_uses_current_defaults_and_replays_result(self):
        task = FakeTask()
        source = FakeChar(0, "source")
        char = PublicApiChar(task, 1, "custom", lambda *_: plan)
        plan = char.plan()
        state = {"duration": 0.5}
        calls = []
        results = []

        def perform(context, *, duration=None):
            calls.append(
                (context.current_char, state["duration"] if duration is None else duration)
            )
            return True

        burst = char.planner_action(
            plan=plan,
            tags=ActionTag.DAMAGE,
            slot=ActionSlot.CUSTOM,
            execute=perform,
        )

        def entry():
            results.append(bool((yield burst(duration=0.9))))

        plan.entry = entry
        task.chars = [source, char]
        planner = CombatPlanner(task)
        planner.reset(task.chars)
        self._publish(
            planner,
            source,
            lambda context: context.request_route(
                [FollowupStep.for_action(char, ActionSlot.CUSTOM)], reason="need custom action"
            ),
        )
        decision = planner.decide_switch(source)
        self.assertEqual(calls, [])
        state["duration"] = 0.8
        planner.prepare_switch(decision)
        planner.complete_switch(decision, previous_char=source)

        planner.perform_current_char(char)

        self.assertEqual(calls, [(char, 0.8)])
        self.assertEqual(results, [True])
        self.assertFalse(planner.context_for(char).has_active_request())

    def test_custom_action_calls_validate_executor_signature_without_executing(self):
        char = PublicApiChar(FakeTask(), 0, "custom", lambda *_: None)
        calls = []

        def perform(context, duration=0.5, /, *, send_click=True):
            calls.append((duration, send_click))
            return True

        plan = char.plan()
        burst = char.planner_action(plan=plan, tags=ActionTag.DAMAGE, execute=perform)
        for params in ({"duration": 0.8}, {"send_clik": False}, {"can_execute": lambda _: True}):
            with self.subTest(params=params), self.assertRaises(TypeError):
                burst(**params)

        context = CombatPlanner(char.task).context_for(char)
        result = burst(0.8, send_click=False).run(context)

        self.assertTrue(result.success)
        self.assertEqual(calls, [(0.8, False)])

    def test_entry_flow_runs_result_followups(self):
        calls = []
        task = FakeTask()

        def plan(source, context):
            skill = source.click_skill_action(reason="skill first")
            ultimate = source.click_ultimate_action(reason="ultimate second")

            def entry():
                skill_result = yield skill
                calls.append(("skill_after", context.current_char.char_name, bool(skill_result)))
                ultimate_result = yield ultimate
                calls.append(
                    ("ultimate_after", context.current_char.char_name, bool(ultimate_result))
                )

            return CombatPlan([skill, ultimate], entry=entry)

        char = PublicApiChar(task, 0, "api_char", plan)
        task.chars = [char]
        planner = CombatPlanner(task)
        planner.reset([char])

        result = planner.perform_current_char(char)

        self.assertEqual(
            calls,
            [
                ("skill_after", "api_char", True),
                ("ultimate_after", "api_char", True),
            ],
        )
        self.assertEqual(char.skill_clicked, 1)
        self.assertEqual(char.ultimate_clicked, 1)
        self.assertEqual(result.name, "api_char_ultimate")

    def test_entry_flow_repeat_for_entry_runs_action_again(self):
        task = FakeTask()

        def plan(source, context):
            skill = source.click_skill_action(name="skill")
            ultimate = source.click_ultimate_action(name="ultimate")

            def entry():
                yield skill
                ultimate_result = yield ultimate
                if ultimate_result:
                    yield skill.repeat_for_entry()

            return CombatPlan([skill, ultimate], entry=entry)

        char = PublicApiChar(task, 0, "api_char", plan)
        task.chars = [char]
        planner = CombatPlanner(task)
        planner.reset([char])

        result = planner.perform_current_char(char)

        self.assertEqual(char.skill_clicked, 2)
        self.assertEqual(char.ultimate_clicked, 1)
        self.assertEqual(result.name, "skill")

    def test_repeat_for_entry_retries_action_after_failure(self):
        task = FakeTask()
        attempts = []

        def plan(source, context):
            skill = ActionIntent(
                name="skill",
                tags={ActionTag.SKILL_ACTION},
                slot=ActionSlot.SKILL,
                execute=lambda _: attempts.append("skill") or len(attempts) == 2,
            )
            ultimate = source.click_ultimate_action(name="ultimate")

            def entry():
                ultimate_result = yield ultimate
                while ultimate_result:
                    skill_result = yield skill.repeat_for_entry()
                    if skill_result:
                        return

            return CombatPlan([skill, ultimate], entry=entry)

        char = PublicApiChar(task, 0, "api_char", plan)
        task.chars = [char]
        planner = CombatPlanner(task)
        planner.reset([char])

        result = planner.perform_current_char(char)

        self.assertEqual(attempts, ["skill", "skill"])
        self.assertEqual(char.ultimate_clicked, 1)
        self.assertTrue(result.success)

    def test_nanally_ultimate_loop_retries_skill_without_yielding_entry_actions(self):
        task = FakeTask()
        nanally = Nanally(task, 0, char_id="nanally")
        context = type(
            "AllowedContext",
            (),
            {"is_action_allowed": lambda _, char, action: True},
        )()
        skill = ActionIntent(name="skill", tags=set(), execute=lambda _: True)
        skill_results = iter([False, True])
        normal_attacks = []
        nanally.skill_available = lambda: True
        nanally.click_skill = lambda: next(skill_results)
        nanally.normal_attack = lambda: normal_attacks.append("normal")
        nanally.sleep = lambda _: None

        with patch("src.utils.clock._read_monotonic", side_effect=[0, 0, 0.2, 6]):
            result = nanally.perform_in_ult(context, skill)

        self.assertTrue(result)
        self.assertEqual(normal_attacks, ["normal", "normal"])

    def test_entry_flow_yielded_action_respects_slot_reservation(self):
        task = FakeTask()
        source = FakeChar(0, "source")

        def plan(source, context):
            skill = source.click_skill_action(name="skill")
            ultimate = source.click_ultimate_action(name="ultimate")

            def entry():
                ultimate_result = yield ultimate
                if ultimate_result:
                    yield skill.repeat_for_entry()

            return CombatPlan([skill, ultimate], entry=entry)

        char = PublicApiChar(task, 1, "api_char", plan)
        task.chars = [source, char]
        planner = CombatPlanner(task)
        planner.reset([source, char])
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [ActionReservation.for_action(char, ActionSlot.SKILL)],
                reason="hold ultimate skill",
                until=NEVER_EXPIRES,
            ),
        )
        result = planner.perform_current_char(char)

        self.assertEqual(char.ultimate_clicked, 1)
        self.assertEqual(char.skill_clicked, 0)
        self.assertFalse(result.success)

    def test_action_result_bool_reflects_success(self):
        task = FakeTask()
        action = ActionIntent(
            name="failing_action",
            tags={ActionTag.SKILL_ACTION},
            slot=ActionSlot.SKILL,
            execute=lambda _: False,
        )
        char = PublicApiChar(
            task,
            0,
            "api_char",
            lambda source, _: CombatPlan([action]),
        )
        task.chars = [char]
        planner = CombatPlanner(task)
        planner.reset([char])

        result = planner.perform_current_char(char)

        self.assertFalse(result)

    def test_basechar_skill_helper_respects_slot_reservation(self):
        expired = {"value": False}
        task = FakeTask()
        source = PublicApiChar(task, 0, "source", lambda source, _: source.plan())
        target = PublicApiChar(
            task,
            1,
            "target",
            lambda source, _: source.plan(source.click_skill_action()),
        )
        task.chars = [source, target]
        planner = CombatPlanner(task)
        planner.reset([source, target])
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [ActionReservation.for_action(target, ActionSlot.SKILL)],
                reason="public API slot reservation",
                until=lambda: expired["value"],
            ),
        )

        result = planner.perform_current_char(target)

        self.assertFalse(result.success)
        self.assertEqual(target.skill_clicked, 0)

        expired["value"] = True
        planner.state.prune()
        result = planner.perform_current_char(target)

        self.assertEqual(target.skill_clicked, 1)
        self.assertEqual(result.name, "target_skill")

    def test_context_is_action_allowed_checks_can_execute_and_reservation(self):
        enabled = {"value": False}
        task = FakeTask()
        source = PublicApiChar(task, 0, "source", lambda source, _: source.plan())
        target = PublicApiChar(
            task,
            1,
            "target",
            lambda source, _: source.plan(
                source.click_skill_action(can_execute=lambda _: enabled["value"])
            ),
        )
        task.chars = [source, target]
        planner = CombatPlanner(task)
        planner.reset([source, target])
        context = planner.context_for(target)
        action = target.combat_plan(context).actions[0]

        self.assertFalse(context.is_action_allowed(target, action))

        enabled["value"] = True
        self.assertTrue(context.is_action_allowed(target, action))

        self._publish(
            planner,
            source,
            lambda source_context: source_context.reserve_actions(
                [ActionReservation.for_action(target, ActionSlot.SKILL)],
                reason="hold target skill",
                until=NEVER_EXPIRES,
            ),
        )

        self.assertFalse(context.is_action_allowed(target, action))

    def test_planner_action_slot_respects_reservation(self):
        expired = {"value": False}
        calls = []
        task = FakeTask()
        source = PublicApiChar(task, 0, "source", lambda source, _: source.plan())
        target = PublicApiChar(
            task,
            1,
            "target",
            lambda source, _: source.plan(
                source.planner_action(
                    tags={ActionTag.DEFAULT_ACTION},
                    slot=ActionSlot.SKILL,
                    execute=lambda _: calls.append("custom_skill") or True,
                    reason="custom skill slot",
                )
            ),
        )
        task.chars = [source, target]
        planner = CombatPlanner(task)
        planner.reset([source, target])
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [ActionReservation.for_action(target, ActionSlot.SKILL)],
                reason="slot reservation",
                until=lambda: expired["value"],
            ),
        )

        result = planner.perform_current_char(target)

        self.assertFalse(result.success)
        self.assertEqual(calls, [])

        expired["value"] = True
        planner.state.prune()

        self.assertTrue(planner.perform_current_char(target).success)
        self.assertEqual(calls, ["custom_skill"])

    def test_perform_current_char_uses_one_plan_for_default_entry(self):
        calls = []
        char = FakeChar(
            0,
            "combo",
            plan_items=lambda _: [
                self._action(
                    "combo_ultimate",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                    calls,
                ),
                self._action(
                    "combo_skill",
                    {ActionTag.SKILL_ACTION},
                    ActionSlot.SKILL,
                    calls,
                ),
            ],
        )
        planner = self._planner([char])

        planner.perform_current_char(char)

        self.assertEqual(calls, ["combo_ultimate", "combo_skill"])
        self.assertEqual(char.plan_calls, 1)

    def test_pending_entry_action_runs_before_replanning_field_time(self):
        calls = []
        char = FakeChar(
            0,
            "jiuyuan",
            plan_items=lambda _: [
                self._action(
                    "jiuyuan_ultimate",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                    calls,
                    priority_ready=lambda _: False,
                )
            ],
            max_field_time=1.0,
        )
        planner = self._planner([char])
        decision = SwitchDecision(
            target=char,
            reason="test expected entry",
            priority=1,
            expected_entry=ExpectedEntry(action_name="jiuyuan_ultimate"),
        )
        planner.prepare_switch(decision)
        planner.complete_switch(decision)

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["jiuyuan_ultimate"])
        self.assertEqual(result.name, "jiuyuan_ultimate")

    def test_request_tags_selects_matching_teammate(self):
        source = FakeChar(0, "source", field_preference=FieldPreference.SETUP_ONLY)
        support = FakeChar(1, "support", tags={ActionTag.SUPPORT})
        dps = FakeChar(2, "dps", tags={ActionTag.DAMAGE})
        planner = self._planner([source, support, dps])
        self._publish(
            planner,
            source,
            lambda context: context.request_tags(
                {ActionTag.SUPPORT},
                reason="needs support",
            ),
        )

        decision = planner.decide_switch(source)

        self.assertEqual(decision.target, support)
        self.assertIn("needs support", decision.reason)

    def test_request_tags_can_return_to_source_after_completion(self):
        source = FakeChar(0, "source", tags={ActionTag.ULTIMATE_ACTION})
        recorder = FakeChar(1, "recorder", tags={ActionTag.SKILL_ACTION})
        planner = self._planner([source, recorder])
        self._publish(
            planner,
            source,
            lambda context: context.request_tags(
                {ActionTag.SKILL_ACTION},
                reason="record skill",
                return_to_source=True,
            ),
        )

        planner.perform_current_char(recorder)
        decision = planner.decide_switch(recorder)

        self.assertEqual(decision.target, source)
        self.assertIn("return to requester", decision.reason)

        planner.perform_current_char(source)

        self.assertEqual(planner.state.active_requests, [])

    def test_request_route_forces_declared_switch_order(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        jiuyuan = FakeChar(1, "jiuyuan", tags={ActionTag.SKILL_ACTION})
        zero = FakeChar(2, "zero", tags={ActionTag.SKILL_ACTION})
        planner = self._planner([hotori, jiuyuan, zero])
        self._publish(
            planner,
            hotori,
            lambda context: context.request_route(
                [
                    FollowupStep.for_action(jiuyuan, ActionSlot.SKILL, reason="Jiuyuan E"),
                    FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E"),
                ],
                reason="hotori record route",
            ),
        )

        decision = planner.decide_switch(hotori)

        self.assertEqual(decision.target, jiuyuan)

        planner.perform_current_char(jiuyuan)
        decision = planner.decide_switch(jiuyuan)

        self.assertEqual(decision.target, zero)

    def test_strict_route_does_not_switch_to_dead_target(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        zero.is_dead = True
        planner = self._planner([hotori, zero])
        self._publish(
            planner,
            hotori,
            lambda context: context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="dead target route",
            ),
        )

        decision = planner.decide_switch(hotori)

        self.assertEqual(decision.target, hotori)
        self.assertEqual(decision.reason, "no switch target")
        self.assertIsNone(planner.state.locked_route)

    def test_strict_route_skips_optional_dead_target(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        dead_support = FakeChar(1, "dead_support", tags={ActionTag.SKILL_ACTION})
        live_support = FakeChar(2, "live_support", tags={ActionTag.SKILL_ACTION})
        dead_support.is_dead = True
        planner = self._planner([hotori, dead_support, live_support])
        self._publish(
            planner,
            hotori,
            lambda context: context.request_route(
                [
                    FollowupStep.for_action(
                        dead_support,
                        ActionSlot.SKILL,
                        reason="dead optional E",
                        optional=True,
                    ),
                    FollowupStep.for_action(
                        live_support,
                        ActionSlot.SKILL,
                        reason="live required E",
                    ),
                ],
                reason="skip dead optional route",
            ),
        )

        decision = planner.decide_switch(hotori)

        self.assertEqual(decision.target, live_support)
        self.assertIn("live required E", decision.reason)

    def test_strict_route_runs_same_target_steps_in_one_entry(self):
        calls = []
        char = FakeChar(
            1,
            "jiuyuan",
            plan_items=lambda _: [
                self._action(
                    "jiuyuan_ultimate",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                    calls,
                ),
                self._action(
                    "jiuyuan_skill",
                    {ActionTag.SKILL_ACTION},
                    ActionSlot.SKILL,
                    calls,
                ),
            ],
        )
        planner = self._planner([char])
        self._publish(
            planner,
            char,
            lambda context: context.request_route(
                [
                    FollowupStep.for_action(char, ActionSlot.ULTIMATE, reason="Q"),
                    FollowupStep.for_action(char, ActionSlot.SKILL, reason="E"),
                ],
                reason="same target route",
            ),
        )

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["jiuyuan_ultimate", "jiuyuan_skill"])
        self.assertEqual(result.name, "jiuyuan_skill")

    def test_strict_route_final_step_continues_target_entry_flow(self):
        calls = []
        source = FakeChar(0, "xun")
        skill = self._action(
            "nanally_skill",
            {ActionTag.SKILL_ACTION},
            ActionSlot.SKILL,
            calls,
        )
        ultimate = self._action(
            "nanally_ultimate",
            {ActionTag.ULTIMATE_ACTION},
            ActionSlot.ULTIMATE,
            calls,
        )

        def entry():
            skill_result = yield skill
            if skill_result:
                yield ultimate

        nanally = FakeChar(
            1,
            "nanally",
            plan_items=lambda _: CombatPlan([skill, ultimate], entry=entry),
        )
        planner = self._planner([source, nanally])
        self._publish(
            planner,
            source,
            lambda context: context.request_route(
                [FollowupStep.for_action(nanally, ActionSlot.SKILL, reason="Nanally E")],
                reason="xun route ends on Nanally",
            ),
        )

        result = planner.perform_current_char(nanally)

        self.assertEqual(calls, ["nanally_skill", "nanally_ultimate"])
        self.assertEqual(result.name, "nanally_ultimate")

    def test_request_route_skips_optional_prepare_step_when_not_ready(self):
        calls = []
        char = FakeChar(
            1,
            "zero",
            plan_items=lambda _: [
                self._action(
                    "zero_ultimate",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                    calls,
                    priority_ready=lambda _: False,
                ),
                self._action(
                    "zero_skill",
                    {ActionTag.SKILL_ACTION},
                    ActionSlot.SKILL,
                    calls,
                ),
            ],
        )
        planner = self._planner([char])
        self._publish(
            planner,
            char,
            lambda context: context.request_route(
                [
                    FollowupStep.for_action(
                        char,
                        ActionSlot.ULTIMATE,
                        reason="Q if ready",
                        optional=True,
                    ),
                    FollowupStep.for_action(char, ActionSlot.SKILL, reason="E"),
                ],
                reason="optional prepare route",
            ),
        )

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["zero_skill"])
        self.assertEqual(result.name, "zero_skill")

    def test_route_switch_does_not_expect_unready_optional_action(self):
        source = FakeChar(0, "source")
        calls = []
        target = FakeChar(
            1,
            "target",
            plan_items=lambda _: [
                self._action(
                    "optional_ultimate",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                    calls,
                    priority_ready=lambda _: False,
                ),
                self._action("required_skill", {ActionTag.SKILL_ACTION}, ActionSlot.SKILL, calls),
            ],
        )
        planner = self._planner([source, target])
        self._publish(
            planner,
            source,
            lambda context: context.request_route(
                [
                    FollowupStep.for_action(target, ActionSlot.ULTIMATE, optional=True),
                    FollowupStep.for_action(target, ActionSlot.SKILL),
                ]
            ),
        )

        decision = planner.decide_switch(source)
        self.assertEqual(decision.target, target)
        self.assertIsNone(decision.expected_entry)
        planner.prepare_switch(decision)
        planner.complete_switch(decision, previous_char=source)
        planner.perform_current_char(target)
        self.assertEqual(calls, ["required_skill"])

    def test_cleared_expected_entry_does_not_override_entry_order(self):
        calls = []
        char = FakeChar(
            0,
            "char",
            plan_items=[
                self._action("skill", {ActionTag.SKILL_ACTION}, ActionSlot.SKILL, calls),
                self._action("ultimate", {ActionTag.ULTIMATE_ACTION}, ActionSlot.ULTIMATE, calls),
            ],
        )
        planner = self._planner([char])
        expected_decision = SwitchDecision(
            target=char,
            reason="expected entry",
            priority=1,
            expected_entry=ExpectedEntry(slot=ActionSlot.ULTIMATE),
        )
        planner.prepare_switch(expected_decision)
        planner.complete_switch(expected_decision)
        planner.prepare_switch(
            SwitchDecision(target=char, reason="clear expected entry", priority=1)
        )

        planner.perform_current_char(char)

        self.assertEqual(calls, ["skill", "ultimate"])

    def test_request_route_skips_failed_optional_prepare_step(self):
        calls = []
        char = FakeChar(
            1,
            "zero",
            plan_items=lambda _: [
                self._action(
                    "zero_ultimate",
                    {ActionTag.ULTIMATE_ACTION},
                    ActionSlot.ULTIMATE,
                    calls,
                    success=False,
                ),
                self._action(
                    "zero_skill",
                    {ActionTag.SKILL_ACTION},
                    ActionSlot.SKILL,
                    calls,
                ),
            ],
        )
        planner = self._planner([char])
        self._publish(
            planner,
            char,
            lambda context: context.request_route(
                [
                    FollowupStep.for_action(
                        char,
                        ActionSlot.ULTIMATE,
                        reason="Q if ready",
                        optional=True,
                    ),
                    FollowupStep.for_action(char, ActionSlot.SKILL, reason="E"),
                ],
                reason="optional prepare route",
            ),
        )

        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["zero_ultimate", "zero_skill"])
        self.assertEqual(result.name, "zero_skill")
        self.assertIsNone(planner.state.locked_route)

    def test_request_route_no_longer_accepts_holds(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        nanally = FakeChar(2, "nanally")
        planner = self._planner([hotori, zero, nanally])

        with self.assertRaises(TypeError):
            self._publish(
                planner,
                hotori,
                lambda context: context.request_route(
                    [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                    reason="hotori record route",
                    holds=[ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                ),
            )

    def test_combat_policies_publish_long_lived_reservation_on_reset(self):
        target = FakeChar(1, "target")
        source = FakeChar(
            0,
            "source",
            policies=lambda context: context.reserve_actions(
                [ActionReservation.for_action(target, ActionSlot.SKILL)],
                reason="policy reservation",
                until=NEVER_EXPIRES,
            ),
        )

        planner = self._planner([source, target])
        context = planner.context_for(source)

        self.assertFalse(context.is_slot_available(target, ActionSlot.SKILL))

    def test_combat_plan_published_requests_are_ignored(self):
        target = FakeChar(1, "target")
        source = FakeChar(
            0,
            "source",
            plan_items=lambda context: (
                context.reserve_actions(
                    [ActionReservation.for_action(target, ActionSlot.SKILL)],
                    reason="invalid plan side effect",
                    until=NEVER_EXPIRES,
                )
                or []
            ),
        )

        planner = self._planner([source, target])
        planner.decide_switch(source)
        context = planner.context_for(source)

        self.assertTrue(context.is_slot_available(target, ActionSlot.SKILL))

    def test_reserve_actions_blocks_until_condition_releases(self):
        source = FakeChar(0, "source")
        nanally = FakeChar(1, "nanally")
        expired = {"value": False}
        planner = self._planner([source, nanally])
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                reason="hold Nanally E",
                until=lambda: expired["value"],
            ),
        )
        context = planner.context_for(source)

        self.assertFalse(context.is_slot_available(nanally, ActionSlot.SKILL))

        expired["value"] = True
        planner.state.prune()

        self.assertTrue(context.is_slot_available(nanally, ActionSlot.SKILL))

    def test_reserve_actions_requires_explicit_lifetime(self):
        source = FakeChar(0, "source")
        nanally = FakeChar(1, "nanally")
        planner = self._planner([source, nanally])

        with self.assertRaises(TypeError):
            self._publish(
                planner,
                source,
                lambda context: context.reserve_actions(
                    [ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                    reason="missing lifetime",
                ),
            )

    def test_permanent_reservation_rejects_on_finish_callback(self):
        source = FakeChar(0, "source")
        nanally = FakeChar(1, "nanally")
        planner = self._planner([source, nanally])

        with self.assertRaises(ValueError):
            self._publish(
                planner,
                source,
                lambda context: context.reserve_actions(
                    [ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                    reason="bad permanent reservation",
                    until=NEVER_EXPIRES,
                    on_finish=lambda: None,
                ),
            )

    def test_reserve_actions_supports_never_expires(self):
        source = FakeChar(0, "source")
        nanally = FakeChar(1, "nanally")
        planner = self._planner([source, nanally])
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                reason="permanent hold",
                until=NEVER_EXPIRES,
            ),
        )

        planner.state.prune()
        context = planner.context_for(source)

        self.assertFalse(context.is_slot_available(nanally, ActionSlot.SKILL))

    def test_reset_closes_request_handles(self):
        source = FakeChar(0, "source")
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        nanally = FakeChar(2, "nanally")
        handles = {}
        planner = self._planner([source, zero, nanally])

        def publish(context):
            handles["route"] = context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="reset route",
            )
            handles["reservation"] = context.reserve_actions(
                [ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                reason="reset reservation",
                until=Planner.NEVER_EXPIRES,
            )

        self._publish(planner, source, publish)

        self.assertFalse(handles["route"].is_closed)
        self.assertFalse(handles["reservation"].is_closed)

        planner.state.reset([source, zero, nanally])

        self.assertTrue(handles["route"].is_closed)
        self.assertTrue(handles["reservation"].is_closed)

    def test_reservation_can_release_when_route_is_fulfilled(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        nanally = FakeChar(2, "nanally")
        planner = self._planner([hotori, zero, nanally])

        def publish(context):
            route = context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="hotori record route",
            )
            context.reserve_actions(
                [ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                reason="release after route fulfilled",
                until=route.when.fulfilled,
            )

        self._publish(planner, hotori, publish)
        context = planner.context_for(hotori)

        self.assertFalse(context.is_slot_available(nanally, ActionSlot.SKILL))

        planner.perform_current_char(zero)

        self.assertEqual(planner.state.locked_route, None)
        self.assertTrue(context.is_slot_available(nanally, ActionSlot.SKILL))

    def test_reservation_can_release_when_route_is_expired(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        nanally = FakeChar(2, "nanally")
        expired = {"value": False}
        planner = self._planner([hotori, zero, nanally])

        def publish(context):
            route = context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="hotori record route",
                until=lambda: expired["value"],
            )
            context.reserve_actions(
                [ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                reason="release after route expired",
                until=route.when.expired,
            )

        self._publish(planner, hotori, publish)
        context = planner.context_for(hotori)

        self.assertFalse(context.is_slot_available(nanally, ActionSlot.SKILL))

        expired["value"] = True
        planner.state.prune()

        self.assertIsNone(planner.state.locked_route)
        self.assertTrue(context.is_slot_available(nanally, ActionSlot.SKILL))

    def test_reservation_can_release_when_route_is_closed(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        nanally = FakeChar(2, "nanally")
        planner = self._planner([hotori, zero, nanally])
        handles = {}

        def publish(context):
            route = context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="hotori record route",
                return_to_source=True,
            )
            handles["route"] = route
            context.reserve_actions(
                [ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                reason="release after route closes",
                until=route.when.closed,
            )

        self._publish(planner, hotori, publish)
        context = planner.context_for(hotori)

        planner.perform_current_char(zero)

        self.assertTrue(handles["route"].is_fulfilled)
        self.assertFalse(handles["route"].is_closed)
        self.assertFalse(context.is_slot_available(nanally, ActionSlot.SKILL))

        planner.perform_current_char(hotori)

        self.assertTrue(handles["route"].is_closed)
        self.assertTrue(context.is_slot_available(nanally, ActionSlot.SKILL))

    def test_route_expiration_can_happen_after_fulfillment(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        nanally = FakeChar(2, "nanally")
        expired = {"value": False}
        handles = {}
        calls = []
        planner = self._planner([hotori, zero, nanally])

        def publish(context):
            route = context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="hotori record route",
                until=lambda: expired["value"],
            )
            handles["route"] = route
            route.on_fulfilled(lambda: calls.append("fulfilled"))
            route.on_expired(lambda: calls.append("expired"))
            context.reserve_actions(
                [ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                reason="release after route window expires",
                until=route.when.expired,
            )

        self._publish(planner, hotori, publish)
        context = planner.context_for(hotori)

        planner.perform_current_char(zero)

        self.assertTrue(handles["route"].is_fulfilled)
        self.assertFalse(handles["route"].is_expired)
        self.assertFalse(context.is_slot_available(nanally, ActionSlot.SKILL))
        self.assertEqual(calls, ["fulfilled"])

        handles["route"].on_expired(lambda: calls.append("late_expired"))

        expired["value"] = True
        planner.state.prune()

        self.assertTrue(handles["route"].is_fulfilled)
        self.assertTrue(handles["route"].is_expired)
        self.assertTrue(context.is_slot_available(nanally, ActionSlot.SKILL))
        self.assertEqual(calls, ["fulfilled", "expired", "late_expired"])

    def test_route_handle_calls_fulfilled_callback(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        calls = []
        planner = self._planner([hotori, zero])

        def publish(context):
            route = context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="hotori record route",
            )
            route.on_fulfilled(lambda: calls.append("fulfilled"))

        self._publish(planner, hotori, publish)

        planner.perform_current_char(zero)

        self.assertEqual(calls, ["fulfilled"])

    def test_route_handle_calls_expired_callback(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        expired = {"value": False}
        calls = []
        planner = self._planner([hotori, zero])

        def publish(context):
            route = context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="hotori record route",
                until=lambda: expired["value"],
            )
            route.on_expired(lambda: calls.append("expired"))

        self._publish(planner, hotori, publish)

        expired["value"] = True
        planner.state.prune()

        self.assertEqual(calls, ["expired"])

    def test_route_handle_callback_registered_after_finish_runs_immediately(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        calls = []
        handles = {}
        planner = self._planner([hotori, zero])

        self._publish(
            planner,
            hotori,
            lambda context: handles.setdefault(
                "route",
                context.request_route(
                    [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                    reason="hotori record route",
                ),
            ),
        )
        planner.perform_current_char(zero)

        handles["route"].on_fulfilled(lambda: calls.append("fulfilled"))

        self.assertEqual(calls, ["fulfilled"])

    def test_new_strict_route_expires_replaced_route_handle(self):
        source = FakeChar(0, "source")
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        nanally = FakeChar(2, "nanally", tags={ActionTag.SKILL_ACTION})
        calls = []
        handles = {}
        planner = self._planner([source, zero, nanally])

        def publish(context):
            handles["first"] = context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="first route",
            )
            handles["first"].on_expired(lambda: calls.append("expired"))
            handles["second"] = context.request_route(
                [FollowupStep.for_action(nanally, ActionSlot.SKILL, reason="Nanally E")],
                reason="second route",
            )

        self._publish(
            planner,
            source,
            publish,
        )

        self.assertTrue(handles["first"].is_expired)
        self.assertTrue(handles["second"].is_pending)
        self.assertEqual(calls, ["expired"])

    def test_route_completion_does_not_release_window_lifetime_reservation(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        nanally = FakeChar(2, "nanally")
        expired = {"value": False}
        planner = self._planner([hotori, zero, nanally])

        def publish(context):
            context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="hotori record window",
                until=lambda: expired["value"],
            )
            context.reserve_actions(
                [ActionReservation.for_action(nanally, ActionSlot.SKILL)],
                reason="hotori record window hold",
                until=lambda: expired["value"],
            )

        self._publish(planner, hotori, publish)
        context = planner.context_for(hotori)

        planner.perform_current_char(zero)

        self.assertIsNone(planner.state.locked_route)
        self.assertFalse(context.is_slot_available(nanally, ActionSlot.SKILL))

        expired["value"] = True
        planner.state.prune()

        self.assertTrue(context.is_slot_available(nanally, ActionSlot.SKILL))

    def test_request_route_can_request_entry_reaction(self):
        zero = FakeChar(0, "zero", cycle_full=True)
        nanally = FakeChar(1, "nanally")
        planner = self._planner([zero, nanally])
        self._publish(
            planner,
            zero,
            lambda context: context.request_route(
                [
                    FollowupStep.for_entry_reaction(
                        nanally,
                        reason="Nanally entry reaction",
                    )
                ],
                reason="entry reaction route",
            ),
        )

        decision = planner.decide_switch(zero)

        self.assertEqual(decision.target, nanally)
        self.assertIn("entry reaction", decision.reason)

        planner.record_entry_reaction(zero, nanally)

        self.assertIsNone(planner.state.locked_route)

    def test_route_switch_completes_on_arrival_and_restores_normal_flow(self):
        source = FakeChar(0, "source")
        target = FakeChar(1, "target")
        planner = self._planner([source, target])
        self._publish(
            planner,
            source,
            lambda ctx: ctx.request_route(
                [
                    FollowupStep.for_switch(target, wait_for_turn=False),
                ]
            ),
        )

        decision = planner.decide_switch(source)
        self.assertEqual(decision.target, target)
        self.assertIsNone(decision.expected_entry)
        self.assertIsNotNone(planner.state.locked_route)
        planner.record_switch(source)
        self.assertIsNotNone(planner.state.locked_route)
        planner.record_switch(target)
        self.assertIsNone(planner.state.locked_route)
        result = planner.perform_current_char(target)
        self.assertEqual(result.name, "target_action")
        self.assertTrue(result.success)

    def test_route_switch_waits_for_normal_turn_before_next_character(self):
        calls = []
        source = FakeChar(0, "source")
        a = FakeChar(
            1,
            "a",
            plan_items=[
                ActionIntent(
                    name="failed", tags=set(), execute=lambda _: calls.append("failed") or False
                ),
                ActionIntent(
                    name="skill", tags=set(), execute=lambda _: calls.append("skill") or True
                ),
                ActionIntent(
                    name="ultimate", tags=set(), execute=lambda _: calls.append("ultimate") or True
                ),
            ],
        )
        b = FakeChar(2, "b", tags={ActionTag.ULTIMATE_ACTION})
        planner = self._planner([source, a, b])
        self._publish(
            planner,
            source,
            lambda ctx: ctx.request_route(
                [
                    FollowupStep.for_switch(a),
                    FollowupStep.for_action(b, ActionSlot.ULTIMATE),
                ]
            ),
        )
        self.assertEqual(planner.decide_switch(source).target, a)
        planner.record_switch(a)
        self.assertEqual(planner.decide_switch(a).target, a)
        planner.perform_current_char(a)
        self.assertEqual(calls, ["failed", "skill", "ultimate"])
        self.assertEqual(planner.decide_switch(a).target, b)
        self.assertEqual(planner.perform_current_char(b).name, "b_action")
        self.assertIsNone(planner.state.locked_route)

    def test_route_switch_waits_for_current_character_turn(self):
        a = FakeChar(0, "a")
        planner = self._planner([a])
        self._publish(
            planner,
            a,
            lambda ctx: ctx.request_route(
                [
                    FollowupStep.for_switch(a),
                ]
            ),
        )
        self.assertEqual(planner.decide_switch(a).target, a)
        self.assertIsNotNone(planner.state.locked_route)
        self.assertEqual(planner.perform_current_char(a).name, "a_action")
        self.assertIsNone(planner.state.locked_route)

    def test_route_switch_normal_turn_uses_field_fallback(self):
        a = FakeChar(0, "a", plan_items=[])
        planner = self._planner([a])
        self._publish(
            planner,
            a,
            lambda ctx: ctx.request_route(
                [
                    FollowupStep.for_switch(a),
                ]
            ),
        )
        planner.perform_current_char(a)
        self.assertGreater(a.waited, 0)
        self.assertIsNone(planner.state.locked_route)

    def test_route_wait_step_activated_during_turn_completes_without_repeating_turn(self):
        for prefix_kind in ("arrival", "optional_action"):
            with self.subTest(prefix=prefix_kind):
                calls = []
                a = FakeChar(
                    0,
                    "a",
                    plan_items=[
                        ActionIntent(
                            name="normal",
                            tags=set(),
                            execute=lambda _: calls.append("a") or True,
                        )
                    ],
                )
                b = FakeChar(1, "b", tags={ActionTag.ULTIMATE_ACTION})
                planner = self._planner([a, b])
                prefix = (
                    FollowupStep.for_switch(a, wait_for_turn=False)
                    if prefix_kind == "arrival"
                    else FollowupStep.for_action(a, ActionSlot.SKILL, optional=True)
                )
                self._publish(
                    planner,
                    a,
                    lambda ctx: ctx.request_route(
                        [
                            prefix,
                            FollowupStep.for_switch(a),
                            FollowupStep.for_action(b, ActionSlot.ULTIMATE),
                        ]
                    ),
                )

                planner.perform_current_char(a)

                self.assertEqual(calls, ["a"])
                self.assertEqual(planner.decide_switch(a).target, b)

    def test_route_replaced_by_last_normal_action_does_not_complete_new_turn(self):
        a = FakeChar(
            0,
            "a",
            plan_items=lambda _: [
                ActionIntent(
                    name="replace",
                    tags=set(),
                    execute=lambda ctx: ctx.request_route([FollowupStep.for_switch(a)]) or True,
                )
            ],
        )
        planner = self._planner([a])
        self._publish(
            planner,
            a,
            lambda ctx: ctx.request_route(
                [
                    FollowupStep.for_switch(a),
                ]
            ),
        )

        planner.perform_current_char(a)

        self.assertIsNotNone(planner.state.locked_route)
        self.assertEqual(planner.decide_switch(a).target, a)

    def test_route_switch_expiration_during_turn_does_not_fulfill(self):
        expired = []
        handles = []
        a = FakeChar(
            0,
            "a",
            plan_items=[
                ActionIntent(
                    name="expire",
                    tags=set(),
                    execute=lambda _: expired.append(True) or True,
                )
            ],
        )
        planner = self._planner([a])
        self._publish(
            planner,
            a,
            lambda ctx: handles.append(
                ctx.request_route(
                    [FollowupStep.for_switch(a)],
                    until=lambda: bool(expired),
                )
            ),
        )
        planner.perform_current_char(a)
        self.assertEqual(handles[0].status, Planner.RequestStatus.EXPIRED)
        self.assertIsNone(planner.state.locked_route)

    def test_turn_end_does_not_expire_unrelated_switch_request(self):
        for wait_route in (False, True):
            with self.subTest(wait_route=wait_route):
                expired = []
                finished = []
                handles = []
                a = FakeChar(
                    0,
                    "a",
                    plan_items=[
                        ActionIntent(
                            name="close_window",
                            tags=set(),
                            execute=lambda _: expired.append(True) or True,
                        )
                    ],
                )
                b = FakeChar(1, "b")
                planner = self._planner([a, b])
                self._publish(
                    planner,
                    a,
                    lambda ctx: handles.append(
                        ctx.request_switch(
                            b,
                            until=lambda: bool(expired),
                            on_finish=lambda: finished.append(True),
                        )
                    ),
                )
                if wait_route:
                    self._publish(
                        planner,
                        a,
                        lambda ctx: ctx.request_route(
                            [
                                FollowupStep.for_switch(a),
                            ]
                        ),
                    )

                planner.perform_current_char(a)

                self.assertEqual(finished, [])
                self.assertFalse(handles[0].is_expired)
                self.assertIsNone(planner.state.locked_route)
                planner.context_for(a)
                self.assertTrue(handles[0].is_expired)
                self.assertEqual(finished, [True])

    def test_route_switch_advances_to_next_action_without_target_turn(self):
        source = FakeChar(0, "source")
        a = FakeChar(1, "a")
        b = FakeChar(2, "b", tags={ActionTag.ULTIMATE_ACTION})
        planner = self._planner([source, a, b])
        self._publish(
            planner,
            source,
            lambda ctx: ctx.request_route(
                [
                    FollowupStep.for_switch(a, wait_for_turn=False),
                    FollowupStep.for_action(b, ActionSlot.ULTIMATE),
                ]
            ),
        )

        self.assertEqual(planner.decide_switch(source).target, a)
        planner.record_switch(a)
        decision = planner.decide_switch(a)
        self.assertEqual(decision.target, b)
        self.assertEqual(decision.expected_entry.slot, ActionSlot.ULTIMATE)

    def test_route_switch_already_current_restores_normal_flow(self):
        target = FakeChar(0, "target")
        planner = self._planner([target])
        self._publish(
            planner,
            target,
            lambda ctx: ctx.request_route(
                [
                    FollowupStep.for_switch(target, wait_for_turn=False),
                    FollowupStep.for_switch(target, wait_for_turn=False),
                ]
            ),
        )

        result = planner.perform_current_char(target)
        self.assertEqual(result.name, "target_action")
        self.assertIsNone(planner.state.locked_route)

    def test_route_switch_dead_target_expires(self):
        source = FakeChar(0, "source")
        target = FakeChar(1, "target")
        planner = self._planner([source, target])
        handles = []
        self._publish(
            planner,
            source,
            lambda ctx: handles.append(
                ctx.request_route(
                    [
                        FollowupStep.for_switch(target, wait_for_turn=False),
                    ]
                )
            ),
        )
        target.is_dead = True

        self.assertEqual(planner.decide_switch(source).target, source)
        self.assertIsNone(planner.state.locked_route)
        self.assertEqual(handles[0].status, Planner.RequestStatus.EXPIRED)

    def test_request_switch_prefers_target_without_expected_action(self):
        source = FakeChar(0, "source")
        zero = FakeChar(1, "zero")
        dps = FakeChar(2, "dps", field_preference=FieldPreference.MAIN_DPS)
        planner = self._planner([source, zero, dps])
        self._publish(
            planner,
            source,
            lambda context: context.request_switch(
                zero,
                reason="return Zero after Hotori",
            ),
        )

        decision = planner.decide_switch(source)

        self.assertEqual(decision.target, zero)
        self.assertIsNone(decision.expected_entry)
        self.assertIn("switch request", decision.reason)

    def test_request_role_prefers_matching_role_without_forcing_action(self):
        source = FakeChar(0, "source")
        support = self._support(1, "support")
        dps = self._main_dps(2, "dps")
        planner = self._planner([source, support, dps])
        self._publish(
            planner,
            source,
            lambda context: context.request_role(Role.SUPPORT, reason="need a support role"),
        )

        decision = planner.decide_switch(source)

        self.assertEqual(decision.target, support)
        self.assertIsNone(decision.expected_entry)
        self.assertIn("switch request", decision.reason)

        planner.record_switch(support)

        self.assertFalse(planner.context_for(source).has_active_request())

    def test_request_role_uses_normal_scoring_between_matching_roles(self):
        source = FakeChar(0, "source")
        low_priority_support = FakeChar(
            1,
            "low priority support",
            role=Role.SUPPORT,
            tags={ActionTag.DEFAULT_ACTION},
        )
        high_priority_support = FakeChar(
            2,
            "high priority support",
            role=Role.SUPPORT,
            tags={ActionTag.ULTIMATE_ACTION},
        )
        planner = self._planner([source, low_priority_support, high_priority_support])
        self._publish(
            planner,
            source,
            lambda context: context.request_role(Role.SUPPORT, reason="need any support role"),
        )

        decision = planner.decide_switch(source)

        self.assertEqual(decision.target, high_priority_support)

    def test_request_switch_does_not_stop_current_entry_flow(self):
        calls = []
        source = PublicApiChar(
            FakeTask(),
            0,
            "source",
            lambda char, context: char.plan(
                char.planner_action(
                    tags={ActionTag.DEFAULT_ACTION},
                    execute=lambda ctx: (
                        calls.append("request")
                        or ctx.request_switch(target, reason="switch after entry flow")
                        or True
                    ),
                    reason="publish switch request",
                ),
                char.planner_action(
                    tags={ActionTag.DEFAULT_ACTION},
                    execute=lambda _: calls.append("second") or True,
                    reason="continue current entry flow",
                ),
            ),
        )
        target = FakeChar(1, "target")
        task = source.task
        task.chars = [source, target]
        planner = CombatPlanner(task)
        planner.reset(task.chars)

        planner.perform_current_char(source)

        self.assertEqual(calls, ["request", "second"])

    def test_request_switch_waits_when_strict_route_preempts_it(self):
        source = FakeChar(0, "source")
        zero = FakeChar(1, "zero")
        jiuyuan = FakeChar(2, "jiuyuan", tags={ActionTag.SKILL_ACTION})
        planner = self._planner([source, zero, jiuyuan])
        self._publish(
            planner,
            source,
            lambda context: context.request_switch(
                zero,
                reason="switch to Zero after hard route",
            ),
        )
        self._publish(
            planner,
            source,
            lambda context: context.request_route(
                [FollowupStep.for_action(jiuyuan, ActionSlot.SKILL, reason="Jiuyuan E")],
                reason="hard route",
            ),
        )

        first_decision = planner.decide_switch(source)
        self.assertEqual(first_decision.target, jiuyuan)

        planner.perform_current_char(jiuyuan)
        second_decision = planner.decide_switch(jiuyuan)

        self.assertEqual(second_decision.target, zero)
        self.assertIn("switch request", second_decision.reason)

    def test_record_switch_consumes_matching_switch_request(self):
        source = FakeChar(0, "source")
        zero = FakeChar(1, "zero")
        planner = self._planner([source, zero])
        self._publish(
            planner,
            source,
            lambda context: context.request_switch(zero, reason="one shot switch"),
        )

        planner.record_switch(zero)
        decision = planner.decide_switch(source)

        self.assertNotIn("switch request", decision.reason)

    def test_strict_route_wants_action_query_supports_slots(self):
        source = FakeChar(0, "source")
        zero = FakeChar(1, "zero")
        planner = self._planner([source, zero])
        self._publish(
            planner,
            source,
            lambda context: context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="zero skill route",
            ),
        )
        context = planner.context_for(zero)

        self.assertTrue(context.strict_route_wants_action(zero, slot=ActionSlot.SKILL))
        self.assertFalse(context.strict_route_wants_action(zero, slot=ActionSlot.ULTIMATE))

    def test_has_strict_route_only_reports_locked_route(self):
        source = FakeChar(0, "source")
        zero = FakeChar(1, "zero")
        planner = self._planner([source, zero])
        context = planner.context_for(zero)
        self._publish(
            planner,
            source,
            lambda context: context.request_tags(
                {ActionTag.SUPPORT},
                reason="needs support",
            ),
        )

        self.assertTrue(context.has_active_request())
        self.assertFalse(context.has_strict_route())

        self._publish(
            planner,
            source,
            lambda context: context.request_route(
                [FollowupStep.for_action(zero, ActionSlot.SKILL, reason="Zero E")],
                reason="zero skill route",
            ),
        )

        self.assertTrue(context.has_strict_route())
        self.assertTrue(planner.has_strict_route(zero))

    def test_switch_in_guard_is_exposed_through_planner(self):
        waiting_calls = []
        current = FakeChar(0, "current")
        target = FakeChar(
            1,
            "target",
            switch_in_guard=lambda *_: SwitchInGuard.delay_until_ready(
                lambda: False,
                timeout=0.2,
                reason="wait target entry",
                while_waiting=lambda: waiting_calls.append("wait"),
            ),
        )
        planner = self._planner([current, target])

        guard = planner.switch_in_guard(current, target, has_intro=True)

        self.assertTrue(guard.should_delay())
        self.assertEqual(guard.reason, "wait target entry")
        self.assertIsNotNone(guard.while_waiting)
        guard.while_waiting()
        self.assertEqual(waiting_calls, ["wait"])

    def test_switch_in_guard_belongs_to_target_char(self):
        calls = []
        current = FakeChar(0, "current")
        target = FakeChar(
            1,
            "target",
            switch_in_guard=lambda context, from_char, has_intro: (
                calls.append((context.current_char, from_char, has_intro)) or SwitchInGuard.allow()
            ),
        )
        planner = self._planner([current, target])

        guard = planner.switch_in_guard(current, target, has_intro=True)

        self.assertFalse(guard.should_delay())
        self.assertEqual(calls, [(target, current, True)])

    def test_core_does_not_use_active_requests_as_boolean_gate(self):
        core_path = Path("src/combat/planner/core.py")
        source = core_path.read_text(encoding="utf-8")

        self.assertNotIn("if self.state.active_requests:", source)
        self.assertNotIn("if context._state.active_requests:", source)

    def test_characters_do_not_read_planner_internal_state(self):
        forbidden = [
            "context.state",
            "context._state",
            ".locked_route",
            ".active_requests",
            "src.combat.planner.requests",
            "src.combat.planner.state",
        ]
        offenders = []
        for path in Path("src/char").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for pattern in forbidden:
                if pattern in source:
                    offenders.append(f"{path}:{pattern}")

        self.assertEqual(offenders, [])


class TestSwitchDecisionExecution(unittest.TestCase):
    def test_ordinary_decision_waits_by_default(self):
        decision = SwitchDecision(target=Mock(), reason="ordinary", priority=1)

        self.assertFalse(decision.skip_switch_waits)

    def test_switch_next_char_uses_decision_wait_policy(self):
        current = Mock(index=0)
        target = Mock(index=1)
        for skip_switch_waits in (False, True):
            with self.subTest(skip_switch_waits=skip_switch_waits):
                task = Mock(team_size=2)
                task.combat_session.switch_enabled = True
                task.combat_planner.decide_switch.return_value = SwitchDecision(
                    target=target,
                    reason="selected target",
                    priority=1,
                    skip_switch_waits=skip_switch_waits,
                )
                task.combat_planner.has_strict_route.side_effect = AssertionError(
                    "execution must use the decision policy"
                )

                BaseCombatTask.switch_next_char(task, current)

                self.assertEqual(task._wait_switch_in_guard.called, not skip_switch_waits)
                self.assertEqual(current.wait_switch_cd.called, not skip_switch_waits)
                task._switch_to_char.assert_called_once()
                current.wait_switch_cd.reset_mock()

    def test_intro_replan_uses_new_decision_wait_policy(self):
        current = Mock(index=0, ufn_name="current")
        current.is_cycle_full.return_value = True
        original = SimpleNamespace(index=1, ufn_name="original", has_intro=False)
        original_decision = SwitchDecision(
            target=original,
            reason="original target",
            priority=1,
        )
        target = SimpleNamespace(index=2, ufn_name="new", has_intro=False)
        for skip_switch_waits in (False, True):
            with self.subTest(skip_switch_waits=skip_switch_waits):
                task = Mock(debug=False, clock=clock)
                task.skip_sleep_checks.return_value = nullcontext(
                    SimpleNamespace(check_combat=False)
                )
                task.is_in_team.return_value = True
                task.scene.health_snapshot.return_value = object()
                task._switch_detection_reason.side_effect = [
                    (None, 0),
                    ("char index fallback", 0),
                ]
                new_decision = SwitchDecision(
                    target=target,
                    reason="intro target",
                    priority=1,
                    has_intro=True,
                    skip_switch_waits=skip_switch_waits,
                )
                task._decide_switch_to.return_value = new_decision
                task.combat_planner.has_strict_route.side_effect = AssertionError(
                    "execution must use the new decision policy"
                )
                task.record_element_reaction.return_value = False

                BaseCombatTask._switch_to_char(
                    task,
                    original_decision,
                    current_char=current,
                    retry_intro=True,
                )

                self.assertEqual(task._wait_switch_in_guard.called, not skip_switch_waits)
                task._set_current_char.assert_called_once_with(current, target, True)
                task.combat_planner.prepare_switch.assert_has_calls(
                    [unittest.mock.call(original_decision), unittest.mock.call(new_decision)]
                )
                task.combat_planner.complete_switch.assert_called_once_with(
                    new_decision,
                    previous_char=current,
                    entry_reaction=False,
                )

    def test_intro_replan_registers_only_final_target_expectation(self):
        current = Mock(index=0, ufn_name="current")
        current.is_cycle_full.return_value = True
        original = SimpleNamespace(index=1, ufn_name="original", has_intro=False)
        original_decision = SwitchDecision(
            target=original,
            reason="original target",
            priority=1,
            expected_entry=ExpectedEntry(action_name="original_action"),
        )
        target = SimpleNamespace(index=2, ufn_name="new", has_intro=False)
        new_expected = ExpectedEntry(action_name="new_action")
        task = Mock(debug=False, clock=clock)
        task.skip_sleep_checks.return_value = nullcontext(SimpleNamespace(check_combat=False))
        task.is_in_team.return_value = True
        task.scene.health_snapshot.return_value = object()
        task._switch_detection_reason.side_effect = [(None, 0), ("char index fallback", 0)]
        new_decision = SwitchDecision(
            target=target,
            reason="intro target",
            priority=1,
            has_intro=True,
            expected_entry=new_expected,
            skip_switch_waits=True,
        )
        task._decide_switch_to.return_value = new_decision
        task.record_element_reaction.return_value = False

        BaseCombatTask._switch_to_char(
            task,
            original_decision,
            current_char=current,
            retry_intro=True,
        )

        task.combat_planner.prepare_switch.assert_has_calls(
            [unittest.mock.call(original_decision), unittest.mock.call(new_decision)]
        )
        task.combat_planner.complete_switch.assert_called_once_with(
            new_decision,
            previous_char=current,
            entry_reaction=False,
        )

    def test_successful_switch_completes_with_selected_decision(self):
        target = Mock(index=1, ufn_name="target")
        decision = SwitchDecision(
            target=target,
            reason="selected target",
            priority=1,
            expected_entry=ExpectedEntry(slot=ActionSlot.SKILL),
        )
        task = Mock(debug=False, clock=clock)
        task.skip_sleep_checks.return_value = nullcontext(SimpleNamespace(check_combat=False))
        task.is_in_team.return_value = True
        task.scene.health_snapshot.return_value = object()
        task._switch_detection_reason.return_value = ("char index fallback", 0)

        BaseCombatTask._switch_to_char(task, decision)

        task.combat_planner.prepare_switch.assert_called_once_with(decision)
        task.combat_planner.complete_switch.assert_called_once_with(
            decision,
            previous_char=None,
            entry_reaction=False,
        )

    def test_failed_switch_clears_target_expectation(self):
        current = Mock(index=0, ufn_name="current")
        target = Mock(index=1, ufn_name="target")
        decision = SwitchDecision(target=target, reason="selected target", priority=1)
        task = Mock(debug=False, clock=clock)
        task.skip_sleep_checks.return_value = nullcontext(SimpleNamespace(check_combat=False))
        task.is_in_team.return_value = True
        task.scene.health_snapshot.return_value = object()
        task._switch_detection_reason.return_value = (None, 0)

        BaseCombatTask._switch_to_char(task, decision, current_char=current, time_out=0)

        task.combat_planner.prepare_switch.assert_called_once_with(decision)
        task.combat_planner.complete_switch.assert_not_called()
        task.combat_planner.record_switch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
