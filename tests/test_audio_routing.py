import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ok.util.handler import ExitEvent

from src import GAME_EXE
from src.audio import routing as audio_routing
from src.audio.routing import (
    CONF_ENABLE,
    CONF_SVCL_PATH,
    DEFAULT_RENDER_DEVICE,
    _background_audio_routing_validator,
    _BackgroundAudioRouter,
    _current_process_render_output_device,
    _parse_sound_items_csv,
    _RouteRequest,
    audio_route_command,
    discover_output_devices,
)


class AudioRoutingTests(unittest.TestCase):
    def setUp(self):
        self.target_pids = patch.object(audio_routing, "_target_process_ids", return_value={80940})
        self.target_pids.start()
        self.addCleanup(self.target_pids.stop)

    def test_discover_output_devices_uses_sounddevice_playback_devices(self):
        fake_sounddevice = SimpleNamespace(
            query_hostapis=lambda: [
                {"name": "MME"},
                {"name": "Windows WASAPI"},
                {"name": "Windows WDM-KS"},
            ],
            query_devices=lambda: [
                {"name": "Microsoft Sound Mapper - Output", "max_output_channels": 2, "hostapi": 0},
                {"name": "Speakers (Realtek(R) Audio)", "max_output_channels": 2, "hostapi": 1},
                {"name": "Speakers (Realtek(R) Audio)", "max_output_channels": 2, "hostapi": 1},
                {"name": "Microphone (Realtek(R) Audio)", "max_output_channels": 0, "hostapi": 1},
                {"name": "Speakers 1", "max_output_channels": 2, "hostapi": 2},
            ],
        )

        with patch.dict(sys.modules, {"sounddevice": fake_sounddevice}):
            devices = discover_output_devices()

        self.assertEqual(devices, [DEFAULT_RENDER_DEVICE, "Speakers (Realtek(R) Audio)"])

    def test_parse_sound_items_csv_infers_item_fields_from_svcl_stdout(self):
        data = _parse_sound_items_csv(
            "\ufeffCommand-Line Friendly ID,Item ID,Device State,Direction,Process ID\n"
            "NVIDIA Broadcast\\Application\\异环,{0.0.0.00000000}.{app},Active,Render,80940\n"
            "VB-Audio VoiceMeeter VAIO\\Device\\VoiceMeeter Input\\Render,"
            "{0.0.0.00000000}.{render},Active,Render,\n"
        )

        self.assertEqual(data[0]["type"], "Application")
        self.assertEqual(data[0]["name"], "异环")
        self.assertEqual(data[0]["processid"], "80940")
        self.assertEqual(data[1]["type"], "Device")
        self.assertEqual(data[1]["name"], "VoiceMeeter Input")

    def test_export_sound_items_reads_svcl_stdout_csv_without_temp_file(self):
        stdout = (
            "\ufeffCommand-Line Friendly ID,Item ID,Device State,Direction,Process ID\n"
            "NVIDIA Broadcast\\Application\\异环,{0.0.0.00000000}.{app},Active,Render,80940\n"
        ).encode("utf-8-sig")

        with patch.object(audio_routing, "_is_svcl_path", return_value=True):
            with patch.object(
                audio_routing.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout=stdout),
            ) as run:
                data = audio_routing._export_sound_items("svcl.exe")

        self.assertEqual(data[0]["name"], "异环")
        self.assertEqual(
            run.call_args.args[0],
            [
                "svcl.exe",
                "/scomma",
                "",
                "/Columns",
                "Command-LineFriendlyID,ItemID,DeviceState,Direction,ProcessID,Default",
            ],
        )

    def test_current_process_render_output_device_matches_active_target_pid(self):
        data = [
            {
                "commandlinefriendlyid": "NVIDIA Broadcast\\Device\\Speakers\\Render",
                "itemid": "{0.0.0.00000000}.{broadcast}",
                "type": "Device",
                "direction": "Render",
            },
            {
                "commandlinefriendlyid": "Realtek USB Audio\\Device\\喇叭\\Render",
                "itemid": "{0.0.0.00000000}.{realtek}",
                "type": "Device",
                "direction": "Render",
            },
            {
                "commandlinefriendlyid": "NVIDIA Broadcast\\Application\\异环",
                "itemid": "{0.0.0.00000000}.{broadcast}|app",
                "type": "Application",
                "direction": "Render",
                "devicestate": "Active",
                "processid": "80940",
            },
            {
                "commandlinefriendlyid": "Realtek USB Audio\\Application\\异环",
                "itemid": "{0.0.0.00000000}.{realtek}|app",
                "type": "Application",
                "direction": "Render",
                "devicestate": "Active",
                "processid": "12345",
            },
        ]
        device = _current_process_render_output_device(data, GAME_EXE)

        self.assertEqual(device.route_device, "NVIDIA Broadcast\\Device\\Speakers\\Render")

    def test_audio_route_command_targets_game_process(self):
        self.assertEqual(
            audio_route_command("USB Audio\\Device\\Speakers\\Render"),
            [
                "/SetAppDefault",
                "USB Audio\\Device\\Speakers\\Render",
                "0",
                GAME_EXE,
            ],
        )

    def test_audio_route_command_can_restore_default_render_device(self):
        self.assertEqual(
            audio_route_command(DEFAULT_RENDER_DEVICE),
            [
                "/SetAppDefault",
                DEFAULT_RENDER_DEVICE,
                "0",
                GAME_EXE,
            ],
        )

    def test_router_captures_original_app_output_device_before_background_route(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest("Speakers", save_current=True)
        data = [
            {
                "name": "Speakers",
                "commandlinefriendlyid": "Speaker Audio\\Device\\Speakers\\Render",
                "type": "Device",
                "direction": "Render",
                "Device": "Speaker Audio",
            },
            {
                "name": "Headphones",
                "commandlinefriendlyid": "Headphone Audio\\Device\\Headphones\\Render",
                "type": "Device",
                "direction": "Render",
                "Device": "Headphone Audio",
                "default": "Render",
            },
            {
                "name": GAME_EXE,
                "commandlinefriendlyid": "Headphone Audio\\Application\\HTGame.exe",
                "itemid": "\\Device\\HarddiskVolume2\\Game\\HTGame.exe%b1",
                "type": "Application",
                "direction": "Render",
                "devicestate": "Active",
                "processid": "80940",
            },
            {
                "name": GAME_EXE,
                "commandlinefriendlyid": "Speaker Audio\\Application\\HTGame.exe",
                "itemid": "\\Device\\HarddiskVolume2\\Game\\HTGame.exe%b1",
                "type": "Application",
                "direction": "Render",
                "devicestate": "Inactive",
                "processid": "80940",
            },
        ]

        with patch.object(audio_routing, "_export_sound_items", return_value=data):
            with patch.object(
                audio_routing.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ) as run:
                router._run_pending_routes("svcl.exe")

                self.assertEqual(
                    router._original_device, "Headphone Audio\\Device\\Headphones\\Render"
                )
                self.assertEqual(router._requested_device, "Speakers")
                data[1]["default"] = ""
                data.append(
                    {
                        "commandlinefriendlyid": "New Default Audio\\Device\\Speakers\\Render",
                        "default": "Render",
                    }
                )
                data[2]["devicestate"] = "Inactive"
                data[3]["devicestate"] = "Active"
                router._pending_route = _RouteRequest(router._original_device)
                router._run_pending_routes("svcl.exe")

        self.assertEqual(run.call_args.args[0][2], DEFAULT_RENDER_DEVICE)
        self.assertFalse(router._restore_needed)

    def test_router_saves_default_when_current_device_is_background_target(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest("Speakers", save_current=True)
        data = [
            {
                "name": "Speakers",
                "commandlinefriendlyid": "Speaker Audio\\Device\\Speakers\\Render",
                "type": "Device",
                "direction": "Render",
                "Device": "Speaker Audio",
            },
            {
                "name": GAME_EXE,
                "commandlinefriendlyid": "Speaker Audio\\Application\\HTGame.exe",
                "itemid": "\\Device\\HarddiskVolume2\\Game\\HTGame.exe%b1",
                "type": "Application",
                "direction": "Render",
                "devicestate": "Active",
                "processid": "80940",
            },
        ]

        with patch.object(audio_routing, "_export_sound_items", return_value=data):
            with patch.object(
                audio_routing.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ):
                router._run_pending_routes("svcl.exe")

        self.assertEqual(router._original_device, DEFAULT_RENDER_DEVICE)
        self.assertTrue(router._restore_needed)

    def test_router_saves_default_when_current_device_name_matches_background_target_id(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest(
            "VB-Audio VoiceMeeter VAIO\\Device\\VoiceMeeter Input\\Render",
            save_current=True,
        )
        data = [
            {
                "name": "VoiceMeeter Input",
                "commandlinefriendlyid": (
                    "VB-Audio VoiceMeeter VAIO\\Device\\VoiceMeeter Input\\Render"
                ),
                "type": "Device",
                "direction": "Render",
                "Device": "VB-Audio VoiceMeeter VAIO",
            },
            {
                "name": "异环",
                "commandlinefriendlyid": "VB-Audio VoiceMeeter VAIO\\Application\\异环",
                "itemid": "\\Device\\HarddiskVolume2\\Game\\HTGame.exe%b1",
                "type": "Application",
                "direction": "Render",
                "devicestate": "Active",
                "processid": "80940",
            },
        ]

        with patch.object(audio_routing, "_export_sound_items", return_value=data):
            with patch.object(
                audio_routing.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ):
                router._run_pending_routes("svcl.exe")

        self.assertEqual(router._original_device, DEFAULT_RENDER_DEVICE)
        self.assertTrue(router._restore_needed)

    def test_router_matches_sounddevice_names_with_trademark_markers(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest(
            "Speakers (Realtek(R) Audio)",
            save_current=True,
        )
        data = [
            {
                "name": "Speakers",
                "commandlinefriendlyid": "Realtek Audio\\Device\\Speakers\\Render",
                "type": "Device",
                "direction": "Render",
                "Device": "Realtek Audio",
            },
            {
                "name": "异环",
                "commandlinefriendlyid": "Realtek Audio\\Application\\异环",
                "itemid": "\\Device\\HarddiskVolume2\\Game\\HTGame.exe%b1",
                "type": "Application",
                "direction": "Render",
                "devicestate": "Active",
                "processid": "80940",
            },
        ]

        with patch.object(audio_routing, "_export_sound_items", return_value=data):
            with patch.object(
                audio_routing.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ) as run:
                router._run_pending_routes("svcl.exe")

        self.assertEqual(
            run.call_args.args[0],
            [
                "svcl.exe",
                "/SetAppDefault",
                "Realtek Audio\\Device\\Speakers\\Render",
                "0",
                GAME_EXE,
            ],
        )

    def test_router_matches_target_from_global_render_devices_not_process_devices(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest(
            "VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)",
            save_current=True,
        )
        data = [
            {
                "name": "VoiceMeeter Input",
                "commandlinefriendlyid": (
                    "VB-Audio VoiceMeeter VAIO\\Device\\VoiceMeeter Input\\Render"
                ),
                "type": "Device",
                "direction": "Render",
                "Device Name": "VB-Audio VoiceMeeter VAIO",
            },
            {
                "name": "喇叭",
                "commandlinefriendlyid": "Realtek USB Audio\\Device\\喇叭\\Render",
                "type": "Device",
                "direction": "Render",
                "Device Name": "Realtek USB Audio",
            },
            {
                "name": "异环",
                "commandlinefriendlyid": "Realtek USB Audio\\Application\\异环",
                "itemid": "\\Device\\HarddiskVolume2\\Game\\HTGame.exe%b1",
                "type": "Application",
                "direction": "Render",
                "devicestate": "Active",
                "processid": "80940",
            },
        ]

        with patch.object(audio_routing, "_export_sound_items", return_value=data):
            with patch.object(
                audio_routing.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ) as run:
                router._run_pending_routes("svcl.exe")

        self.assertEqual(router._original_device, "Realtek USB Audio\\Device\\喇叭\\Render")
        self.assertTrue(router._restore_needed)
        self.assertEqual(
            run.call_args.args[0],
            [
                "svcl.exe",
                "/SetAppDefault",
                "VB-Audio VoiceMeeter VAIO\\Device\\VoiceMeeter Input\\Render",
                "0",
                GAME_EXE,
            ],
        )

    def test_router_restores_exact_render_endpoint_when_current_controller_has_multiple_outputs(
        self,
    ):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest(
            "VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)",
            save_current=True,
        )
        data = [
            {
                "name": "VoiceMeeter Input",
                "commandlinefriendlyid": (
                    "VB-Audio VoiceMeeter VAIO\\Device\\VoiceMeeter Input\\Render"
                ),
                "itemid": "{0.0.0.00000000}.{be88ae54}",
                "type": "Device",
                "direction": "Render",
                "Device Name": "VB-Audio VoiceMeeter VAIO",
            },
            {
                "name": "Realtek Digital Output",
                "commandlinefriendlyid": (
                    "Realtek USB Audio\\Device\\Realtek Digital Output\\Render"
                ),
                "itemid": "{0.0.0.00000000}.{digital}",
                "type": "Device",
                "direction": "Render",
                "Device Name": "Realtek USB Audio",
            },
            {
                "name": "喇叭",
                "commandlinefriendlyid": "Realtek USB Audio\\Device\\喇叭\\Render",
                "itemid": "{0.0.0.00000000}.{speaker}",
                "type": "Device",
                "direction": "Render",
                "Device Name": "Realtek USB Audio",
            },
            {
                "name": "异环",
                "commandlinefriendlyid": "Realtek USB Audio\\Application\\异环",
                "itemid": (
                    "{0.0.0.00000000}.{speaker}|\\Device\\HarddiskVolume2\\Game\\HTGame.exe%b1"
                ),
                "type": "Application",
                "direction": "Render",
                "devicestate": "Active",
                "processid": "80940",
            },
        ]

        with patch.object(audio_routing, "_export_sound_items", return_value=data):
            with patch.object(
                audio_routing.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ):
                router._run_pending_routes("svcl.exe")

        self.assertEqual(router._original_device, "Realtek USB Audio\\Device\\喇叭\\Render")
        self.assertTrue(router._restore_needed)

    def test_router_skips_background_route_when_original_device_capture_fails(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest(
            "USB Audio\\Device\\Speakers\\Render", save_current=True
        )

        with patch.object(
            audio_routing,
            "_export_sound_items",
            side_effect=RuntimeError("export failed"),
        ):
            with patch.object(audio_routing.subprocess, "run") as run:
                router._run_pending_routes("svcl.exe")

        run.assert_not_called()
        self.assertIsNone(router._original_device)
        self.assertFalse(router._restore_needed)

    def test_router_skips_background_route_when_capture_returns_default_placeholder(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest(
            "USB Audio\\Device\\Speakers\\Render", save_current=True
        )
        data = [
            {
                "name": GAME_EXE,
                "commandlinefriendlyid": "USB Audio\\Application\\HTGame.exe",
                "itemid": "\\Device\\HarddiskVolume2\\Game\\HTGame.exe%b1",
                "type": "Application",
                "direction": "Render",
                "devicestate": "Inactive",
                "processid": "80940",
            },
        ]

        with patch.object(audio_routing, "_export_sound_items", return_value=data):
            with patch.object(audio_routing.subprocess, "run") as run:
                router._run_pending_routes("svcl.exe")

        run.assert_not_called()
        self.assertIsNone(router._original_device)
        self.assertFalse(router._restore_needed)

    def test_failed_route_does_not_mark_device_as_requested(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest("Speakers", save_current=True)
        router._original_device = DEFAULT_RENDER_DEVICE
        data = [
            {
                "name": "Speakers",
                "commandlinefriendlyid": "Speaker Audio\\Device\\Speakers\\Render",
                "type": "Device",
                "direction": "Render",
                "Device": "Speaker Audio",
            },
            {
                "name": GAME_EXE,
                "commandlinefriendlyid": "Speaker Audio\\Application\\HTGame.exe",
                "itemid": "\\Device\\HarddiskVolume2\\Game\\HTGame.exe%b1",
                "type": "Application",
                "direction": "Render",
                "devicestate": "Active",
                "processid": "80940",
            },
        ]

        with patch.object(audio_routing, "_export_sound_items", return_value=data):
            with patch.object(
                audio_routing.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=1),
            ):
                router._run_pending_routes("svcl.exe")

        self.assertIsNone(router._requested_device)

    def test_restore_route_uses_original_device(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest("USB Audio\\Device\\Headphones\\Render")
        router._original_device = "USB Audio\\Device\\Headphones\\Render"
        router._background_device = audio_routing._RenderOutputDevice("background", "background")

        with (
            patch.object(audio_routing, "_export_sound_items", return_value=[]),
            patch.object(
                audio_routing,
                "_current_process_render_output_device",
                return_value=router._background_device,
            ),
            patch.object(
                audio_routing.subprocess, "run", return_value=SimpleNamespace(returncode=0)
            ) as run,
        ):
            router._run_pending_routes("svcl.exe")

        self.assertEqual(run.call_args.args[0][2], "USB Audio\\Device\\Headphones\\Render")
        self.assertIsNone(router._requested_device)
        self.assertIsNone(router._original_device)
        self.assertFalse(router._restore_needed)

    def test_restore_keeps_output_manually_changed_while_backgrounded(self):
        for on_exit in (False, True):
            with self.subTest(on_exit=on_exit):
                router = _BackgroundAudioRouter()
                router._background_device = audio_routing._RenderOutputDevice(
                    DEFAULT_RENDER_DEVICE
                    if on_exit
                    else "Background Audio\\Device\\Speakers\\Render",
                    "background",
                )
                router._original_device = "Original Audio\\Device\\Speakers\\Render"
                router._requested_device = router._background_device.route_device
                router._restore_exe_path = "svcl.exe"
                router._restore_needed = True
                data = [
                    {
                        "commandlinefriendlyid": "Background Audio\\Device\\Speakers\\Render",
                        "default": "Render",
                    },
                    {
                        "commandlinefriendlyid": "Background Audio\\Device\\Headphones\\Render",
                        "itemid": "{0.0.0.00000000}.{headphones}",
                    },
                    {
                        "commandlinefriendlyid": "Background Audio\\Application\\game",
                        "itemid": "{0.0.0.00000000}.{headphones}|app",
                        "direction": "Render",
                        "devicestate": "Active",
                        "processid": "80940",
                    },
                ]
                with (
                    patch.object(audio_routing, "_export_sound_items", return_value=data),
                    patch.object(audio_routing, "_is_svcl_path", return_value=True),
                    patch.object(audio_routing.subprocess, "run") as run,
                ):
                    if on_exit:
                        router.restore_on_exit()
                    else:
                        router._pending_route = _RouteRequest(router._original_device)
                        router._run_pending_routes("svcl.exe")

                run.assert_not_called()
                self.assertFalse(router._restore_needed)
                self.assertIsNone(router._requested_device)
                self.assertIsNone(router._original_device)

    def test_restore_updates_requested_device_after_disable(self):
        router = _BackgroundAudioRouter()
        router._requested_device = "USB Audio\\Device\\Speakers\\Render"
        router._original_device = "USB Audio\\Device\\Headphones\\Render"
        router._restore_exe_path = "svcl.exe"
        router._restore_needed = True
        calls = []

        def route(_exe_path, device, save_current=False):
            calls.append(device)
            return device

        router._switch_process_device = route

        with patch.object(audio_routing, "_is_svcl_path", return_value=True):
            router.restore_on_exit()

        self.assertEqual(calls, ["USB Audio\\Device\\Headphones\\Render"])
        self.assertIsNone(router._requested_device)
        self.assertIsNone(router._original_device)
        self.assertFalse(router._restore_needed)

    def test_restore_on_exit_skips_when_original_device_is_unknown(self):
        router = _BackgroundAudioRouter()
        router._requested_device = "USB Audio\\Device\\Speakers\\Render"
        router._restore_exe_path = "svcl.exe"
        router._restore_needed = True
        calls = []
        router._switch_process_device = lambda _exe_path, device, save_current=False: (
            calls.append(device) or device
        )

        with patch.object(audio_routing, "_is_svcl_path", return_value=True):
            router.restore_on_exit()

        self.assertEqual(calls, [])
        self.assertTrue(router._restore_needed)

    def test_disabling_config_restores_audio_router(self):
        validator = _background_audio_routing_validator([DEFAULT_RENDER_DEVICE])

        with patch.object(audio_routing, "restore_background_audio_router") as restore:
            self.assertEqual(validator(CONF_ENABLE, False), (True, None))

        restore.assert_called_once_with()

    def test_enabling_config_routes_current_window_state(self):
        validator = _background_audio_routing_validator([DEFAULT_RENDER_DEVICE])

        with patch.object(audio_routing, "route_background_audio_for_current_window") as route:
            self.assertEqual(validator(CONF_ENABLE, True), (True, None))

        route.assert_called_once_with()

    def test_reset_to_default_restores_audio_when_enabled(self):
        class ResettableConfig(dict):
            def reset_to_default(self):
                self.clear()
                self.update({CONF_ENABLE: False})

        config = ResettableConfig({CONF_ENABLE: True})
        global_config = SimpleNamespace(get_config=lambda _name: config)

        with patch.object(audio_routing.og, "global_config", global_config, create=True):
            audio_routing._routing_config()
            audio_routing._routing_config()

        with patch.object(audio_routing, "restore_background_audio_router") as restore:
            config.reset_to_default()

        restore.assert_called_once_with()

    def test_reset_to_default_patch_preserves_original_arguments(self):
        class ResettableConfig(dict):
            def reset_to_default(self, enabled):
                self.clear()
                self.update({CONF_ENABLE: enabled})

        config = ResettableConfig({CONF_ENABLE: True})
        global_config = SimpleNamespace(get_config=lambda _name: config)

        with patch.object(audio_routing.og, "global_config", global_config, create=True):
            audio_routing._routing_config()

        with patch.object(audio_routing, "restore_background_audio_router") as restore:
            config.reset_to_default(False)

        restore.assert_called_once_with()

    def test_route_current_window_state_uses_last_window_signal(self):
        router = _BackgroundAudioRouter()
        router._last_visible = False
        calls = []

        router._request_route = lambda visible, enabled=None: calls.append((visible, enabled))

        router.route_current_window_state()

        self.assertEqual(calls, [(False, True)])

    def test_foreground_route_cancels_background_route_before_worker_starts(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _RouteRequest(
            "USB Audio\\Device\\Speakers\\Render", save_current=True
        )
        router._worker = SimpleNamespace(is_alive=lambda: True)
        config = {CONF_SVCL_PATH: "svcl.exe"}

        with patch.object(audio_routing, "_routing_config", return_value=config):
            with patch.object(audio_routing, "_is_svcl_path", return_value=True):
                router._request_route(True, enabled=True)

        self.assertIsNone(router._pending_route)

    def test_foreground_route_without_restore_needed_does_not_restore_default(self):
        router = _BackgroundAudioRouter()
        router._original_device = DEFAULT_RENDER_DEVICE
        router._restore_needed = False
        router._worker = SimpleNamespace(is_alive=lambda: False)
        config = {CONF_SVCL_PATH: "svcl.exe"}

        with patch.object(audio_routing, "_routing_config", return_value=config):
            with patch.object(audio_routing, "_is_svcl_path", return_value=True):
                router._request_route(True, enabled=True)

        self.assertIsNone(router._pending_route)

    def test_foreground_route_queues_restore_to_saved_original_device(self):
        router = _BackgroundAudioRouter()
        router._original_device = "USB Audio\\Device\\Headphones\\Render"
        router._restore_needed = True
        router._worker = SimpleNamespace(is_alive=lambda: True)
        config = {CONF_SVCL_PATH: "svcl.exe"}

        with patch.object(audio_routing, "_routing_config", return_value=config):
            with patch.object(audio_routing, "_is_svcl_path", return_value=True):
                router._request_route(True, enabled=True)

        self.assertEqual(
            router._pending_route,
            _RouteRequest("USB Audio\\Device\\Headphones\\Render"),
        )

    def test_window_change_during_audio_route_is_not_dropped(self):
        original = "USB Audio\\Device\\Headphones\\Render"
        background = "Virtual Audio\\Device\\Speakers\\Render"
        config = {
            CONF_ENABLE: True,
            CONF_SVCL_PATH: "svcl.exe",
            audio_routing.CONF_BACKGROUND_DEVICE: background,
        }
        for first_visible in (True, False):
            with self.subTest(first_visible=first_visible):
                router = _BackgroundAudioRouter()
                router._original_device = original
                router._requested_device = background if first_visible else None
                router._restore_needed = first_visible
                router._last_visible = not first_visible
                router._worker = SimpleNamespace(is_alive=lambda: True)
                calls = []

                def switch(_exe_path, device, save_current=False):
                    calls.append(device)
                    if len(calls) == 1:
                        router.on_window(not first_visible)
                    return device

                with (
                    patch.object(audio_routing, "_routing_config", return_value=config),
                    patch.object(audio_routing, "_is_svcl_path", return_value=True),
                    patch.object(router, "_switch_process_device", side_effect=switch),
                ):
                    router.on_window(first_visible)
                    router._run_pending_routes("svcl.exe")

                expected = [original, background] if first_visible else [background, original]
                self.assertEqual(calls, expected)
                self.assertEqual(router._requested_device, background if first_visible else None)

    def test_router_binds_to_ok_exit_event_for_forced_terminal_exit(self):
        router = _BackgroundAudioRouter()
        bound = []
        exit_event = SimpleNamespace(bind_stop=lambda obj: bound.append(obj))

        with patch.object(
            audio_routing.og, "ok", SimpleNamespace(exit_event=exit_event), create=True
        ):
            with patch.object(audio_routing.og, "exit_event", None, create=True):
                router._bind_exit_event()

        self.assertEqual(bound, [router])

    def test_router_does_not_hold_lock_when_exit_event_stops_immediately(self):
        router = _BackgroundAudioRouter()
        exit_event = ExitEvent()
        exit_event.set()
        lock_acquired_by_stop = []

        def stop():
            acquired = router._lock.acquire(timeout=0.2)
            lock_acquired_by_stop.append(acquired)
            if acquired:
                router._lock.release()

        router.stop = stop
        with patch.object(audio_routing, "_ok_exit_event", return_value=exit_event):
            router._bind_exit_event()

        self.assertEqual(lock_acquired_by_stop, [True])

    def test_router_stop_restores_audio(self):
        router = _BackgroundAudioRouter()

        with patch.object(router, "restore_on_exit") as restore:
            router.stop()

        restore.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
