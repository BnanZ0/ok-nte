import unittest
from unittest.mock import Mock, patch

from ok import TaskDisabledException

from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.utils import clock as runtime_clock


class _BaseTask:
    def run(self, *args, **kwargs):
        self.base_run_called = True


class _OneTimeTask(NTEOneTimeTask, _BaseTask):
    def __init__(self, game_capture_ready, connected):
        self.scene = Mock()
        self.scene.game_capture_ready.return_value = game_capture_ready
        self.executor = Mock()
        self.executor.connected.return_value = connected
        self.executor.interaction = None
        self.executor.device_manager = Mock()
        self.executor.device_manager.capture_method = None
        self.log_warning = Mock()
        self.log_info = Mock()
        self.log_error = Mock()
        self.disable = Mock()
        self.clock = runtime_clock
        self.sleep = Mock()
        self.set_check_monthly_card = Mock()
        self.base_run_called = False


class TestNTEOneTimeTask(unittest.TestCase):
    def setUp(self):
        # The existing cases describe the local variant. Keep the cloud branch
        # out of them so their original assertions stay exact.
        local_only = patch(
            "src.tasks.NTEOneTimeTask._cloud_window_selected", return_value=False
        )
        local_only.start()
        self.addCleanup(local_only.stop)
        # Cloud capture tests assume the client window is already open; the
        # manual-launch guard is covered separately in TestCloudLaunchGuard.
        window_present = patch(
            "src.tasks.NTEOneTimeTask._cloud_launch_blocked", return_value=False
        )
        window_present.start()
        self.addCleanup(window_present.stop)

    def test_skips_task_when_game_capture_is_not_ready(self):
        task = _OneTimeTask(game_capture_ready=False, connected=True)

        with self.assertRaisesRegex(TaskDisabledException, "Game capture is not ready"):
            task.run()

        task.log_warning.assert_called_once()
        # TaskExecutor only `continue`s on TaskDisabledException and re-selects
        # any still-enabled task, so the local branch must disable itself too.
        task.disable.assert_called_once()
        self.assertFalse(task.base_run_called)

    def test_skips_task_when_game_capture_connection_is_lost(self):
        task = _OneTimeTask(game_capture_ready=True, connected=False)

        with self.assertRaisesRegex(TaskDisabledException, "Game capture is not ready"):
            task.run()

        self.assertFalse(task.base_run_called)
        task.disable.assert_called_once()

    def test_runs_when_game_capture_is_ready_and_connected(self):
        task = _OneTimeTask(game_capture_ready=True, connected=True)

        task.run()

        self.assertTrue(task.base_run_called)
        task.set_check_monthly_card.assert_called_once()

    def _cloud_task(self):
        task = _OneTimeTask(game_capture_ready=False, connected=False)
        now = [1000.0]

        def fake_sleep(seconds):
            now[0] += seconds

        task.sleep = Mock(side_effect=fake_sleep)
        return task, now

    def test_cloud_waits_until_capture_is_ready(self):
        task, now = self._cloud_task()
        task.scene.game_capture_ready.side_effect = lambda: now[0] >= 1001.0
        task.executor.connected.side_effect = lambda: now[0] >= 1001.0

        with (
            patch("src.tasks.NTEOneTimeTask._cloud_window_selected", return_value=True),
            patch("src.utils.clock._read_monotonic", side_effect=lambda: now[0]),
        ):
            task.run()

        self.assertTrue(task.base_run_called)
        task.set_check_monthly_card.assert_called_once()
        task.disable.assert_not_called()

    def test_cloud_restores_capture_ready_after_capture_comes_up(self):
        task, now = self._cloud_task()
        task.scene.game_capture_ready.return_value = False
        task.executor.connected.side_effect = lambda: now[0] >= 1001.0

        with (
            patch("src.tasks.NTEOneTimeTask._cloud_window_selected", return_value=True),
            patch("src.utils.clock._read_monotonic", side_effect=lambda: now[0]),
        ):
            task.run()

        task.scene.set_game_capture_ready.assert_any_call(True)
        task.disable.assert_not_called()
        self.assertTrue(task.base_run_called)
        task.set_check_monthly_card.assert_called_once()

    def test_cloud_timeout_disables_task_to_stop_retry_loop(self):
        task, now = self._cloud_task()
        task.scene.game_capture_ready.return_value = False
        task.executor.connected.return_value = False

        with (
            patch("src.tasks.NTEOneTimeTask._cloud_window_selected", return_value=True),
            patch("src.utils.clock._read_monotonic", side_effect=lambda: now[0]),
        ):
            with self.assertRaisesRegex(TaskDisabledException, "Game capture is not ready"):
                task.run()

        task.disable.assert_called_once()
        task.log_error.assert_called_once()
        self.assertGreater(task.executor.device_manager.use_windows_capture.call_count, 1)
        self.assertFalse(task.base_run_called)
