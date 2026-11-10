import unittest
from unittest.mock import Mock, patch

from src.patches import admin_restart_patch as admin_restart


class TestAdminRestartPatch(unittest.TestCase):
    def test_elevation_succeeded_accepts_a_launched_instance(self):
        self.assertTrue(admin_restart.elevation_succeeded(33))
        self.assertTrue(admin_restart.elevation_succeeded(42))

    def test_elevation_succeeded_rejects_cancel_and_errors(self):
        # ERROR_CANCELLED is greater than 32, so it must be checked explicitly.
        self.assertFalse(admin_restart.elevation_succeeded(1223))
        self.assertFalse(admin_restart.elevation_succeeded(32))
        self.assertFalse(admin_restart.elevation_succeeded(0))
        self.assertFalse(admin_restart.elevation_succeeded(5))

    def test_launch_elevated_quotes_arguments_and_passes_cwd(self):
        with patch.object(admin_restart, "_shell_execute", return_value=42) as execute:
            result = admin_restart.launch_elevated(
                argv=["main_debug.py", "--profile", "a b"],
                executable=r"C:\Python 3.12\python.exe",
                cwd=r"C:\repo\ok-nte",
            )

        self.assertEqual(result, 42)
        execute.assert_called_once_with(
            r"C:\Python 3.12\python.exe",
            'main_debug.py --profile "a b"',
            r"C:\repo\ok-nte",
            1,
        )

    def test_finish_restart_quits_only_after_the_elevated_instance_starts(self):
        app = Mock()
        show_error = Mock()

        self.assertTrue(admin_restart.finish_restart(app, 42, show_error))
        app.quit.assert_called_once()
        show_error.assert_not_called()

    def test_finish_restart_keeps_the_app_alive_when_uac_is_cancelled(self):
        app = Mock()
        show_error = Mock()

        self.assertFalse(admin_restart.finish_restart(app, 1223, show_error))
        app.quit.assert_not_called()
        show_error.assert_called_once()

    def test_finish_restart_keeps_the_app_alive_when_launch_fails(self):
        app = Mock()
        show_error = Mock()

        self.assertFalse(admin_restart.finish_restart(app, 0, show_error))
        app.quit.assert_not_called()
        show_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
