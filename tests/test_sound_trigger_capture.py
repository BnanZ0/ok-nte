import sys
import tempfile
import threading
import unittest
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

from src.sound_trigger.capture.base import AudioCaptureSource
from src.sound_trigger.capture.process_resolver import name_set


class _TestCaptureSource(AudioCaptureSource):
    @property
    def name(self):
        return "test"

    def _produce(self, push):
        pass


class AudioCaptureSourceTests(unittest.TestCase):
    def test_read_returns_latest_chunk_and_drops_backlog(self):
        source = _TestCaptureSource(queue_max=4)

        for value in range(6):
            source._push(np.array([value], dtype=np.float32))

        self.assertEqual(source.read(timeout=0.01).tolist(), [5.0])
        self.assertIsNone(source.read(timeout=0.01))


class ProcessResolverTests(unittest.TestCase):
    def test_name_set_normalizes_scalar_and_collection(self):
        self.assertEqual(name_set("HTGame.exe"), {"htgame.exe"})
        self.assertEqual(
            name_set(["HTGame.exe", "NTEGame.exe"]),
            {"htgame.exe", "ntegame.exe"},
        )
        self.assertEqual(name_set(["", None]), set())

    def test_window_capture_uses_its_pid_even_with_another_browser_in_foreground(self):
        from src.sound_trigger.capture import process_resolver

        with (
            patch.object(process_resolver, "_window_pid", side_effect=[42, None]),
            patch.object(process_resolver, "_process_name", return_value="msedge.exe"),
            patch.object(process_resolver, "_foreground_pid", return_value=100) as foreground,
            patch.object(process_resolver.psutil, "process_iter") as processes,
        ):
            self.assertEqual(process_resolver.resolve_target_pid("msedge.exe", hwnd=123), 42)
            self.assertIsNone(process_resolver.resolve_target_pid("msedge.exe", hwnd=123))

        foreground.assert_not_called()
        processes.assert_not_called()


class ProcessLoopbackTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "WASAPI loopback is Windows-only")
    def test_failed_com_initialization_does_not_mark_ready_or_uninitialize(self):
        from src.sound_trigger.capture import process_loopback

        source = process_loopback.ProcessLoopbackSource("missing-test-process.exe")
        with (
            patch.object(process_loopback, "_capability_available", return_value=True),
            patch.object(
                process_loopback.comtypes, "CoInitializeEx", side_effect=OSError("COM failed")
            ),
            patch.object(process_loopback.comtypes, "CoUninitialize") as uninitialize,
        ):
            with self.assertRaises(OSError):
                source._produce(lambda _chunk: None)

        self.assertFalse(source._ready.is_set())
        uninitialize.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "WASAPI loopback is Windows-only")
    def test_successful_com_initialization_is_balanced_when_capture_stops(self):
        from src.sound_trigger.capture import process_loopback

        source = process_loopback.ProcessLoopbackSource("missing-test-process.exe")
        source._stop.set()
        with (
            patch.object(process_loopback, "_capability_available", return_value=True),
            patch.object(process_loopback.comtypes, "CoInitializeEx") as initialize,
            patch.object(process_loopback.comtypes, "CoUninitialize") as uninitialize,
        ):
            source._produce(lambda _chunk: None)

        initialize.assert_called_once_with(process_loopback.comtypes.COINIT_MULTITHREADED)
        uninitialize.assert_called_once_with()
        self.assertTrue(source._ready.is_set())

    @unittest.skipUnless(sys.platform == "win32", "WASAPI loopback is Windows-only")
    def test_windows_sdk_struct_layouts_match_expected_sizes(self):
        import ctypes

        from src.sound_trigger.capture.process_loopback import (
            AUDIOCLIENT_ACTIVATION_PARAMS,
            AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS,
            WAVEFORMATEX,
            WAVEFORMATEXTENSIBLE,
        )

        self.assertEqual(ctypes.sizeof(AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS), 8)
        self.assertEqual(ctypes.sizeof(AUDIOCLIENT_ACTIVATION_PARAMS), 12)
        self.assertEqual(ctypes.sizeof(WAVEFORMATEX), 18)
        self.assertEqual(ctypes.sizeof(WAVEFORMATEXTENSIBLE), 40)
        self.assertEqual(WAVEFORMATEXTENSIBLE.dwChannelMask.offset, 20)
        self.assertEqual(WAVEFORMATEXTENSIBLE.SubFormat.offset, 24)

    @unittest.skipUnless(sys.platform == "win32", "WASAPI loopback is Windows-only")
    def test_hresult_hex_formats_negative_and_positive_values(self):
        from src.sound_trigger.capture.process_loopback import _hresult_hex

        self.assertEqual(_hresult_hex(-1), "0xFFFFFFFF")
        self.assertEqual(_hresult_hex(0x88890008), "0x88890008")

    @unittest.skipUnless(sys.platform == "win32", "WASAPI loopback is Windows-only")
    def test_to_mono_averages_stereo_float32_samples(self):
        from src.sound_trigger.capture.process_loopback import _to_mono

        stereo = np.array([1.0, -1.0, 0.25, 0.75], dtype=np.float32)

        np.testing.assert_allclose(_to_mono(stereo.tobytes(), is_float=True), [0.0, 0.5])

    @unittest.skipUnless(sys.platform == "win32", "WASAPI loopback is Windows-only")
    def test_to_mono_converts_stereo_pcm16_samples(self):
        from src.sound_trigger.capture.process_loopback import _to_mono

        stereo = np.array([32767, -32768, 16384, 0], dtype=np.int16)

        np.testing.assert_allclose(
            _to_mono(stereo.tobytes(), is_float=False),
            [-1.0 / 65536.0, 0.25],
            atol=1e-6,
        )


class SoundListenerTests(unittest.TestCase):
    def test_listener_rebinds_when_the_shared_window_changes(self):
        from src.game_window import GameWindow, game_window
        from src.sound_trigger.capture import MODE_PROCESS
        from src.sound_trigger.SoundListener import SoundListener

        native_window = GameWindow("HTGame.exe")
        next_window = GameWindow(r"C:\Browser\msedge.exe", 123, False, 25)
        stop_event = threading.Event()
        native, next_source, restored = Mock(), Mock(), Mock()

        def publish(window):
            game_window._current = window

        def wait_for_window(_timeout):
            self.assertIsNone(game_window.current)
            publish(next_window)

        native.read.side_effect = lambda **_: publish(None)
        next_source.read.side_effect = lambda **_: publish(native_window)
        restored.read.side_effect = lambda **_: stop_event.set()
        with (
            patch.object(game_window, "_current", native_window),
            patch.object(stop_event, "wait", side_effect=wait_for_window) as wait,
            patch.object(SoundListener, "_load_samples"),
            patch(
                "src.sound_trigger.SoundListener.create_capture_source",
                side_effect=[native, next_source, restored],
            ) as create_source,
        ):
            listener = SoundListener(sample_path="", counter_attack_sample_path="")
            listener._running = True
            self.addCleanup(listener._stop_capture)
            listener._listen_once(stop_event)

        self.assertEqual(
            create_source.call_args_list,
            [
                call(MODE_PROCESS, process_name="HTGame.exe", hwnd=None),
                call(MODE_PROCESS, process_name="msedge.exe", hwnd=123),
                call(MODE_PROCESS, process_name="HTGame.exe", hwnd=None),
            ],
        )
        wait.assert_called_once()
        native.stop.assert_called_once()
        next_source.stop.assert_called_once()

    def test_context_initializes_before_task_binding_and_preserves_action_throttles(self):
        from src.sound_trigger.SoundCombatContext import SoundCombatContext

        with (
            patch.object(SoundCombatContext, "_instance", None),
            patch("src.sound_trigger.SoundListener.SoundListener"),
        ):
            context = SoundCombatContext()
            try:
                context.setup(task=None)
                self.assertTrue(context.is_active)
                self.assertFalse(context._is_computation_required())

                task = SimpleNamespace(executor=SimpleNamespace(paused=False))
                dodge, counter = Mock(), Mock()
                context.update_task(task, dodge_action=dodge, counter_action=counter)
                self.assertTrue(context._is_computation_required())
                with patch("src.utils.clock._read_monotonic", return_value=100):
                    context.trigger.execute_dodge()
                    context.trigger.execute_dodge()
                    context.trigger.execute_counter_attack()
                    context.trigger.execute_counter_attack()
                dodge.assert_called_once_with()
                counter.assert_called_once_with()

                context.clear_task_if(task)
                self.assertFalse(context._is_computation_required())
            finally:
                context.shutdown()

    def test_cancelled_initialization_does_not_load_samples(self):
        from src.sound_trigger.SoundListener import SoundListener

        stop_event = threading.Event()
        stop_event.set()
        with patch.object(SoundListener, "_load_and_cache") as load_sample:
            with self.assertRaises(CancelledError):
                SoundListener(
                    sample_path="unused.wav",
                    counter_attack_sample_path="unused-counter.wav",
                    initialization_stop_event=stop_event,
                )

        load_sample.assert_not_called()

    def test_stop_during_decode_cancels_without_cache_or_counter_load(self):
        from src.sound_trigger.SoundCombatContext import SoundCombatContext
        from src.sound_trigger.SoundListener import SoundListener

        for fail_during_shutdown in (False, True):
            with self.subTest(fail_during_shutdown=fail_during_shutdown):
                stop_event = threading.Event()

                def decode(*args, **kwargs):
                    stop_event.set()
                    if fail_during_shutdown:
                        raise RuntimeError("can't register atexit after shutdown")
                    return np.ones(100), SoundListener.used_sr

                with tempfile.TemporaryDirectory() as directory:
                    sample_path = Path(directory) / "dodge.wav"
                    sample_path.touch()
                    with (
                        patch(
                            "src.sound_trigger.SoundListener.librosa",
                            SimpleNamespace(load=Mock(side_effect=decode)),
                        ) as decoder,
                        patch.object(SoundCombatContext, "_instance", None),
                    ):
                        context = SoundCombatContext()
                        with self.assertRaises(CancelledError):
                            context.setup(
                                task=None,
                                sample_path=str(sample_path),
                                counter_attack_sample_path=str(Path(directory) / "counter.wav"),
                                initialization_stop_event=stop_event,
                            )

                    self.assertFalse(context.is_active)
                    self.assertIsNone(context.listener)
                    decoder.load.assert_called_once()
                    self.assertEqual(list(Path(directory).iterdir()), [sample_path])

    def test_decode_failure_without_stop_preserves_original_error(self):
        from src.sound_trigger.SoundListener import SoundListener

        error = OSError("simulated decoder failure")
        with tempfile.TemporaryDirectory() as directory:
            sample_path = Path(directory) / "dodge.wav"
            sample_path.touch()
            with patch(
                "src.sound_trigger.SoundListener.librosa",
                SimpleNamespace(load=Mock(side_effect=error)),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    SoundListener(
                        sample_path=str(sample_path),
                        counter_attack_sample_path="",
                        initialization_stop_event=threading.Event(),
                    )

        self.assertIs(raised.exception.__cause__, error)

    def test_missing_sample_file_fails_fast(self):
        from src.sound_trigger.SoundListener import SoundListener

        with self.assertRaises(RuntimeError):
            SoundListener(
                sample_path="assets/sounds/__missing_dodge__.wav",
                counter_attack_sample_path="",
            )

    def test_listener_restarts_after_loop_error(self):
        from src.sound_trigger.SoundListener import SoundListener

        class RestartingListener(SoundListener):
            restart_interval = 0.01

            def _load_samples(self):
                pass

            def _listen_once(self, stop_event):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("simulated listener failure")
                self._running = False

        listener = RestartingListener(sample_path="", counter_attack_sample_path="")
        listener.attempts = 0

        # Exercise the retry loop synchronously. Starting a daemon thread and
        # immediately joining it makes this assertion dependent on OS thread
        # scheduling, which can exceed the timeout on a busy CI runner.
        listener._running = True
        listener._listen_loop(threading.Event())

        self.assertEqual(listener.attempts, 2)
        self.assertFalse(listener.is_running)

    def test_listener_start_is_idempotent(self):
        import threading

        from src.sound_trigger.SoundListener import SoundListener

        class BlockingListener(SoundListener):
            def _load_samples(self):
                pass

            def _listen_once(self, stop_event):
                self.attempts += 1
                self.started.set()
                stop_event.wait(1.0)

        listener = BlockingListener(sample_path="", counter_attack_sample_path="")
        listener.attempts = 0
        listener.started = threading.Event()

        self.assertTrue(listener.start())
        first_thread = listener._listener_thread
        self.assertTrue(listener.start())
        self.assertTrue(listener.started.wait(1.0))
        self.assertIs(listener._listener_thread, first_thread)

        listener.stop()

        self.assertEqual(listener.attempts, 1)
        self.assertFalse(listener.is_running)

    def test_listener_start_returns_false_when_thread_start_fails(self):
        from src.sound_trigger.SoundListener import SoundListener

        class FailingListener(SoundListener):
            def _load_samples(self):
                pass

        listener = FailingListener(sample_path="", counter_attack_sample_path="")

        with patch(
            "src.sound_trigger.SoundListener.threading.Thread.start",
            side_effect=RuntimeError("simulated thread startup failure"),
        ):
            self.assertFalse(listener.start())

        self.assertFalse(listener._running)
        self.assertIsNone(listener._stop_event)
        self.assertIsNone(listener._listener_thread)


if __name__ == "__main__":
    unittest.main()
