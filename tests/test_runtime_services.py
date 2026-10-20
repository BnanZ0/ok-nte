import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

import psutil


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
