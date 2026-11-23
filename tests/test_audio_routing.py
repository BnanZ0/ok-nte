import sys
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from ok.util.config import Config
from ok.util.handler import ExitEvent

from src import GAME_EXE
from src.audio import browser_window, routing_config, svcl
from src.audio import routing as audio_routing
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


def _applied_route(
    original: str, background: str, window: browser_window.BrowserWindow | None = None
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
        router = _BackgroundAudioRouter()
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
        self.assertFalse(router.restore_pending)

    def test_router_saves_default_when_current_device_is_background_target(self):
        router = _BackgroundAudioRouter()
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
        router = _BackgroundAudioRouter()
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
        router = _BackgroundAudioRouter()
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
        router = _BackgroundAudioRouter()
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
        router = _BackgroundAudioRouter()
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
        router = _BackgroundAudioRouter()
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
        router = _BackgroundAudioRouter()
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
        router = _BackgroundAudioRouter()
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
                router = _BackgroundAudioRouter()
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

                    router.restore_on_exit()

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
        router = _BackgroundAudioRouter()
        original = r"USB Audio\Device\Headphones\Render"
        background = r"Speaker Audio\Device\Speakers\Render"
        router._saved_route = _applied_route(original, background)

        with (
            patch.object(
                svcl, "list_sound_items", return_value=_native_audio_records(background, original)
            ),
            patch.object(svcl, "set_app_output", return_value=True) as switch,
        ):
            router.restore_on_exit()

        switch.assert_called_once_with("svcl.exe", original, GAME_EXE)
        self.assertIsNone(router._saved_route)

    def test_restore_keeps_output_manually_changed_while_backgrounded(self):
        for on_exit in (False, True):
            with self.subTest(on_exit=on_exit):
                router = _BackgroundAudioRouter()
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
                        router.restore_on_exit()
                    else:
                        router._pending_route = "restore"
                        router._run_pending_routes()

                run.assert_not_called()
                self.assertIsNone(router._saved_route)

    def test_failed_restore_keeps_saved_route_for_retry(self):
        router = _BackgroundAudioRouter()
        original = r"USB Audio\Device\Headphones\Render"
        background = r"Speaker Audio\Device\Speakers\Render"
        saved = router._saved_route = _applied_route(original, background)

        with (
            patch.object(
                svcl, "list_sound_items", return_value=_native_audio_records(background, original)
            ),
            patch.object(svcl, "set_app_output", side_effect=[False, True]) as switch,
        ):
            router.restore_on_exit()
            self.assertIs(router._saved_route, saved)
            router.restore_on_exit()

        self.assertEqual(switch.call_args_list, [call("svcl.exe", original, GAME_EXE)] * 2)
        self.assertIsNone(router._saved_route)

    def test_restore_on_exit_skips_without_a_saved_route(self):
        router = _BackgroundAudioRouter()

        with patch.object(svcl, "list_sound_items") as read:
            router.restore_on_exit()

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

    def test_route_current_window_state_uses_last_window_signal(self):
        router = _BackgroundAudioRouter()
        router._last_visible = False
        with patch.object(router, "_request_route") as request:
            router.route_current_window_state()

        request.assert_called_once_with(False, enabled=True)

    def test_foreground_route_cancels_background_route_before_worker_starts(self):
        router = _BackgroundAudioRouter()
        router._pending_route = _BackgroundRequest(
            "USB Audio\\Device\\Speakers\\Render", "svcl.exe"
        )
        router._worker = SimpleNamespace(is_alive=lambda: True)
        config = {CONF_SVCL_PATH: "svcl.exe"}

        with (
            patch.object(audio_routing, "_routing_config", return_value=config),
            patch.object(svcl, "list_sound_items") as read,
        ):
            router._request_route(True, enabled=True)
            router._run_pending_routes()

        read.assert_not_called()

    def test_browser_target_does_not_start_native_background_route(self):
        router = _BackgroundAudioRouter()
        config = {
            CONF_ENABLE: True,
            CONF_SVCL_PATH: "svcl.exe",
            audio_routing.CONF_BACKGROUND_DEVICE: "Speakers",
        }

        with (
            patch.object(audio_routing, "_routing_config", return_value=config),
            patch.object(svcl, "is_svcl_path", return_value=True),
            patch.object(
                audio_routing, "get_game_target_path", return_value=(r"C:\Browser\msedge.exe", 123)
            ),
            patch.object(audio_routing.threading, "Thread") as worker,
        ):
            router.on_window(visible=False)

        worker.assert_not_called()

    def test_browser_focus_changes_route_and_restore_the_cloud_audio_session(self):
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
        browser = SimpleNamespace(
            exe=lambda: r"C:\Browser\msedge.exe",
            children=lambda recursive: [SimpleNamespace(pid=42)],
        )
        exit_event = ExitEvent()
        target_path = (r"C:\Browser\msedge.exe", 123)
        native = _BackgroundAudioRouter()
        monitor = browser_window.BrowserWindowMonitor(
            _BackgroundAudioRouter, native.request_restore, lambda: exit_event
        )
        with (
            patch.object(audio_routing, "get_game_target_path", return_value=target_path),
            patch.object(browser_window, "get_game_target_path", return_value=target_path),
            patch.object(browser_window, "is_foreground_window", side_effect=[False, True]),
            patch.object(
                browser_window.win32process, "GetWindowThreadProcessId", return_value=(1, 25)
            ),
            patch.object(browser_window.psutil, "Process", return_value=browser) as process,
            patch.object(audio_routing, "_routing_config", return_value=config),
            patch.object(svcl, "list_sound_items", return_value=data),
            patch.object(audio_routing.threading, "Thread") as worker,
            patch.object(svcl.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
        ):

            def run_route(_seconds):
                worker.call_args.kwargs["target"]()
                if run.call_count == 1:
                    data[3]["devicestate"] = "Inactive"
                    data[4]["devicestate"] = "Active"
                else:
                    exit_event.set()

            with patch.object(exit_event, "wait", side_effect=run_route):
                monitor._watch(exit_event)

        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["svcl.exe", "/SetAppDefault", speakers, "0", "42"],
                ["svcl.exe", "/SetAppDefault", headphones, "0", "42"],
            ],
        )
        self.assertEqual([call.args for call in process.call_args_list], [(25,), (25,)])

    def test_leaving_browser_restores_its_route_and_disconnects_focus_events(self):
        window = browser_window.BrowserWindow(r"c:\browser\msedge.exe", 123, 25)
        router = _BackgroundAudioRouter(window)
        exit_event = ExitEvent()
        monitor = browser_window.BrowserWindowMonitor(
            lambda _: router, lambda: None, lambda: exit_event
        )
        with (
            patch.object(router, "request_restore") as restore,
            patch.object(router, "on_window") as on_window,
        ):
            monitor._change_window(window, exit_event)
            monitor._change_window(None, exit_event)
            monitor.focus_changed.emit(False)

        restore.assert_called_once_with()
        on_window.assert_not_called()

    def test_browser_focus_events_skip_duplicate_polls_and_keep_retries(self):
        path = r"c:\browser\msedge.exe"
        ticks = [
            (0.00, 123, False),
            (0.25, 123, False),
            (0.50, 123, True),
            (0.75, 123, True),
            (2.00, 123, True),
            (2.25, 456, True),
        ]
        index = 0
        exit_event = ExitEvent()
        monitor = browser_window.BrowserWindowMonitor(
            _BackgroundAudioRouter, lambda: None, lambda: exit_event
        )

        def advance(_timeout):
            nonlocal index
            index += 1
            if index == len(ticks):
                exit_event.set()

        with (
            patch.object(
                browser_window.clock, "_read_monotonic", side_effect=lambda: ticks[index][0]
            ),
            patch.object(
                browser_window, "get_game_target_path", side_effect=lambda: (path, ticks[index][1])
            ),
            patch.object(
                browser_window.win32process,
                "GetWindowThreadProcessId",
                side_effect=lambda hwnd: (1, hwnd),
            ),
            patch.object(
                browser_window, "is_foreground_window", side_effect=lambda _: ticks[index][2]
            ),
            patch.object(exit_event, "wait", side_effect=advance),
            patch.object(_BackgroundAudioRouter, "on_window") as on_window,
        ):
            monitor._watch(exit_event)

        self.assertEqual(
            on_window.call_args_list, [call(False), call(True), call(True), call(True)]
        )

    def test_closed_browser_retries_restore_for_the_same_executable_on_reopen(self):
        path = r"c:\browser\msedge.exe"
        first = browser_window.BrowserWindow(path, 123, 25)
        reopened = browser_window.BrowserWindow(path, 456, 55)
        different = browser_window.BrowserWindow(r"d:\other\msedge.exe", 789, 75)
        original = r"Headset\Device\Headphones\Render"
        background = r"Speaker\Device\Speakers\Render"
        exit_event = ExitEvent()
        monitor = browser_window.BrowserWindowMonitor(
            _BackgroundAudioRouter, lambda: None, lambda: exit_event
        )
        monitor._change_window(first, exit_event)
        router = monitor.listener
        router._saved_route = _applied_route(original, background, first)
        records = svcl.parse_sound_items_csv(
            "Command-Line Friendly ID,Device State,Direction,Process ID\n"
            f"{background},Active,Render,\n"
            "Speaker\\Application\\msedge.exe,Inactive,Render,42\n"
        )
        with (
            patch.object(svcl, "list_sound_items", return_value=records),
            patch.object(svcl, "set_app_output", return_value=True) as switch,
            patch.object(
                browser_window.BrowserWindow,
                "process_ids",
                autospec=True,
                side_effect=lambda window: {42} if window == first else {82},
            ),
        ):
            monitor._change_window(None, exit_event)
            router.restore_on_exit()
            switch.assert_not_called()
            monitor._change_window(different, exit_event)
            self.assertIsNot(monitor.listener, router)
            monitor._change_window(reopened, exit_event)
            self.assertIs(monitor.listener, router)

            records[-1]["devicestate"] = "Active"
            records[-1]["processid"] = "82"
            monitor.listener.restore_on_exit()

        switch.assert_called_once_with("svcl.exe", original, 82)
        self.assertFalse(router.restore_pending)

    def test_reenabling_routing_supersedes_a_queued_disable_even_during_restore(self):
        original = r"USB Audio\Device\Headphones\Render"
        background = r"Virtual Audio\Device\Speakers\Render"
        for restore_started in (False, True):
            with self.subTest(restore_started=restore_started):
                router = _BackgroundAudioRouter()
                router._saved_route = _applied_route(original, background)
                router._last_visible = False
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
                    patch.object(audio_routing, "_browser_monitor", SimpleNamespace(listener=None)),
                    patch.object(audio_routing, "_routing_config", return_value=config),
                    patch.object(
                        audio_routing, "get_game_target_path", return_value=(GAME_EXE, None)
                    ),
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
        router = _BackgroundAudioRouter()
        original = r"USB Audio\Device\Headphones\Render"
        background = r"Virtual Audio\Device\Speakers\Render"
        router._saved_route = _applied_route(original, background)
        exit_event = ExitEvent()
        monitor = browser_window.BrowserWindowMonitor(
            _BackgroundAudioRouter, router.request_restore, lambda: exit_event
        )
        with (
            patch.object(browser_window, "get_game_target_path", return_value=(None, None)),
            patch.object(exit_event, "wait", side_effect=lambda _: exit_event.set()),
            patch.object(audio_routing.threading, "Thread") as worker,
            patch.object(
                svcl, "list_sound_items", return_value=_native_audio_records(background, original)
            ),
            patch.object(svcl, "set_app_output", return_value=True) as switch,
        ):
            monitor._watch(exit_event)
            worker.call_args.kwargs["target"]()

        switch.assert_called_once_with("svcl.exe", original, GAME_EXE)
        self.assertIsNone(monitor.listener)

    def test_foreground_route_without_restore_needed_does_not_restore_default(self):
        router = _BackgroundAudioRouter()
        output = r"USB Audio\Device\Headphones\Render"
        router._saved_route = _applied_route(output, output)
        config = {CONF_SVCL_PATH: "svcl.exe"}

        with (
            patch.object(audio_routing, "_routing_config", return_value=config),
            patch.object(audio_routing.threading, "Thread") as worker,
        ):
            router._request_route(True, enabled=True)

        worker.assert_not_called()

    def test_foreground_route_queues_restore_to_saved_original_device(self):
        router = _BackgroundAudioRouter()
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
            router._request_route(True, enabled=True)
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
                router = _BackgroundAudioRouter()
                if first_visible:
                    router._saved_route = _applied_route(original, background)
                records = (
                    _native_audio_records(background, original)
                    if first_visible
                    else _native_audio_records(original, background)
                )
                router._last_visible = not first_visible
                router._worker = SimpleNamespace(is_alive=lambda: True)
                calls = []

                def switch(_exe_path, device, _process):
                    calls.append(device)
                    if len(calls) == 1:
                        router.on_window(not first_visible)
                    _set_native_output(records, device)
                    return True

                with (
                    patch.object(audio_routing, "_routing_config", return_value=config),
                    patch.object(svcl, "list_sound_items", return_value=records),
                    patch.object(svcl, "set_app_output", side_effect=switch),
                ):
                    router.on_window(first_visible)
                    router._run_pending_routes()

                expected = [original, background] if first_visible else [background, original]
                self.assertEqual(calls, expected)
                self.assertEqual(router.restore_pending, first_visible)

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
