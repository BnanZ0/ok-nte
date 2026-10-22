import unittest
from itertools import count
from unittest.mock import patch

from src.tasks.FishingTask import FishingSession, FishingTask
from src.tasks.flow.scene_flow import SceneFlow, SceneReplan
from src.tasks.mixin.RoundMixin import RoundState


class TestFishingSceneFlow(unittest.TestCase):
    def _task(self):
        task = object.__new__(FishingTask)
        task._fishing_session = FishingSession()
        task._round_state = RoundState()
        task.config = {}
        task._set_stage = lambda _stage: None
        task.log_info = lambda _message: None
        task.log_warning = lambda _message: None
        task.log_round_info = lambda _message, **_kwargs: None
        task.info_set = lambda _key, _value: None
        task.info_get = lambda _key: None
        task.sleep = lambda _seconds: None
        return task

    def _configured_task(self, state, events):
        task = self._task()
        task.scene_flow = SceneFlow()
        task.is_in_team = lambda: state["scene"] == "team"
        task.is_ready_to_cast = lambda: state["scene"] == "ready"
        task.is_waiting_bite = lambda: state["scene"] == "bite"
        task.is_playing_fish = lambda: state["scene"] == "control"
        task.has_success_overlay = lambda: state["scene"] == "result"
        task.is_sell_menu = lambda: state["scene"] == "sell"
        task.is_fish_hold = lambda: state["scene"] == "hold"
        task.is_bait_shop = lambda: state["scene"] == "shop"
        task._enter_fishing_from_interaction = lambda: events.append("enter")
        task._cast = lambda: events.append("cast")
        task._open_sell_menu = lambda: events.append("open_sell")
        task._sell = lambda: events.append("sell")
        task._close_fish_hold = lambda: events.append("close_hold")
        task._open_bait_menu = lambda: events.append("open_bait")
        task._buy_bait = lambda: events.append("buy_bait")
        task._confirm_bait = lambda: events.append("confirm_bait")
        task._wait_bite = lambda: events.append("wait_bite")
        task._control = lambda: events.append("control")
        task._collect_result = lambda: events.append("result")

        def send_key(key, **_kwargs):
            events.append(key)
            if key == "esc":
                state["scene"] = "ready"
            return True

        task.send_key = send_key
        task._recover_fishing_scene = lambda: events.append("recover")
        task._configure_scene_flow()
        return task

    def _run_without_waiting(self, task, until, start):
        clock = count()
        with patch("src.utils.clock._read_monotonic", side_effect=lambda: next(clock)):
            return task.scene_flow.run(until, start=start, poll_interval=0)

    def test_four_casts_route_to_restock_when_enabled(self):
        state = {"scene": "ready"}
        events = []
        task = self._configured_task(state, events)
        task.config = {task.CONF_AUTO_BUY_BAIT: True}
        task._open_sell_menu = lambda: events.append("open_sell")
        task.scene_flow = SceneFlow()
        task._configure_scene_flow()

        self.assertTrue(
            self._run_without_waiting(
                task,
                lambda: events == ["cast", "cast", "cast", "cast", "open_sell"],
                task.FishingStep.CAST,
            )
        )

    def test_four_casts_fail_the_round_when_restock_is_disabled(self):
        state = {"scene": "ready"}
        events = []
        failures = []
        task = self._configured_task(state, events)
        task.config = {task.CONF_AUTO_BUY_BAIT: False}
        task._capture_cast_failure_info = lambda: events.append("capture")
        task.add_failed = lambda reason: failures.append(reason)

        self.assertTrue(
            self._run_without_waiting(
                task,
                lambda: bool(failures),
                task.FishingStep.CAST,
            )
        )
        self.assertEqual(events[:5], ["cast", "cast", "cast", "cast", "capture"])
        self.assertEqual(failures, ["未检测到进入抛竿状态"])

    def test_team_guard_returns_to_the_pending_restock_step(self):
        state = {"scene": "team"}
        events = []
        task = self._configured_task(state, events)

        def enter():
            events.append("enter")
            state["scene"] = "ready"

        def open_bait():
            events.append("open_bait")
            state["done"] = True

        task._enter_fishing_from_interaction = enter
        task._open_bait_menu = open_bait
        task.scene_flow = SceneFlow()
        task._configure_scene_flow()

        self.assertTrue(
            self._run_without_waiting(
                task,
                lambda: state.get("done", False),
                task.FishingStep.OPEN_BAIT,
            )
        )
        self.assertEqual(events, ["enter", "open_bait"])

    def test_transition_leaves_a_completed_panel_without_replaying_its_action(self):
        state = {"scene": "hold"}
        events = []
        task = self._configured_task(state, events)

        def open_bait():
            events.append("open_bait")
            state["done"] = True

        task._open_bait_menu = open_bait
        task.scene_flow = SceneFlow()
        task._configure_scene_flow()

        self.assertTrue(
            self._run_without_waiting(
                task,
                lambda: state.get("done", False),
                task.FishingStep.FISH_HOLD,
            )
        )
        self.assertEqual(events, ["close_hold", "esc", "open_bait"])

    def test_control_interrupt_keeps_result_association_and_missing_result_fails_the_round(self):
        task = self._task()
        task._round_state = RoundState(index=3)
        task.control_until_finish = lambda: (_ for _ in ()).throw(SceneReplan())

        with self.assertRaises(SceneReplan):
            task._control()

        self.assertEqual(task._fishing_session.interrupted_control_round, 3)
        task._fishing_session.awaiting_result_round = 3
        failures = []
        task.add_failed = lambda reason: failures.append(reason)
        task._record_missing_result_before_next_control(task._fishing_session)
        self.assertEqual(failures, ["下一轮控条前未检测到成功面板"])


if __name__ == "__main__":
    unittest.main()
