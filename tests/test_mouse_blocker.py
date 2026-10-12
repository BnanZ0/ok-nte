import ctypes
import queue
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

if sys.platform == "win32":
    import win32con

    from src.interaction.mouse_blocker import MouseInputBlocker, _MouseHookData
    from src.interaction.NTEInteraction import NTEInteraction


class FakeMouseApi:
    def __init__(self, held=()):
        self.callback = None
        self.install_count = 0
        self.messages = queue.Queue()
        self.SetWindowsHookExW = Mock(side_effect=self._install)
        self.UnhookWindowsHookEx = Mock(side_effect=self._uninstall)
        self.CallNextHookEx = Mock(return_value=123)
        self.GetAsyncKeyState = Mock(side_effect=lambda key: 0x8000 if key in held else 0)
        self.PeekMessageW = Mock(return_value=0)
        self.GetMessageW = Mock(side_effect=lambda *_args: self.messages.get(timeout=2))
        self.PostThreadMessageW = Mock(side_effect=self._post_quit)

    def _install(self, hook_type, callback, module, thread_id):
        if hook_type != 14:
            raise AssertionError("Only the mouse hook may be installed")
        self.callback = callback
        self.install_count += 1
        return 1

    def _uninstall(self, hook):
        self.callback = None
        return 1

    def _post_quit(self, *_args):
        self.messages.put(0)
        return 1

    def send(self, message, flags=0, mouse_data=0, code=0):
        data = _MouseHookData(flags=flags, mouseData=mouse_data)
        return self.callback(code, message, ctypes.addressof(data))


class MouseHookTestSupport:
    def make_blocker(self, held=()):
        api = FakeMouseApi(held)
        kernel = SimpleNamespace(GetCurrentThreadId=Mock(return_value=42))
        with patch(
            "src.interaction.mouse_blocker.ctypes.WinDLL",
            side_effect=lambda name, **_kwargs: api if name == "user32" else kernel,
        ):
            blocker = MouseInputBlocker()
        self.addCleanup(blocker.close)
        return blocker, api


@unittest.skipUnless(sys.platform == "win32", "Windows mouse hook adapter")
class MouseInputBlockerTest(MouseHookTestSupport, unittest.TestCase):
    def test_hook_structure_matches_windows_pointer_width(self):
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(ctypes.sizeof(_MouseHookData), 32 if pointer_size == 8 else 24)
        self.assertEqual(_MouseHookData.dwExtraInfo.offset, 24 if pointer_size == 8 else 20)

    def test_blocks_physical_mouse_but_passes_injected_events(self):
        blocker, api = self.make_blocker()
        blocker.start()
        for message in (
            win32con.WM_MOUSEMOVE,
            win32con.WM_MOUSEWHEEL,
            0x020E,
            win32con.WM_LBUTTONDOWN,
            win32con.WM_LBUTTONUP,
            0x020B,
            0x020C,
        ):
            with self.subTest(message=message):
                self.assertEqual(api.send(message), 1)
                self.assertEqual(api.send(message, flags=1), 123)
        self.assertEqual(api.send(win32con.WM_MOUSEMOVE, code=-1), 123)

    def test_releases_buttons_held_before_blocking_without_allowing_new_clicks(self):
        blocker, api = self.make_blocker(held=(win32con.VK_LBUTTON, win32con.VK_XBUTTON2))
        blocker.start()
        self.assertEqual(api.send(win32con.WM_LBUTTONUP), 123)
        self.assertEqual(api.send(win32con.WM_LBUTTONDOWN), 1)
        self.assertEqual(api.send(win32con.WM_LBUTTONUP), 1)
        self.assertEqual(api.send(win32con.WM_RBUTTONUP), 1)
        self.assertEqual(api.send(0x020C, mouse_data=2 << 16), 123)
        self.assertEqual(api.send(0x020C, mouse_data=1 << 16), 1)

    def test_stop_uninstalls_and_subsequent_operation_installs_a_new_hook(self):
        blocker, api = self.make_blocker()
        blocker.start()
        blocker.stop()
        self.assertIsNone(api.callback)
        blocker.start()
        self.assertEqual(api.send(win32con.WM_MOUSEMOVE), 1)
        blocker.stop()
        self.assertIsNone(api.callback)

    def test_startup_timeout_removes_late_installed_hook(self):
        blocker, api = self.make_blocker()
        blocker.START_TIMEOUT = 0.01
        release_install = threading.Event()

        def delayed_install(*args):
            release_install.wait()
            return api._install(*args)

        def quit_and_release_install(*args):
            release_install.set()
            return api._post_quit(*args)

        api.SetWindowsHookExW.side_effect = delayed_install
        api.PostThreadMessageW.side_effect = quit_and_release_install
        try:
            with self.assertRaises(RuntimeError):
                blocker.start()
        finally:
            release_install.set()
        self.assertIsNone(api.callback)

    def test_callback_failure_releases_input_and_is_reported_on_stop(self):
        blocker, api = self.make_blocker()
        blocker.start()
        with patch.object(blocker, "_should_block", side_effect=ValueError("invalid event")):
            self.assertEqual(api.send(win32con.WM_MOUSEMOVE), 123)
        self.assertEqual(api.send(win32con.WM_MOUSEMOVE), 123)
        with self.assertRaises(RuntimeError) as error:
            blocker.stop()
        self.assertIsNone(api.callback)
        self.assertIsInstance(error.exception.__cause__, ValueError)

    def test_uninstall_failure_is_reported_after_input_is_released(self):
        blocker, api = self.make_blocker()
        blocker.start()
        api.UnhookWindowsHookEx.side_effect = None
        api.UnhookWindowsHookEx.return_value = 0
        with self.assertRaises(RuntimeError):
            blocker.stop()
        self.assertEqual(api.send(win32con.WM_MOUSEMOVE), 123)


@unittest.skipUnless(sys.platform == "win32", "Windows interaction adapter")
class MouseBlockingOperateTest(MouseHookTestSupport, unittest.TestCase):
    def make_interaction(self):
        blocker, api = self.make_blocker()
        interaction = NTEInteraction.__new__(NTEInteraction)
        interaction._input_lock = threading.RLock()
        interaction._operating = False
        interaction._mouse_blocker = blocker
        interaction._mouse_block_depth = 0
        interaction._cursor_sync = Mock()
        interaction.hwnd_window = SimpleNamespace(device_manager=None)
        interaction._restore_cursor = Mock()
        return interaction, api

    def test_nested_operation_keeps_mouse_blocked_until_outer_operation_returns(self):
        interaction, api = self.make_interaction()

        def action():
            self.assertEqual(api.send(win32con.WM_MOUSEMOVE), 1)
            interaction.operate(lambda: None, block=True)
            self.assertEqual(api.send(win32con.WM_MOUSEMOVE), 1)
            return "done"

        with patch("src.interaction.NTEInteraction.GetCursorPos", return_value=(20, 30)):
            result = interaction.operate(action, block=True)
        self.assertEqual(result, "done")
        self.assertIsNone(api.callback)
        self.assertEqual(api.install_count, 1)

    def test_action_exception_unblocks_mouse(self):
        interaction, api = self.make_interaction()
        with (
            patch("src.interaction.NTEInteraction.GetCursorPos", return_value=(20, 30)),
            self.assertRaisesRegex(ValueError, "action failed"),
        ):
            interaction.operate(Mock(side_effect=ValueError("action failed")), block=True)
        self.assertIsNone(api.callback)

    def test_hook_startup_failure_skips_action_and_allows_retry(self):
        interaction, api = self.make_interaction()
        api.SetWindowsHookExW.side_effect = None
        api.SetWindowsHookExW.return_value = 0
        action = Mock()
        with self.assertRaises(RuntimeError):
            interaction.operate(action, block=True)
        action.assert_not_called()
        api.SetWindowsHookExW.side_effect = api._install
        with patch("src.interaction.NTEInteraction.GetCursorPos", return_value=(20, 30)):
            self.assertEqual(interaction.operate(lambda: "done", block=True), "done")
        self.assertIsNone(api.callback)

    def test_destroy_releases_active_hook_and_prevents_reinstallation(self):
        interaction, api = self.make_interaction()
        interaction.block_input()
        interaction.on_destroy()
        interaction.unblock_input()
        self.assertIsNone(api.callback)
        with self.assertRaises(RuntimeError):
            interaction.block_input()
