import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.patches import window_variants_patch as wvp
from src.patches.startup_patches import resolve_runtime_ui_mode


class _WritingConfig(dict):
    """Mimics ok.util.config.Config: every direct assignment persists to disk."""

    def __init__(self, preferred):
        super().__init__(preferred=preferred)
        self.writes = 0

    def __setitem__(self, key, value):
        self.writes += 1
        super().__setitem__(key, value)


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
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
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
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertEqual(
                wvp._update_pc_device_with_variants(manager), "pc_cloud"
            )

    def test_cloud_variant_is_hidden_until_the_experimental_switch_is_on(self):
        manager = self._manager("pc_cloud")

        with (
            patch("ok.util.window.find_hwnd", side_effect=self._fake_find_hwnd),
            patch.object(wvp, "cloud_experimental_enabled", return_value=False),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            result = wvp._update_pc_device_with_variants(manager)
            self.assertEqual(wvp._LAST_PREFERRED_VARIANT, "pc_local")

        self.assertEqual(result, "pc_local")
        self.assertNotIn("pc_cloud", manager.device_dict)
        # The background refresh never owns the config write. The GUI writes
        # the fallback when the switch is turned off (see the validator tests).
        self.assertEqual(manager.config["preferred"], "pc_cloud")
        self.assertEqual(
            manager.windows_capture_config["exe"],
            list(wvp.WINDOW_VARIANTS[0]["exe"]),
        )

    def test_refresh_thread_never_writes_config_when_switch_is_off(self):
        manager = self._manager("pc_cloud")
        config = _WritingConfig("pc_cloud")
        manager.config = config

        with (
            patch("ok.util.window.find_hwnd", side_effect=self._fake_find_hwnd),
            patch.object(wvp, "cloud_experimental_enabled", return_value=False),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            self.assertEqual(wvp._update_pc_device_with_variants(manager), "pc_local")

        self.assertEqual(config["preferred"], "pc_cloud")
        self.assertEqual(config.writes, 0)

    def test_refresh_thread_does_not_save_a_real_config_object(self):
        from ok.util.config import Config

        manager = self._manager("pc_cloud")
        with patch(
            "ok.util.config.read_json_file",
            return_value={"preferred": "pc_cloud"},
        ):
            config = Config("devices", {"preferred": ""})
        manager.config = config

        with (
            patch("ok.util.window.find_hwnd", side_effect=self._fake_find_hwnd),
            patch.object(wvp, "cloud_experimental_enabled", return_value=False),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
            patch.object(Config, "save_file") as save,
        ):
            self.assertEqual(wvp._update_pc_device_with_variants(manager), "pc_local")

        self.assertEqual(config["preferred"], "pc_cloud")
        save.assert_not_called()

    def test_cloud_experimental_switch_defaults_off_without_global_config(self):
        with patch("ok.og", SimpleNamespace()):
            self.assertFalse(wvp.cloud_experimental_enabled())

    def test_cloud_experimental_switch_reads_global_config(self):
        global_config = SimpleNamespace(
            get_config=Mock(
                return_value={wvp.CLOUD_EXPERIMENTAL_ENABLE_KEY: True}
            )
        )
        with patch("ok.og", SimpleNamespace(global_config=global_config)):
            self.assertTrue(wvp.cloud_experimental_enabled())
        global_config.get_config.assert_called_once_with(
            wvp.CLOUD_EXPERIMENTAL_CONFIG_NAME
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
            patch.object(wvp, "_sync_launcher_task_name") as sync_launcher,
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            wrapped = wvp._wrap_set_preferred_device(original)
            self.assertTrue(wrapped(manager, "pc_cloud"))
            self.assertEqual(wvp._LAST_PREFERRED_VARIANT, "pc_cloud")

        apply.assert_called_once_with(manager, device)
        sync_launcher.assert_called_once()
        # No audio sync event: the listener resolves the variant by itself.
        self.assertFalse(hasattr(wvp, "_sync_sound_trigger_process"))

    def test_set_preferred_device_does_not_re_sync_launcher_for_the_same_variant(self):
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
            patch.object(wvp, "_sync_launcher_task_name") as sync_launcher,
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            wrapped = wvp._wrap_set_preferred_device(original)
            self.assertTrue(wrapped(manager, "pc_cloud"))

        sync_launcher.assert_not_called()

    @staticmethod
    def _cloud_check_manager(preferred, device=None):
        manager = SimpleNamespace(device_dict={}, config={"preferred": preferred})
        manager.get_preferred_device = Mock(return_value=device)
        return manager

    def test_is_cloud_selected_uses_persisted_preference_before_first_refresh(self):
        manager = self._cloud_check_manager("pc_cloud")

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertTrue(wvp.is_cloud_window_selected())

    def test_is_cloud_selected_uses_last_choice_when_config_was_cleared(self):
        manager = self._cloud_check_manager("")

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            self.assertTrue(wvp.is_cloud_window_selected())

    def test_is_cloud_selected_is_false_for_a_local_preference(self):
        manager = self._cloud_check_manager("pc_local")

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertFalse(wvp.is_cloud_window_selected())

    def test_is_cloud_selected_accepts_a_cloud_path_from_an_unpatched_manager(self):
        device = {"full_path": r"C:\Games\NTECloud\NTECloudGame.exe"}
        manager = self._cloud_check_manager("", device=device)

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertTrue(wvp.is_cloud_window_selected())

    def test_is_cloud_selected_rejects_a_launcher_device(self):
        device = {"full_path": r"C:\Games\NTEGame.exe"}
        manager = self._cloud_check_manager("pc_launcher", device=device)

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            self.assertFalse(wvp.is_cloud_window_selected())

    def test_is_cloud_selected_false_when_switch_is_off(self):
        device = {"full_path": r"C:\Games\NTECloud\NTECloudGame.exe"}
        manager = self._cloud_check_manager("pc_cloud", device=device)

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=False),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            self.assertFalse(wvp.is_cloud_window_selected())

    def test_get_selected_variant_id_clamps_to_local_when_switch_is_off(self):
        with (
            patch("ok.og", SimpleNamespace()),
            patch.object(wvp, "cloud_experimental_enabled", return_value=False),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            self.assertEqual(wvp.get_selected_variant_id(), "pc_local")

    def test_restart_first_frame_clamps_a_cloud_preference(self):
        manager = SimpleNamespace(device_dict={}, config={"preferred": "pc_cloud"})
        manager.get_preferred_device = Mock(return_value=None)

        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=False),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertFalse(wvp.is_cloud_window_selected())
            self.assertEqual(wvp.get_selected_variant_id(), "pc_local")
            self.assertEqual(
                wvp.resolve_audio_capture_spec().factory_process_name,
                "HTGame.exe",
            )

    def test_apply_variant_settings_uses_local_when_switch_is_off(self):
        manager = SimpleNamespace(
            windows_capture_config={
                "exe": ["NTECloudGame.exe"],
                "hwnd_class": ["Qt51517QWindowIcon"],
                "capture_method": ["WGC"],
            }
        )
        device = {"window_variant": "pc_cloud"}

        with patch.object(wvp, "cloud_experimental_enabled", return_value=False):
            wvp._apply_variant_settings(manager, device)

        self.assertEqual(manager.windows_capture_config["exe"], ["HTGame.exe"])
        self.assertEqual(
            manager.windows_capture_config["hwnd_class"], ["UnrealWindow"]
        )

    def test_clear_devices_does_not_switch_back_to_the_local_capture_target(self):
        # ensure_capture() calls clear_devices(), which wipes config['preferred'].
        manager = self._manager("")

        with (
            patch("ok.util.window.find_hwnd", side_effect=self._fake_find_hwnd),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
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


class TestExperimentalSwitchValidator(unittest.TestCase):
    """The switch persists itself through Config and applies changes on the GUI thread."""

    def setUp(self):
        self.manager = SimpleNamespace(
            config=_WritingConfig("pc_cloud"),
            refresh=Mock(),
        )

    def test_config_load_before_device_manager_has_no_side_effect(self):
        with (
            patch("ok.og", SimpleNamespace()),
            patch.object(wvp, "_persist_local_preference") as persist,
            patch.object(wvp, "_schedule_device_refresh") as refresh,
        ):
            self.assertEqual(
                wvp.on_experimental_switch_changed(
                    wvp.CLOUD_EXPERIMENTAL_ENABLE_KEY, False
                ),
                (True, None),
            )
        persist.assert_not_called()
        refresh.assert_not_called()

    def test_fresh_install_first_toggle_still_refreshes_the_window_list(self):
        # A new config file is created by reset_to_default(), which does not
        # call the validator. The very first validator call must still work.
        with (
            patch("ok.og", SimpleNamespace(device_manager=self.manager)),
            patch.object(wvp, "_persist_local_preference") as persist,
            patch.object(wvp, "_schedule_device_refresh") as refresh,
        ):
            self.assertEqual(
                wvp.on_experimental_switch_changed(
                    wvp.CLOUD_EXPERIMENTAL_ENABLE_KEY, True
                ),
                (True, None),
            )
        persist.assert_not_called()
        refresh.assert_called_once_with(self.manager)

    def test_turning_switch_off_persists_local_and_refreshes(self):
        with (
            patch("ok.og", SimpleNamespace(device_manager=self.manager)),
            patch.object(wvp, "_persist_local_preference") as persist,
            patch.object(wvp, "_schedule_device_refresh") as refresh,
        ):
            self.assertEqual(
                wvp.on_experimental_switch_changed(
                    wvp.CLOUD_EXPERIMENTAL_ENABLE_KEY, False
                ),
                (True, None),
            )
        persist.assert_called_once_with(self.manager)
        refresh.assert_called_once_with(self.manager)

    def test_turning_switch_on_only_refreshes_the_window_list(self):
        with (
            patch("ok.og", SimpleNamespace(device_manager=self.manager)),
            patch.object(wvp, "_persist_local_preference") as persist,
            patch.object(wvp, "_schedule_device_refresh") as refresh,
        ):
            wvp.on_experimental_switch_changed(
                wvp.CLOUD_EXPERIMENTAL_ENABLE_KEY, True
            )
        persist.assert_not_called()
        refresh.assert_called_once_with(self.manager)

    def test_unrelated_key_is_ignored(self):
        with patch("ok.og", SimpleNamespace(device_manager=self.manager)):
            self.assertEqual(
                wvp.on_experimental_switch_changed("some other key", False),
                (True, None),
            )

    def test_failing_side_effect_never_blocks_the_setting(self):
        with (
            patch("ok.og", SimpleNamespace(device_manager=self.manager)),
            patch.object(
                wvp, "_persist_local_preference", side_effect=RuntimeError("disk")
            ),
        ):
            self.assertEqual(
                wvp.on_experimental_switch_changed(
                    wvp.CLOUD_EXPERIMENTAL_ENABLE_KEY, False
                ),
                (True, None),
            )

    def test_reset_to_default_runs_the_same_reconcile(self):
        class _ResettableConfig:
            def __init__(self):
                self.reset_calls = 0

            def reset_to_default(self):
                self.reset_calls += 1

        config = _ResettableConfig()
        with patch.object(wvp, "_reconcile_cloud_experimental_state") as reconcile:
            wvp._patch_cloud_experimental_reset(config)
            config.reset_to_default()

        self.assertEqual(config.reset_calls, 1)
        reconcile.assert_called_once_with(persist=True, force_refresh=True)

    def test_reconcile_off_persists_local_and_refreshes(self):
        manager = SimpleNamespace(config=_WritingConfig("pc_cloud"), refresh=Mock())
        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=False),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
            patch.object(wvp, "_schedule_device_refresh") as refresh,
        ):
            wvp._reconcile_cloud_experimental_state(persist=True)
            self.assertEqual(wvp._LAST_PREFERRED_VARIANT, "pc_local")

        self.assertEqual(manager.config["preferred"], "pc_local")
        refresh.assert_called_once_with(manager)

    def test_reconcile_on_does_not_write_or_refresh(self):
        manager = SimpleNamespace(config=_WritingConfig("pc_cloud"), refresh=Mock())
        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_schedule_device_refresh") as refresh,
        ):
            wvp._reconcile_cloud_experimental_state(persist=True)

        self.assertEqual(manager.config["preferred"], "pc_cloud")
        refresh.assert_not_called()

    def test_reconcile_without_a_change_does_not_refresh(self):
        manager = SimpleNamespace(config=_WritingConfig("pc_local"), refresh=Mock())
        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=False),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
            patch.object(wvp, "_schedule_device_refresh") as refresh,
        ):
            self.assertFalse(
                wvp._reconcile_cloud_experimental_state(persist=True)
            )
        refresh.assert_not_called()

    def test_headless_do_start_reconciles_without_scheduling_another_refresh(self):
        original = Mock(return_value="started")
        wrapped = wvp._wrap_do_start(original)
        with (
            patch("PySide6.QtWidgets.QApplication.instance", return_value=None),
            patch.object(wvp, "_reconcile_cloud_experimental_state") as reconcile,
        ):
            self.assertEqual(wrapped(object()), "started")

        original.assert_called_once()
        reconcile.assert_called_once_with(persist=True, allow_refresh=False)

    def test_gui_do_start_leaves_reconcile_to_start_success(self):
        original = Mock(return_value="started")
        wrapped = wvp._wrap_do_start(original)
        with (
            patch("PySide6.QtWidgets.QApplication.instance", return_value=Mock()),
            patch.object(wvp, "_reconcile_cloud_experimental_state") as reconcile,
        ):
            self.assertEqual(wrapped(object()), "started")

        reconcile.assert_not_called()

    def test_set_preferred_device_ignores_a_stale_cloud_row_when_switch_is_off(self):
        cloud = {"imei": "pc_cloud", "window_variant": "pc_cloud"}
        local = {"imei": "pc_local", "window_variant": "pc_local"}
        manager = SimpleNamespace(
            device_dict={"pc_local": local, "pc_cloud": cloud},
            config=_WritingConfig(""),
            windows_capture_config={},
        )
        manager.get_devices = Mock(return_value=[local, cloud])
        manager.get_preferred_device = Mock(return_value=cloud)
        original = Mock(return_value=True)

        with (
            patch.object(wvp, "cloud_experimental_enabled", return_value=False),
            patch.object(wvp, "_apply_variant_settings") as apply,
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            wrapped = wvp._wrap_set_preferred_device(original)
            self.assertTrue(wrapped(manager, "pc_cloud"))
            self.assertEqual(wvp._LAST_PREFERRED_VARIANT, "pc_local")

        apply.assert_called_once_with(manager, local)
        original.assert_called_once_with(manager, imei="pc_local", index=-1)

    def test_start_success_handler_reconciles_once(self):
        with (
            patch.object(wvp, "_cloud_experimental_config") as config,
            patch.object(wvp, "_reconcile_cloud_experimental_state") as reconcile,
        ):
            wvp._on_start_success()

        config.assert_called_once_with()
        reconcile.assert_called_once_with(persist=True)

    def test_persist_local_preference_writes_once_through_config(self):
        with patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"):
            wvp._persist_local_preference(self.manager)
            self.assertEqual(wvp._LAST_PREFERRED_VARIANT, "pc_local")
            wvp._persist_local_preference(self.manager)

        self.assertEqual(self.manager.config["preferred"], "pc_local")
        self.assertEqual(self.manager.config.writes, 1)

    def test_persist_local_preference_leaves_a_local_choice_untouched(self):
        self.manager.config = _WritingConfig("pc_local")
        with patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_local"):
            wvp._persist_local_preference(self.manager)
        self.assertEqual(self.manager.config.writes, 0)

    def test_switch_change_refreshes_the_window_list_on_the_next_tick(self):
        with (
            patch("PySide6.QtWidgets.QApplication.instance", return_value=Mock()),
            patch("PySide6.QtCore.QTimer.singleShot") as single_shot,
        ):
            wvp._schedule_device_refresh(self.manager)
        single_shot.assert_called_once_with(0, self.manager.refresh)
        self.manager.refresh.assert_not_called()

    def test_headless_switch_change_posts_the_refresh_directly(self):
        with patch("PySide6.QtWidgets.QApplication.instance", return_value=None):
            wvp._schedule_device_refresh(self.manager)
        self.manager.refresh.assert_called_once_with()

    def test_switch_off_persists_the_fallback_and_audio_follows_it(self):
        from ok.util.config import Config

        manager = SimpleNamespace(config=None, refresh=Mock())
        with patch(
            "ok.util.config.read_json_file",
            return_value={"preferred": "pc_cloud"},
        ):
            manager.config = Config("devices", {"preferred": ""})
        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
            patch.object(Config, "save_file") as save,
        ):
            wvp._persist_local_preference(manager)
            self.assertEqual(wvp.get_selected_variant_id(), "pc_local")
        self.assertEqual(manager.config["preferred"], "pc_local")
        save.assert_called_once_with()

    def test_persisted_cloud_choice_survives_a_restart_with_the_switch_on(self):
        manager = SimpleNamespace(config={"preferred": "pc_cloud"})
        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertEqual(wvp.get_selected_variant_id(), "pc_cloud")

    def test_gui_switch_write_persists_both_states_and_refreshes(self):
        from ok.util.config import Config

        from src import config as app_config

        key = app_config.CLOUD_EXPERIMENTAL_ENABLE_KEY
        manager = SimpleNamespace(config=_WritingConfig("pc_cloud"), refresh=Mock())
        with (
            patch("ok.util.config.read_json_file", return_value={key: True}),
            patch("ok.og", SimpleNamespace()),
        ):
            switch = Config(
                app_config.CLOUD_EXPERIMENTAL_CONFIG_NAME,
                {key: False},
                validator=app_config._cloud_experimental_validator,
            )
        with (
            patch("ok.og", SimpleNamespace(device_manager=manager)),
            patch.object(Config, "save_file") as save,
            patch("PySide6.QtWidgets.QApplication.instance", return_value=None),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            switch[key] = False

        self.assertFalse(switch[key])
        self.assertEqual(manager.config["preferred"], "pc_local")
        self.assertEqual(manager.config.writes, 1)
        manager.refresh.assert_called_once_with()
        save.assert_called_once_with()

    def test_switch_value_is_persisted_through_the_real_config_class(self):
        from ok.util.config import Config

        from src import config as app_config

        key = app_config.CLOUD_EXPERIMENTAL_ENABLE_KEY
        with patch("ok.util.config.read_json_file", return_value={key: False}):
            config = Config(
                app_config.CLOUD_EXPERIMENTAL_CONFIG_NAME,
                {key: False},
                validator=app_config._cloud_experimental_validator,
            )
        with (
            patch("ok.og", SimpleNamespace()),
            patch.object(Config, "save_file") as save,
        ):
            config[key] = True
        self.assertTrue(config[key])
        save.assert_called_once_with()

    def test_config_option_uses_the_change_validator(self):
        from src import config as app_config

        self.assertIs(
            app_config.cloud_experimental_config_option.validator,
            app_config._cloud_experimental_validator,
        )
        self.assertIn(
            app_config.cloud_experimental_config_option,
            app_config.config["global_configs"],
        )
        with patch("ok.og", SimpleNamespace()):
            self.assertEqual(
                app_config._cloud_experimental_validator(
                    app_config.CLOUD_EXPERIMENTAL_ENABLE_KEY, False
                ),
                (True, None),
            )


if __name__ == "__main__":
    unittest.main()
