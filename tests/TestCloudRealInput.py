"""Cloud-variant real keyboard/mouse input: behaviour without a real game window."""

import ctypes
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.interaction import cloud_real_input


@unittest.skipUnless(os.name == "nt", "Windows real-input adapter")
class CloudRealInputStructTest(unittest.TestCase):
    def test_keyboard_event_carries_virtual_key_and_no_scan_code(self):
        down = cloud_real_input.keyboard_input(0x1B, 0)
        self.assertEqual(down.type, cloud_real_input.INPUT_KEYBOARD)
        self.assertEqual(down.u.ki.wVk, 0x1B)
        self.assertEqual(down.u.ki.wScan, 0)
        self.assertEqual(down.u.ki.dwFlags, 0)

        sent = []
        with patch.object(
            cloud_real_input, "send_input", side_effect=lambda i: sent.append(i) or 1
        ):
            self.assertEqual(cloud_real_input.key_event(0x1B, up=True), 1)
        self.assertEqual(len(sent), 1)
        up = sent[0]
        self.assertEqual(up.u.ki.wVk, 0x1B)
        self.assertEqual(up.u.ki.wScan, 0)
        self.assertEqual(up.u.ki.dwFlags, cloud_real_input.KEYEVENTF_KEYUP)

    def test_jiggle_mouse_moves_left_then_right_by_the_same_amount(self):
        sent = []

        with (
            patch.object(
                cloud_real_input,
                "send_input",
                side_effect=lambda inp: sent.append(inp) or 1,
            ),
            patch.object(cloud_real_input.time, "sleep"),
        ):
            self.assertEqual(cloud_real_input.jiggle_mouse(4, 0), 1)

        self.assertEqual(
            [(inp.u.mi.dx, inp.u.mi.dy) for inp in sent],
            [(-4, 0), (4, 0)],
        )

    def test_extended_keys_keep_the_extended_bit_on_down_and_up(self):
        down = cloud_real_input.keyboard_input(0x1B)
        with patch.object(cloud_real_input, "send_input", return_value=1) as send:
            cloud_real_input.key_event(0x26, up=False)
            cloud_real_input.key_event(0x26, up=True)
        flags = [call.args[0].u.ki.dwFlags for call in send.call_args_list]
        self.assertEqual(
            flags,
            [
                cloud_real_input.KEYEVENTF_EXTENDEDKEY,
                cloud_real_input.KEYEVENTF_EXTENDEDKEY | cloud_real_input.KEYEVENTF_KEYUP,
            ],
        )
        self.assertEqual(down.u.ki.wScan, 0)

    def test_mouse_event_uses_the_mouse_union_and_button_flags(self):
        sent = []
        with patch.object(
            cloud_real_input, "send_input", side_effect=lambda i: sent.append(i) or 1
        ):
            cloud_real_input.mouse_event(cloud_real_input.MOUSEEVENTF_LEFTDOWN)
            cloud_real_input.xbutton_event(1, up=True)
        self.assertEqual([i.type for i in sent], [cloud_real_input.INPUT_MOUSE] * 2)
        self.assertEqual(sent[0].u.mi.dwFlags, cloud_real_input.MOUSEEVENTF_LEFTDOWN)
        self.assertEqual(sent[1].u.mi.dwFlags, cloud_real_input.MOUSEEVENTF_XUP)
        self.assertEqual(sent[1].u.mi.mouseData, 1)

    def test_private_windll_does_not_pollute_the_shared_user32(self):
        self.assertIsNot(cloud_real_input.SendInput, ctypes.windll.user32.SendInput)
        self.assertIsNotNone(cloud_real_input.SendInput.argtypes)

    @unittest.skipUnless(ctypes.sizeof(ctypes.c_void_p) == 8, "64-bit layout")
    def test_x64_struct_sizes(self):
        self.assertEqual(ctypes.sizeof(cloud_real_input.INPUT), 40)
        self.assertEqual(ctypes.sizeof(cloud_real_input.MOUSEINPUT), 32)
        self.assertEqual(ctypes.sizeof(cloud_real_input.KEYBDINPUT), 24)
        self.assertEqual(ctypes.sizeof(cloud_real_input.CURSORINFO), 24)


@unittest.skipUnless(os.name == "nt", "Windows real-input adapter")
class CloudInteractionDispatchTest(unittest.TestCase):
    def setUp(self):
        from src.interaction.NTEInteraction import NTEInteraction

        self.interaction = NTEInteraction.__new__(NTEInteraction)
        self.interaction._input_lock = threading.RLock()
        self.interaction._disable_key_mapping = 1
        self.interaction._dynamic_target_hwnd = 0
        self.interaction._operating = False
        self.interaction._cloud_captured = False
        self.interaction._cloud_pressed_vks = set()
        self.interaction._cloud_pressed_buttons = set()
        self.interaction._mouse_block_depth = 0
        self.interaction._activate_require = True
        self.interaction.hwnd_window = SimpleNamespace(
            hwnd=123, top_hwnd=0, is_foreground=lambda: True
        )
        self.interaction.capture = SimpleNamespace(
            width=1600,
            height=900,
            get_abs_cords=lambda x, y: (x + 899, y + 212),
        )
        self.interaction._cursor_sync = Mock()
        self.interaction._mouse_blocker = Mock()
        self.interaction._cloud_set_cursor_pos = Mock()
        self.interaction.post = Mock(return_value=True)
        self.interaction.try_activate = Mock()
        self.interaction._restore_cursor = Mock()
        self.interaction._CLOUD_WARMUP_HOLD = 0
        self.interaction._CLOUD_WARMUP_SETTLE = 0
        self.interaction._CLOUD_MIN_DOWN_TIME = 0
        self.interaction._CLOUD_FOREGROUND_TIMEOUT = 0.1

        self.cloud = patch(
            "src.interaction.NTEInteraction._cloud_variant_selected", return_value=True
        )
        self.cloud.start()
        self.addCleanup(self.cloud.stop)

        for name in (
            "key_event",
            "mouse_event",
            "xbutton_event",
            "wheel_event",
            "jiggle_mouse",
        ):
            mocked = patch.object(cloud_real_input, name, return_value=1)
            mocked.start()
            self.addCleanup(mocked.stop)
        for name, value in (
            ("raise_window", None),
            ("window_center", (1707, 681)),
            ("is_foreground", True),
            ("foreground_info", (123, "cloud game")),
            ("try_set_foreground", True),
            ("point_belongs_to_window", True),
            ("set_cursor_pos", None),
        ):
            mocked = patch.object(cloud_real_input, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

        import win32con

        self.win32con = win32con

    def test_cloud_key_down_and_up_use_real_virtual_key_events(self):
        self.assertTrue(self.interaction.send_key_down("q"))
        self.assertTrue(self.interaction.send_key_up("q"))
        self.assertEqual(
            [call.args for call in cloud_real_input.key_event.call_args_list],
            [(0x51,), (0x51,)],
        )
        self.assertEqual(
            [call.kwargs for call in cloud_real_input.key_event.call_args_list],
            [{"up": False}, {"up": True}],
        )
        self.interaction.post.assert_not_called()

    def test_vk_key_scan_shift_state_is_masked_off(self):
        self.assertTrue(self.interaction.send_key_down("Q"))
        self.assertEqual(cloud_real_input.key_event.call_args.args[0], 0x51)

    def test_mouse_button_keys_use_real_xbutton_events(self):
        self.assertTrue(self.interaction.send_key_down("mouse4"))
        self.assertTrue(self.interaction.send_key_up("mouse4"))
        self.assertEqual(
            [call.args for call in cloud_real_input.xbutton_event.call_args_list],
            [(1,), (1,)],
        )
        self.assertEqual(
            [call.kwargs for call in cloud_real_input.xbutton_event.call_args_list],
            [{"up": False}, {"up": True}],
        )
        self.interaction.post.assert_not_called()

    def test_warm_up_happens_once_until_focus_is_lost(self):
        self.interaction.send_key_down("w")
        self.interaction.send_key_up("w")
        self.interaction.send_key_down("a")

        warmup_downs = [
            call
            for call in cloud_real_input.mouse_event.call_args_list
            if call.args[0] == cloud_real_input.MOUSEEVENTF_LEFTDOWN
        ]
        self.assertEqual(len(warmup_downs), 1)
        self.assertTrue(self.interaction._cloud_captured)

        self.interaction.on_visible(False)
        self.assertFalse(self.interaction._cloud_captured)
        self.interaction.send_key_down("d")
        warmup_downs = [
            call
            for call in cloud_real_input.mouse_event.call_args_list
            if call.args[0] == cloud_real_input.MOUSEEVENTF_LEFTDOWN
        ]
        self.assertEqual(len(warmup_downs), 2)

    def test_no_foreground_refuses_to_send_real_input(self):
        self.interaction.hwnd_window.is_foreground = lambda: False
        with patch.object(cloud_real_input, "is_foreground", return_value=False):
            self.assertFalse(self.interaction.send_key_down("w"))
            self.assertFalse(self.interaction.click(800, 450))
        cloud_real_input.key_event.assert_not_called()
        self.interaction.post.assert_not_called()

    def test_key_up_is_always_sent_even_without_foreground(self):
        self.interaction.hwnd_window.is_foreground = lambda: False
        with patch.object(cloud_real_input, "is_foreground", return_value=False):
            self.assertFalse(self.interaction.send_key_down("w"))
            self.assertTrue(self.interaction.send_key_up("w"))
        self.assertEqual(
            [call.kwargs for call in cloud_real_input.key_event.call_args_list],
            [{"up": True}],
        )

    def test_cloud_cursor_move_uses_set_cursor_pos(self):
        with patch.object(cloud_real_input, "set_cursor_pos") as set_pos:
            # setUp replaces the instance method with a Mock; call the real
            # class method to verify the delegation.
            method = type(self.interaction)._cloud_set_cursor_pos
            method(self.interaction, (10, 20))

        set_pos.assert_called_once_with((10, 20))

    def test_cloud_click_sends_real_button_events_and_moves_the_cursor(self):
        self.interaction._cloud_captured = True  # skip the warm-up in this test
        self.assertTrue(self.interaction.click(800, 450, down_time=0.01))
        self.interaction._cloud_set_cursor_pos.assert_called_once_with((1699, 662))
        self.interaction._cursor_sync.set_cursor_pos.assert_not_called()
        flags = [call.args[0] for call in cloud_real_input.mouse_event.call_args_list]
        self.assertEqual(
            flags,
            [cloud_real_input.MOUSEEVENTF_LEFTDOWN, cloud_real_input.MOUSEEVENTF_LEFTUP],
        )
        self.interaction.post.assert_not_called()

    def test_cloud_click_supports_right_and_middle_buttons(self):
        self.interaction._cloud_captured = True
        self.interaction.click(10, 10, key="right")
        self.interaction.click(10, 10, key="middle")
        flags = [call.args[0] for call in cloud_real_input.mouse_event.call_args_list]
        self.assertEqual(
            flags,
            [
                cloud_real_input.MOUSEEVENTF_RIGHTDOWN,
                cloud_real_input.MOUSEEVENTF_RIGHTUP,
                cloud_real_input.MOUSEEVENTF_MIDDLEDOWN,
                cloud_real_input.MOUSEEVENTF_MIDDLEUP,
            ],
        )

    def test_local_variant_keeps_the_original_post_message_path(self):
        with patch(
            "src.interaction.NTEInteraction._cloud_variant_selected", return_value=False
        ):
            self.assertTrue(self.interaction.send_key_down("q"))
            self.assertTrue(self.interaction.send_key_up("q"))
            self.interaction.click(800, 450)  # local click returns None

        cloud_real_input.key_event.assert_not_called()
        cloud_real_input.mouse_event.assert_not_called()
        messages = [call.args[0] for call in self.interaction.post.call_args_list]
        self.assertIn(self.win32con.WM_KEYDOWN, messages)
        self.assertIn(self.win32con.WM_KEYUP, messages)
        self.assertIn(self.win32con.WM_LBUTTONDOWN, messages)
        self.assertIn(self.win32con.WM_LBUTTONUP, messages)

    def test_prepare_activates_explicitly_before_the_first_key(self):
        self.assertTrue(self.interaction.send_key_down("q"))
        cloud_real_input.try_set_foreground.assert_called_with(123)
        self.assertGreaterEqual(cloud_real_input.try_set_foreground.call_count, 2)
        cloud_real_input.point_belongs_to_window.assert_called_with((1707, 681), 123)

    def test_unsafe_center_point_skips_the_warm_up_click(self):
        with (
            patch.object(cloud_real_input, "point_belongs_to_window", return_value=False),
            patch.object(cloud_real_input, "click_at") as click_at,
        ):
            self.assertTrue(self.interaction.send_key_down("q"))
        click_at.assert_not_called()
        cloud_real_input.key_event.assert_called_once_with(0x51, up=False)

    def test_focus_loss_releases_a_held_real_key(self):
        self.assertTrue(self.interaction.send_key_down("w"))
        self.assertIn(0x57, self.interaction._cloud_pressed_vks)
        cloud_real_input.key_event.reset_mock()
        self.interaction.on_visible(False)
        self.assertFalse(self.interaction._cloud_pressed_vks)
        self.assertFalse(self.interaction._cloud_captured)
        cloud_real_input.key_event.assert_called_once_with(0x57, up=True)

    def test_cloud_click_always_sends_the_matching_button_up(self):
        self.interaction._cloud_captured = True
        with patch(
            "src.interaction.NTEInteraction.time.sleep",
            side_effect=[None, RuntimeError("task disabled mid-click")],
        ):
            self.assertFalse(self.interaction.click(800, 450))
        flags = [call.args[0] for call in cloud_real_input.mouse_event.call_args_list]
        self.assertEqual(
            flags,
            [
                cloud_real_input.MOUSEEVENTF_LEFTDOWN,
                cloud_real_input.MOUSEEVENTF_LEFTUP,
            ],
        )
        self.assertFalse(self.interaction._cloud_pressed_buttons)

    def test_cloud_variant_skips_mouse_hook_and_cursor_restore(self):
        self.interaction.block_input()
        self.interaction._mouse_blocker.start.assert_not_called()
        self.assertEqual(self.interaction._mouse_block_depth, 0)
        self.interaction.unblock_input()
        self.interaction._mouse_blocker.stop.assert_not_called()

        self.interaction._enter_reset_scene_without_check = Mock(return_value=None)
        self.interaction._exit_reset_scene_without_check = Mock()
        # GetCursorPos needs an interactive desktop; keep the test headless.
        with patch(
            "src.interaction.NTEInteraction.GetCursorPos", return_value=(100, 100)
        ):
            self.assertEqual(self.interaction.operate(lambda: "ok", block=True), "ok")
        self.interaction._restore_cursor.assert_not_called()

    def test_cloud_scroll_uses_a_real_wheel_event(self):
        self.interaction._cloud_captured = True
        self.assertTrue(self.interaction.scroll(0.5, 0.5, -1))
        cloud_real_input.wheel_event.assert_called_once_with(-1)
        self.interaction.post.assert_not_called()

    def test_cloud_move_mouse_relative_requires_foreground(self):
        self.interaction.hwnd_window.is_foreground = lambda: False
        with patch.object(cloud_real_input, "is_foreground", return_value=False):
            self.assertEqual(self.interaction.move_mouse_relative(10, 0), 0)
        self.interaction._cursor_sync.mark_internal_move.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows real-input adapter")
class CloudActivationTest(unittest.TestCase):
    def test_already_foreground_needs_no_alt_or_attach(self):
        with (
            patch.object(cloud_real_input.win32gui, "GetForegroundWindow", return_value=42),
            patch.object(cloud_real_input.win32api, "keybd_event") as keybd,
            patch.object(cloud_real_input, "_AttachThreadInput") as attach,
        ):
            self.assertTrue(cloud_real_input.try_set_foreground(42))
        keybd.assert_not_called()
        attach.assert_not_called()

    def test_alt_and_attach_are_both_attempted_when_locked(self):
        with (
            patch.object(
                cloud_real_input.win32gui,
                "GetForegroundWindow",
                side_effect=[7, 7, 7, 42],
            ),
            patch.object(cloud_real_input.win32gui, "BringWindowToTop"),
            patch.object(cloud_real_input.win32gui, "SetForegroundWindow"),
            patch.object(cloud_real_input.win32api, "keybd_event") as keybd,
            patch.object(cloud_real_input.win32api, "GetCurrentThreadId", return_value=1),
            patch.object(
                cloud_real_input.win32process,
                "GetWindowThreadProcessId",
                side_effect=[(2, 0), (3, 0)],
            ),
            patch.object(cloud_real_input, "_AttachThreadInput", return_value=1) as attach,
            patch.object(cloud_real_input.time, "sleep"),
        ):
            self.assertTrue(cloud_real_input.try_set_foreground(42))
        self.assertEqual(keybd.call_count, 2)  # ALT down + ALT up
        self.assertEqual(attach.call_count, 4)  # 2 attaches + 2 detaches

    def test_activation_failure_is_reported_without_raising(self):
        with (
            patch.object(cloud_real_input.win32gui, "GetForegroundWindow", return_value=7),
            patch.object(cloud_real_input.win32gui, "BringWindowToTop"),
            patch.object(cloud_real_input.win32gui, "SetForegroundWindow"),
            patch.object(cloud_real_input.win32api, "keybd_event"),
            patch.object(cloud_real_input.win32api, "GetCurrentThreadId", return_value=1),
            patch.object(
                cloud_real_input.win32process,
                "GetWindowThreadProcessId",
                return_value=(2, 0),
            ),
            patch.object(cloud_real_input, "_AttachThreadInput", return_value=1),
            patch.object(cloud_real_input.time, "sleep"),
        ):
            self.assertFalse(cloud_real_input.try_set_foreground(42))

    def test_center_point_guard_rejects_a_foreign_window(self):
        with (
            patch.object(cloud_real_input.win32gui, "WindowFromPoint", return_value=99),
            patch.object(
                cloud_real_input.win32process,
                "GetWindowThreadProcessId",
                # pywin32 returns (thread_id, process_id)
                side_effect=[(0, 111), (0, 222)],
            ),
        ):
            self.assertFalse(cloud_real_input.point_belongs_to_window((10, 10), 42))
        with (
            patch.object(cloud_real_input.win32gui, "WindowFromPoint", return_value=99),
            patch.object(
                cloud_real_input.win32process,
                "GetWindowThreadProcessId",
                side_effect=[(0, 111), (0, 111)],
            ),
        ):
            self.assertTrue(cloud_real_input.point_belongs_to_window((10, 10), 42))

    def test_click_at_releases_the_button_even_if_the_hold_fails(self):
        with (
            patch.object(cloud_real_input, "set_cursor_pos") as set_pos,
            patch.object(cloud_real_input, "mouse_event", return_value=1) as mouse,
            patch.object(
                cloud_real_input.time,
                "sleep",
                side_effect=[None, RuntimeError("interrupted")],
            ),
        ):
            with self.assertRaises(RuntimeError):
                cloud_real_input.click_at((10, 10), hold=0)
        set_pos.assert_called_once_with((10, 10))
        self.assertEqual(
            [call.args[0] for call in mouse.call_args_list],
            [
                cloud_real_input.MOUSEEVENTF_LEFTDOWN,
                cloud_real_input.MOUSEEVENTF_LEFTUP,
            ],
        )

    def test_wheel_event_unsigned_delta(self):
        sent = []
        with patch.object(
            cloud_real_input, "send_input", side_effect=lambda i: sent.append(i) or 1
        ):
            self.assertEqual(cloud_real_input.wheel_event(-1), 1)
        self.assertEqual(
            sent[0].u.mi.dwFlags, cloud_real_input.MOUSEEVENTF_WHEEL
        )
        self.assertEqual(sent[0].u.mi.mouseData, (-120) & 0xFFFFFFFF)


class CloudVariantServicesTest(unittest.TestCase):
    # Audio target resolution and variant-switch rebinding are covered in
    # tests/test_audio_capture_reconcile.py. The old event-driven sync hook no
    # longer exists: SoundListener resolves the selected variant itself.

    def test_cursor_sync_is_inert_for_the_cloud_variant(self):
        from src.interaction.cursor_sync import CursorSync

        sync = CursorSync()
        baseline = sync._is_enabled()
        with patch(
            "src.patches.window_variants_patch.is_cloud_window_selected",
            return_value=True,
        ):
            self.assertFalse(sync._is_enabled())
        with patch(
            "src.patches.window_variants_patch.is_cloud_window_selected",
            return_value=False,
        ):
            self.assertEqual(sync._is_enabled(), baseline)


if __name__ == "__main__":
    unittest.main()
