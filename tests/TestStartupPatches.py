import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.patches import window_variants_patch as wvp
from src.patches.startup_patches import resolve_runtime_ui_mode


class TestStartupPatches(unittest.TestCase):
    def test_headless_overrides_configured_qt_mode(self):
        config = {"gui": {"type": "qt"}}

        self.assertEqual(resolve_runtime_ui_mode(config, []), "qt")
        self.assertIsNone(resolve_runtime_ui_mode(config, ["--headless"]))
        self.assertIsNone(resolve_runtime_ui_mode(config, ["-h"]))


class TestWindowVariantsPatch(unittest.TestCase):
    def _manager(self, preferred):
        devices = {}
        manager = SimpleNamespace(
            windows_capture_config={
                "title": None,
                "top_hwnd_class": None,
                "capture_method": ["WGC"],
            },
            config={"preferred": preferred},
            device_dict=devices,
        )
        manager._replace_pc_devices = Mock(side_effect=devices.update)
        manager.get_preferred_device = Mock(return_value=None)
        return manager

    @staticmethod
    def _fake_find_hwnd(title, exe, *args, **kwargs):
        names = [str(item).lower() for item in (exe or [])]
        if any("ntecloudgame.exe" in name for name in names):
            return (
                "云·异环",
                6163716,
                r"C:\Games\NTECloud\NTECloudGame.exe",
                0,
                0,
                1600,
                900,
                [(6163716,)],
            )
        return (None, 0, None, 0, 0, 0, 0, [])

    def test_update_pc_device_returns_the_selected_variant_imei(self):
        manager = self._manager("pc_cloud")

        with (
            patch("ok.util.window.find_hwnd", side_effect=self._fake_find_hwnd),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            self.assertEqual(
                wvp._update_pc_device_with_variants(manager), "pc_cloud"
            )

        self.assertTrue(manager.device_dict["pc_cloud"]["connected"])
        self.assertFalse(manager.device_dict["pc_local"]["connected"])

    def test_update_pc_device_falls_back_to_connected_variant(self):
        manager = self._manager("")

        with (
            patch("ok.util.window.find_hwnd", side_effect=self._fake_find_hwnd),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertEqual(
                wvp._update_pc_device_with_variants(manager), "pc_cloud"
            )

    def test_sync_launcher_task_name_updates_the_hidden_start_game_task(self):
        class LauncherTask:
            name = "Start Game"
            _launcher_display_name = staticmethod(lambda: "准备云游戏窗口")

        task = LauncherTask()
        executor = SimpleNamespace(onetime_tasks=[task])

        with patch("ok.og", SimpleNamespace(executor=executor)):
            wvp._sync_launcher_task_name()

        self.assertEqual(task.name, "准备云游戏窗口")

    def test_temporary_non_variant_capture_config_uses_original_update_pc_device(self):
        manager = SimpleNamespace(
            windows_capture_config={
                "exe": ["NTEGame.exe"],
                "hwnd_class": "Qt51517QWindowOwnDC",
                "title": None,
                "top_hwnd_class": ["Qt51517QWindowToolSaveBitsOwnDC"],
                "capture_method": ["WGC"],
            },
            device_dict={},
            config={"preferred": ""},
        )
        original = Mock(return_value="pc_launcher")

        with (
            patch.object(wvp, "_ORIGINAL_UPDATE_PC_DEVICE", original),
            patch("ok.util.window.find_hwnd") as find_hwnd,
        ):
            result = wvp._update_pc_device_with_variants(manager)

        self.assertEqual(result, "pc_launcher")
        original.assert_called_once_with(manager)
        find_hwnd.assert_not_called()

    def test_set_preferred_device_keeps_a_temporary_launcher_capture_config(self):
        device = {"imei": "pc_launcher", "full_path": r"C:\Games\NTEGame.exe"}
        manager = SimpleNamespace(
            device_dict={"pc_launcher": device},
            config={"preferred": "pc_launcher"},
            windows_capture_config={
                "exe": ["NTEGame.exe"],
                "hwnd_class": "Qt51517QWindowOwnDC",
            },
        )
        manager.get_preferred_device = Mock(return_value=device)
        original = Mock(return_value=True)

        with patch.object(wvp, "_apply_variant_settings") as apply:
            wrapped = wvp._wrap_set_preferred_device(original)
            self.assertTrue(wrapped(manager, "pc_launcher"))

        apply.assert_not_called()
        original.assert_called_once_with(manager, imei="pc_launcher", index=-1)

    def test_set_preferred_device_applies_variant_settings_and_remembers_choice(self):
        device = {"imei": "pc_cloud", "window_variant": "pc_cloud"}
        manager = SimpleNamespace(
            device_dict={"pc_cloud": device},
            config={"preferred": "pc_cloud"},
            windows_capture_config={
                "exe": ["NTECloudGame.exe"],
                "hwnd_class": ["Qt51517QWindowIcon"],
            },
        )
        manager.get_preferred_device = Mock(return_value=device)
        original = Mock(return_value=True)

        with (
            patch.object(wvp, "_apply_variant_settings") as apply,
            patch.object(wvp, "_sync_sound_trigger_process") as sync_sound,
            patch.object(wvp, "_sync_launcher_task_name") as sync_launcher,
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            wrapped = wvp._wrap_set_preferred_device(original)
            self.assertTrue(wrapped(manager, "pc_cloud"))
            self.assertEqual(wvp._LAST_PREFERRED_VARIANT, "pc_cloud")

        apply.assert_called_once_with(manager, device)
        sync_sound.assert_called_once()
        sync_launcher.assert_called_once()

    def test_set_preferred_device_does_not_re_sync_the_same_variant(self):
        device = {"imei": "pc_cloud", "window_variant": "pc_cloud"}
        manager = SimpleNamespace(
            device_dict={"pc_cloud": device},
            config={"preferred": "pc_cloud"},
            windows_capture_config={
                "exe": ["NTECloudGame.exe"],
                "hwnd_class": ["Qt51517QWindowIcon"],
            },
        )
        manager.get_preferred_device = Mock(return_value=device)
        original = Mock(return_value=True)

        with (
            patch.object(wvp, "_apply_variant_settings"),
            patch.object(wvp, "_sync_sound_trigger_process") as sync_sound,
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            wrapped = wvp._wrap_set_preferred_device(original)
            self.assertTrue(wrapped(manager, "pc_cloud"))

        sync_sound.assert_not_called()

    @staticmethod
    def _cloud_check_manager(preferred, device=None):
        manager = SimpleNamespace(device_dict={}, config={"preferred": preferred})
        manager.get_preferred_device = Mock(return_value=device)
        return manager

    def test_is_cloud_selected_uses_persisted_preference_before_first_refresh(self):
        manager = self._cloud_check_manager("pc_cloud")

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertTrue(wvp.is_cloud_window_selected())

    def test_is_cloud_selected_uses_last_choice_when_config_was_cleared(self):
        manager = self._cloud_check_manager("")

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            self.assertTrue(wvp.is_cloud_window_selected())

    def test_is_cloud_selected_is_false_for_a_local_preference(self):
        manager = self._cloud_check_manager("pc_local")

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertFalse(wvp.is_cloud_window_selected())

    def test_is_cloud_selected_accepts_a_cloud_path_from_an_unpatched_manager(self):
        device = {"full_path": r"C:\Games\NTECloud\NTECloudGame.exe"}
        manager = self._cloud_check_manager("", device=device)

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertTrue(wvp.is_cloud_window_selected())

    def test_is_cloud_selected_rejects_a_launcher_device(self):
        device = {"full_path": r"C:\Games\NTEGame.exe"}
        manager = self._cloud_check_manager("pc_launcher", device=device)

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            self.assertFalse(wvp.is_cloud_window_selected())

    def test_clear_devices_does_not_switch_back_to_the_local_capture_target(self):
        # ensure_capture() calls clear_devices(), which wipes config['preferred'].
        manager = self._manager("")

        with (
            patch("ok.util.window.find_hwnd", side_effect=self._fake_find_hwnd),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            wvp._update_pc_device_with_variants(manager)

        self.assertEqual(
            manager.windows_capture_config["exe"], ["NTECloudGame.exe"]
        )
        self.assertEqual(
            manager.windows_capture_config["hwnd_class"], ["Qt51517QWindowIcon"]
        )
        self.assertEqual(manager.windows_capture_config["capture_method"], ["WGC"])


if __name__ == "__main__":
    unittest.main()
