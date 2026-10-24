"""Compatibility layer for running ok-script TaskTestCase suites together.

Why this exists:
    ok.test.TaskTestCase was designed around ok.test.init_ok() keeping a
    module-level OK singleton. That is fine when test files are executed one at
    a time, but a single unittest discovery run keeps the Python process alive
    across many TaskTestCase classes. Without resetting ok-script's shared
    runtime state, later tests can inherit a finished executor/exit_event and
    fail with FinishedException when they call TaskTestCase.set_image().

    The TaskTestCase suites exercise task logic only, but the stock
    ok.test.init_ok() helper touches ok.app explicitly. That creates a GUI
    App/QApplication and leaves queued Qt cleanup work behind despite the test
    runner never entering Qt's event loop. Run these suites with ok's existing
    HeadlessApp instead. UI tests should create and own their QApplication
    explicitly instead of inheriting this process-wide runtime.

Future maintenance checklist:
    - If ok.test.TaskTestCase gains a real headless mode, remove the
      init_ok/destroy_ok patches below.
    - If ok.OK adds or removes class-level runtime attributes, update
      _OK_CLASS_STATE_ATTRS so no stale executor/device/feature state survives.
    - If ok.og gains new process-global runtime references, update
      _OK_GLOBAL_STATE_ATTRS for the same reason.

This module is imported by tests.__init__, so unittest discovery must run with
``--top-level-directory .``. Short ``-t .`` is intentionally avoided because
ok-script also parses ``-t`` as its task argument.
"""

from __future__ import annotations

import os
import tempfile
import threading

import ok as _ok
import ok.test as _ok_test
import ok.test.TaskTestCase as _ok_task_test_case
from ok.task.TaskExecutor import TaskExecutor
from ok.ui.qt.Communicate import communicate
from ok.util.handler import ExitEvent

_ORIGINALS_ATTR = "_ok_nte_runtime_isolation_originals"
_TEST_CUSTOM_CHARS_ROOT = tempfile.TemporaryDirectory(
    prefix="ok_nte_test_custom_chars_",
    ignore_cleanup_errors=True,
)
_TEST_CONFIGS_ROOT = tempfile.TemporaryDirectory(
    prefix="ok_nte_test_configs_",
    ignore_cleanup_errors=True,
)

_OK_CLASS_STATE_ATTRS = (
    "executor",
    "feature_set",
    "device_manager",
    "ocr",
    "overlay_window",
    "screenshot",
    "init_error",
)

_OK_GLOBAL_STATE_ATTRS = (
    "app",
    "executor",
    "device_manager",
    "handler",
    "exit_event",
    "my_app",
    "ok",
    "config",
    "task_manager",
    "global_config",
)


def _disable_async_preheats_for_tests() -> None:
    """Avoid background cache workers crossing TaskTestCase boundaries."""
    from src.char.custom.CustomCharManager import CustomCharManager
    from src.tasks.mixin.CharUIMixin import CharElementUIMixin

    if not hasattr(CharElementUIMixin, "_ok_nte_original_preheat"):
        CharElementUIMixin._ok_nte_original_preheat = (
            CharElementUIMixin.preheat_element_template_cache_async
        )
        CharElementUIMixin.preheat_element_template_cache_async = classmethod(
            lambda _cls: None
        )
    if not hasattr(CustomCharManager, "_ok_nte_original_preheat"):
        CustomCharManager._ok_nte_original_preheat = CustomCharManager.preheat_feature_cache_async
        CustomCharManager.preheat_feature_cache_async = lambda _self: None


def _redirect_custom_char_storage_for_tests() -> None:
    """Keep TaskTestCase suites away from the user's custom character data."""
    from src.char.custom import CustomCharDb as custom_char_db_module
    from src.char.custom import CustomCharManager as manager_module

    root = _TEST_CUSTOM_CHARS_ROOT.name
    manager_module.CUSTOM_CHARS_DIR = root
    manager_module.FEATURES_DIR = os.path.join(root, "features")
    manager_module.DB_PATH = os.path.join(root, "db.json")
    manager_module.EXTERNAL_CHARS_DIR = os.path.join(root, "external_chars")
    manager_module.CustomCharManager._instance = None
    custom_char_db_module.CustomCharDb.reset_instance()


def install_ok_test_runtime_isolation() -> None:
    """Patch ok.test helpers so each TaskTestCase class starts from clean state."""
    _redirect_custom_char_storage_for_tests()
    _disable_async_preheats_for_tests()
    if not hasattr(_ok_test, _ORIGINALS_ATTR):
        setattr(_ok_test, _ORIGINALS_ATTR, True)

        def init_ok_with_fresh_runtime(config):
            _ok_test.ok = None
            reset_ok_runtime_state()

            test_config = dict(config)
            test_config["analytics"] = None
            test_config["check_mutex"] = False
            test_config["debug"] = True
            test_config["config_folder"] = tempfile.mkdtemp(
                prefix="configs_",
                dir=_TEST_CONFIGS_ROOT.name,
            )
            # Test discovery starts a fresh ok runtime for each TaskTestCase
            # class. Avoid reopening the shared application log on every
            # setup, which is unnecessary for these tests and can be locked
            # by another local ok-script process on Windows.
            test_config["disable_file_log"] = True
            test_config["use_gui"] = False
            # TaskTestCase creates the task under test itself. Do not
            # initialize the application's unrelated task registry during
            # every class setup.
            test_config["onetime_tasks"] = []
            test_config["trigger_tasks"] = []
            # The nested GUI config takes precedence over the legacy
            # use_gui flag. Disable it explicitly so TaskTestCase suites use
            # ok-script's HeadlessApp and do not create QApplication.
            test_config["gui"] = None
            # HeadlessApp defaults to en_US, while TaskTestCase sets the
            # language through ok's Qt config before calling init_ok(). Keep
            # translations consistent with the suite's selected language.
            from ok.ui.qt.util.app import cfg as qt_config

            test_config["locale"] = qt_config.get(qt_config.language).value.name()
            test_config["my_app"] = None
            test_config["blur_area"] = None

            runtime = _ok.OK(test_config)
            _ok_test.ok = runtime
            _ok.og.exit_event = runtime.exit_event
            runtime.task_executor.debug_mode = True
            runtime.device_manager.capture_method = _ok.ImageCaptureMethod(
                runtime.device_manager.exit_event, []
            )
            runtime.device_manager.device_dict["image"] = {
                "address": "",
                "imei": "image",
                "device": "image",
                "nick": "Image",
                "width": 0,
                "height": 0,
                "capture": "image",
                "connected": True,
            }
            runtime.device_manager.config["preferred"] = "image"
            runtime.device_manager.interaction = _ok.DoNothingInteraction(
                runtime.device_manager.capture_method
            )
            if scene_config := test_config.get("scene"):
                runtime.task_executor.scene = _ok.init_class_by_name(
                    scene_config[0], scene_config[1]
                )
            runtime.task_executor.start()

        def destroy_ok_and_clear_singleton():
            runtime = _ok_test.ok
            try:
                if runtime is not None:
                    runtime.task_executor.stop()
                    runtime.quit()
                    executor_thread = runtime.task_executor.thread
                    if executor_thread and executor_thread is not threading.current_thread():
                        executor_thread.join(timeout=5.0)
                    runtime.device_manager.close()
                    if runtime._headless_app is not None:
                        try:
                            communicate.quit.disconnect(runtime._headless_app.quit)
                        except RuntimeError:
                            pass
            finally:
                _ok_test.ok = None
                reset_ok_runtime_state()

        def init_default_ocr_on_demand(_executor):
            """Avoid native OCR initialization racing interpreter shutdown in tests."""

        TaskExecutor.init_default_ocr = init_default_ocr_on_demand
        _ok_test.init_ok = init_ok_with_fresh_runtime
        _ok_test.destroy_ok = destroy_ok_and_clear_singleton

    # TaskTestCase imports these helpers directly.  Updating ok.test alone does
    # not affect a TaskTestCase module that was imported before this installer.
    _ok_task_test_case.init_ok = _ok_test.init_ok
    _ok_task_test_case.destroy_ok = _ok_test.destroy_ok


def reset_ok_runtime_state() -> None:
    """Clear ok-script process globals that leak between TaskTestCase classes."""
    from src.utils.visual_template_cache import reset_visual_template_cache_for_tests

    reset_visual_template_cache_for_tests()
    ExitEvent.queues = set()
    ExitEvent.to_stops = set()
    _ok.OK.exit_event = ExitEvent()

    for attr in _OK_CLASS_STATE_ATTRS:
        setattr(_ok.OK, attr, None)

    for attr in _OK_GLOBAL_STATE_ATTRS:
        setattr(_ok.og, attr, None)
