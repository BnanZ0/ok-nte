import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from ok.core.events import EventSignal
from ok.device.capture_methods.browser import BrowserCaptureMethod
from ok.util.config import Config
from ok.util.handler import ExitEvent

import src.game_window as window_module
from src import GAME_EXE
from src.audio import routing as audio_routing
from src.audio import routing_config, svcl
from src.audio.routing import (
    CONF_ENABLE,
    CONF_SVCL_PATH,
    DEFAULT_RENDER_DEVICE,
    _BackgroundAudioRouter,
    _BackgroundRequest,
    _SavedRoute,
    audio_route_command,
    discover_output_devices,
)
from src.game_window import GameWindow, GameWindowMonitor, game_window

NATIVE_WINDOW = GameWindow(GAME_EXE)
NATIVE_FOREGROUND = replace(NATIVE_WINDOW, foreground=True)
NATIVE_BACKGROUND = replace(NATIVE_WINDOW, foreground=False)


def _applied_route(
    original: str, background: str, window: GameWindow = NATIVE_WINDOW
) -> _SavedRoute:
    return _SavedRoute(
        _BackgroundRequest(background, "svcl.exe"),
        window,
        original,
        svcl.RenderOutputDevice(background, ""),
        None,
    )


def _native_audio_records(active: str, other: str) -> list[dict[str, str]]:
    records = [
        {"commandlinefriendlyid": active},
        {"commandlinefriendlyid": other},
        {
            "type": "Application",
            "direction": "Render",
            "devicestate": "Active",
            "processid": "80940",
        },
    ]
    _set_native_output(records, active)
    return records


def _set_native_output(records: list[dict[str, str]], device: str) -> None:
    controller = device.split("\\Device\\", 1)[0]
    records[-1]["commandlinefriendlyid"] = rf"{controller}\Application\{GAME_EXE}"


def _native_router() -> _BackgroundAudioRouter:
    router = _BackgroundAudioRouter()
    router._requested_window = NATIVE_WINDOW
    return router


def _report_window(
    router: _BackgroundAudioRouter, window: GameWindow | None, *, enabled: bool | None = None
) -> None:
    game_window._current = window
    router.on_window(window, enabled=enabled)


class AudioRoutingTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(game_window, "_current", NATIVE_WINDOW))
        self.enterContext(
            patch.object(
                window_module.psutil,
                "process_iter",
                return_value=[SimpleNamespace(pid=80940, info={"name": GAME_EXE})],
            )
        )

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
        data = svcl.parse_sound_items_csv(
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

        with patch.object(svcl, "is_svcl_path", return_value=True):
            with patch.object(
                svcl.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout=stdout),
            ) as run:
                data = svcl.list_sound_items("svcl.exe")

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
        device, pid = svcl.find_render_session(data, {80940})
        self.assertEqual(pid, 80940)

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
        router = _native_router()
        router._pending_route = _BackgroundRequest("Speakers", "svcl.exe")
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

        with patch.object(svcl, "list_sound_items", return_value=data):
            with patch.object(
                svcl.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ) as run:
                router._run_pending_routes()

                self.assertEqual(
                    router._saved_route.original_device,
                    "Headphone Audio\\Device\\Headphones\\Render",
                )
                self.assertEqual(router._saved_route.request.device, "Speakers")
                data[1]["default"] = ""
                data.append(
                    {
                        "commandlinefriendlyid": "New Default Audio\\Device\\Speakers\\Render",
                        "default": "Render",
                    }
                )
                data[2]["devicestate"] = "Inactive"
                data[3]["devicestate"] = "Active"
                router._pending_route = "restore"
                router._run_pending_routes()

        self.assertEqual(run.call_args.args[0][2], DEFAULT_RENDER_DEVICE)
        self.assertIsNone(router._saved_route)

    def test_router_saves_default_when_current_device_is_background_target(self):
        router = _native_router()
        router._pending_route = _BackgroundRequest("Speakers", "svcl.exe")
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

        with patch.object(svcl, "list_sound_items", return_value=data):
            with patch.object(
                svcl.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ):
                router._run_pending_routes()

        self.assertEqual(router._saved_route.original_device, DEFAULT_RENDER_DEVICE)
        self.assertTrue(router._saved_route.needs_restore)

    def test_router_saves_default_when_current_device_name_matches_background_target_id(self):
        router = _native_router()
        router._pending_route = _BackgroundRequest(
            "VB-Audio VoiceMeeter VAIO\\Device\\VoiceMeeter Input\\Render", "svcl.exe"
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

        with patch.object(svcl, "list_sound_items", return_value=data):
            with patch.object(
                svcl.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ):
                router._run_pending_routes()

        self.assertEqual(router._saved_route.original_device, DEFAULT_RENDER_DEVICE)
        self.assertTrue(router._saved_route.needs_restore)

    def test_router_matches_sounddevice_names_with_trademark_markers(self):
        router = _native_router()
        router._pending_route = _BackgroundRequest("Speakers (Realtek(R) Audio)", "svcl.exe")
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

        with patch.object(svcl, "list_sound_items", return_value=data):
            with patch.object(
                svcl.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ) as run:
                router._run_pending_routes()

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
        router = _native_router()
        router._pending_route = _BackgroundRequest(
            "VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)", "svcl.exe"
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

        with patch.object(svcl, "list_sound_items", return_value=data):
            with patch.object(
                svcl.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ) as run:
                router._run_pending_routes()

        self.assertEqual(
            router._saved_route.original_device, "Realtek USB Audio\\Device\\喇叭\\Render"
        )
        self.assertTrue(router._saved_route.needs_restore)
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
        router = _native_router()
        router._pending_route = _BackgroundRequest(
            "VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)", "svcl.exe"
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

        with patch.object(svcl, "list_sound_items", return_value=data):
            with patch.object(
                svcl.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ):
                router._run_pending_routes()

        self.assertEqual(
            router._saved_route.original_device, "Realtek USB Audio\\Device\\喇叭\\Render"
        )
        self.assertTrue(router._saved_route.needs_restore)

    def test_router_skips_background_route_when_original_device_capture_fails(self):
        router = _native_router()
        router._pending_route = _BackgroundRequest(
            "USB Audio\\Device\\Speakers\\Render", "svcl.exe"
        )

        with patch.object(
            svcl,
            "list_sound_items",
            side_effect=RuntimeError("export failed"),
        ):
            with patch.object(svcl.subprocess, "run") as run:
                router._run_pending_routes()

        run.assert_not_called()
        self.assertIsNone(router._saved_route)

    def test_router_skips_background_route_when_capture_returns_default_placeholder(self):
        router = _native_router()
        router._pending_route = _BackgroundRequest(
            "USB Audio\\Device\\Speakers\\Render", "svcl.exe"
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

        with patch.object(svcl, "list_sound_items", return_value=data):
            with patch.object(svcl.subprocess, "run") as run:
                router._run_pending_routes()

        run.assert_not_called()
        self.assertIsNone(router._saved_route)

    def test_failed_route_does_not_mark_device_as_requested(self):
        router = _native_router()
        router._pending_route = _BackgroundRequest("Speakers", "svcl.exe")
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

        with patch.object(svcl, "list_sound_items", return_value=data):
            with patch.object(
                svcl.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=1),
            ):
                router._run_pending_routes()

        self.assertIsNone(router._saved_route)

    def test_rerouting_in_background_preserves_the_original_output(self):
        original = r"Headset\Device\Headphones\Render"
        speakers = r"Speaker\Device\Speakers\Render"
        alternate = r"Virtual\Device\Cable\Render"
        for replacement_succeeds in (False, True):
            with self.subTest(replacement_succeeds=replacement_succeeds):
                router = _native_router()
                records = svcl.parse_sound_items_csv(
                    "Command-Line Friendly ID,Device State,Direction,Process ID\n"
                    f"{original},Active,Render,\n"
                    f"{speakers},Active,Render,\n"
                    f"{alternate},Active,Render,\n"
                    "Headset\\Application\\HTGame.exe,Active,Render,80940\n"
                )
                result = SimpleNamespace(returncode=0)
                replacement = SimpleNamespace(returncode=int(not replacement_succeeds))
                with (
                    patch.object(svcl, "list_sound_items", return_value=records),
                    patch.object(svcl, "is_svcl_path", return_value=True),
                    patch.object(
                        svcl.subprocess, "run", side_effect=[result, replacement, result]
                    ) as run,
                ):
                    router._pending_route = _BackgroundRequest(speakers, "current/svcl.exe")
                    router._run_pending_routes()

                    session = records[-1]
                    session["commandlinefriendlyid"] = r"Speaker\Application\HTGame.exe"
                    router._pending_route = _BackgroundRequest(alternate, "replacement/svcl.exe")
                    router._run_pending_routes()
                    if replacement_succeeds:
                        session["commandlinefriendlyid"] = r"Virtual\Application\HTGame.exe"

                    router.stop()

                expected_tool = (
                    "replacement/svcl.exe" if replacement_succeeds else "current/svcl.exe"
                )
                self.assertEqual(
                    [invocation.args[0] for invocation in run.call_args_list],
                    [
                        ["current/svcl.exe", "/SetAppDefault", speakers, "0", GAME_EXE],
                        ["replacement/svcl.exe", "/SetAppDefault", alternate, "0", GAME_EXE],
                        [expected_tool, "/SetAppDefault", original, "0", GAME_EXE],
                    ],
                )

    def test_restore_route_uses_original_device(self):
        router = _native_router()
        original = r"USB Audio\Device\Headphones\Render"
        background = r"Speaker Audio\Device\Speakers\Render"
        router._saved_route = _applied_route(original, background)

        with (
            patch.object(
                svcl, "list_sound_items", return_value=_native_audio_records(background, original)
            ),
            patch.object(svcl, "set_app_output", return_value=True) as switch,
        ):
            router.stop()

        switch.assert_called_once_with("svcl.exe", original, GAME_EXE)
        self.assertIsNone(router._saved_route)

    def test_restore_keeps_output_manually_changed_while_backgrounded(self):
        for on_exit in (False, True):
            with self.subTest(on_exit=on_exit):
                router = _native_router()
                router._saved_route = _applied_route(
                    r"Original Audio\Device\Speakers\Render",
                    DEFAULT_RENDER_DEVICE
                    if on_exit
                    else "Background Audio\\Device\\Speakers\\Render",
                )
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
                    patch.object(svcl, "list_sound_items", return_value=data),
                    patch.object(svcl, "is_svcl_path", return_value=True),
                    patch.object(svcl.subprocess, "run") as run,
                ):
                    if on_exit:
                        router.stop()
                    else:
                        router._pending_route = "restore"
                        router._run_pending_routes()

                run.assert_not_called()
                self.assertIsNone(router._saved_route)

    def test_failed_restore_keeps_saved_route_for_retry(self):
        router = _native_router()
        original = r"USB Audio\Device\Headphones\Render"
        background = r"Speaker Audio\Device\Speakers\Render"
        saved = router._saved_route = _applied_route(original, background)

        with (
            patch.object(
                svcl, "list_sound_items", return_value=_native_audio_records(background, original)
            ),
            patch.object(svcl, "set_app_output", side_effect=[False, True]) as switch,
        ):
            router.stop()
            self.assertIs(router._saved_route, saved)
            router.stop()

        self.assertEqual(switch.call_args_list, [call("svcl.exe", original, GAME_EXE)] * 2)
        self.assertIsNone(router._saved_route)

    def test_restore_on_exit_skips_without_a_saved_route(self):
        router = _native_router()

        with patch.object(svcl, "list_sound_items") as read:
            router.stop()

        read.assert_not_called()

    def test_disabling_config_restores_audio_router(self):
        with (
            patch.object(audio_routing, "connect_background_audio_router"),
            patch(
                "src.audio.routing_config.discover_output_devices",
                return_value=[DEFAULT_RENDER_DEVICE],
            ),
            patch.object(audio_routing, "restore_background_audio_router") as restore,
        ):
            option = audio_routing.create_background_audio_routing_config_option()
            self.assertEqual(option.validator(CONF_ENABLE, False), (True, None))

        restore.assert_called_once_with()

    def test_enabling_config_routes_current_window_state(self):
        with (
            patch.object(audio_routing, "connect_background_audio_router"),
            patch(
                "src.audio.routing_config.discover_output_devices",
                return_value=[DEFAULT_RENDER_DEVICE],
            ),
            patch.object(audio_routing, "route_background_audio_for_current_window") as route,
        ):
            option = audio_routing.create_background_audio_routing_config_option()
            self.assertEqual(option.validator(CONF_ENABLE, True), (True, None))

        route.assert_called_once_with()

    def test_reset_to_default_restores_audio_when_enabled(self):
        class ResettableConfig(dict):
            def reset_to_default(self):
                self.clear()
                self.update({CONF_ENABLE: False})

        config = ResettableConfig({CONF_ENABLE: True})
        global_config = SimpleNamespace(get_config=lambda _name: config)

        with (
            patch.object(audio_routing.og, "global_config", global_config, create=True),
            patch.object(audio_routing, "restore_background_audio_router") as restore,
        ):
            audio_routing._routing_config()
            audio_routing._routing_config()
            config.reset_to_default()

        restore.assert_called_once_with()

    def test_reset_to_default_patch_preserves_original_arguments(self):
        class ResettableConfig(dict):
            def reset_to_default(self, enabled):
                self.clear()
                self.update({CONF_ENABLE: enabled})

        config = ResettableConfig({CONF_ENABLE: True})
        global_config = SimpleNamespace(get_config=lambda _name: config)

        with (
            patch.object(audio_routing.og, "global_config", global_config, create=True),
            patch.object(audio_routing, "restore_background_audio_router") as restore,
        ):
            audio_routing._routing_config()
            config.reset_to_default(False)

        restore.assert_called_once_with()

    def test_enabling_routing_uses_current_window_snapshot(self):
        game_window._current = NATIVE_BACKGROUND
        with patch.object(audio_routing, "_router") as router:
            audio_routing.route_background_audio_for_current_window()

        router.on_window.assert_called_once_with(NATIVE_BACKGROUND, enabled=True)

    def test_foreground_route_cancels_background_route_before_worker_starts(self):
        router = _native_router()
        router._pending_route = _BackgroundRequest(
            "USB Audio\\Device\\Speakers\\Render", "svcl.exe"
        )
        router._worker = SimpleNamespace(is_alive=lambda: True)
        config = {CONF_SVCL_PATH: "svcl.exe"}

        with (
            patch.object(audio_routing, "_routing_config", return_value=config),
            patch.object(svcl, "list_sound_items") as read,
        ):
            _report_window(router, NATIVE_FOREGROUND, enabled=True)
            router._run_pending_routes()

        read.assert_not_called()

    def test_queued_background_route_is_skipped_after_target_changes(self):
        router = _native_router()
        router._pending_route = _BackgroundRequest(r"USB\Device\Speakers\Render", "svcl.exe")
        game_window._current = GameWindow(r"c:\browser\msedge.exe", 123, False, 25)

        with patch.object(svcl, "list_sound_items") as read:
            router._run_pending_routes()

        read.assert_not_called()

    def test_window_focus_routes_and_restores_its_audio_session(self):
        config = {
            CONF_ENABLE: True,
            CONF_SVCL_PATH: "svcl.exe",
            audio_routing.CONF_BACKGROUND_DEVICE: "Speakers",
        }
        speakers = r"Speaker\Device\Speakers\Render"
        headphones = r"Headset\Device\Headphones\Render"
        data = svcl.parse_sound_items_csv(
            "\n".join(
                (
                    "Command-Line Friendly ID,Device State,Direction,Process ID",
                    f"{speakers},Active,Render,",
                    f"{headphones},Active,Render,",
                    r"Speaker\Application\msedge.exe,Active,Render,99",
                    r"Headset\Application\msedge.exe,Active,Render,42",
                    r"Speaker\Application\msedge.exe,Inactive,Render,42",
                )
            )
        )
        process = SimpleNamespace(
            exe=lambda: r"C:\Browser\msedge.exe",
            children=lambda recursive: [SimpleNamespace(pid=42)],
        )
        window = GameWindow(r"c:\browser\msedge.exe", 123, False, 25)
        routes = _BackgroundAudioRouter()
        with (
            patch.object(window_module.psutil, "Process", return_value=process) as find_process,
            patch.object(audio_routing, "_routing_config", return_value=config),
            patch.object(svcl, "list_sound_items", return_value=data),
            patch.object(audio_routing.threading, "Thread") as worker,
            patch.object(svcl, "set_app_output", return_value=True) as switch,
        ):
            game_window._current = window
            routes.on_window(window)
            worker.call_args.kwargs["target"]()
            data[3]["devicestate"] = "Inactive"
            data[4]["devicestate"] = "Active"

            foreground = replace(window, foreground=True)
            game_window._current = foreground
            routes.on_window(foreground)
            worker.call_args.kwargs["target"]()

        self.assertEqual(
            switch.call_args_list,
            [call("svcl.exe", speakers, 42), call("svcl.exe", headphones, 42)],
        )
        self.assertEqual(find_process.call_args_list, [call(25), call(25)])

    def test_missing_target_restores_previous_route_and_ignores_its_stale_focus_event(self):
        window = GameWindow(r"c:\browser\msedge.exe", 123, False, 25)
        router = _BackgroundAudioRouter()
        original = r"Headset\Device\Headphones\Render"
        background = r"Speaker\Device\Speakers\Render"
        router._saved_route = _applied_route(original, background, window)
        records = _native_audio_records(background, original)

        with (
            patch.object(GameWindow, "process_ids", return_value={80940}),
            patch.object(audio_routing.threading, "Thread") as worker,
            patch.object(svcl, "list_sound_items", return_value=records),
            patch.object(svcl, "set_app_output", return_value=True) as switch,
        ):
            _report_window(router, None)
            router.on_window(window)
            worker.call_args.kwargs["target"]()

        switch.assert_called_once_with("svcl.exe", original, 80940)

    def test_exited_target_is_not_restored_after_reopen(self):
        path = r"c:\browser\msedge.exe"
        first = GameWindow(path, 123, False, 25)
        reopened = GameWindow(path, 456, True, 55)
        original = r"Headset\Device\Headphones\Render"
        background = r"Speaker\Device\Speakers\Render"
        router = _BackgroundAudioRouter()
        router._saved_route = _applied_route(original, background, first)
        records = svcl.parse_sound_items_csv(
            "Command-Line Friendly ID,Device State,Direction,Process ID\n"
            f"{background},Active,Render,\n"
            r"Speaker\Application\msedge.exe,Active,Render,82"
        )
        with (
            patch.object(audio_routing, "_routing_config", return_value={}),
            patch.object(audio_routing.threading, "Thread") as worker,
            patch.object(svcl, "list_sound_items", return_value=records),
            patch.object(svcl, "set_app_output", return_value=True) as switch,
            patch.object(
                GameWindow,
                "process_ids",
                autospec=True,
                side_effect=lambda window: {82} if window == reopened else set(),
            ),
        ):
            _report_window(router, None)
            worker.call_args.kwargs["target"]()
            _report_window(router, reopened)

        switch.assert_not_called()
        worker.assert_called_once()
        self.assertIsNone(router._saved_route)

    def test_target_switch_retries_restore_once_before_routing_the_new_target(self):
        original = r"Headset\Device\Headphones\Render"
        background = r"Speaker\Device\Speakers\Render"
        browser = GameWindow(r"c:\browser\msedge.exe", 123, False, 25)
        intermediate = GameWindow(r"c:\browser\chrome.exe", 456, False, 55)
        config = {
            CONF_ENABLE: True,
            CONF_SVCL_PATH: "svcl.exe",
            audio_routing.CONF_BACKGROUND_DEVICE: background,
        }
        records = _native_audio_records(original, background)
        records.append(
            {
                **records[-1],
                "commandlinefriendlyid": r"Speaker\Application\msedge.exe",
                "processid": "42",
            }
        )

        for retry_succeeds in (True, False):
            with self.subTest(retry_succeeds=retry_succeeds):
                router = _BackgroundAudioRouter()
                router._saved_route = _applied_route(original, background, browser)
                results = iter((False, retry_succeeds, True))

                def set_output(_exe_path, _device, _process):
                    if switch.call_count == 1:
                        _report_window(router, NATIVE_BACKGROUND)
                    return next(results)

                with (
                    patch.object(audio_routing, "_routing_config", return_value=config),
                    patch.object(audio_routing.threading, "Thread") as worker,
                    patch.object(
                        GameWindow,
                        "process_ids",
                        autospec=True,
                        side_effect=lambda window: {42} if window == browser else {80940},
                    ),
                    patch.object(svcl, "list_sound_items", return_value=records),
                    patch.object(svcl, "set_app_output", side_effect=set_output) as switch,
                ):
                    _report_window(router, intermediate)
                    worker.call_args.kwargs["target"]()

                worker.assert_called_once()
                self.assertEqual(
                    switch.call_args_list,
                    [
                        call("svcl.exe", original, 42),
                        call("svcl.exe", original, 42),
                        call("svcl.exe", background, GAME_EXE),
                    ],
                )
                self.assertEqual(router._saved_route.window, NATIVE_BACKGROUND)

    def test_reenabling_routing_supersedes_a_queued_disable_even_during_restore(self):
        original = r"USB Audio\Device\Headphones\Render"
        background = r"Virtual Audio\Device\Speakers\Render"
        for restore_started in (False, True):
            with self.subTest(restore_started=restore_started):
                router = _native_router()
                router._saved_route = _applied_route(original, background)
                game_window._current = NATIVE_BACKGROUND
                records = _native_audio_records(background, original)
                config = dict.__new__(Config)
                dict.update(
                    config,
                    {
                        CONF_ENABLE: True,
                        CONF_SVCL_PATH: "svcl.exe",
                        audio_routing.CONF_BACKGROUND_DEVICE: background,
                    },
                )
                config.validator = routing_config._validator(
                    [background],
                    audio_routing.route_background_audio_for_current_window,
                    audio_routing.restore_background_audio_router,
                )
                config.save_file = lambda: None

                def switch(_exe_path, device, _process):
                    if device == original:
                        config[CONF_ENABLE] = True
                    _set_native_output(records, device)
                    return True

                with (
                    patch.object(audio_routing, "_router", router),
                    patch.object(audio_routing, "_routing_config", return_value=config),
                    patch.object(audio_routing.threading, "Thread") as worker,
                    patch.object(svcl, "list_sound_items", return_value=records),
                    patch.object(svcl, "set_app_output", side_effect=switch) as route,
                ):
                    config[CONF_ENABLE] = False
                    if not restore_started:
                        config[CONF_ENABLE] = True
                    worker.call_args.kwargs["target"]()

                if restore_started:
                    self.assertEqual(
                        route.call_args_list,
                        [
                            call("svcl.exe", original, GAME_EXE),
                            call("svcl.exe", background, GAME_EXE),
                        ],
                    )
                else:
                    route.assert_not_called()
                self.assertTrue(config[CONF_ENABLE])

    def test_selecting_browser_restores_native_output_before_browser_window_exists(self):
        original = r"USB Audio\Device\Headphones\Render"
        background = r"Virtual Audio\Device\Speakers\Render"
        records = _native_audio_records(original, background)
        config = {
            CONF_ENABLE: True,
            CONF_SVCL_PATH: "svcl.exe",
            audio_routing.CONF_BACKGROUND_DEVICE: background,
        }
        selection = dict.__new__(Config)
        dict.update(selection, preferred="windows")
        selection.validator = None
        selection.save_file = lambda: None
        native = SimpleNamespace(hwnd=101, exe_full_path=r"C:\Game\HTGame.exe", visible=True)
        manager = SimpleNamespace(config=selection, hwnd_window=native)
        process = SimpleNamespace(name=lambda: GAME_EXE, children=lambda recursive: [])
        native_signal = EventSignal("test_native_window")
        monitor = GameWindowMonitor()
        routes = _BackgroundAudioRouter()
        monitor.updated.connect(routes.on_window)
        with (
            patch.object(audio_routing, "game_window", monitor),
            patch.object(audio_routing, "_routing_config", return_value=config),
            patch.object(window_module.og, "device_manager", manager),
            patch.object(window_module.communicate, "window", native_signal),
            patch.object(window_module, "get_exit_event", return_value=ExitEvent()),
            patch.object(window_module, "_window_pid", return_value=80940),
            patch.object(window_module.psutil, "Process", return_value=process),
            patch.object(window_module, "Thread"),
            patch.object(audio_routing.threading, "Thread") as worker,
            patch.object(svcl, "list_sound_items", return_value=records),
            patch.object(svcl, "set_app_output", return_value=True) as switch,
        ):
            monitor.start()
            native.visible = False
            native_signal.emit(False)
            worker.call_args.kwargs["target"]()
            _set_native_output(records, background)

            selection["preferred"] = "browser"
            worker.call_args.kwargs["target"]()

        self.assertEqual(
            switch.call_args_list,
            [call("svcl.exe", background, 80940), call("svcl.exe", original, 80940)],
        )
        self.assertIsNone(monitor.current)

    def test_foreground_route_without_restore_needed_does_not_restore_default(self):
        router = _native_router()
        output = r"USB Audio\Device\Headphones\Render"
        router._saved_route = _applied_route(output, output)
        config = {CONF_SVCL_PATH: "svcl.exe"}

        with (
            patch.object(audio_routing, "_routing_config", return_value=config),
            patch.object(audio_routing.threading, "Thread") as worker,
        ):
            _report_window(router, NATIVE_FOREGROUND, enabled=True)

        worker.assert_not_called()

    def test_foreground_route_queues_restore_to_saved_original_device(self):
        router = _native_router()
        original = r"USB Audio\Device\Headphones\Render"
        background = r"Virtual Audio\Device\Speakers\Render"
        router._saved_route = _applied_route(original, background)
        router._worker = SimpleNamespace(is_alive=lambda: True)
        config = {CONF_SVCL_PATH: "svcl.exe"}

        with (
            patch.object(audio_routing, "_routing_config", return_value=config),
            patch.object(
                svcl, "list_sound_items", return_value=_native_audio_records(background, original)
            ),
            patch.object(svcl, "set_app_output", return_value=True) as switch,
        ):
            _report_window(router, NATIVE_FOREGROUND, enabled=True)
            router._run_pending_routes()

        switch.assert_called_once_with("svcl.exe", original, GAME_EXE)

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
                router = _native_router()
                if first_visible:
                    router._saved_route = _applied_route(original, background)
                records = (
                    _native_audio_records(background, original)
                    if first_visible
                    else _native_audio_records(original, background)
                )
                router._worker = SimpleNamespace(is_alive=lambda: True)
                calls = []

                def switch(_exe_path, device, _process):
                    calls.append(device)
                    if len(calls) == 1:
                        _report_window(router, replace(NATIVE_WINDOW, foreground=not first_visible))
                    _set_native_output(records, device)
                    return True

                with (
                    patch.object(audio_routing, "_routing_config", return_value=config),
                    patch.object(svcl, "list_sound_items", return_value=records),
                    patch.object(svcl, "set_app_output", side_effect=switch),
                ):
                    _report_window(router, replace(NATIVE_WINDOW, foreground=first_visible))
                    router._run_pending_routes()

                expected = [original, background] if first_visible else [background, original]
                self.assertEqual(calls, expected)
                self.assertEqual(router._saved_route is not None, first_visible)

    def test_routes_bind_to_ok_exit_event_for_forced_terminal_exit(self):
        routes = _BackgroundAudioRouter()
        bind_stop = Mock()
        exit_event = SimpleNamespace(bind_stop=bind_stop)

        with (
            patch.object(window_module.og, "ok", SimpleNamespace(exit_event=exit_event)),
            patch.object(window_module.og, "exit_event", None, create=True),
        ):
            routes.bind_exit()

        bind_stop.assert_called_once_with(routes)

    def test_routes_restore_when_binding_to_an_already_set_exit_event(self):
        router = _BackgroundAudioRouter()
        exit_event = ExitEvent()
        exit_event.set()

        with (
            patch.object(audio_routing, "get_exit_event", return_value=exit_event),
            patch.object(router, "stop") as restore,
        ):
            router.bind_exit()

        restore.assert_called_once_with()


class GameWindowMonitorTests(unittest.TestCase):
    def test_browser_monitor_starts_on_selection_and_stops_on_return_to_native(self):
        config = dict.__new__(Config)
        dict.update(config, preferred="windows")
        config.validator = lambda key, value: (value != "rejected", None)
        config.save_file = lambda: None
        native = SimpleNamespace(hwnd=101, exe_full_path=r"C:\Game\HTGame.exe", visible=True)
        manager = SimpleNamespace(config=config, hwnd_window=native)
        exit_event = ExitEvent()
        monitor = GameWindowMonitor()
        signal = EventSignal("test_native_window")
        changed = Mock()
        monitor.updated.connect(changed)
        with (
            patch.object(window_module.og, "device_manager", manager),
            patch.object(window_module.communicate, "window", signal),
            patch.object(window_module, "get_exit_event", return_value=exit_event),
            patch.object(window_module, "_window_pid", return_value=25),
            patch.object(window_module, "Thread") as thread,
        ):
            monitor.start()
            monitor.start()
            config["preferred"] = "rejected"
            self.assertEqual(config["preferred"], "windows")
            native.visible = False
            signal.emit(False)
            thread.assert_not_called()
            changed.assert_called_with(GameWindow(GAME_EXE, 101, False, 25))

            config["preferred"] = "browser"
            thread.assert_called_once()
            changed.assert_called_with(None)
            changed.reset_mock()
            native.visible = True
            signal.emit(True)
            changed.assert_not_called()

            config["preferred"] = "windows"
            thread.call_args.kwargs["target"](exit_event)
            self.assertEqual(monitor.current, GameWindow(GAME_EXE, 101, True, 25))

            config["preferred"] = "browser"
            self.assertEqual(thread.call_count, 2)

    def test_browser_focus_events_skip_duplicate_polls_and_keep_retries(self):
        path = r"c:\browser\msedge.exe"
        ticks = [
            (0.00, 123, False),
            (0.25, 123, False),
            (0.50, 123, True),
            (0.75, 123, True),
            (2.50, 123, True),
            (2.75, 456, True),
            (3.00, None, None),
        ]
        index = 0
        exit_event = ExitEvent()
        monitor = GameWindowMonitor()
        monitor._browser_selected = True
        changed = Mock()
        monitor.updated.connect(changed)
        capture = object.__new__(BrowserCaptureMethod)
        capture.exe_full_path = path
        capture.hwnd = ticks[0][1]
        manager = SimpleNamespace(capture_method=capture)

        def advance(_timeout):
            nonlocal index
            index += 1
            if index == len(ticks):
                exit_event.set()
            else:
                capture.hwnd = ticks[index][1]

        with (
            patch.object(window_module.og, "device_manager", manager),
            patch.object(
                window_module.clock, "_read_monotonic", side_effect=lambda: ticks[index][0]
            ),
            patch.object(
                window_module.win32process,
                "GetWindowThreadProcessId",
                side_effect=lambda hwnd: (1, hwnd),
            ),
            patch.object(
                window_module, "is_foreground_window", side_effect=lambda _: ticks[index][2]
            ),
            patch.object(exit_event, "wait", side_effect=advance),
        ):
            monitor._watch_browser(exit_event)

        self.assertEqual(
            changed.call_args_list,
            [
                call(GameWindow(path, 123, False, 123)),
                call(GameWindow(path, 123, True, 123)),
                call(GameWindow(path, 123, True, 123)),
                call(GameWindow(path, 456, True, 456)),
                call(None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
