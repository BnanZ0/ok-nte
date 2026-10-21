import os
import subprocess
import sys
import textwrap
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import psutil


class SoundServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.services = self.make_services()

    @staticmethod
    def make_services():
        from src.runtime.services import RuntimeServices

        with patch.dict(sys.modules, {"comtypes": Mock()}):
            return RuntimeServices()

    def test_stop_before_start_does_not_create_audio_context_or_threads(self):
        with (
            patch("src.sound_trigger.SoundCombatContext.SoundCombatContext") as context_type,
            patch("src.runtime.services.threading.Thread") as thread_type,
        ):
            self.services.stop()
            self.services.start()
            self.services._init_sound_context()

        context_type.assert_not_called()
        thread_type.assert_not_called()

    def test_stop_during_setup_cleans_up_without_starting_listener(self):
        for fail_during_shutdown in (False, True):
            with self.subTest(fail_during_shutdown=fail_during_shutdown):
                services = self.make_services()
                context = Mock()

                def setup(**kwargs):
                    self.assertIs(kwargs["initialization_stop_event"], services._stop_event)
                    services.stop()
                    if fail_during_shutdown:
                        raise RuntimeError("can't register atexit after shutdown")

                context.setup.side_effect = setup
                with patch(
                    "src.sound_trigger.SoundCombatContext.SoundCombatContext",
                    return_value=context,
                ):
                    services._init_sound_context()

                context.enter.assert_not_called()
                context.shutdown.assert_called_once_with()

    def test_running_audio_is_cleaned_up_by_its_owner_after_stop(self):
        entered = threading.Event()
        context = Mock()
        context.enter.side_effect = lambda: entered.set() or True
        worker = threading.Thread(target=self.services._init_sound_context, daemon=True)
        with patch(
            "src.sound_trigger.SoundCombatContext.SoundCombatContext", return_value=context
        ):
            worker.start()
            try:
                self.assertTrue(entered.wait(3.0))
                context.shutdown.assert_not_called()
            finally:
                self.services.stop()
                worker.join(timeout=3.0)

        self.assertFalse(worker.is_alive())
        context.shutdown.assert_called_once_with()

    def test_failed_listener_start_cleans_up_without_waiting_for_stop(self):
        context = Mock()
        context.enter.return_value = False
        with patch(
            "src.sound_trigger.SoundCombatContext.SoundCombatContext", return_value=context
        ):
            self.services._init_sound_context()

        context.shutdown.assert_called_once_with()


class RuntimeServicesTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "COM runtime is Windows-only")
    def test_audio_import_after_runtime_construction_exits_cleanly_with_winrt_objects(self):
        # A separate interpreter is essential: comtypes import/atexit state is
        # process-wide, and the regression occurs after the script returns.
        script = textwrap.dedent("""
            import importlib
            import threading

            from ok.rotypes.roapi import GetActivationFactory
            GetActivationFactory("Windows.Graphics.Capture.GraphicsCaptureItem")

            from src.runtime.services import RuntimeServices
            services = RuntimeServices()
            errors = []

            def load_audio():
                try:
                    importlib.import_module("src.sound_trigger.capture.process_loopback")
                    try:
                        RuntimeServices()
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("Worker-thread runtime construction was accepted")
                except BaseException as error:
                    errors.append(error)

            worker = threading.Thread(target=load_audio, name="SoundContextInit")
            worker.start()
            worker.join(timeout=10)
            if worker.is_alive():
                raise RuntimeError("Audio module import did not finish")
            if errors:
                raise errors[0]
        """)
        environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        with subprocess.Popen(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            env=environment,
        ) as process:
            try:
                output, _ = process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                try:
                    for child in psutil.Process(process.pid).children(recursive=True):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                except psutil.NoSuchProcess:
                    pass
                process.kill()
                output, _ = process.communicate(timeout=5)
                self.fail(f"Audio runtime process did not exit: {output}")

        self.assertEqual(process.returncode, 0, output)


if __name__ == "__main__":
    unittest.main()
