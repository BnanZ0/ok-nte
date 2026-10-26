import unittest
from contextlib import nullcontext
from functools import wraps
from inspect import signature
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from src.char.Akane import Akane
from src.char.BaseChar import BaseChar
from src.char.Blackbird import Blackbird
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

    def can_element_react(self, source_char, target_char):
        return source_char is not target_char and self.reaction_target is target_char


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
        self.calls = []
        self.action_results = {}

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

    @property
    def combat_context(self):
        return self._combat_context

    def combat_plan(self):
        context = self.combat_context
        self.plan_calls += 1
        if self._plan_items is not None:
            return self._plan_items(context)

        claims = self._claims(context) if callable(self._claims) else self._claims
        slot = None
        if ActionTag.SKILL_ACTION in self._tags:
            slot = ActionSlot.SKILL
        elif ActionTag.ULTIMATE_ACTION in self._tags:
            slot = ActionSlot.ULTIMATE
        plan = CombatPlan(claims=claims)
        plan.register(
            self.perform_action,
            name=f"{self.name}_action",
            tags=self._tags,
            slot=slot,
            reason=f"{self.name} available",
            can_execute=self._can_execute,
            priority_ready=self._priority_ready,
        )
        return plan

    def perform_action(self):
        return True

    def skill(self):
        self.calls.append("skill")
        return self.action_results.get("skill", True)

    def ultimate(self):
        self.calls.append("ultimate")
        return self.action_results.get("ultimate", True)

    def followup(self):
        self.calls.append("followup")
        return self.action_results.get("followup", True)

    def combat_policies(self):
        context = self.combat_context
        if self._policies is not None:
            self._policies(context)

    def is_cycle_full(self):
        return self._cycle_full

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return self._elapsed

    def continues_normal_attack(self, duration):
        self.waited = duration

    def switch_in_guard(self, from_char, has_intro):
        context = self.combat_context
        if self._switch_in_guard is None:
            return SwitchInGuard.allow()
        return self._switch_in_guard(context, from_char, has_intro)


class LegacyChar(FakeChar):
    def combat_plan(self):
        if self._plan_items is None:
            return super().combat_plan()
        self.plan_calls += 1
        items = (
            self._plan_items(self.combat_context)
            if callable(self._plan_items)
            else self._plan_items
        )
        if isinstance(items, CombatPlan):
            return items
        items = list(items)
        return CombatPlan(
            actions=[item for item in items if isinstance(item, ActionIntent)],
            claims=[item for item in items if isinstance(item, FieldClaim)],
        )


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

    def combat_plan(self):
        return self._plan_factory(self, self.combat_context)

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


class RegisteredApiChar(PublicApiChar):
    def __init__(self, task, index, name):
        super().__init__(task, index, name, self.build_registered_plan)
        self.events = []
        self.results = []
        self.skill_ready = True
        self.skill_allowed = True
        self.skill_result = True
        self.raise_skill = False
        self.repetitions = 1
        self.entry_count = 0

    def build_registered_plan(self, char, context):
        self.plan_context = self.combat_context
        plan = self.plan()
        self.register_skill(
            plan,
            name=f"{self}_registered_skill",
            can_execute=lambda: self.skill_allowed,
        )
        self.register_ultimate(plan)
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        self.entry_count += 1
        for _ in range(self.repetitions):
            self.results.append(self.click_skill(down_time=0.3, post_sleep=0.2))
        self.perform_followup()

    def perform_followup(self):
        self.click_ultimate(send_click=False)

    def click_skill(self, down_time=0.05, post_sleep=0, send_click=True, time_out=0) -> bool:
        if self.raise_skill:
            raise ValueError("native skill failed")
        self.events.append(("skill", down_time, post_sleep, send_click, time_out))
        self.skill_clicked += 1
        return self.skill_result

    def click_ultimate(self, send_click=True, wait_if_no_cd=0) -> bool:
        self.events.append(("ultimate", send_click, wait_if_no_cd))
        self.ultimate_clicked += 1
        return True

    def skill_available(self):
        return self.skill_ready

    def is_cycle_full(self):
        return False


class _PlannerTestCase(unittest.TestCase):
    def _action_plan(self, char, order=("skill", "ultimate"), *, entry=None):
        plan = CombatPlan(entry=entry)
        metadata = {
            "skill": (ActionSlot.SKILL, ActionTag.SKILL_ACTION),
            "ultimate": (ActionSlot.ULTIMATE, ActionTag.ULTIMATE_ACTION),
            "followup": (None, ActionTag.DEFAULT_ACTION),
        }
        for kind in order:
            slot, tag = metadata[kind]
            plan.register(
                getattr(char, kind),
                name=f"{char.name}_{kind}",
                slot=slot,
                tags={tag},
                priority_ready=char._priority_ready,
            )
        return plan

    def _registered_team(self, char_type=RegisteredApiChar):
        task = FakeTask()
        chars = [char_type(task, i, name) for i, name in enumerate(("a", "b"))]
        source = FakeChar(2, "source")
        task.chars = [*chars, source]
        planner = CombatPlanner(task)
        planner.reset(task.chars)
        return planner, *chars, source

    def _publish_method_route(self, planner, source, steps, **kwargs):
        handles = []
        self._publish(
            planner,
            source,
            lambda context: handles.append(context.request_route(steps, **kwargs)),
        )
        return handles[0]

    def _skill_only_plan(self, char):
        plan = char.plan()
        char.register_skill(plan)
        plan.entry = lambda: None
        return plan

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

        def combat_plan():
            def entry():
                publish(source.combat_context)

            return CombatPlan(entry=entry)

        source.combat_plan = combat_plan
        try:
            planner.perform_current_char(source)
        finally:
            source.combat_plan = original_combat_plan


class TestCombatPlanner(_PlannerTestCase):
    """Method registration and shared combat behavior."""

    def test_route_published_by_action_waits_for_return_values_state_and_remaining_actions(self):
        class Publisher(RegisteredApiChar):
            route = None

            def click_skill(self, *args, **kwargs):
                result = super().click_skill(*args, **kwargs)
                if self.index == 0 and self.route is None:
                    self.route = self.combat_context.request_route(self.task.chars[1].click_skill)
                return result

            def perform_entry(self):
                try:
                    self.received_result = self.click_skill()
                    self.skill_done = self.received_result
                    self.pending_observation = (
                        self.combat_context.has_active_request(),
                        self.combat_context.has_strict_route(),
                        self.combat_context.strict_route_wants_action(
                            self.task.chars[1], ActionSlot.SKILL
                        ),
                        self.route.is_pending if self.route is not None else False,
                    )
                    self.ultimate_result = self.click_ultimate()
                finally:
                    self.finally_ran = True

        planner, char, teammate, _ = self._registered_team(Publisher)
        planner.perform_current_char(char)
        self.assertIs(char.received_result, True)
        self.assertTrue(char.skill_done)
        self.assertTrue(char.finally_ran)
        self.assertIs(char.ultimate_result, True)
        self.assertEqual(char.pending_observation, (True, False, False, True))
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 1))
        self.assertTrue(char.route.is_pending)
        self.assertIs(planner.decide_switch(char).target, teammate)

    def test_explicit_return_ends_entry_before_remaining_actions_and_enables_route(self):
        planner, char, teammate, _ = self._registered_team()
        routes = []

        def build(owner, context):
            plan = owner.plan()
            owner.register_skill(plan)
            owner.register_ultimate(plan)

            def entry():
                owner.click_skill()
                routes.append(context.request_route(teammate.click_skill))
                return

            plan.entry = entry
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 0))
        self.assertTrue(routes[0].is_pending)
        self.assertIs(planner.decide_switch(char).target, teammate)

    def test_pending_route_cannot_be_satisfied_by_later_calls_or_turn_in_same_entry(self):
        for whole_turn in (False, True):
            with self.subTest(whole_turn=whole_turn):
                planner, char, _, _ = self._registered_team()
                routes = []

                def build(owner, context):
                    plan = owner.plan()
                    owner.register_skill(plan)

                    def entry():
                        if not routes:
                            routes.append(
                                context.request_route(owner if whole_turn else owner.click_skill)
                            )
                        owner.click_skill()
                        owner.click_skill()

                    plan.entry = entry
                    return plan

                char._plan_factory = build
                planner.perform_current_char(char)
                self.assertEqual(char.skill_clicked, 2)
                self.assertTrue(routes[0].is_pending)
                planner.perform_current_char(char)
                self.assertTrue(routes[0].is_fulfilled)

    def test_multiple_pending_routes_replace_active_route_only_after_entry(self):
        planner, char, teammate, source = self._registered_team()
        old = self._publish_method_route(planner, source, [char, teammate.click_skill])
        routes = []
        observations = []
        finished = []
        char.entry_done = False
        old.on_expired(lambda: finished.append(char.entry_done))

        def build(owner, context):
            plan = owner.plan()
            owner.register_skill(plan)
            owner.register_ultimate(plan)

            def entry():
                routes.append(context.request_route(teammate.click_skill))
                owner.click_skill()
                observations.append((old.is_pending, context.has_strict_route()))
                routes.append(context.request_route(teammate.click_ultimate))
                owner.click_ultimate()
                observations.append((old.is_pending, routes[0].is_expired, routes[1].is_pending))
                owner.entry_done = True

            plan.entry = entry
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        self.assertEqual(observations, [(True, True), (True, True, True)])
        self.assertEqual(finished, [True])
        self.assertTrue(old.is_expired)
        self.assertIs(planner.state.locked_route.handle, routes[1])

    def test_pending_route_deadline_does_not_replace_an_existing_active_route(self):
        planner, char, teammate, source = self._registered_team()
        old = self._publish_method_route(planner, source, [char, teammate.click_skill])
        expired = [False]
        routes = []

        def build(owner, context):
            plan = owner.plan()
            owner.register_skill(plan)
            owner.register_ultimate(plan)

            def entry():
                routes.append(
                    context.request_route(teammate.click_ultimate, until=lambda: expired[0])
                )
                owner.click_skill()
                expired[0] = True
                owner.click_ultimate()

            plan.entry = entry
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        self.assertTrue(routes[0].is_expired)
        self.assertTrue(old.is_pending)
        self.assertIs(planner.state.locked_route.handle, old)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 1))

    def test_window_expiration_closes_pending_route_before_activation(self):
        planner, char, teammate, _ = self._registered_team()
        expired = [False]
        objects = []

        def build(owner, context):
            plan = owner.plan()
            owner.register_skill(plan)
            owner.register_ultimate(plan)

            def entry():
                window = context.open_window(until=lambda: expired[0])
                route = window.request_route(teammate.click_skill)
                hold = window.reserve_actions(
                    [Planner.Reserve.action(teammate, slot=ActionSlot.SKILL)]
                )
                objects.extend((window.handle, route, hold))
                owner.click_skill()
                expired[0] = True
                owner.click_ultimate()

            plan.entry = entry
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        self.assertTrue(all(handle.is_expired and handle.is_closed for handle in objects))
        self.assertIsNone(planner.state.locked_route)
        self.assertEqual(planner.state.pending_routes, [])

    def test_reset_closes_routes_waiting_for_current_entry_to_finish(self):
        planner, char, teammate, _ = self._registered_team()
        routes = []

        def build(owner, context):
            plan = owner.plan()
            owner.register_skill(plan)

            def entry():
                routes.append(context.request_route(teammate.click_skill))
                owner.click_skill()
                planner.reset(planner.task.chars)

            plan.entry = entry
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        self.assertTrue(routes[0].is_closed)
        self.assertFalse(routes[0].is_fulfilled)
        self.assertIsNone(planner.state.locked_route)
        self.assertEqual(planner.state.pending_routes, [])

    def test_registered_calls_share_permissions_and_recording_with_or_without_cache_and_entry(self):
        for phase in (None, "callback", "policy", "entry"):
            for cached in (False, True):
                for allowed in (False, True):
                    with self.subTest(phase=phase, cached=cached, allowed=allowed):
                        planner, char, _, _ = self._registered_team()
                        char._plan_factory = lambda owner, _: self._skill_only_plan(owner)
                        char.skill_ready = allowed
                        with planner._frame(char, "read"):
                            snapshot = planner._plan_snapshot_for(char)
                        scope = (
                            nullcontext()
                            if phase is None
                            else planner._frame(
                                char, phase, cache={char.index: snapshot} if cached else {}
                            )
                        )
                        with patch.object(
                            type(planner.state),
                            "record_action",
                            autospec=True,
                            side_effect=type(planner.state).record_action,
                        ) as record:
                            with scope:
                                result = char.click_skill(down_time=0.4)
                        self.assertEqual(result, allowed)
                        self.assertEqual(char.skill_clicked, int(allowed))
                        self.assertEqual(record.call_count, int(allowed))

    def test_policy_and_lifecycle_callback_use_registered_conditions_without_entry(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed):

                class PolicyChar(RegisteredApiChar):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        self.skill_allowed = allowed

                    def combat_policies(self):
                        if self.index == 0:
                            self.combat_plan()
                            self.policy_result = self.click_skill()

                planner, char, teammate, source = self._registered_team(PolicyChar)
                self.assertEqual(char.policy_result, allowed)
                self.assertEqual(char.skill_clicked, int(allowed))
                results = []
                self._publish(
                    planner,
                    source,
                    lambda context: context.request_switch(
                        teammate, on_finish=lambda: results.append(char.click_skill())
                    ),
                )
                planner.record_switch(teammate)
                self.assertEqual(results, [allowed])
                self.assertEqual(char.skill_clicked, int(allowed) * 2)

    def test_dynamic_instance_methods_and_read_only_callback_subscriptions_are_supported(self):
        planner, char, _, source = self._registered_team()
        calls = []

        def external_skill(owner, amount=1):
            calls.append(amount)
            return True

        original = MethodType(external_skill, char)
        char.extra = original

        def build(owner, _):
            plan = owner.plan()
            plan.register(owner.extra)
            plan.entry = lambda: owner.extra(amount=2)
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        self.assertEqual(signature(char.extra), signature(original))
        handle = self._publish_method_route(planner, source, char.extra)
        finished = []
        with planner._frame(char, "read"):
            handle.on_fulfilled(lambda: finished.append(True))
        planner.perform_current_char(char)
        self.assertTrue(handle.is_fulfilled)
        self.assertEqual(finished, [True])
        self.assertEqual(calls, [2, 1, 2])

    def test_body_exception_preserves_cleanup_errors_and_remaining_notifications(self):
        planner, char, teammate, _ = self._registered_team()
        primary = ValueError("entry body failed")
        finished = []

        def build(owner, context):
            plan = owner.plan()

            def entry():
                handle = context.request_switch(teammate, until=lambda: True)
                handle.on_expired(lambda: (_ for _ in ()).throw(RuntimeError("cleanup failed")))
                handle.on_finish(lambda: finished.append(True))
                raise primary

            plan.entry = entry
            return plan

        char._plan_factory = build
        with self.assertRaises(ValueError) as caught:
            planner.perform_current_char(char)
        self.assertIs(caught.exception, primary)
        self.assertIn("RuntimeError: cleanup failed", "\n".join(primary.__notes__))
        self.assertEqual(finished, [True])
        self.assertEqual(planner._frames, [])
        self.assertEqual(planner._entry_depth, 0)

    def test_multiple_callback_failures_keep_all_details_and_accepted_routes(self):
        planner, char, teammate, source = self._registered_team()
        primary = ValueError("first callback failed")
        routes = []

        def publish(context):
            handle = context.request_switch(char)

            def first():
                routes.append(context.request_route(teammate.click_skill))
                raise primary

            handle.on_fulfilled(first)
            handle.on_finish(lambda: (_ for _ in ()).throw(TypeError("second callback failed")))

        self._publish(planner, source, publish)
        with self.assertRaises(ValueError) as caught:
            planner.record_switch(char)
        self.assertIs(caught.exception, primary)
        self.assertIn("TypeError: second callback failed", "\n".join(primary.__notes__))
        self.assertTrue(routes[0].is_pending)
        self.assertIs(planner.state.locked_route.handle, routes[0])
        self.assertEqual(planner._frames, [])

    def test_later_deadline_failure_does_not_hide_an_earlier_callback_failure(self):
        planner, char, _, source = self._registered_team()
        primary = ValueError("callback failed first")
        broken = [False]
        notified = []

        def deadline():
            if broken[0]:
                raise TypeError("deadline failed later")
            return False

        def publish(context):
            context.open_window(until=deadline)
            handle = context.request_switch(char)

            def first():
                broken[0] = True
                raise primary

            handle.on_fulfilled(first)
            handle.on_finish(lambda: notified.append(True))

        self._publish(planner, source, publish)
        with self.assertRaises(ValueError) as caught:
            planner.record_switch(char)
        self.assertIs(caught.exception, primary)
        self.assertIn("TypeError: deadline failed later", "\n".join(primary.__notes__))
        self.assertEqual(notified, [True])
        self.assertEqual(planner._frames, [])

    def test_saved_context_and_window_survive_entries_but_not_reset(self):
        planner, char, teammate, source = self._registered_team()
        context = char.combat_context
        windows = []
        handles = []

        def build(owner, _):
            plan = owner.plan()
            if not windows:
                plan.entry = lambda: windows.append(context.open_window(until=lambda: False))
            else:
                plan.entry = lambda: handles.append(windows[0].request_route(teammate.click_skill))
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        planner.perform_current_char(char)
        self.assertIs(char.combat_context, context)
        self.assertTrue(handles[0].is_pending)
        planner.perform_current_char(teammate)
        self.assertTrue(handles[0].is_fulfilled)
        self.assertTrue(windows[0].handle.is_pending)
        planner.reset(planner.task.chars)
        self.assertIsNot(char.combat_context, context)
        self.assertTrue(windows[0].handle.is_closed)
        with self.assertRaisesRegex(RuntimeError, "inactive planner cycle"):
            context.has_active_request()
        with self.assertRaisesRegex(RuntimeError, "inactive planner cycle"):
            self._publish(planner, source, lambda _: context.request_route(teammate.click_skill))
        with self.assertRaisesRegex(RuntimeError, "inactive planner cycle"):
            self._publish(planner, source, lambda _: windows[0].request_route(teammate.click_skill))
        self.assertIsNone(planner.state.locked_route)

    def test_expired_window_rejects_late_nonempty_request_without_replacing_route(self):
        planner, a, b, source = self._registered_team()
        expired = [False]
        windows = []
        self._publish(
            planner,
            source,
            lambda context: windows.append(context.open_window(until=lambda: expired[0])),
        )
        expired[0] = True
        planner.context_for(source)
        live = self._publish_method_route(planner, source, b.click_skill)
        with planner._frame(source, "entry"):
            self.assertIsNone(windows[0].request_route([]))
            with self.assertRaisesRegex(RuntimeError, "expired window"):
                windows[0].request_route(a.click_skill)
            with self.assertRaisesRegex(RuntimeError, "expired window"):
                windows[0].reserve_actions([Planner.Reserve.action(a, slot=ActionSlot.SKILL)])
        self.assertTrue(live.is_pending)
        self.assertIs(planner.state.locked_route.handle, live)

    def test_callback_batch_keeps_registration_order_and_requests_after_exception(self):
        planner, a, b, source = self._registered_team()
        calls = []
        handles = []

        def publish(context):
            route = context.request_route(a.click_skill)
            handles.append(route)

            def first():
                calls.append(("first", context.current_char, context.has_strict_route()))
                handles.append(context.request_route(b.click_skill))
                raise ValueError("callback failed after publication")

            route.on_fulfilled(first)
            route.on_finish(
                lambda: calls.append(("second", context.current_char, context.has_strict_route()))
            )

        self._publish(planner, source, publish)
        with self.assertRaisesRegex(ValueError, "callback failed after publication"):
            planner.perform_current_char(a)
        self.assertEqual(calls, [("first", source, False), ("second", source, False)])
        self.assertTrue(handles[0].is_fulfilled)
        self.assertTrue(handles[0].is_closed)
        self.assertTrue(handles[1].is_pending)
        self.assertEqual(planner._frames, [])
        planner.perform_current_char(b)
        self.assertTrue(handles[1].is_fulfilled)

    def test_expiration_callback_exception_finishes_children_and_keeps_other_notifications(self):
        planner, a, b, source = self._registered_team()
        expired = [False]
        handles = []
        calls = []

        def publish(context):
            def broken():
                calls.append("window")
                raise ValueError("window callback failed")

            window = context.open_window(until=lambda: expired[0], on_finish=broken)
            route = window.request_route(a.click_skill)
            hold = window.reserve_actions([Planner.Reserve.action(a, slot=ActionSlot.ULTIMATE)])
            route.on_expired(lambda: calls.append("route"))

            def after_hold():
                calls.append("hold")
                handles.append(context.request_route(b.click_skill))

            hold.on_expired(after_hold)
            handles.extend((window.handle, route, hold))

        self._publish(planner, source, publish)
        expired[0] = True
        with self.assertRaisesRegex(ValueError, "window callback failed"):
            planner.decide_switch(source)
        self.assertEqual(calls, ["window", "route", "hold"])
        self.assertTrue(all(handle.is_expired and handle.is_closed for handle in handles[:3]))
        self.assertTrue(handles[3].is_pending)
        planner.perform_current_char(b)
        self.assertTrue(handles[3].is_fulfilled)

    def test_saved_window_used_by_another_entry_keeps_original_owner(self):
        planner, a, b, source = self._registered_team()
        windows = []
        handles = []
        calls = []
        self._publish(
            planner,
            source,
            lambda context: windows.append(context.open_window(until=lambda: False)),
        )

        def build(owner, _):
            plan = owner.plan()
            plan.entry = lambda: handles.append(
                windows[0].request_route(
                    b.click_skill,
                    on_finish=lambda: calls.append(source.combat_context.current_char),
                )
            )
            return plan

        a._plan_factory = build
        planner.perform_current_char(a)
        self.assertIs(a.combat_context.current_char, a)
        planner.perform_current_char(b)
        self.assertTrue(handles[0].is_fulfilled)
        self.assertEqual(calls, [source])

    def test_lifecycle_callbacks_publish_through_saved_live_window(self):
        for signal in ("fulfilled", "expired"):
            with self.subTest(signal=signal):
                planner, a, b, source = self._registered_team()
                expired = [False]
                windows = []
                children = []

                def publish(context):
                    window = context.open_window(until=lambda: False)
                    windows.append(window)
                    route = context.request_route(a.click_skill, until=lambda: expired[0])
                    getattr(route, f"on_{signal}")(
                        lambda: children.append(window.request_route(b.click_ultimate))
                    )

                self._publish(planner, source, publish)
                if signal == "fulfilled":
                    planner.perform_current_char(a)
                else:
                    expired[0] = True
                    planner.decide_switch(source)
                self.assertEqual(len(children), 1)
                self.assertTrue(children[0].is_pending)
                self.assertTrue(windows[0].handle.is_pending)
                planner.perform_current_char(b)
                self.assertTrue(children[0].is_fulfilled)

    def test_action_cannot_fulfill_the_route_it_publishes(self):
        class Publisher(RegisteredApiChar):
            published = None

            def click_skill(self, *args, **kwargs):
                self.skill_clicked += 1
                if self.published is None:
                    self.published = self.combat_context.request_route(self.click_skill)
                return True

        for registered in (False, True):
            with self.subTest(registered=registered):
                planner, char, _, source = self._registered_team(Publisher)
                if not registered:
                    char._plan_factory = lambda owner, _: owner.plan(entry=lambda: None)
                    self._publish_method_route(planner, source, char.click_skill)
                planner.perform_current_char(char)
                self.assertEqual(char.skill_clicked, 1)
                self.assertTrue(char.published.is_pending)
                planner.perform_current_char(char)
                self.assertTrue(char.published.is_fulfilled)

    def test_queries_read_committed_state_without_running_deadlines(self):
        planner, a, _, source = self._registered_team()
        expired = [False]
        calls = []

        def publish(context):
            window = context.open_window(until=lambda: expired[0])
            window.reserve_actions([Planner.Reserve.action(a, slot=ActionSlot.ULTIMATE)])
            window.request_route(a.click_skill)
            window.handle.on_expired(
                lambda: calls.append(
                    (context.has_strict_route(), context.is_slot_available(a, ActionSlot.ULTIMATE))
                )
            )

        self._publish(planner, source, publish)
        context = source.combat_context
        expired[0] = True
        self.assertTrue(context.has_strict_route())
        self.assertFalse(context.is_slot_available(a, ActionSlot.ULTIMATE))
        self.assertTrue(context.strict_route_wants_action(a, ActionSlot.SKILL))
        self.assertEqual(calls, [])
        planner.decide_switch(current_char=source)
        self.assertEqual(calls, [(False, True)])

    def test_switch_decision_rechecks_requests_expiring_during_readiness(self):
        for kind in ("route", "window", "reservation", "tags"):
            with self.subTest(kind=kind):
                expired = [False]
                armed = [False]

                def readiness():
                    # Simulate the deadline passing while readiness detection runs.
                    if armed[0]:
                        expired[0] = True
                    return True

                tags = {ActionTag.SKILL_ACTION}
                if kind == "reservation":
                    tags.add(ActionTag.TEAM_BUFF)
                target = self._char(1, "target", tags=tags, can_execute=readiness)
                alternative = self._char(2, "alternative", tags={ActionTag.TEAM_BUFF})
                source = self._char(0, "source", max_field_time=0)
                planner = self._planner([source, target, alternative])
                handles = []

                def publish(context):
                    def until():
                        return expired[0]

                    if kind == "route":
                        handle = context.request_route(
                            Planner.Route.action(target, slot=ActionSlot.SKILL), until=until
                        )
                    elif kind == "window":
                        window = context.open_window(until=until)
                        handles.append(window.handle)
                        handle = window.request_route(
                            Planner.Route.action(target, slot=ActionSlot.SKILL)
                        )
                    elif kind == "reservation":
                        handle = context.reserve_actions(
                            [Planner.Reserve.action(target, slot=ActionSlot.SKILL)], until=until
                        )
                    else:
                        handle = context.request_tags({ActionTag.SKILL_ACTION}, until=until)
                    handles.append(handle)

                self._publish(planner, source, publish)
                armed[0] = True
                decision = planner.decide_switch(source)

                expected = target if kind == "reservation" else alternative
                self.assertIs(decision.target, expected)
                self.assertIsNone(decision.expected_entry)
                self.assertTrue(all(handle.is_expired and handle.is_closed for handle in handles))
                self.assertEqual(target.calls, [])

    def test_optional_route_configuration_and_predicate_errors_propagate(self):
        for failure in ("signature", "predicate", "body"):
            with self.subTest(failure=failure):
                planner, a, _, source = self._registered_team()
                if failure == "predicate":

                    def build(owner, _):
                        plan = owner.plan()
                        owner.register_skill(
                            plan,
                            can_execute=lambda: (_ for _ in ()).throw(
                                ValueError("condition error")
                            ),
                        )
                        return plan

                    a._plan_factory = build
                if failure == "signature":

                    def requires_argument(owner, required):
                        return True

                    step = MethodType(requires_argument, a)
                elif failure == "body":

                    def broken(owner):
                        raise ValueError("method error")

                    step = MethodType(broken, a)
                else:
                    step = a.click_skill
                route = self._publish_method_route(planner, source, Planner.Route.optional(step))
                with self.assertRaises(TypeError if failure == "signature" else ValueError):
                    planner.perform_current_char(current_char=a)
                self.assertFalse(route.is_fulfilled)
                self.assertTrue(route.is_failed)
                self.assertEqual(planner._frames, [])

    def test_route_action_progress_uses_selected_definition_not_returned_metadata(self):
        planner, char, _, source = self._registered_team()
        char.skill_result = ActionResult(tags={ActionTag.ARC_ACTION}, slot=ActionSlot.ARC)
        char._plan_factory = lambda owner, _: self._skill_only_plan(owner)
        route = self._publish_method_route(
            planner,
            source,
            Planner.Route.action(char, slot=ActionSlot.SKILL, tags={ActionTag.SKILL_ACTION}),
        )
        planner.perform_current_char(char)
        self.assertTrue(route.is_fulfilled)

    def test_route_action_optional_defaults_and_false_result_select_only_one_candidate(self):
        for optional in (None, False, True):
            for allowed in (False, True):
                with self.subTest(optional=optional, allowed=allowed):
                    planner, char, _, source = self._registered_team()
                    char.skill_result = False

                    def build(owner, _):
                        plan = owner.plan()
                        plan.register(
                            owner.click_skill, slot=ActionSlot.CUSTOM, can_execute=lambda: allowed
                        )
                        plan.register(
                            owner.click_ultimate,
                            slot=ActionSlot.CUSTOM,
                            can_execute=lambda: allowed,
                        )
                        plan.entry = lambda: None
                        return plan

                    char._plan_factory = build
                    options = {} if optional is None else {"optional": optional}
                    route = self._publish_method_route(
                        planner,
                        source,
                        Planner.Route.action(char, slot=ActionSlot.CUSTOM, **options),
                    )
                    planner.perform_current_char(char)
                    self.assertEqual(route.is_fulfilled, optional is True)
                    self.assertEqual(route.is_pending, optional is not True)
                    self.assertEqual(char.skill_clicked, int(allowed))
                    self.assertEqual(char.ultimate_clicked, 0)
                    if not allowed and optional is not True:
                        self.assertEqual(char.normal_attack_time, 0.15)

    def test_optional_reaction_checks_only_actual_source_after_arrival(self):
        for eligible in (False, True):
            with self.subTest(eligible=eligible):
                planner, a, b, source = self._registered_team()
                source._cycle_full = True
                a.is_cycle_full = Mock(return_value=False)
                planner.task.can_element_react = Mock(return_value=eligible)
                route = self._publish_method_route(
                    planner, source, Planner.Route.reaction(a, b, optional=True)
                )
                self.assertIs(planner.decide_switch(source).target, a)
                a.is_cycle_full.assert_not_called()
                self.assertTrue(route.is_pending)
                planner.complete_switch(SwitchDecision(a, "arrival", 1, False), source)
                planner.perform_current_char(a)
                self.assertTrue(route.is_fulfilled)
                self.assertGreater(a.is_cycle_full.call_count, 0)

    def test_reaction_confirmation_failure_and_exact_pair(self):
        for optional in (None, False, True):
            for confirmed in (False, True):
                with self.subTest(optional=optional, confirmed=confirmed):
                    planner, a, b, source = self._registered_team()
                    a.is_cycle_full = lambda: True
                    planner.task.reaction_target = b
                    options = {} if optional is None else {"optional": optional}
                    route = self._publish_method_route(
                        planner, source, Planner.Route.reaction(a, b, **options)
                    )
                    planner.record_entry_reaction(source, b)
                    self.assertTrue(route.is_pending)
                    decision = planner.decide_switch(a)
                    self.assertIs(decision.target, b)
                    planner.complete_switch(decision, a, entry_reaction=confirmed)
                    self.assertEqual(route.is_fulfilled, confirmed or optional is True)
                    self.assertEqual(route.is_pending, not confirmed and optional is not True)
                    if route.is_pending:
                        self.assertIs(planner.decide_switch(b).target, a)

    def test_optional_reaction_ineligible_pair_and_dead_participants(self):
        for unavailable in ("pair", "source", "target"):
            for optional in (False, True):
                with self.subTest(unavailable=unavailable, optional=optional):
                    planner, a, b, source = self._registered_team()
                    a.is_cycle_full = lambda: True
                    planner.task.reaction_target = b if unavailable != "pair" else None
                    if unavailable != "pair":
                        (a if unavailable == "source" else b).is_dead = True
                    route = self._publish_method_route(
                        planner, source, Planner.Route.reaction(a, b, optional=optional)
                    )
                    planner.perform_current_char(a)
                    self.assertEqual(route.is_fulfilled, optional)
                    self.assertEqual(route.is_failed, not optional and unavailable != "pair")
                    self.assertEqual(route.is_pending, not optional and unavailable == "pair")

    def test_reservation_rejects_foreign_character_with_same_index(self):
        planner, char, _, source = self._registered_team()
        foreign = RegisteredApiChar(FakeTask(), char.index, "foreign")
        with self.assertRaisesRegex(ValueError, "current team"):
            self._publish(
                planner,
                source,
                lambda context: context.reserve_actions(
                    [Planner.Reserve.action(foreign, slot=ActionSlot.SKILL)], until=NEVER_EXPIRES
                ),
            )
        self.assertTrue(char.combat_context.is_slot_available(char, ActionSlot.SKILL))

    def test_new_read_only_hooks_reject_requests_and_keep_owner_context(self):
        for hook in ("build", "condition", "priority", "role", "guard", "deadline"):
            with self.subTest(hook=hook):
                planner, char, teammate, source = self._registered_team()
                calls = []

                def illegal():
                    context = char.combat_context
                    calls.append(context.current_char)
                    context.request_switch(teammate)
                    return True

                def build(owner, _):
                    if hook == "build":
                        illegal()
                    plan = owner.plan()
                    owner.register_skill(
                        plan,
                        can_execute=illegal if hook == "condition" else None,
                        priority_ready=illegal if hook == "priority" else None,
                    )
                    return plan

                char._plan_factory = build
                if hook == "role":
                    char.describe_role = lambda: illegal() or RoleProfile()
                if hook == "guard":
                    char.switch_in_guard = lambda *_: illegal()
                with self.assertRaisesRegex(RuntimeError, "planner requests require"):
                    if hook == "guard":
                        planner.switch_in_guard(source, char, False)
                    elif hook == "deadline":
                        self._publish(
                            planner, char, lambda context: context.open_window(until=illegal)
                        )
                    else:
                        planner.decide_switch(source)
                self.assertEqual(calls, [char])
                self.assertEqual(planner._frames, [])

    def test_arc_takeover_through_real_perform_path_is_explicit(self):
        for takeover in (False, True):
            for allowed in (False, True):
                with self.subTest(takeover=takeover, allowed=allowed):
                    planner, char, _, _ = self._registered_team()
                    char.planner_handles_arc = takeover

                    def build(owner, _):
                        plan = owner.plan()
                        owner.register_arc(plan, can_execute=lambda: allowed)
                        return plan

                    char._plan_factory = build
                    task = planner.task
                    task.combat_planner = planner
                    task.record_first_engage = Mock()
                    task.refresh_cd = Mock()
                    char.switch_next_char = Mock()
                    char.perform()
                    self.assertEqual(char.arc_clicked, int(not takeover) + int(allowed))
                    self.assertEqual(char.planner_handles_arc, takeover)

    def test_registered_methods_keep_native_arguments_returns_and_unlimited_attempts(self):
        planner, char, _, _ = self._registered_team()
        char.repetitions = 25

        planner.perform_current_char(char)

        self.assertEqual(char.skill_clicked, char.repetitions)
        self.assertTrue(all(value is True for value in char.results))
        self.assertEqual(char.events[0], ("skill", 0.3, 0.2, True, 0))
        self.assertEqual(char.events[-1], ("ultimate", False, 0))
        self.assertIs(char.plan_context.current_char, char)
        self.assertIs(char.combat_context, planner.context_for(char))

    def test_registered_procedure_without_return_succeeds_and_preserves_native_none(self):
        class Procedure(RegisteredApiChar):
            use_entry = True

            def build_registered_plan(self, char, context):
                plan = self.plan(entry=self.perform_entry if self.use_entry else None)
                plan.register(self.procedure)
                return plan

            def procedure(self):
                self.events.append("ran")

            def perform_entry(self):
                self.results.append(self.procedure())

        for explicit_entry in (False, True):
            with self.subTest(explicit_entry=explicit_entry):
                planner, char, _, _ = self._registered_team(Procedure)
                char.use_entry = explicit_entry
                char._max_field_time = 1
                planner.task.time_elapsed_accounting_for_freeze = lambda start: 0

                result = planner.perform_current_char(char)

                self.assertTrue(result.success)
                self.assertEqual(char.events, ["ran"])
                self.assertEqual(char.normal_attack_time, 0)
                self.assertEqual(char.results, [None] if explicit_entry else [])

    def test_route_selector_waits_for_selected_composite_result(self):
        class Composite(RegisteredApiChar):
            def combined(self):
                self.click_skill()
                return False

            def combat_plan(self):
                plan = self.plan()
                plan.register(self.combined, slot=ActionSlot.CUSTOM)
                plan.register(self.click_skill, slot=ActionSlot.CUSTOM)
                plan.entry = lambda: None
                return plan

        planner, char, _, source = self._registered_team(Composite)
        route = self._publish_method_route(
            planner, source, Planner.Route.action(char, slot=ActionSlot.CUSTOM)
        )
        planner.perform_current_char(char)
        self.assertEqual(char.skill_clicked, 1)
        self.assertTrue(route.is_pending)
        planner.perform_current_char(char)
        self.assertEqual(char.skill_clicked, 2)
        self.assertTrue(route.is_pending)

    def test_nested_publication_cannot_complete_from_parent_already_in_progress(self):
        class Publisher(RegisteredApiChar):
            published = None

            def combined(self):
                return self.click_skill()

            def click_skill(self, *args, **kwargs):
                self.skill_clicked += 1
                if self.published is None:
                    self.published = self.combat_context.request_route(self.combined)
                return True

            def combat_plan(self):
                plan = self.plan()
                plan.register(self.combined)
                self.register_skill(plan)
                plan.entry = self.combined
                return plan

        planner, char, _, _ = self._registered_team(Publisher)
        planner.perform_current_char(char)
        self.assertTrue(char.published.is_pending)
        planner.perform_current_char(char)
        self.assertTrue(char.published.is_fulfilled)

    def test_callback_route_waits_until_current_entry_finishes(self):
        planner, a, b, source = self._registered_team()
        routes = []

        def publish(context):
            routes.append(
                context.request_route(
                    a.click_skill,
                    on_finish=lambda: routes.append(context.request_route(b.click_skill)),
                )
            )

        self._publish(planner, source, publish)
        planner.perform_current_char(a)
        self.assertTrue(routes[0].is_fulfilled)
        self.assertTrue(routes[1].is_pending)
        self.assertEqual(a.entry_count, 1)
        planner.perform_current_char(b)
        self.assertTrue(routes[1].is_fulfilled)

    def test_registered_methods_recheck_cooldown_and_condition(self):
        for blocked_by in ("skill_ready", "skill_allowed"):
            with self.subTest(blocked_by=blocked_by):
                planner, char, _, _ = self._registered_team()
                setattr(char, blocked_by, False)
                planner.perform_current_char(char)
                self.assertEqual(char.results, [False])
                self.assertEqual(char.skill_clicked, 0)
                setattr(char, blocked_by, True)
                planner.perform_current_char(char)
                self.assertIs(char.results[-1], True)
                self.assertEqual(char.skill_clicked, 1)

        planner, char, _, _ = self._registered_team()
        char.ultimate_available = lambda: False
        planner.perform_current_char(char)
        self.assertEqual(char.ultimate_clicked, 0)
        char.ultimate_available = lambda: True
        planner.perform_current_char(char)
        self.assertEqual(char.ultimate_clicked, 1)

    def test_registered_callbacks_have_owner_context_during_scoring_and_execution(self):
        planner, char, _, source = self._registered_team()
        calls = []

        def condition():
            calls.append(char.combat_context.current_char)
            return True

        def build(owner, context):
            plan = owner.plan()
            owner.register_skill(plan, can_execute=condition, priority_ready=condition)
            plan.entry = owner.click_skill
            return plan

        char._plan_factory = build
        planner.reset([source, char])
        self.assertIs(planner.decide_switch(source).target, char)
        self.assertEqual(char.skill_clicked, 0)
        planner.perform_current_char(char)
        self.assertEqual(char.skill_clicked, 1)
        self.assertTrue(calls)
        self.assertTrue(all(owner is char for owner in calls))
        self.assertIs(char.combat_context, planner.context_for(char))

    def test_predicate_type_error_propagates_without_retry_or_context_leak(self):
        planner, char, _, source = self._registered_team()
        calls = []

        def broken():
            calls.append(char.combat_context.current_char)
            raise TypeError("predicate bug")

        def build(owner, context):
            plan = owner.plan()
            owner.register_skill(plan, can_execute=broken)
            return plan

        char._plan_factory = build
        planner.reset([source, char])
        with self.assertRaisesRegex(TypeError, "predicate bug"):
            planner.decide_switch(source)
        self.assertEqual(calls, [char])
        self.assertIs(char.combat_context, planner.context_for(char))

    def test_modern_switch_guard_and_deferred_callbacks_have_target_context(self):
        calls = []

        class Guarded(RegisteredApiChar):
            def switch_in_guard(self, from_char, has_intro):
                calls.append((self.combat_context.current_char, from_char, has_intro))
                return SwitchInGuard.delay_until_ready(
                    lambda: self.combat_context.current_char is self,
                    timeout=1,
                    while_waiting=lambda: calls.append(self.combat_context.current_char),
                )

        planner, char, _, source = self._registered_team(Guarded)
        guard = planner.switch_in_guard(source, char, True)
        self.assertFalse(guard.should_delay())
        guard.while_waiting()
        self.assertEqual(calls, [(char, source, True), char])
        self.assertIs(char.combat_context, planner.context_for(char))

    def test_zero_argument_hooks_use_native_registration_defaults(self):
        class Modern(RegisteredApiChar):
            def combat_policies(self, *, debug=False):
                self.policy_owner = self.combat_context.current_char
                self.policy_debug = debug

            def combat_plan(self, *, debug=False):
                self.plan_debug = debug
                plan = self.plan()
                self.register_skill(plan)
                return plan

        planner, char, _, _ = self._registered_team(Modern)
        self.assertIs(char.policy_owner, char)
        self.assertIs(char.policy_debug, False)
        result = planner.perform_current_char(char)
        self.assertIs(char.plan_debug, False)
        self.assertEqual(result.slot, ActionSlot.SKILL)
        self.assertEqual(result.tags, {ActionTag.SKILL_ACTION})
        self.assertEqual(char.skill_clicked, 1)
        char.skill_ready = False
        planner.perform_current_char(char)
        self.assertEqual(char.skill_clicked, 1)

    def test_distinct_registered_methods_with_same_name_both_execute(self):
        planner, char, _, _ = self._registered_team()

        def build(owner, context):
            plan = owner.plan()
            plan.register(owner.click_skill, name="same")
            plan.register(owner.click_ultimate, name="same")
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 1))

    def test_route_composed_method_receives_nested_failure_and_can_try_alternative(self):
        class Composed(RegisteredApiChar):
            def combined(self):
                return self.click_skill() or self.click_ultimate()

            def combat_plan(self):
                plan = self.plan()
                plan.register(self.combined)
                self.register_skill(plan)
                self.register_ultimate(plan)
                plan.entry = lambda: None
                return plan

        planner, char, _, source = self._registered_team(Composed)
        char.skill_result = False
        route = self._publish_method_route(planner, source, char.combined)
        planner.perform_current_char(char)
        self.assertTrue(route.is_fulfilled)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 1))
        self.assertEqual(char.entry_count, 0)

    def test_window_lifetime_callbacks_have_context_and_completion_keeps_holds(self):
        planner, char, teammate, _ = self._registered_team()
        expired = [False]
        objects = []
        callbacks = []

        def build(owner, context):
            plan = owner.plan()

            def entry():
                window = owner.combat_context.open_window(
                    until=lambda: expired[0] and owner.combat_context.current_char is owner,
                    on_finish=lambda: callbacks.append(owner.combat_context.current_char),
                )
                route = window.request_route(teammate.click_ultimate)
                hold = window.reserve_actions(
                    [Planner.Reserve.action(teammate, slot=ActionSlot.SKILL)]
                )
                objects.extend((window, route, hold))

            plan.entry = entry
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        planner.perform_current_char(teammate)
        window, route, hold = objects
        self.assertTrue(route.is_fulfilled)
        self.assertTrue(window.handle.is_pending)
        self.assertTrue(hold.is_pending)
        self.assertFalse(planner.context_for(char).is_slot_available(teammate, ActionSlot.SKILL))
        expired[0] = True
        planner._settle()
        self.assertTrue(window.handle.is_expired)
        self.assertTrue(hold.is_expired)
        self.assertFalse(route.is_expired)
        self.assertEqual(callbacks, [char])
        self.assertIs(char.combat_context, planner.context_for(char))

    def test_window_deadline_stops_pending_route(self):
        planner, char, teammate, source = self._registered_team()
        expired = [False]
        objects = []

        def publish(context):
            window = context.open_window(until=lambda: expired[0])
            route = window.request_route([char.click_skill, teammate.click_ultimate])
            hold = window.reserve_actions([Planner.Reserve.action(teammate, slot=ActionSlot.SKILL)])
            objects.extend((window, route, hold))

        self._publish(planner, source, publish)
        window, route, hold = objects
        expired[0] = True
        planner._settle()
        self.assertTrue(window.handle.is_expired)
        self.assertTrue(route.is_expired)
        self.assertTrue(hold.is_closed)
        self.assertIsNone(planner.state.locked_route)
        self.assertEqual((char.skill_clicked, teammate.ultimate_clicked), (0, 0))

    def test_lifetime_condition_can_query_context_and_route_callback_error_unlocks(self):
        planner, char, _, source = self._registered_team()
        handles = []

        def publish(context):
            handles.append(
                context.request_route(
                    char.click_skill,
                    until=lambda: source._combat_context.has_strict_route() and False,
                    on_finish=lambda: (_ for _ in ()).throw(ValueError("finish callback bug")),
                )
            )

        self._publish(planner, source, publish)
        planner._settle()
        with self.assertRaisesRegex(ValueError, "finish callback bug"):
            planner.perform_current_char(char)
        self.assertTrue(handles[0].is_fulfilled)
        self.assertTrue(handles[0].is_closed)
        self.assertFalse(planner.has_strict_route(char))

    def test_generic_registration_does_not_infer_native_defaults_or_availability(self):
        for explicit_metadata in (False, True):
            with self.subTest(explicit_metadata=explicit_metadata):
                planner, char, _, _ = self._registered_team()
                char.skill_ready = False
                slot = ActionSlot.ARC if explicit_metadata else None
                tags = {ActionTag.TEAM_BUFF} if explicit_metadata else set()

                def build(owner, context):
                    plan = owner.plan()
                    plan.register(owner.click_skill, slot=slot, tags=tags)
                    plan.entry = owner.click_skill
                    return plan

                char._plan_factory = build
                result = planner.perform_current_char(char)

                self.assertEqual(char.skill_clicked, 1)
                self.assertEqual(result.slot, slot)
                self.assertEqual(result.tags, tags)

    def test_support_registration_defaults_and_explicit_tag_overrides(self):
        class RegisteredSupport(Support, RegisteredApiChar):
            pass

        planner, char, _, _ = self._registered_team(RegisteredSupport)
        with patch.object(
            type(planner.state),
            "record_action",
            autospec=True,
            side_effect=type(planner.state).record_action,
        ) as record:
            planner.perform_current_char(char)
        results = {call.args[2].slot: call.args[2].tags for call in record.call_args_list}
        self.assertEqual(
            results,
            {
                ActionSlot.SKILL: {ActionTag.SKILL_ACTION, ActionTag.SUPPORT},
                ActionSlot.ULTIMATE: {ActionTag.ULTIMATE_ACTION, ActionTag.SUPPORT},
            },
        )

        def build(owner, context):
            plan = owner.plan()
            owner.register_skill(plan, tags=set(), add_tags=ActionTag.HIGH_PRIORITY)
            plan.entry = owner.click_skill
            return plan

        char._plan_factory = build
        result = planner.perform_current_char(char)
        self.assertEqual(result.slot, ActionSlot.SKILL)
        self.assertEqual(result.tags, {ActionTag.HIGH_PRIORITY})

    def test_registered_arc_default_only_disables_switch_scoring(self):
        planner, char, _, source = self._registered_team()

        def build(owner, context):
            plan = owner.plan()
            owner.register_arc(plan)
            return plan

        char._plan_factory = build
        planner.reset([source, char])

        self.assertIs(planner.decide_switch(source).target, source)
        self.assertEqual(char.arc_clicked, 0)

        result = planner.perform_current_char(char)

        self.assertEqual(char.arc_clicked, 1)
        self.assertEqual(result.slot, ActionSlot.ARC)

    def test_registered_override_super_chain_is_checked_and_recorded_once(self):
        class Overridden(RegisteredApiChar):
            def click_skill(self, *args, **kwargs):
                value = super().click_skill(*args, **kwargs)
                self.events.append(("override",))
                return value

        planner, char, _, _ = self._registered_team(Overridden)
        with patch.object(
            type(planner.state),
            "record_action",
            autospec=True,
            side_effect=type(planner.state).record_action,
        ) as record:
            planner.perform_current_char(char)
        self.assertEqual(char.skill_clicked, 1)
        skill_results = [
            call.args[2] for call in record.call_args_list if call.args[2].slot == ActionSlot.SKILL
        ]
        self.assertEqual(len(skill_results), 1)
        char.skill_allowed = False
        planner.perform_current_char(char)
        self.assertEqual(char.events.count(("override",)), 1)

    def test_registered_method_has_one_registration_and_no_planning_input(self):
        planner, char, teammate, _ = self._registered_team()
        plan = char.plan()
        self.assertIsNone(plan.register(char.click_skill))
        with self.assertRaises(ValueError):
            plan.register(char.click_skill, name="different name")
        with self.assertRaises(ValueError):
            plan.register(teammate.click_skill)

        def invalid_plan(owner, context):
            result = owner.plan()
            result.register(owner.click_skill)
            owner.click_skill()
            return result

        char._plan_factory = invalid_plan
        with self.assertRaises(RuntimeError):
            planner.perform_current_char(char)
        self.assertEqual(char.events, [])

    def test_registered_method_preserves_existing_decorator_behavior(self):
        def decorated(method):
            @wraps(method)
            def wrapper(owner, *args, **kwargs):
                owner.events.append(("decorated",))
                return method(owner, *args, **kwargs)

            return wrapper

        class Decorated(RegisteredApiChar):
            @decorated
            def click_skill(self, *args, **kwargs):
                return super().click_skill(*args, **kwargs)

        planner, char, _, _ = self._registered_team(Decorated)
        planner.perform_current_char(char)

        self.assertEqual(char.events.count(("decorated",)), 1)
        self.assertEqual(char.skill_clicked, 1)

    def test_registered_private_method_and_saved_reference_use_current_permissions(self):
        class WithPrivateMethod(RegisteredApiChar):
            def _burst(self):
                return True

        planner, char, _, _ = self._registered_team(WithPrivateMethod)
        original = char._burst
        saved = []
        results = []

        def build(owner, context):
            plan = owner.plan()
            plan.register(original, can_execute=lambda: owner.skill_allowed)
            if not saved:
                saved.append(owner._burst)
            plan.entry = lambda: results.append(saved[0]())
            return plan

        char._plan_factory = build
        char.skill_allowed = False
        planner.perform_current_char(char)
        char.skill_allowed = True
        planner.perform_current_char(char)

        self.assertEqual(results, [False, True])

    def test_reference_saved_before_first_registration_keeps_original_python_behavior(self):
        planner, char, _, _ = self._registered_team()
        original = char.click_skill
        results = []

        def build(owner, _):
            plan = owner.plan()
            owner.register_skill(plan, can_execute=lambda: False)
            plan.entry = lambda: results.extend((original(), owner.click_skill()))
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        self.assertEqual(results, [True, False])
        self.assertEqual(char.skill_clicked, 1)

    def test_action_binding_is_installed_once_with_native_signature_and_instance_isolation(self):
        planner, char, teammate, _ = self._registered_team()
        original = char.click_skill
        other = teammate.click_skill
        helper = char.skill_available
        self.assertNotIn("__getattribute__", BaseChar.__dict__)
        self.assertFalse(hasattr(helper, "__combat_method__"))
        planner.perform_current_char(char)
        installed = char.click_skill
        self.assertIsInstance(installed, MethodType)
        self.assertIs(installed.__self__, char)
        self.assertEqual(signature(installed), signature(original))
        self.assertEqual(installed.__annotations__, original.__annotations__)
        self.assertEqual(installed.__combat_method__, original)
        self.assertIs(char.click_skill, installed)
        self.assertEqual(teammate.click_skill, other)
        self.assertEqual(char.skill_available, helper)
        planner.perform_current_char(char)
        self.assertIs(char.click_skill, installed)
        self.assertEqual(char.skill_clicked, 2)

    def test_saved_registered_binding_reads_new_conditions_and_survives_reset(self):
        planner, char, _, _ = self._registered_team()
        planner.perform_current_char(char)
        saved = char.click_skill
        results = []

        def build(owner, _):
            plan = owner.plan()
            owner.register_skill(plan, can_execute=lambda: owner.skill_allowed)
            plan.entry = lambda: results.append(saved(down_time=0.4))
            return plan

        char._plan_factory = build
        char.skill_allowed = False
        planner.perform_current_char(char)
        planner.reset(planner.task.chars)
        char.skill_allowed = True
        planner.perform_current_char(char)
        self.assertEqual(results, [False, True])
        self.assertEqual(char.skill_clicked, 2)
        self.assertIs(char.click_skill, saved)
        self.assertEqual(char.events[-1], ("skill", 0.4, 0, True, 0))

    def test_registered_binding_calls_original_when_current_plan_does_not_declare_it(self):
        planner, char, _, _ = self._registered_team()
        planner.perform_current_char(char)
        saved = char.click_skill
        results = []

        def build(owner, _):
            plan = owner.plan()
            plan.entry = lambda: results.append(saved())
            return plan

        char._plan_factory = build
        with patch.object(
            type(planner.state),
            "record_action",
            autospec=True,
            side_effect=type(planner.state).record_action,
        ) as record:
            planner.perform_current_char(char)
        self.assertEqual(results, [True])
        self.assertEqual(char.skill_clicked, 2)
        record.assert_not_called()

    def test_original_reference_can_be_used_for_registration_and_route_after_binding(self):
        planner, char, _, source = self._registered_team()
        original = char.click_skill

        def build(owner, _):
            plan = owner.plan()
            plan.register(original, slot=ActionSlot.SKILL)
            plan.entry = lambda: None
            return plan

        char._plan_factory = build
        planner.perform_current_char(char)
        route = self._publish_method_route(planner, source, original)
        planner.perform_current_char(char)
        self.assertTrue(route.is_fulfilled)
        self.assertEqual(char.skill_clicked, 1)

    def test_unregistered_native_methods_are_not_intercepted(self):
        for method_name in ("click_skill", "click_ultimate", "click_arc"):
            with self.subTest(method=method_name):
                planner, char, _, _ = self._registered_team()
                input_method = getattr(char, method_name)

                def build(owner, _):
                    input_method()
                    return owner.plan()

                char._plan_factory = build
                planner.perform_current_char(char)
                self.assertEqual(char.skill_clicked + char.ultimate_clicked + char.arc_clicked, 1)
                self.assertNotIn("_planner_action_bindings", vars(char))
                self.assertFalse(hasattr(getattr(BaseChar, method_name), "__combat_input_guard__"))

    def test_unregistered_override_is_not_wrapped_for_role_queries(self):
        class Unregistered(RegisteredApiChar):
            def describe_role(self):
                self.click_skill()
                return super().describe_role()

        planner, char, _, _ = self._registered_team(Unregistered)
        planner.decide_combat_start_char(char)
        self.assertEqual(char.skill_clicked, 1)

    def test_namespaced_reservation_blocks_only_target_slot_until_expiration(self):
        planner, char, teammate, source = self._registered_team()
        expired = [False]
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [Planner.Reserve.action(char, slot=ActionSlot.SKILL)],
                until=lambda: expired[0],
            ),
        )

        planner.perform_current_char(char)
        planner.perform_current_char(teammate)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (0, 1))
        self.assertEqual((teammate.skill_clicked, teammate.ultimate_clicked), (1, 1))
        expired[0] = True
        planner.perform_current_char(char)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 2))

    def test_namespaced_reservation_allows_explicit_route_consumption(self):
        planner, char, _, source = self._registered_team()
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [Planner.Reserve.action(char, slot=ActionSlot.SKILL)],
                until=NEVER_EXPIRES,
            ),
        )
        route = self._publish_method_route(
            planner,
            source,
            Planner.Route.action(
                char,
                slot=ActionSlot.SKILL,
            ),
        )
        planner.perform_current_char(char)
        self.assertTrue(route.is_fulfilled)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 1))
        self.assertFalse(planner.context_for(char).is_slot_available(char, ActionSlot.SKILL))

    def test_route_action_selects_slot_and_can_mix_with_method_steps(self):
        planner, a, b, source = self._registered_team()
        route = self._publish_method_route(
            planner,
            source,
            [
                Planner.Route.action(a, slot=ActionSlot.SKILL),
                b.click_ultimate,
            ],
        )

        self.assertIs(planner.decide_switch(source).target, a)
        planner.perform_current_char(a)
        self.assertEqual((a.skill_clicked, a.ultimate_clicked), (1, 0))
        self.assertIs(planner.decide_switch(a).target, b)
        planner.perform_current_char(b)
        self.assertTrue(route.is_fulfilled)
        self.assertEqual((a.entry_count, b.entry_count), (0, 1))

    def test_route_action_tags_choose_first_executable_match_in_registration_order(self):
        for first_allowed in (True, False):
            with self.subTest(first_allowed=first_allowed):
                planner, char, _, source = self._registered_team()

                def build(owner, context):
                    plan = owner.plan()
                    plan.register(
                        owner.click_skill,
                        tags={ActionTag.TEAM_BUFF},
                        can_execute=lambda: first_allowed,
                        priority_ready=lambda: False,
                    )
                    plan.register(owner.click_ultimate, tags={ActionTag.SUPPORT})
                    plan.entry = owner.perform_entry
                    return plan

                char._plan_factory = build
                route = self._publish_method_route(
                    planner,
                    source,
                    Planner.Route.action(
                        char,
                        tags={ActionTag.TEAM_BUFF, ActionTag.SUPPORT},
                    ),
                )
                planner.perform_current_char(char)

                self.assertTrue(route.is_fulfilled)
                self.assertEqual(char.skill_clicked, 2 * int(first_allowed))
                self.assertEqual(char.ultimate_clicked, 1 + int(not first_allowed))
                self.assertEqual(char.entry_count, 1)

    def test_route_action_requires_slot_and_tags_and_waits_for_a_match(self):
        planner, char, _, source = self._registered_team()
        skill_tags = {ActionTag.SKILL_ACTION}

        def build(owner, context):
            plan = owner.plan()
            plan.register(owner.click_skill, slot=ActionSlot.SKILL, tags=skill_tags)
            plan.register(
                owner.click_ultimate, slot=ActionSlot.ULTIMATE, tags={ActionTag.TEAM_BUFF}
            )
            return plan

        char._plan_factory = build
        route = self._publish_method_route(
            planner,
            source,
            Planner.Route.action(
                char,
                slot=ActionSlot.SKILL,
                tags={ActionTag.TEAM_BUFF},
            ),
        )
        self.assertTrue(planner.perform_current_char(char))
        self.assertTrue(route.is_pending)
        self.assertEqual(char.events, [])
        self.assertGreater(char.normal_attack_time, 0)
        skill_tags.add(ActionTag.TEAM_BUFF)
        planner.perform_current_char(char)
        self.assertTrue(route.is_fulfilled)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (2, 1))

    def test_route_action_wait_cannot_fulfill_tag_step_with_unregistered_fallback(self):
        planner, char, _, source = self._registered_team()

        def build(owner, context):
            plan = owner.plan()
            plan.register(
                owner.click_skill, tags={ActionTag.DEFAULT_ACTION}, can_execute=lambda: False
            )
            return plan

        char._plan_factory = build
        route = self._publish_method_route(
            planner,
            source,
            Planner.Route.action(
                char,
                tags={ActionTag.DEFAULT_ACTION},
            ),
        )
        self.assertTrue(planner.perform_current_char(char))
        self.assertTrue(route.is_pending)
        self.assertEqual(char.events, [])
        self.assertGreater(char.normal_attack_time, 0)

    def test_route_action_consumes_reserved_slot_and_retries_required_failure(self):
        for success in (True, False):
            with self.subTest(success=success):
                planner, char, _, source = self._registered_team()
                char.skill_result = success
                self._publish(
                    planner,
                    source,
                    lambda context: context.reserve_actions(
                        [Planner.Reserve.action(char, slot=ActionSlot.SKILL)],
                        until=NEVER_EXPIRES,
                    ),
                )
                route = self._publish_method_route(
                    planner,
                    source,
                    Planner.Route.action(
                        char,
                        slot=ActionSlot.SKILL,
                    ),
                )
                planner.perform_current_char(char)
                self.assertEqual(route.is_fulfilled, success)
                self.assertFalse(route.is_failed)
                self.assertEqual(route.is_pending, not success)
                self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, int(success)))
                self.assertEqual(
                    planner.context_for(char).is_slot_available(char, ActionSlot.SKILL), not success
                )

    def test_route_action_validates_filters_and_target_and_copies_tags(self):
        planner, char, _, source = self._registered_team()
        for tags in (None, set()):
            with self.assertRaises(ValueError):
                Planner.Route.action(char, tags=tags)
        outsider = FakeChar(99, "outsider")
        with self.assertRaises(ValueError):
            self._publish_method_route(
                planner,
                source,
                Planner.Route.action(
                    outsider,
                    slot=ActionSlot.SKILL,
                ),
            )
        tags = {ActionTag.SKILL_ACTION}
        step = Planner.Route.action(char, tags=tags)
        tags.clear()
        route = self._publish_method_route(planner, source, step)
        planner.perform_current_char(char)
        self.assertTrue(route.is_fulfilled)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (2, 1))

    def test_method_route_completes_steps_before_resuming_final_target_entry(self):
        planner, a, b, source = self._registered_team()
        route = self._publish_method_route(planner, source, [a.click_skill, b.click_ultimate])

        self.assertEqual(planner.decide_switch(source).target, a)
        planner.perform_current_char(a)
        self.assertTrue(route.is_pending)
        self.assertEqual(planner.decide_switch(a).target, b)
        planner.perform_current_char(b)

        self.assertTrue(route.is_fulfilled)
        self.assertEqual((a.skill_clicked, a.ultimate_clicked), (1, 0))
        self.assertEqual((b.skill_clicked, b.ultimate_clicked), (1, 2))
        self.assertEqual((a.entry_count, b.entry_count), (0, 1))

    def test_method_route_allows_repeated_method_steps_without_limit(self):
        planner, a, _, source = self._registered_team()
        count = 25
        route = self._publish_method_route(planner, source, [a.click_skill] * count)

        planner.perform_current_char(a)

        self.assertTrue(route.is_fulfilled)
        self.assertEqual(a.skill_clicked, count + 1)
        self.assertEqual(a.entry_count, 1)

    def test_method_route_character_step_runs_complete_normal_entry(self):
        planner, a, b, source = self._registered_team()
        route = self._publish_method_route(planner, source, [a.click_skill, b])

        planner.perform_current_char(a)
        planner.perform_current_char(b)

        self.assertTrue(route.is_fulfilled)
        self.assertEqual((a.entry_count, b.entry_count), (0, 1))
        self.assertEqual((b.skill_clicked, b.ultimate_clicked), (1, 1))

    def test_route_completion_rechecks_native_cooldown_during_normal_entry(self):
        class CoolingDown(RegisteredApiChar):
            def click_skill(self, *args, **kwargs):
                result = super().click_skill(*args, **kwargs)
                self.skill_ready = False
                return result

        planner, char, _, source = self._registered_team(CoolingDown)
        route = self._publish_method_route(planner, source, char.click_skill)

        planner.perform_current_char(char)

        self.assertTrue(route.is_fulfilled)
        self.assertEqual(char.entry_count, 1)
        self.assertEqual(char.results, [False])
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 1))

    def test_route_completion_default_entry_attempts_native_method_again(self):
        class DefaultEntry(RegisteredApiChar):
            def combat_plan(self):
                plan = self.plan()
                self.register_skill(plan)
                return plan

            def click_skill(self, *args, **kwargs):
                super().click_skill(*args, **kwargs)
                return self.skill_clicked == 1

        planner, char, _, source = self._registered_team(DefaultEntry)
        route = self._publish_method_route(planner, source, char.click_skill)

        result = planner.perform_current_char(char)

        self.assertTrue(route.is_fulfilled)
        self.assertEqual(char.skill_clicked, 2)
        self.assertFalse(result)

    def test_method_route_waits_with_attacks_and_retries_required_failure(self):
        planner, a, b, source = self._registered_team()
        a.skill_ready = False
        callbacks = []
        route = self._publish_method_route(planner, source, [a.click_skill, b.click_ultimate])
        route.on_finish(lambda: callbacks.append("finish"))
        route.on_failed(lambda: callbacks.append("failed"))

        planner.perform_current_char(a)
        self.assertTrue(route.is_pending)
        self.assertEqual(a.events, [])
        self.assertGreater(a.normal_attack_time, 0)
        a.skill_ready = True
        a.skill_result = False
        planner.perform_current_char(a)
        planner.perform_current_char(a)
        self.assertEqual(a.skill_clicked, 2)
        self.assertTrue(route.is_pending)
        self.assertFalse(route.is_failed)
        self.assertFalse(route.is_closed)
        self.assertEqual(callbacks, [])
        self.assertIs(planner.decide_switch(a).target, a)
        self.assertEqual(b.events, [])

        a.skill_result = True
        planner.perform_current_char(a)
        self.assertEqual(a.entry_count, 0)
        self.assertTrue(route.is_pending)
        self.assertIs(planner.decide_switch(a).target, b)
        planner.perform_current_char(b)
        self.assertTrue(route.is_fulfilled)
        self.assertTrue(route.is_closed)
        self.assertEqual(callbacks, ["finish"])

    def test_method_route_obeys_reservations_and_timeout(self):
        planner, a, b, source = self._registered_team()
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [Planner.Reserve.action(a, slot=ActionSlot.SKILL)], until=NEVER_EXPIRES
            ),
        )
        route = self._publish_method_route(planner, source, [a.click_skill, b])
        planner.perform_current_char(a)
        self.assertTrue(route.is_pending)
        self.assertEqual(a.skill_clicked, 1)
        self.assertFalse(planner.context_for(a).is_slot_available(a, ActionSlot.SKILL))

        planner, a, _, source = self._registered_team()
        timed = self._publish_method_route(planner, source, a.click_skill, timeout=0)
        planner._settle()
        self.assertTrue(timed.is_expired)

    def test_optional_method_route_skips_unavailable_failed_and_dead_steps(self):
        for case in ("unavailable", "failed", "dead"):
            with self.subTest(case=case):
                planner, a, b, source = self._registered_team()
                a.skill_ready = case != "unavailable"
                a.skill_result = case != "failed"
                a.is_dead = case == "dead"
                route = self._publish_method_route(
                    planner,
                    source,
                    [Planner.Route.optional(a.click_skill), b.click_ultimate],
                )

                planner.perform_current_char(a)
                planner.perform_current_char(b)

                self.assertTrue(route.is_fulfilled)
                self.assertEqual(a.skill_clicked, int(case == "failed"))
                self.assertEqual(b.ultimate_clicked, 2)
                self.assertEqual((a.entry_count, b.entry_count), (0, 1))

    def test_optional_method_route_does_not_swallow_program_errors(self):
        planner, a, _, source = self._registered_team()
        a.raise_skill = True
        route = self._publish_method_route(planner, source, Planner.Route.optional(a.click_skill))

        with self.assertRaisesRegex(ValueError, "native skill failed"):
            planner.perform_current_char(a)

        self.assertTrue(route.is_failed)

    def test_reaction_route_waits_for_exact_source_resource_relation_and_event(self):
        planner, a, b, source = self._registered_team()
        ready = [False]
        a.is_cycle_full = lambda: ready[0]
        route = self._publish_method_route(planner, source, Planner.Route.reaction(a, b))
        self.assertIs(planner.decide_switch(source).target, a)

        planner.perform_current_char(a)
        self.assertEqual(a.entry_count, 0)
        self.assertGreater(a.normal_attack_time, 0)
        self.assertIs(planner.decide_switch(a).target, a)
        ready[0] = True
        self.assertIs(planner.decide_switch(a).target, a)
        planner.task.reaction_target = b
        decision = planner.decide_switch(a)
        self.assertIs(decision.target, b)
        self.assertTrue(decision.has_intro)

        planner.complete_switch(decision, previous_char=a, entry_reaction=False)
        planner.record_entry_reaction(source, b)
        self.assertTrue(route.is_pending)
        self.assertIs(planner.decide_switch(b).target, a)

        planner.complete_switch(decision, previous_char=a, entry_reaction=True)
        self.assertTrue(route.is_fulfilled)
        planner.perform_current_char(b)
        self.assertEqual(b.entry_count, 1)
        planner.perform_current_char(b)
        self.assertEqual(b.entry_count, 2)

    def test_reaction_route_uses_pair_eligibility_instead_of_preferred_target(self):
        planner, a, b, source = self._registered_team()
        task = planner.task
        task.element_ring = BaseCombatTask.element_ring
        task.element_ring_index = BaseCombatTask.element_ring_index
        task._get_element_ring_pair = BaseCombatTask._get_element_ring_pair.__get__(task)
        task.can_element_react = BaseCombatTask.can_element_react.__get__(task)
        task.reaction_target = source
        a.element, b.element, source.element = (
            BaseChar.ElementType.WHITE,
            BaseChar.ElementType.YELLOW,
            BaseChar.ElementType.GREEN,
        )
        a.is_cycle_full = lambda: True
        self._publish_method_route(planner, source, Planner.Route.reaction(a, b))

        self.assertIs(planner.decide_switch(a).target, b)
        b.element = BaseChar.ElementType.RED
        self.assertIs(planner.decide_switch(a).target, a)

    def test_reaction_route_runs_explicit_followup_then_resumes_ordinary_entry(self):
        planner, a, b, source = self._registered_team()
        route = self._publish_method_route(
            planner,
            source,
            [
                Planner.Route.reaction(a, b),
                b.click_ultimate,
            ],
        )
        planner.record_entry_reaction(a, b)
        planner.perform_current_char(b)

        self.assertTrue(route.is_fulfilled)
        self.assertEqual((b.entry_count, b.skill_clicked, b.ultimate_clicked), (1, 1, 2))

    def test_reaction_route_fails_when_either_participant_is_dead(self):
        for dead_index in (0, 1):
            with self.subTest(dead_index=dead_index):
                planner, a, b, source = self._registered_team()
                route = self._publish_method_route(planner, source, Planner.Route.reaction(a, b))
                (a, b)[dead_index].is_dead = True
                planner.decide_switch(a)
                self.assertTrue(route.is_failed)

    def test_required_route_retry_keeps_window_reservations_until_expiration(self):
        planner, a, b, source = self._registered_team()
        expired = [False]
        routes = []

        def publish(context):
            window = context.open_window(until=lambda: expired[0])
            route = window.request_route(a.click_skill)
            routes.append(route)
            window.reserve_actions([Planner.Reserve.action(b, slot=ActionSlot.SKILL)])

        self._publish(planner, source, publish)
        a.skill_result = False
        planner.perform_current_char(a)
        self.assertTrue(routes[0].is_pending)
        self.assertFalse(planner.context_for(a).is_slot_available(b, ActionSlot.SKILL))

        expired[0] = True
        planner._settle()
        self.assertTrue(planner.context_for(a).is_slot_available(b, ActionSlot.SKILL))
        self.assertTrue(routes[0].is_expired)

    def test_method_route_fails_dead_targets_with_or_without_registration(self):
        for registered in (False, True):
            with self.subTest(registered=registered):
                planner, a, _, source = self._registered_team()
                if not registered:
                    a._plan_factory = lambda owner, _: owner.plan(entry=lambda: None)
                route = self._publish_method_route(planner, source, a.click_skill)
                a.is_dead = True
                planner.perform_current_char(a)
                self.assertTrue(route.is_failed)
                self.assertEqual(a.events, [])

    def test_unregistered_route_methods_use_identity_without_entering_the_action_catalog(self):
        observations = []

        class Composite(RegisteredApiChar):
            def route_combo(self):
                self.click_skill()
                observations.append(self.combat_context.strict_route_wants_action(self))
                return ActionResult(name="different result", tags={ActionTag.ARC_ACTION})

        planner, a, b, source = self._registered_team(Composite)
        for char in (a, b):
            char._plan_factory = lambda owner, _: self._skill_only_plan(owner)
        route = self._publish_method_route(
            planner, source, [a.route_combo, b.route_combo, a.route_combo]
        )
        context = source.combat_context
        self.assertTrue(context.strict_route_wants_action(a))
        self.assertFalse(context.strict_route_wants_action(b))
        self.assertFalse(context.strict_route_wants_action(a, ActionSlot.SKILL))
        self.assertFalse(context.strict_route_wants_action(a, tags={ActionTag.ARC_ACTION}))
        self.assertIs(planner.decide_switch(source).target, a)
        self.assertEqual(observations, [])
        for current, next_char in ((a, b), (b, a)):
            planner.perform_current_char(current)
            self.assertTrue(route.is_pending)
            self.assertIs(planner.decide_switch(current).target, next_char)
        planner.perform_current_char(a)
        self.assertTrue(route.is_fulfilled)
        self.assertEqual(observations, [True, True, True])
        self.assertEqual((a.skill_clicked, b.skill_clicked), (2, 1))
        planner.perform_current_char(a)
        self.assertEqual(a.skill_clicked, 2)
        self.assertNotIn("route_combo", vars(a))

    def test_route_method_results_retry_or_skip_with_or_without_registration(self):
        for result in (True, False, None, ActionResult(success=True), ActionResult(success=False)):
            for registered, optional in (
                (False, False),
                (False, True),
                (True, False),
                (True, True),
            ):
                with self.subTest(result=result, registered=registered, optional=optional):
                    a = self._char(0, "a", max_field_time=0)
                    b = self._char(1, "b", max_field_time=0)
                    source = self._char(2, "source", max_field_time=0)
                    for char in (a, b):
                        char._plan_items = lambda _: CombatPlan(entry=lambda: None)
                    if registered:
                        a._plan_items = lambda _: self._action_plan(
                            a, order=("followup",), entry=lambda: None
                        )
                    a.action_results["followup"] = result
                    planner = self._planner([a, b, source])
                    step = Planner.Route.optional(a.followup) if optional else a.followup
                    route = self._publish_method_route(planner, source, [step, b.followup])
                    planner.perform_current_char(a)
                    success = (
                        result.success if isinstance(result, ActionResult) else result is not False
                    )
                    self.assertTrue(route.is_pending)
                    self.assertEqual(a.calls, ["followup"])
                    self.assertEqual(b.calls, [])
                    expected = b if success or optional else a
                    self.assertIs(planner.decide_switch(a).target, expected)
                    if not success and not optional:
                        a.action_results["followup"] = True
                        planner.perform_current_char(a)
                        self.assertEqual(a.calls, ["followup", "followup"])
                    planner.perform_current_char(b)
                    self.assertTrue(route.is_fulfilled)

    def test_unregistered_route_combo_keeps_nested_registered_permissions_and_reservations(self):
        class Composite(RegisteredApiChar):
            def route_combo(self):
                return self.click_skill()

        for blocker in ("condition", "reservation"):
            with self.subTest(blocker=blocker):
                planner, a, _, source = self._registered_team(Composite)

                def build(owner, _):
                    plan = owner.plan(entry=lambda: None)
                    owner.register_skill(plan, can_execute=lambda: owner.skill_allowed)
                    return plan

                a._plan_factory = build
                blocked = [True]
                if blocker == "condition":
                    a.skill_allowed = False
                else:
                    self._publish(
                        planner,
                        source,
                        lambda context: context.reserve_actions(
                            [Planner.Reserve.action(a, slot=ActionSlot.SKILL)],
                            until=lambda: not blocked[0],
                        ),
                    )
                route = self._publish_method_route(planner, source, a.route_combo)
                planner.perform_current_char(a)
                self.assertTrue(route.is_pending)
                self.assertEqual(a.skill_clicked, 0)
                a.skill_allowed = True
                blocked[0] = False
                planner.perform_current_char(a)
                self.assertTrue(route.is_fulfilled)
                self.assertEqual(a.skill_clicked, 1)

    def test_method_route_exception_cleans_context_and_marks_failure(self):
        planner, a, _, source = self._registered_team()
        route = self._publish_method_route(planner, source, a.click_skill)
        a.raise_skill = True

        with self.assertRaisesRegex(ValueError, "native skill failed"):
            planner.perform_current_char(a)

        self.assertTrue(route.is_failed)
        self.assertIs(a.combat_context, planner.context_for(a))
        a.raise_skill = False
        planner.perform_current_char(a)
        self.assertEqual(a.skill_clicked, 1)

    def test_method_route_does_not_complete_after_deadline_during_execution(self):
        expired = [False]

        class Expiring(RegisteredApiChar):
            def click_skill(self, *args, **kwargs):
                value = super().click_skill(*args, **kwargs)
                expired[0] = True
                return value

        planner, a, b, source = self._registered_team(Expiring)
        route = self._publish_method_route(
            planner, source, [a.click_skill, b.click_ultimate], until=lambda: expired[0]
        )
        planner.perform_current_char(a)

        self.assertTrue(route.is_expired)
        self.assertFalse(route.is_fulfilled)
        self.assertEqual(b.events, [])

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
            priority_ready=lambda: False,
            max_field_time=0,
            claims=lambda _: [FieldClaim.critical("check mechanism")],
        )
        planner = self._planner([current, claimed])

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, claimed)
        self.assertIn("field claim", decision.reason)

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
            priority_ready=lambda: False,
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
            lambda context: context.request_route([route_target]),
        )

        decision = planner.decide_switch(current)

        self.assertEqual(decision.target, route_target)
        self.assertIn("strict route", decision.reason)
        self.assertTrue(decision.skip_switch_waits)

    def test_strict_field_claim_uses_ordinary_entry(self):
        current, char = FakeChar(0, "current"), FakeChar(1, "claimed")

        def build(context):
            plan = self._action_plan(char, entry=char.skill)
            plan.add(FieldClaim.strict("return now"))
            return plan

        char._plan_items = build
        planner = self._planner([current, char])
        decision = planner.decide_switch(current)
        planner.prepare_switch(decision)
        planner.complete_switch(decision, previous_char=current)
        result = planner.perform_current_char(char)
        self.assertIs(decision.target, char)
        self.assertIsNone(decision.expected_entry)
        self.assertEqual(char.calls, ["skill"])
        self.assertEqual(result.name, "claimed_skill")

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

    def test_planner_falls_back_to_field_time_when_actions_fail(self):
        char = FakeChar(0, "main", max_field_time=1.25)
        char.action_results["skill"] = False
        char._plan_items = lambda _: self._action_plan(char, ("skill",))
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(result.name, "planner_field_time")
        self.assertEqual(result.tags, {ActionTag.FIELD_TIME, ActionTag.DAMAGE})
        self.assertEqual(char.waited, 1.25)

    def test_field_time_remaining_is_calculated_when_fallback_executes(self):
        char = FakeChar(0, "main", max_field_time=10, elapsed=5)
        char.action_results["skill"] = False
        char._plan_items = lambda _: self._action_plan(char, ("skill",))
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(result.name, "planner_field_time")
        self.assertTrue(result.success)
        self.assertEqual(char.waited, 5)

    def test_reservation_does_not_stop_default_entry(self):
        source, reserved, char = [
            FakeChar(i, name) for i, name in enumerate(("source", "reserved", "actor"))
        ]
        char._plan_items = lambda _: self._action_plan(char)
        planner = self._planner([source, reserved, char])
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [Planner.Reserve.action(reserved, slot=ActionSlot.SKILL)], until=NEVER_EXPIRES
            ),
        )

        result = planner.perform_current_char(char)

        self.assertEqual(char.calls, ["skill", "ultimate"])
        self.assertEqual(result.name, "actor_ultimate")

    def test_reservation_does_not_stop_failed_default_entry(self):
        source, reserved, char = [
            FakeChar(i, name) for i, name in enumerate(("source", "reserved", "actor"))
        ]
        char.action_results["ultimate"] = False
        char._plan_items = lambda _: self._action_plan(char, ("ultimate", "skill", "followup"))
        planner = self._planner([source, reserved, char])
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [Planner.Reserve.action(reserved, slot=ActionSlot.SKILL)], until=NEVER_EXPIRES
            ),
        )

        result = planner.perform_current_char(char)

        self.assertEqual(char.calls, ["ultimate", "skill", "followup"])
        self.assertEqual(result.name, "actor_followup")

    def test_switch_request_does_not_stop_failed_default_entry(self):
        source, target, char = [
            FakeChar(i, name) for i, name in enumerate(("source", "target", "actor"))
        ]
        char.action_results["ultimate"] = False
        char._plan_items = lambda _: self._action_plan(char, ("ultimate", "skill"))
        planner = self._planner([source, target, char])
        self._publish(planner, source, lambda context: context.request_switch(target))

        result = planner.perform_current_char(char)

        self.assertEqual(char.calls, ["ultimate", "skill"])
        self.assertEqual(result.name, "actor_skill")

    def test_unfulfilled_tag_request_stops_default_entry_after_success_or_failure(self):
        for success in (False, True):
            with self.subTest(success=success):
                source, char = FakeChar(0, "source"), FakeChar(1, "actor", max_field_time=0)
                char.action_results["ultimate"] = success

                def build(context):
                    plan = CombatPlan()
                    plan.register(char.ultimate, slot=ActionSlot.ULTIMATE, tags={ActionTag.SUPPORT})
                    plan.register(char.skill, slot=ActionSlot.SKILL, tags={ActionTag.SKILL_ACTION})
                    return plan

                char._plan_items = build
                planner = self._planner([source, char])
                self._publish(
                    planner,
                    source,
                    lambda context: context.request_tags({ActionTag.SUPPORT}, count=2),
                )

                result = planner.perform_current_char(char)

                self.assertEqual(char.calls, ["ultimate"])
                self.assertEqual(result.success, success)
                self.assertTrue(source.combat_context.has_active_request())

    def test_default_entry_follows_registration_order_and_builds_one_plan(self):
        for order in (("ultimate", "skill"), ("skill", "ultimate")):
            with self.subTest(order=order):
                char = FakeChar(0, "combo")
                char._plan_items = lambda _: self._action_plan(char, order)
                planner = self._planner([char])

                result = planner.perform_current_char(char)

                self.assertEqual(char.calls, list(order))
                self.assertEqual(result.name, f"combo_{order[-1]}")
                self.assertEqual(char.plan_calls, 1)

    def test_normal_switch_does_not_force_best_scoring_action_as_expected_entry(self):
        current = FakeChar(0, "current", field_preference=FieldPreference.SUPPORT)
        char = FakeChar(1, "actor")
        char._plan_items = lambda _: self._action_plan(char)
        planner = self._planner([current, char])

        decision = planner.decide_switch(current)

        self.assertIs(decision.target, char)
        self.assertIn("actor_ultimate", decision.reason)
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

    def test_action_result_tags_do_not_control_default_entry(self):
        char = FakeChar(0, "actor")
        char.action_results["skill"] = ActionResult(
            name="first", success=True, tags={ActionTag.DEFAULT_ACTION}, slot=ActionSlot.SKILL
        )
        char._plan_items = lambda _: self._action_plan(char)
        planner = self._planner([char])

        result = planner.perform_current_char(char)

        self.assertEqual(char.calls, ["skill", "ultimate"])
        self.assertEqual(result.name, "actor_ultimate")

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

    def test_expected_action_executes_despite_scoring_readiness_before_field_fallback(self):
        char = FakeChar(0, "actor", priority_ready=lambda: False, max_field_time=1)
        char._plan_items = lambda _: self._action_plan(char, ("ultimate",))
        planner = self._planner([char])
        decision = SwitchDecision(
            target=char,
            reason="expected action",
            priority=1,
            expected_entry=ExpectedEntry(action_name="actor_ultimate"),
        )
        planner.prepare_switch(decision)
        planner.complete_switch(decision)

        result = planner.perform_current_char(char)

        self.assertEqual(char.calls, ["ultimate", "ultimate"])
        self.assertEqual(result.name, "actor_ultimate")
        self.assertEqual(char.waited, 0)

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
                    Planner.Route.action(jiuyuan, slot=ActionSlot.SKILL),
                    Planner.Route.action(zero, slot=ActionSlot.SKILL),
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
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
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
                    Planner.Route.action(dead_support, slot=ActionSlot.SKILL, optional=True),
                    Planner.Route.action(live_support, slot=ActionSlot.SKILL),
                ],
                reason="skip dead optional route",
            ),
        )

        decision = planner.decide_switch(hotori)

        self.assertEqual(decision.target, live_support)
        self.assertEqual(decision.expected_entry.slot, ActionSlot.SKILL)

    def test_strict_route_runs_same_target_steps_in_one_entry(self):
        source, char = FakeChar(0, "source"), FakeChar(1, "actor")
        char._plan_items = lambda _: self._action_plan(char, entry=lambda: None)
        planner = self._planner([source, char])
        self._publish(
            planner,
            source,
            lambda context: context.request_route(
                [
                    Planner.Route.action(char, slot=ActionSlot.ULTIMATE),
                    Planner.Route.action(char, slot=ActionSlot.SKILL),
                ]
            ),
        )
        result = planner.perform_current_char(char)
        self.assertEqual(char.calls, ["ultimate", "skill"])
        self.assertEqual(result.name, "actor_skill")

    def test_route_switch_expects_allowed_optional_action_despite_scoring_readiness(self):
        source, target = FakeChar(0, "source"), FakeChar(1, "target", priority_ready=lambda: False)
        target._plan_items = lambda _: self._action_plan(target, entry=lambda: None)
        planner = self._planner([source, target])
        self._publish(
            planner,
            source,
            lambda context: context.request_route(
                [
                    Planner.Route.action(target, slot=ActionSlot.ULTIMATE, optional=True),
                    Planner.Route.action(target, slot=ActionSlot.SKILL),
                ]
            ),
        )
        decision = planner.decide_switch(source)
        self.assertIs(decision.target, target)
        self.assertEqual(decision.expected_entry.action_name, "target_ultimate")
        planner.prepare_switch(decision)
        planner.complete_switch(decision, previous_char=source)
        planner.perform_current_char(target)
        self.assertEqual(target.calls, ["ultimate", "skill"])

    def test_cleared_expected_entry_does_not_override_entry_order(self):
        char = FakeChar(0, "actor")
        char._plan_items = lambda _: self._action_plan(char)
        planner = self._planner([char])
        expected = SwitchDecision(
            target=char,
            reason="expected",
            priority=1,
            expected_entry=ExpectedEntry(slot=ActionSlot.ULTIMATE),
        )
        planner.prepare_switch(expected)
        planner.complete_switch(expected)
        planner.prepare_switch(SwitchDecision(target=char, reason="clear", priority=1))

        planner.perform_current_char(char)

        self.assertEqual(char.calls, ["skill", "ultimate"])

    def test_request_route_skips_failed_optional_prepare_step(self):
        source, char = FakeChar(0, "source"), FakeChar(1, "actor")
        char.action_results["ultimate"] = False
        char._plan_items = lambda _: self._action_plan(char, entry=lambda: None)
        planner = self._planner([source, char])
        routes = []
        self._publish(
            planner,
            source,
            lambda context: routes.append(
                context.request_route(
                    [
                        Planner.Route.action(char, slot=ActionSlot.ULTIMATE, optional=True),
                        Planner.Route.action(char, slot=ActionSlot.SKILL),
                    ]
                )
            ),
        )
        result = planner.perform_current_char(char)
        self.assertEqual(char.calls, ["ultimate", "skill"])
        self.assertEqual(result.name, "actor_skill")
        self.assertTrue(routes[0].is_fulfilled)

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
                    [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
                    reason="hotori record route",
                    holds=[Planner.Reserve.action(nanally, slot=ActionSlot.SKILL)],
                ),
            )

    def test_combat_policies_publish_long_lived_reservation_on_reset(self):
        target = FakeChar(1, "target")
        source = FakeChar(
            0,
            "source",
            policies=lambda context: context.reserve_actions(
                [Planner.Reserve.action(target, slot=ActionSlot.SKILL)],
                reason="policy reservation",
                until=NEVER_EXPIRES,
            ),
        )

        planner = self._planner([source, target])
        context = planner.context_for(source)

        self.assertFalse(context.is_slot_available(target, ActionSlot.SKILL))

    def test_combat_plan_rejects_request_side_effects(self):
        target = FakeChar(1, "target")
        source = FakeChar(
            0,
            "source",
            plan_items=lambda context: (
                context.reserve_actions(
                    [Planner.Reserve.action(target, slot=ActionSlot.SKILL)],
                    reason="invalid plan side effect",
                    until=NEVER_EXPIRES,
                )
                or []
            ),
        )

        planner = self._planner([source, target])
        with self.assertRaisesRegex(RuntimeError, "planner requests require"):
            planner.perform_current_char(source)
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
                [Planner.Reserve.action(nanally, slot=ActionSlot.SKILL)],
                reason="hold Nanally E",
                until=lambda: expired["value"],
            ),
        )
        context = planner.context_for(source)

        self.assertFalse(context.is_slot_available(nanally, ActionSlot.SKILL))

        expired["value"] = True
        planner._settle()

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
                    [Planner.Reserve.action(nanally, slot=ActionSlot.SKILL)],
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
                    [Planner.Reserve.action(nanally, slot=ActionSlot.SKILL)],
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
                [Planner.Reserve.action(nanally, slot=ActionSlot.SKILL)],
                reason="permanent hold",
                until=NEVER_EXPIRES,
            ),
        )

        planner._settle()
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
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
                reason="reset route",
            )
            handles["reservation"] = context.reserve_actions(
                [Planner.Reserve.action(nanally, slot=ActionSlot.SKILL)],
                reason="reset reservation",
                until=Planner.NEVER_EXPIRES,
            )

        self._publish(planner, source, publish)

        self.assertFalse(handles["route"].is_closed)
        self.assertFalse(handles["reservation"].is_closed)

        planner.reset([source, zero, nanally])

        self.assertTrue(handles["route"].is_closed)
        self.assertTrue(handles["reservation"].is_closed)

    def test_reservation_can_release_when_route_is_fulfilled(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        nanally = FakeChar(2, "nanally")
        planner = self._planner([hotori, zero, nanally])

        def publish(context):
            route = context.request_route(
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
                reason="hotori record route",
            )
            context.reserve_actions(
                [Planner.Reserve.action(nanally, slot=ActionSlot.SKILL)],
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
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
                reason="hotori record route",
                until=lambda: expired["value"],
            )
            context.reserve_actions(
                [Planner.Reserve.action(nanally, slot=ActionSlot.SKILL)],
                reason="release after route expired",
                until=route.when.expired,
            )

        self._publish(planner, hotori, publish)
        context = planner.context_for(hotori)

        self.assertFalse(context.is_slot_available(nanally, ActionSlot.SKILL))

        expired["value"] = True
        planner._settle()

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
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
                reason="hotori record route",
                return_to_source=True,
            )
            handles["route"] = route
            context.reserve_actions(
                [Planner.Reserve.action(nanally, slot=ActionSlot.SKILL)],
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

    def test_route_handle_calls_fulfilled_callback(self):
        hotori = FakeChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = FakeChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        calls = []
        planner = self._planner([hotori, zero])

        def publish(context):
            route = context.request_route(
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
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
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
                reason="hotori record route",
                until=lambda: expired["value"],
            )
            route.on_expired(lambda: calls.append("expired"))

        self._publish(planner, hotori, publish)

        expired["value"] = True
        planner._settle()

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
                    [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
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
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
                reason="first route",
            )
            handles["first"].on_expired(lambda: calls.append("expired"))
            handles["second"] = context.request_route(
                [Planner.Route.action(nanally, slot=ActionSlot.SKILL)],
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
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
                reason="hotori record window",
                until=lambda: expired["value"],
            )
            context.reserve_actions(
                [Planner.Reserve.action(nanally, slot=ActionSlot.SKILL)],
                reason="hotori record window hold",
                until=lambda: expired["value"],
            )

        self._publish(planner, hotori, publish)
        context = planner.context_for(hotori)

        planner.perform_current_char(zero)

        self.assertIsNone(planner.state.locked_route)
        self.assertFalse(context.is_slot_available(nanally, ActionSlot.SKILL))

        expired["value"] = True
        planner._settle()

        self.assertTrue(context.is_slot_available(nanally, ActionSlot.SKILL))

    def test_route_character_step_waits_for_normal_turn_before_next_character(self):
        source, a, b = FakeChar(0, "source"), FakeChar(1, "a"), FakeChar(2, "b")
        a.action_results["followup"] = False
        a._plan_items = lambda _: self._action_plan(a, ("followup", "skill", "ultimate"))
        b._tags = {ActionTag.ULTIMATE_ACTION}
        planner = self._planner([source, a, b])
        self._publish(
            planner,
            source,
            lambda context: context.request_route(
                [a, Planner.Route.action(b, slot=ActionSlot.ULTIMATE)]
            ),
        )
        self.assertIs(planner.decide_switch(source).target, a)
        planner.record_switch(a)
        self.assertIs(planner.decide_switch(a).target, a)
        planner.perform_current_char(a)
        self.assertEqual(a.calls, ["followup", "skill", "ultimate"])
        self.assertIs(planner.decide_switch(a).target, b)
        self.assertEqual(planner.perform_current_char(b).name, "b_action")
        self.assertFalse(source.combat_context.has_strict_route())

    def test_route_switch_waits_for_current_character_turn(self):
        a = FakeChar(0, "a")
        planner = self._planner([a])
        self._publish(
            planner,
            a,
            lambda ctx: ctx.request_route(
                [
                    a,
                ]
            ),
        )
        self.assertEqual(planner.decide_switch(a).target, a)
        self.assertIsNotNone(planner.state.locked_route)
        self.assertEqual(planner.perform_current_char(a).name, "a_action")
        self.assertIsNone(planner.state.locked_route)

    def test_route_switch_normal_turn_uses_field_fallback(self):
        a = FakeChar(0, "a", plan_items=lambda _: CombatPlan())
        planner = self._planner([a])
        self._publish(
            planner,
            a,
            lambda ctx: ctx.request_route(
                [
                    a,
                ]
            ),
        )
        planner.perform_current_char(a)
        self.assertGreater(a.waited, 0)
        self.assertIsNone(planner.state.locked_route)

    def test_route_replaced_by_last_normal_action_does_not_complete_new_turn(self):
        source, a = FakeChar(0, "source"), FakeChar(1, "a")
        routes = []

        def replace_route(owner):
            routes.append(owner.combat_context.request_route(owner))
            return True

        a.replace_route = MethodType(replace_route, a)

        def build(context):
            plan = CombatPlan()
            plan.register(a.replace_route)
            return plan

        a._plan_items = build
        planner = self._planner([source, a])
        self._publish(planner, source, lambda context: context.request_route(a))
        planner.perform_current_char(a)
        self.assertTrue(routes[0].is_pending)
        self.assertIs(planner.decide_switch(a).target, a)

    def test_route_character_step_expiration_during_turn_does_not_fulfill(self):
        expired, handles = [], []
        source, a = FakeChar(0, "source"), FakeChar(1, "a")

        def expire(owner):
            expired.append(True)
            return True

        a.expire = MethodType(expire, a)

        def build(context):
            plan = CombatPlan()
            plan.register(a.expire)
            return plan

        a._plan_items = build
        planner = self._planner([source, a])
        self._publish(
            planner,
            source,
            lambda context: handles.append(context.request_route(a, until=lambda: bool(expired))),
        )
        planner.perform_current_char(a)
        self.assertTrue(handles[0].is_expired)
        self.assertFalse(handles[0].is_fulfilled)
        self.assertFalse(source.combat_context.has_strict_route())

    def test_action_boundary_expires_all_due_requests(self):
        for wait_route in (False, True):
            with self.subTest(wait_route=wait_route):
                expired, finished, handles = [], [], []
                source, a, b = FakeChar(0, "source"), FakeChar(1, "a"), FakeChar(2, "b")

                def close_window(owner):
                    expired.append(True)
                    return True

                a.close_window = MethodType(close_window, a)

                def build(context):
                    plan = CombatPlan()
                    plan.register(a.close_window)
                    return plan

                a._plan_items = build
                planner = self._planner([source, a, b])
                self._publish(
                    planner,
                    source,
                    lambda context: handles.append(
                        context.request_switch(
                            b, until=lambda: bool(expired), on_finish=lambda: finished.append(True)
                        )
                    ),
                )
                if wait_route:
                    self._publish(planner, source, lambda context: context.request_route(a))
                planner.perform_current_char(a)
                self.assertFalse(source.combat_context.has_strict_route())
                self.assertTrue(handles[0].is_expired)
                self.assertEqual(finished, [True])
                planner.context_for(a)
                self.assertEqual(finished, [True])

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

    def test_request_switch_does_not_stop_current_entry(self):
        planner, source, target, _ = self._registered_team()
        handles = []

        def build(owner, context):
            plan = owner.plan()
            owner.register_skill(plan)
            owner.register_ultimate(plan)

            def entry():
                handles.append(context.request_switch(target))
                owner.click_skill()
                owner.click_ultimate()

            plan.entry = entry
            return plan

        source._plan_factory = build
        planner.perform_current_char(source)
        self.assertEqual([event[0] for event in source.events], ["skill", "ultimate"])
        self.assertTrue(handles[0].is_pending)

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
                [Planner.Route.action(jiuyuan, slot=ActionSlot.SKILL)],
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
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
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
                [Planner.Route.action(zero, slot=ActionSlot.SKILL)],
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

    def test_field_claim_expected_action_precedes_ordinary_entry(self):
        current = FakeChar(0, "current")
        char = FakeChar(1, "claimed", priority_ready=lambda: False, max_field_time=0)

        def build(context):
            plan = self._action_plan(char, entry=char.skill)
            plan.add(
                FieldClaim.critical(
                    "claim burst", expected_entry=ExpectedEntry(slot=ActionSlot.ULTIMATE)
                )
            )
            return plan

        char._plan_items = build
        planner = self._planner([current, char])

        decision = planner.decide_switch(current)
        planner.prepare_switch(decision)
        planner.complete_switch(decision, previous_char=current)
        result = planner.perform_current_char(char)

        self.assertIs(decision.target, char)
        self.assertEqual(char.calls, ["ultimate", "skill"])
        self.assertEqual(result.name, "claimed_skill")

    def test_new_entries_do_not_use_legacy_generator_interpreter(self):
        planner, char, _, _ = self._registered_team()
        char.repetitions = 25
        with patch("src.combat.planner.compatibility._run_legacy_flow") as legacy:
            planner.perform_current_char(char)

            def default_plan(owner, _):
                plan = owner.plan()
                owner.register_skill(plan)
                return plan

            char._plan_factory = default_plan
            planner.perform_current_char(char)
        legacy.assert_not_called()
        self.assertEqual(char.skill_clicked, char.repetitions + 1)


class TestCombatPlannerCompatibility(_PlannerTestCase):
    """Published external-character protocols and their modern adapters."""

    def test_default_and_legacy_entries_finish_remaining_actions_before_route_activation(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                planner, char, teammate, _ = self._registered_team()
                routes = []
                received = []

                def execute_skill(context):
                    result = char.click_skill()
                    routes.append(context.request_route(teammate.click_skill))
                    return result

                def build(owner, context):
                    if legacy:
                        skill = owner.planner_action(
                            tags=ActionTag.SKILL_ACTION, execute=execute_skill
                        )
                        ultimate = owner.click_ultimate_action()

                        def entry():
                            received.append(bool((yield skill)))
                            received.append(bool((yield ultimate)))

                        return owner.plan(skill, ultimate, entry=entry)
                    plan = owner.plan()
                    plan.register(owner.publish_skill, slot=ActionSlot.SKILL)
                    owner.register_ultimate(plan)
                    return plan

                def publish_skill(owner):
                    result = owner.click_skill()
                    routes.append(owner.combat_context.request_route(teammate.click_skill))
                    return result

                char.publish_skill = MethodType(publish_skill, char)
                char._plan_factory = build
                planner.perform_current_char(char)
                self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 1))
                self.assertTrue(routes[0].is_pending)
                self.assertIs(planner.decide_switch(char).target, teammate)
                if legacy:
                    self.assertEqual(received, [True, True])

    def test_legacy_generator_close_failure_does_not_replace_action_failure(self):
        planner, char, _, _ = self._registered_team()
        primary = ValueError("legacy action failed")

        def build(owner, _):
            def execute(context):
                raise primary

            action = owner.planner_action(tags=ActionTag.SKILL_ACTION, execute=execute)

            def entry():
                try:
                    yield action
                finally:
                    raise TypeError("generator cleanup failed")

            return owner.plan(action, entry=entry)

        char._plan_factory = build
        with self.assertRaises(ValueError) as caught:
            planner.perform_current_char(char)
        self.assertIs(caught.exception, primary)
        self.assertIn("TypeError: generator cleanup failed", "\n".join(primary.__notes__))
        self.assertEqual(planner._frames, [])

    def test_legacy_positional_action_constructor_preserves_published_fields(self):
        calls = []
        action = ActionIntent(
            {ActionTag.SKILL_ACTION},
            lambda context: calls.append(context) or True,
            "legacy",
            ActionSlot.SKILL,
            "legacy reason",
            lambda c=None: c == "context",
            lambda c=None: c == "context",
            7,
        )
        self.assertTrue(action.is_priority_ready("context"))
        self.assertTrue(action.run("context"))
        self.assertEqual(calls, ["context"])
        self.assertIn("entry_repeat:7", action.identity_key())

    def test_modern_and_legacy_super_chains_and_optional_context_predicate(self):
        calls = []

        class Modern(RegisteredApiChar):
            def combat_plan(self):
                calls.append(("modern", self.combat_context.current_char))
                return super().combat_plan()

            def switch_in_guard(self, from_char, has_intro, *, debug=False):
                self.guard_debug = debug
                self.guard_from = from_char
                return super().switch_in_guard(from_char, has_intro)

        class Legacy(Modern):
            def combat_plan(self, c=None, *, debug=False, **kwargs):
                calls.append(("legacy", c.current_char))
                self.legacy_options = (debug, kwargs)
                plan = super().combat_plan(c)
                return (
                    self.plan(
                        self.click_skill_action(can_execute=lambda c=None: c.current_char is self)
                    )
                    if plan
                    else plan
                )

            def combat_policies(self, c=None, *, debug=False, **kwargs):
                self.policy_owner = c.current_char
                super().combat_policies(c)

            def switch_in_guard(
                self, c=None, from_char=None, has_intro=False, *, debug=False, **kwargs
            ):
                self.guard_owner = c.current_char
                return super().switch_in_guard(c, from_char, has_intro)

        planner, char, _, source = self._registered_team(Legacy)
        self.assertIs(char.policy_owner, char)
        planner.perform_current_char(char)
        self.assertEqual(calls, [("legacy", char), ("modern", char)])
        self.assertEqual(char.skill_clicked, 1)
        self.assertEqual(char.legacy_options, (False, {}))
        self.assertFalse(planner.switch_in_guard(source, char, False).should_delay())
        self.assertIs(char.guard_owner, char)
        self.assertIs(char.guard_from, source)
        self.assertIs(char.guard_debug, False)

    def test_legacy_context_hooks_can_still_call_base_hooks_with_context(self):
        def legacy_plan(owner, context):
            return getattr(BaseChar, "combat_plan")(owner, context)

        def legacy_policies(owner, context):
            return getattr(BaseChar, "combat_policies")(owner, context=context)

        legacy_type = type(
            "LegacyPlannerChar",
            (RegisteredApiChar,),
            {"combat_plan": legacy_plan, "combat_policies": legacy_policies},
        )
        planner, char, _, _ = self._registered_team(legacy_type)

        planner.perform_current_char(char)

        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 1))

    def test_legacy_action_created_after_native_binding_executes_original_once(self):
        planner, char, _, _ = self._registered_team()
        planner.perform_current_char(char)
        installed = char.click_skill
        results = []

        def build(owner, _):
            action = owner.click_skill_action(can_execute=lambda ctx: ctx.current_char is owner)

            def entry():
                results.append(bool((yield action)))
                results.append(bool((yield action)))

            return owner.plan(action, entry=entry)

        char._plan_factory = build
        with patch.object(
            type(planner.state),
            "record_action",
            autospec=True,
            side_effect=type(planner.state).record_action,
        ) as record:
            planner.perform_current_char(char)
        self.assertEqual(results, [True, True])
        self.assertEqual(char.skill_clicked, 2)
        self.assertEqual(record.call_count, 1)
        self.assertIs(char.click_skill, installed)

    def test_legacy_multi_slot_reservation_keeps_published_behavior(self):
        planner, char, _, source = self._registered_team()
        expired = [False]
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [ActionReservation.for_slots(char, {ActionSlot.SKILL, ActionSlot.ULTIMATE})],
                until=lambda: expired[0],
            ),
        )
        planner.perform_current_char(char)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (0, 0))
        expired[0] = True
        planner.perform_current_char(char)
        self.assertEqual((char.skill_clicked, char.ultimate_clicked), (1, 1))

    def test_route_execution_never_evaluates_scoring_readiness(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                planner, char, _, source = self._registered_team()

                def scoring_only():
                    raise AssertionError("route must not evaluate switch scoring readiness")

                def legacy_scoring_only(context):
                    return scoring_only()

                def build(owner, context):
                    plan = owner.plan()
                    if legacy:
                        action = owner.click_ultimate_action(plan)
                        action.priority_ready = legacy_scoring_only
                        owner.click_skill_action(plan)
                    else:
                        owner.register_ultimate(plan, priority_ready=scoring_only)
                        owner.register_skill(plan)
                    plan.entry = lambda: None
                    return plan

                char._plan_factory = build
                steps = (
                    [
                        FollowupStep.for_action(char, ActionSlot.ULTIMATE, optional=True),
                        FollowupStep.for_action(char, ActionSlot.SKILL),
                    ]
                    if legacy
                    else [Planner.Route.optional(char.click_ultimate), char.click_skill]
                )
                route = self._publish_method_route(planner, source, steps)
                decision = planner.decide_switch(source)
                self.assertIs(decision.target, char)
                self.assertEqual(decision.expected_entry.slot, ActionSlot.ULTIMATE)
                planner.perform_current_char(char)

                self.assertTrue(route.is_fulfilled)
                self.assertEqual([event[0] for event in char.events], ["ultimate", "skill"])

    def test_method_route_can_consume_reserved_legacy_skill_and_replay_entry_result(self):
        planner, a, b, source = self._registered_team()
        planner.task.reaction_target = b
        a.is_cycle_full = lambda: True
        a.ultimate_available = lambda: False

        def build(owner, context):
            return owner.plan(
                owner.click_ultimate_action(),
                owner.click_skill_action(can_execute=owner.should_instant_cycle),
            )

        a._plan_factory = build
        self._publish(
            planner,
            source,
            lambda context: context.reserve_actions(
                [ActionReservation.for_action(a, ActionSlot.SKILL)], until=NEVER_EXPIRES
            ),
        )
        route = self._publish_method_route(
            planner,
            source,
            [
                Planner.Route.optional(a.click_ultimate),
                a.click_skill,
            ],
        )
        planner.decide_switch(a)
        self.assertTrue(planner.context_for(a).strict_route_wants_action(a, ActionSlot.SKILL))
        planner.perform_current_char(a)

        self.assertTrue(route.is_fulfilled)
        self.assertEqual((a.skill_clicked, a.ultimate_clicked), (1, 0))
        self.assertEqual(a.events[0][1], 0.01)
        self.assertFalse(planner.context_for(a).is_slot_available(a, ActionSlot.SKILL))

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
        char = LegacyChar(
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
        received = []

        def entry():
            received.append(bool((yield action)))

        char = LegacyChar(
            0,
            "legacy",
            max_field_time=0,
            plan_items=lambda _: CombatPlan(actions=[action], entry=entry),
        )
        planner = self._planner([char])
        self.assertFalse(planner.perform_current_char(char).success)
        self.assertEqual(received, [False])

    def test_active_request_result_replays_into_entry_flow(self):
        calls = []
        source = LegacyChar(0, "source")
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

        target = LegacyChar(
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
        source = LegacyChar(0, "source")
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

        target = LegacyChar(
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

        char = LegacyChar(
            0, "fadia", plan_items=lambda _: CombatPlan([ultimate, skill], entry=entry)
        )
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
        target = LegacyChar(1, "target")

        def plan(context):
            def entry():
                ultimate_result = yield ultimate
                if ultimate_result:
                    context.request_route(
                        [FollowupStep.for_switch(target, reason="switch to target")],
                        reason="source finished ultimate",
                    )

            return CombatPlan([ultimate], entry=entry)

        source = LegacyChar(0, "source", plan_items=plan)
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

        char = LegacyChar(
            0, "fadia", plan_items=lambda _: CombatPlan([ultimate, skill], entry=entry)
        )
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

        char = LegacyChar(
            0, "fadia", plan_items=lambda _: CombatPlan([ultimate, skill], entry=entry)
        )
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

    def test_plan_add_claim_selects_char_and_entry_receives_action_results(self):
        task = FakeTask()
        current = LegacyChar(0, "current")
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
        planner = CombatPlanner(task)
        planner.reset([char])
        context = planner.context_for(char)

        action = char.combat_plan().actions[0]
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
        planner = CombatPlanner(task)
        planner.reset([char])
        context = planner.context_for(char)

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
        current = LegacyChar(0, "current")
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
        source = LegacyChar(0, "source")
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
                    source = LegacyChar(0, "source")
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
                source = LegacyChar(0, "source")
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
        source = LegacyChar(0, "source")
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

        planner = CombatPlanner(char.task)
        planner.reset([char])
        context = planner.context_for(char)
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

    def test_legacy_repeat_retries_failure_and_keeps_entry_attempt_limit(self):
        for succeeds_on in (2, 25):
            with self.subTest(succeeds_on=succeeds_on):
                task, attempts = FakeTask(), []

                def plan(source, context):
                    skill = ActionIntent(
                        name="skill",
                        tags={ActionTag.SKILL_ACTION},
                        slot=ActionSlot.SKILL,
                        execute=lambda _: attempts.append("skill") or len(attempts) == succeeds_on,
                    )
                    ultimate = source.click_ultimate_action(name="ultimate")

                    def entry():
                        if (yield ultimate):
                            while not (yield skill.repeat_for_entry()):
                                pass

                    return CombatPlan([skill, ultimate], entry=entry)

                char = PublicApiChar(task, 0, "api_char", plan)
                planner = CombatPlanner(task)
                task.chars = [char]
                planner.reset(task.chars)

                result = planner.perform_current_char(char)

                self.assertEqual(len(attempts), 2 if succeeds_on == 2 else 19)
                self.assertEqual(char.ultimate_clicked, 1)
                self.assertEqual(result.success, succeeds_on == 2)

    def test_entry_flow_yielded_action_respects_slot_reservation(self):
        task = FakeTask()
        source = LegacyChar(0, "source")

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
        planner._settle()
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
        action = target.combat_plan().actions[0]

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
        planner._settle()

        self.assertTrue(planner.perform_current_char(target).success)
        self.assertEqual(calls, ["custom_skill"])

    def test_strict_route_final_step_continues_target_entry_flow(self):
        calls = []
        source = LegacyChar(0, "xun")
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

        nanally = LegacyChar(
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

    def test_route_expiration_can_happen_after_fulfillment(self):
        hotori = LegacyChar(0, "hotori", field_preference=FieldPreference.SETUP_ONLY)
        zero = LegacyChar(1, "zero", tags={ActionTag.SKILL_ACTION})
        nanally = LegacyChar(2, "nanally")
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
        planner._settle()

        self.assertTrue(handles["route"].is_fulfilled)
        self.assertTrue(handles["route"].is_expired)
        self.assertTrue(context.is_slot_available(nanally, ActionSlot.SKILL))
        self.assertEqual(calls, ["fulfilled", "expired", "late_expired"])

    def test_request_route_can_request_entry_reaction(self):
        zero = LegacyChar(0, "zero", cycle_full=True)
        nanally = LegacyChar(1, "nanally")
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
        source = LegacyChar(0, "source")
        target = LegacyChar(1, "target")
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

    def test_route_wait_step_activated_during_turn_completes_without_repeating_turn(self):
        for prefix_kind in ("arrival", "optional_action"):
            with self.subTest(prefix=prefix_kind):
                calls = []
                a = LegacyChar(
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
                b = LegacyChar(1, "b", tags={ActionTag.ULTIMATE_ACTION})
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

    def test_route_switch_advances_to_next_action_without_target_turn(self):
        source = LegacyChar(0, "source")
        a = LegacyChar(1, "a")
        b = LegacyChar(2, "b", tags={ActionTag.ULTIMATE_ACTION})
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
        target = LegacyChar(0, "target")
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
        source = LegacyChar(0, "source")
        target = LegacyChar(1, "target")
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
        char = LegacyChar(0, "builder", plan_items=lambda _: plan)
        planner = self._planner([char])

        self.assertEqual(calls, [])
        result = planner.perform_current_char(char)

        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(result.name, "second")

    def test_mixed_old_and_new_route_steps_use_new_route_lifetime(self):
        planner, char, teammate, source = self._registered_team()
        expired = [False]
        handle = self._publish_method_route(
            planner,
            source,
            [
                FollowupStep.for_action(char, ActionSlot.SKILL),
                teammate.click_ultimate,
            ],
            until=lambda: expired[0],
        )
        planner.perform_current_char(char)
        self.assertTrue(handle.is_pending)
        planner.perform_current_char(teammate)
        self.assertTrue(handle.is_fulfilled)
        expired[0] = True
        planner._settle()
        self.assertFalse(handle.is_expired)


class TestBuiltinPlannerRegistration(_PlannerTestCase):
    """Registered native/composite actions at real character boundaries."""

    def test_blackbird_burst_skill_keeps_stage_permission_and_reservation(self):
        for in_ult in (False, True):
            for reserved in (False, True):
                with self.subTest(in_ult=in_ult, reserved=reserved):
                    task = FakeTask()
                    char = Blackbird(task, 0)
                    source = FakeChar(1, "source")
                    task.chars = [char, source]
                    task.reaction_target = source
                    char.in_ult = in_ult
                    char.skill_available = lambda: True
                    char.ultimate_available = lambda *args: True
                    char.is_cycle_full = lambda: True
                    char.normal_attack = Mock()
                    char.sleep = Mock()
                    timer = SimpleNamespace(
                        expired=Mock(side_effect=[False, True]), elapsed=lambda: 0
                    )
                    char.clock = SimpleNamespace(timer=lambda **kwargs: timer)
                    original_plan = char.combat_plan

                    def build():
                        plan = original_plan()
                        plan.entry = char.perform_in_ult if in_ult else char.click_skill
                        return plan

                    char.combat_plan = build
                    planner = CombatPlanner(task)
                    planner.reset(task.chars)
                    if reserved:
                        self._publish(
                            planner,
                            source,
                            lambda context: context.reserve_actions(
                                [Planner.Reserve.action(char, slot=ActionSlot.SKILL)],
                                until=NEVER_EXPIRES,
                            ),
                        )

                    with patch.object(
                        BaseChar, "click_skill", autospec=True, return_value=True
                    ) as skill:
                        planner.perform_current_char(char)

                    self.assertEqual(skill.call_count, int(in_ult and not reserved))

    def test_akane_registered_skill_keeps_parameters_and_plain_combo_call(self):
        for boss_fight, down_time in ((False, 0.3), (True, 0.01)):
            with self.subTest(boss_fight=boss_fight):
                task = FakeTask()
                task._boss_fight = boss_fight
                task.time_elapsed_accounting_for_freeze = Mock(return_value=0)
                char = Akane(task, 0)
                char.skill_available = Mock(return_value=True)
                char.ultimate_available = Mock(return_value=True)
                char.click_skill = Mock(return_value=True)
                char.perform_ult_combo = Mock()
                task.chars = [char]
                planner = CombatPlanner(task)
                planner.reset(task.chars)

                with patch.object(
                    BaseChar, "click_ultimate", autospec=True, return_value=True
                ) as ultimate:
                    planner.perform_current_char(char)

                char.click_skill.assert_called_once_with(down_time=down_time, post_sleep=0.2)
                ultimate.assert_called_once_with(char)
                char.perform_ult_combo.assert_called_once_with()

    def test_nanally_burst_retries_failed_skill_until_success(self):
        nanally = Nanally(FakeTask(), 0, char_id="nanally")
        skill_results = iter([False, True])
        normal_attacks = []
        nanally.click_skill = lambda: next(skill_results)
        nanally.normal_attack = lambda: normal_attacks.append("normal")
        nanally.sleep = lambda _: None

        with patch("src.utils.clock._read_monotonic", side_effect=[0, 0, 0.2, 6]):
            result = nanally.perform_in_ult()

        self.assertTrue(result)
        self.assertEqual(normal_attacks, ["normal", "normal"])

    def test_sakiri_keeps_registered_skill_duration(self):
        task = FakeTask()
        char = Sakiri(task, 0)
        char.skill_available = Mock(return_value=True)
        char.ultimate_available = Mock(return_value=False)
        task.chars = [char]
        planner = CombatPlanner(task)
        planner.reset(task.chars)

        with patch.object(BaseChar, "click_skill", autospec=True, return_value=True) as skill:
            result = planner.perform_current_char(char)

        skill.assert_called_once_with(char, 0.25, 0, True, 0)
        self.assertTrue(result.success)
        self.assertEqual(result.slot, ActionSlot.SKILL)


class TestSwitchDecisionExecution(_PlannerTestCase):
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

    def test_intro_replan_uses_final_wait_policy_and_only_final_target_expectation(self):
        for skip_switch_waits in (False, True):
            with self.subTest(skip_switch_waits=skip_switch_waits):
                current, original, target = [
                    FakeChar(i, name) for i, name in enumerate(("current", "original", "target"))
                ]
                current._cycle_full = True
                for char in (current, original, target):
                    char.ufn_name = char.name
                planner = self._planner([current, original, target])
                stale = ExpectedEntry(action_name="stale")
                for char in (current, original, target):
                    planner.complete_switch(
                        SwitchDecision(char, "seed expectation", 1, expected_entry=stale)
                    )
                original_decision = SwitchDecision(
                    original,
                    "original target",
                    1,
                    expected_entry=ExpectedEntry(action_name="original_action"),
                )
                expected = ExpectedEntry(action_name="new_action")
                new_decision = SwitchDecision(
                    target,
                    "intro target",
                    1,
                    has_intro=True,
                    expected_entry=expected,
                    skip_switch_waits=skip_switch_waits,
                )
                task = Mock(debug=False, clock=clock, combat_planner=planner)
                task.skip_sleep_checks.return_value = nullcontext(
                    SimpleNamespace(check_combat=False)
                )
                task.is_in_team.return_value = True
                task.scene.health_snapshot.return_value = object()
                task._switch_detection_reason.side_effect = [(None, 0), ("char index fallback", 0)]
                task._decide_switch_to.return_value = new_decision
                task.record_element_reaction.return_value = False
                BaseCombatTask._switch_to_char(
                    task, original_decision, current_char=current, retry_intro=True
                )
                self.assertEqual(task._wait_switch_in_guard.called, not skip_switch_waits)
                task._set_current_char.assert_called_once_with(current, target, True)
                self.assertEqual(planner.state.pending_entry_expectations, {target.index: expected})

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

    def test_failed_switch_clears_target_expectation_without_completing_route(self):
        current, target = FakeChar(0, "current"), FakeChar(1, "target")
        current.ufn_name, target.ufn_name = current.name, target.name
        target.mark_dead = Mock()
        planner = self._planner([current, target])
        planner.complete_switch(
            SwitchDecision(
                target,
                "seed expectation",
                1,
                expected_entry=ExpectedEntry(slot=ActionSlot.ULTIMATE),
            )
        )
        routes = []
        self._publish(
            planner,
            current,
            lambda context: routes.append(
                context.request_route(Planner.Route.action(target, slot=ActionSlot.SKILL))
            ),
        )
        self.assertIn(target.index, planner.state.pending_entry_expectations)
        decision = SwitchDecision(target, "selected target", 1)
        task = Mock(debug=False, clock=clock, combat_planner=planner)
        task.skip_sleep_checks.return_value = nullcontext(SimpleNamespace(check_combat=False))
        task.is_in_team.return_value = True
        task.scene.health_snapshot.return_value = object()
        task._switch_detection_reason.return_value = (None, 0)
        BaseCombatTask._switch_to_char(task, decision, current_char=current, time_out=0)
        self.assertNotIn(target.index, planner.state.pending_entry_expectations)
        self.assertTrue(routes[0].is_pending)
        self.assertFalse(routes[0].is_fulfilled)
        self.assertEqual(target.calls, [])
        target.mark_dead.assert_called_once()
        task._set_current_char.assert_not_called()


if __name__ == "__main__":
    unittest.main()
