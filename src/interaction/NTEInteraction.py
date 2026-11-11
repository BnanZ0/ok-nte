import ctypes
import threading
import time
from typing import Any

import win32api
import win32con
import win32gui
from ok import og
from ok.device.interaction_methods.keys import get_mouse_button, is_mouse_button
from ok.device.intercation import (
    INPUT,
    MOUSEINPUT,
    PostMessageInteraction,
    SendInput,
)
from ok.util.logger import Logger
from win32api import GetCursorPos

from src.interaction import cloud_real_input
from src.interaction.cursor_sync import CursorSync
from src.interaction.keyboard_layout import QwertyPhysicalKeyMapper
from src.interaction.mouse_blocker import MouseInputBlocker
from src.utils import clock

logger = Logger.get_logger(__name__)

_cloud_variant_checker = None


def _not_cloud() -> bool:
    """Fallback when the variant patch cannot be imported."""
    return False


def _cloud_variant_selected() -> bool:
    """True when the chosen window is PC版云异环.

    The import is deliberately lazy: src.config imports NTEInteraction at
    module load, while window_variants_patch imports WINDOW_VARIANTS from
    src.config. A module-level import here would create a circular import.
    Any failure means "not cloud", so the local PostMessage path stays the
    safe default.
    """
    global _cloud_variant_checker
    if _cloud_variant_checker is None:
        try:
            from src.patches.window_variants_patch import is_cloud_window_selected

            _cloud_variant_checker = is_cloud_window_selected
        except Exception as exc:
            logger.warning(f"cloud variant check unavailable, using local input: {exc}")
            _cloud_variant_checker = _not_cloud
    try:
        return bool(_cloud_variant_checker())
    except Exception as exc:
        logger.warning(f"cloud variant check failed, using local input: {exc}")
        return False


class NTEInteraction(PostMessageInteraction):
    _ACTIVATE_REFRESH_INTERVAL = 60 * 60

    # Cloud-client timing, taken from the verified input_probe results.
    # probe_12 clicked twice with hold=0.30s and a 0.50s gap; the second
    # click produced a normal attack. probe_13 proved a single click after
    # focus loss is consumed to re-capture the mouse.
    _CLOUD_WARMUP_HOLD = 0.30
    _CLOUD_WARMUP_SETTLE = 0.50
    _CLOUD_FOREGROUND_TIMEOUT = 2.0
    _CLOUD_MIN_DOWN_TIME = 0.05
    # on_visible()/on_destroy() run on other threads. Wait for an in-flight
    # real-input sequence, but never block teardown forever if it is stuck.
    _CLOUD_RELEASE_LOCK_TIMEOUT = 3.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_position = None
        self._operating = False
        self._input_lock = threading.RLock()
        self._mouse_blocker = MouseInputBlocker()
        self._mouse_block_depth = 0
        self.user32 = ctypes.windll.user32
        self.qwerty_physical_key_mapper = QwertyPhysicalKeyMapper()
        self._disable_key_mapping = 0
        self._activate_require = True
        self._next_try_activate_at = clock.UNSET
        self._cursor_sync = CursorSync()
        self._cursor_sync.start()
        # Cloud only: False means the client has to re-capture the mouse, so
        # the next real input must be preceded by one warm-up click.
        self._cloud_captured = False
        # Real SendInput keys/buttons are GLOBAL: if a task is interrupted
        # between down and up, the physical key stays held down until it is
        # released. Track them so focus loss / destroy can always clean up.
        self._cloud_pressed_vks = set()
        self._cloud_pressed_buttons = set()
        self.hwnd_window.visible_monitors.append(self)

    def on_visible(self, visible):
        self._activate_require = not visible
        if not visible:
            logger.info(
                "input: focus lost (visible=False); "
                f"captured={getattr(self, '_cloud_captured', None)} "
                f"held_vks={sorted(getattr(self, '_cloud_pressed_vks', set()))} "
                f"held_buttons={sorted(getattr(self, '_cloud_pressed_buttons', set()))}"
            )
            self._cloud_release_after_focus_change()
        else:
            logger.info("input: focus gained (visible=True)")

    def on_destroy(self):
        try:
            self._cloud_release_after_focus_change()
            # Never leave a low-level mouse hook installed after a task aborts.
            self._mouse_block_depth = 0
            self._mouse_blocker.stop(raise_on_error=False)
        except Exception as exc:
            logger.error(f"cloud input cleanup failed: {exc}")
        finally:
            try:
                self._mouse_blocker.close()
            finally:
                self._cursor_sync.stop()
                super().on_destroy()

    def send_key(self, key, down_time=0.01):
        with self._input_lock:
            key = self._map_key(key)
            self._disable_key_mapping += 1
            try:
                return super().send_key(key, down_time=down_time)
            finally:
                self._disable_key_mapping -= 1
    
    def send_key_down(self, key, activate=True) -> Any:
        with self._input_lock:
            key = self._map_key(key)
            if _cloud_variant_selected():
                return self._cloud_key_down(key)
            if activate:
                self.try_activate()
            vk_code = self.get_key_by_str(key)
            lparam = self.make_lparam(vk_code, is_up=False)
            return self.post(win32con.WM_KEYDOWN, vk_code, lparam)

    def send_key_up(self, key) -> Any:
        with self._input_lock:
            key = self._map_key(key)
            if _cloud_variant_selected():
                return self._cloud_key_up(key)
            vk_code = self.get_key_by_str(key)
            lparam = self.make_lparam(vk_code, is_up=True)
            return self.post(win32con.WM_KEYUP, vk_code, lparam)

    def post(self, message, wParam=0, lParam=0, hwnd=None) -> Any:
        try:
            if hwnd is None:
                hwnd = self.hwnd
            win32gui.PostMessage(hwnd, message, wParam, lParam)
        except Exception as exc:
            logger.error(f"PostMessage failed for message {message:#x}: {exc}")
            return False
        return True

    # ------------------------------------------------------- cloud real input
    # The cloud client uploads input from the system input queue, so every
    # key/click must be a real SendInput event. The local variant above stays
    # completely untouched.
    def _cloud_hwnd(self):
        window = self.hwnd_window
        return window.top_hwnd or window.hwnd

    def _cloud_is_foreground(self):
        try:
            return bool(self.hwnd_window.is_foreground())
        except Exception:
            return cloud_real_input.is_foreground(self._cloud_hwnd())

    def _cloud_set_cursor_pos(self, position):
        """Cloud input never goes through CursorSync.

        CursorSync pulls the cursor back to a saved position whenever the game
        is not foreground. With real input that fights the cloud client's mouse
        capture and can leave the cursor apparently stuck, so cloud input uses
        the physical SetCursorPos directly. No synthetic mouse move is sent
        while the hover/capture behavior is being investigated.
        """
        cloud_real_input.set_cursor_pos(position)

    def _cloud_release_after_focus_change(self):
        """Release held real inputs with the input lock held.

        on_visible()/on_destroy() run on the HwndWindow poll and teardown
        threads. Releasing without the lock can observe an empty pressed set
        just before the executor adds the key it is sending, leaving that key
        physically held forever. A bounded acquire still guarantees cleanup if
        the input thread is stuck.
        """
        acquired = self._input_lock.acquire(timeout=self._CLOUD_RELEASE_LOCK_TIMEOUT)
        try:
            self._cloud_captured = False
            self._cloud_release_all()
        finally:
            if acquired:
                self._input_lock.release()

    def _cloud_release_all(self):
        """Release every real key/button still held down.

        SendInput events go into the SYSTEM input queue, not a window message
        queue. If a task is disabled between down and up (exactly what happened
        to 异象界域), the key or mouse button stays physically held. This is
        called on focus loss and on destroy so a stopped task cannot leave the
        user with a stuck keyboard/mouse.
        """
        vks = getattr(self, "_cloud_pressed_vks", None)
        buttons = getattr(self, "_cloud_pressed_buttons", None)
        if not vks and not buttons:
            return
        released = []
        for vk in list(vks or ()):
            try:
                cloud_real_input.key_event(vk, up=True)
            except Exception as exc:
                logger.error(f"cloud input: failed to release vk=0x{vk:02X}: {exc}")
            vks.discard(vk)
            released.append(f"vk=0x{vk:02X}")
        for up_flag, data in list(buttons or ()):
            try:
                cloud_real_input.mouse_event(up_flag, data=data)
            except Exception as exc:
                logger.error(f"cloud input: failed to release button {up_flag:#x}: {exc}")
            buttons.discard((up_flag, data))
            released.append(f"button={up_flag:#x}")
        logger.info(f"cloud input: released held real inputs: {', '.join(released)}")

    def _cloud_prepare(self):
        """Ensure foreground and mouse capture before any real input.

        Two verified constraints are handled here:

        * Windows' foreground lock refuses a plain SetForegroundWindow when the
          ok GUI (or a terminal) last received input. input_probe proved that
          ALT + AttachThreadInput, plus one injected center click, reliably
          takes the foreground back. Without this, the very first key of a
          onetime task (e.g. F1 in 异象界域) was never sent and the task
          silently "completed".
        * After a focus loss the cloud client consumes the FIRST physical click
          to re-capture the mouse (probe_11/13 no effect, probe_12 second click
          works). One center click is therefore spent as a warm-up.

        The warm-up click is only injected when the center point belongs to the
        game process. An unguarded click once landed on the ok GUI and disabled
        the running task. In steady state this is a cheap foreground check.
        """
        if getattr(self, "_cloud_captured", False) and self._cloud_is_foreground():
            return True
        try:
            hwnd = self._cloud_hwnd()
            fg_hwnd, fg_title = cloud_real_input.foreground_info()
            logger.info(
                f"cloud input: prepare start game_hwnd={hwnd} "
                f"foreground_hwnd={fg_hwnd} foreground_title={fg_title!r} "
                f"captured={getattr(self, '_cloud_captured', False)}"
            )
            cloud_real_input.raise_window(hwnd)
            activated = cloud_real_input.try_set_foreground(hwnd)
            logger.info(f"cloud input: activate#1 ALT/Attach result={activated}")

            center = cloud_real_input.window_center(hwnd)
            safe = cloud_real_input.point_belongs_to_window(center, hwnd)
            if not safe and activated:
                # The foreground switch can also change Z-order. Re-raise and
                # re-check before giving up on the warm-up click.
                cloud_real_input.raise_window(hwnd)
                safe = cloud_real_input.point_belongs_to_window(center, hwnd)
            clicks = None
            if not getattr(self, "_cloud_captured", False) and safe:
                # This single click both unlocks the foreground and is consumed
                # by the cloud client to re-capture the mouse.
                clicks = cloud_real_input.click_at(center, hold=self._CLOUD_WARMUP_HOLD)
                logger.info(
                    f"cloud input: warm-up center click at {center} "
                    f"SendInput(down,up)={clicks}"
                )
                time.sleep(self._CLOUD_WARMUP_SETTLE)
                activated = cloud_real_input.try_set_foreground(hwnd)
                logger.info(f"cloud input: activate#2 ALT/Attach result={activated}")
            elif not safe:
                logger.warning(
                    f"cloud input: center point {center} is not on the game window, "
                    "skipping warm-up click to avoid clicking the GUI"
                )
                cloud_real_input.try_set_foreground(hwnd)

            timer = clock.timer(timeout=self._CLOUD_FOREGROUND_TIMEOUT)
            while not timer.expired():
                if self._cloud_is_foreground():
                    break
                time.sleep(0.05)
            ready = self._cloud_is_foreground()
            fg_hwnd, fg_title = cloud_real_input.foreground_info()
            logger.info(
                f"cloud input: prepare done ready={ready} game_hwnd={hwnd} "
                f"foreground_hwnd={fg_hwnd} foreground_title={fg_title!r} "
                f"warmup_click={clicks}"
            )
        except Exception as exc:
            logger.error(f"cloud input warm-up failed: {exc}")
            self._cloud_captured = False
            self._cloud_release_all()
            return False
        # Only a real (consumed) warm-up click re-establishes mouse capture.
        # If the click was skipped because the center point was unsafe, keep
        # trying on the next input instead of pretending capture succeeded.
        self._cloud_captured = ready and clicks is not None
        if not ready:
            logger.error(
                "cloud input: game window is not foreground, refusing to send "
                "real input to another window. The task will stop without input."
            )
            self._cloud_release_all()
        return ready

    def _cloud_vk(self, key):
        try:
            value = self.get_key_by_str(key)
        except Exception as exc:
            logger.error(f"cloud input: cannot resolve key {key!r}: {exc}")
            return None
        if value is None or value < 0:
            return None
        # VkKeyScan packs the shift state into the high byte. The local
        # PostMessage path passes the whole value; SendInput needs just the VK.
        return value & 0xFF

    def _cloud_mouse_button(self, button, up):
        release = (cloud_real_input.MOUSEEVENTF_XUP, button)
        if up:
            # Releases are always sent, even after focus loss: a stray button-up
            # cannot trigger anything, but skipping it would leave a stuck button.
            sent = cloud_real_input.xbutton_event(button, up=True) == 1
            if sent:
                self._cloud_pressed_buttons.discard(release)
            logger.debug(f"cloud input: xbutton up button={button} sent={sent}")
            if not sent:
                logger.warning(f"cloud input: SendInput xbutton up failed button={button}")
            return sent
        if not self._cloud_prepare():
            return False
        sent = cloud_real_input.xbutton_event(button, up=False) == 1
        if sent:
            self._cloud_pressed_buttons.add(release)
        logger.debug(f"cloud input: xbutton down button={button} sent={sent}")
        if not sent:
            logger.warning(f"cloud input: SendInput xbutton down failed button={button}")
        return sent

    def _cloud_key_down(self, key):
        if is_mouse_button(key):
            return self._cloud_mouse_button(get_mouse_button(key), up=False)
        if not self._cloud_prepare():
            return False
        vk = self._cloud_vk(key)
        if vk is None:
            return False
        sent = cloud_real_input.key_event(vk, up=False) == 1
        if sent:
            self._cloud_pressed_vks.add(vk)
        # DEBUG: keys are sent frequently during combat; keep the log but
        # avoid INFO noise. A failed injection is always visible as WARNING.
        logger.debug(f"cloud input: key down vk=0x{vk:02X} sent={sent}")
        if not sent:
            logger.warning(f"cloud input: SendInput key down failed vk=0x{vk:02X}")
        return sent

    def _cloud_key_up(self, key):
        if is_mouse_button(key):
            return self._cloud_mouse_button(get_mouse_button(key), up=True)
        # Same reasoning as the mouse release above: never leave a key held.
        vk = self._cloud_vk(key)
        if vk is None:
            return False
        sent = cloud_real_input.key_event(vk, up=True) == 1
        if sent:
            self._cloud_pressed_vks.discard(vk)
        logger.debug(f"cloud input: key up vk=0x{vk:02X} sent={sent}")
        if not sent:
            logger.warning(f"cloud input: SendInput key up failed vk=0x{vk:02X}")
        return sent

    def _cloud_click(self, x, y, down_time, move, key):
        # No cursor restore / move_back here: real input needs the physical
        # cursor inside the game window, and cloud mode cannot run in the
        # background anyway.
        if not self._cloud_prepare():
            return False
        down_flag, up_flag = cloud_real_input.CLICK_BUTTON_FLAGS.get(
            key, cloud_real_input.CLICK_BUTTON_FLAGS["left"]
        )
        release = (up_flag, 0)
        try:
            if x < 0:
                x, y = round(self.capture.width * 0.5), round(self.capture.height * 0.5)
            if move:
                abs_x, abs_y = self.capture.get_abs_cords(x, y)
                self._cloud_set_cursor_pos((abs_x, abs_y))
                jiggled = cloud_real_input.jiggle_mouse(4, 0)
                # Give the client a moment to turn the relative nudge into a
                # hover state before the click.
                time.sleep(0.08)
                if jiggled == 0:
                    logger.warning(
                        f"cloud input: mouse jiggle failed at ({abs_x},{abs_y})"
                    )
                actual = cloud_real_input.get_cursor_pos()
                # Kept at INFO: this line is the primary evidence for diagnosing
                # "clicks do nothing" on the cloud client, and DEBUG may be off.
                logger.info(
                    f"cloud input: click ({x},{y}) screen=({abs_x},{abs_y}) cursor={actual}"
                )
                if (
                    actual is not None
                    and not getattr(self, "_cloud_cursor_warned", False)
                    and (abs(actual[0] - abs_x) > 3 or abs(actual[1] - abs_y) > 3)
                ):
                    # The streaming client may lock the cursor to the center
                    # (relative-mouse mode). Every non-center UI click would
                    # then land in the wrong place with no error at all.
                    self._cloud_cursor_warned = True
                    logger.warning(
                        f"cloud input: cursor did not reach the click target "
                        f"requested=({abs_x},{abs_y}) actual={actual}. "
                        f"The cloud client may be capturing relative mouse input."
                    )
            n_down = cloud_real_input.mouse_event(down_flag)
            if n_down:
                self._cloud_pressed_buttons.add(release)
            try:
                time.sleep(max(down_time, self._CLOUD_MIN_DOWN_TIME))
            finally:
                # A missing LEFTUP leaves the physical button held forever.
                n_up = cloud_real_input.mouse_event(up_flag)
                self._cloud_pressed_buttons.discard(release)
            if not (n_down and n_up):
                logger.warning(
                    f"cloud input: click SendInput failed down={n_down} up={n_up}"
                )
            return bool(n_down and n_up)
        except Exception as exc:
            logger.error(f"cloud input click failed: {exc}")
            return False

    def _cloud_scroll(self, x, y, scroll_amount):
        """Real mouse wheel. The old PostMessage(WM_MOUSEWHEEL) is ignored by
        the cloud client, just like its PostMessage keyboard/mouse events."""
        if not self._cloud_prepare():
            return False
        try:
            if x > 0 and y > 0:
                abs_x, abs_y = self.capture.get_abs_cords(x, y)
                self._cloud_set_cursor_pos((abs_x, abs_y))
                time.sleep(0.035)
            return cloud_real_input.wheel_event(scroll_amount) == 1
        except Exception as exc:
            logger.error(f"cloud input scroll failed: {exc}")
            return False

    def scroll(self, x, y, scroll_amount):
        with self._input_lock:
            if _cloud_variant_selected():
                return self._cloud_scroll(x, y, scroll_amount)
            self.try_activate()
            # logger.debug(f"scroll {x}, {y}, {scroll_amount}")

            base_hwnd = (
                self.hwnd_window.top_hwnd if self.hwnd_window.top_hwnd else self.hwnd_window.hwnd
            )
            if x > 0 and y > 0:
                top_x, top_y = self.hwnd_window.get_top_window_cords(x, y)
                abs_x, abs_y = win32gui.ClientToScreen(base_hwnd, (int(top_x), int(top_y)))
                self.bg_mouse_pos = (top_x, top_y)
                self._dynamic_target_hwnd = self._target_hwnd_at(abs_x, abs_y, base_hwnd)
                long_position = win32api.MAKELONG(abs_x, abs_y)
            else:
                self._dynamic_target_hwnd = base_hwnd
                long_position = 0

            wparam = win32api.MAKELONG(0, win32con.WHEEL_DELTA * scroll_amount)
            self.post(win32con.WM_MOUSEWHEEL, wparam, long_position)

    def _target_hwnd_at(self, abs_x, abs_y, fallback_hwnd):
        for hwnd_info in getattr(self.hwnd_window, "hwnds", []):
            candidate = hwnd_info[0]
            if not win32gui.IsWindow(candidate):
                continue
            try:
                left = hwnd_info[4]
                top = hwnd_info[5]
                right = left + hwnd_info[2]
                bottom = top + hwnd_info[3]
                if left <= abs_x < right and top <= abs_y < bottom:
                    return candidate
            except Exception:
                continue
        return fallback_hwnd

    def _map_key(self, key):
        if self._disable_key_mapping or not og.global_config.get_config("Game Hotkey Config").get(
            "Use QWERTY Physical Keys", False
        ):
            return key

        return self.qwerty_physical_key_mapper.map_key(key) or key

    def click(self, x=-1, y=-1, move_back=False, name=None, down_time=0.01, move=True, key="left"):
        with self._input_lock:
            if _cloud_variant_selected():
                return self._cloud_click(x, y, down_time, move, key)
            self.try_activate()
            if x < 0:
                x, y = round(self.capture.width * 0.5), round(self.capture.height * 0.5)

            should_restore = move and move_back and not self._operating
            if move:
                if should_restore:
                    self.cursor_position = GetCursorPos()
                abs_x, abs_y = self.capture.get_abs_cords(x, y)
                self._cursor_sync.set_cursor_pos((abs_x, abs_y))
                time.sleep(0.035)
            click_pos = win32api.MAKELONG(x, y)
            if key == "left":
                btn_down = win32con.WM_LBUTTONDOWN
                btn_mk = win32con.MK_LBUTTON
                btn_up = win32con.WM_LBUTTONUP
            elif key == "middle":
                btn_down = win32con.WM_MBUTTONDOWN
                btn_mk = win32con.MK_MBUTTON
                btn_up = win32con.WM_MBUTTONUP
            else:
                btn_down = win32con.WM_RBUTTONDOWN
                btn_mk = win32con.MK_RBUTTON
                btn_up = win32con.WM_RBUTTONUP
            self.post(btn_down, btn_mk, click_pos)
            time.sleep(down_time)
            self.post(btn_up, 0, click_pos)
            if should_restore:
                self._restore_cursor()

    def operate(self, fun, block=False, restore_cursor=True):
        with self._input_lock:
            result = None
            reset_scene_executor = None
            is_outer_operate = not self._operating
            cursor_saved = False
            input_blocked = False
            try:
                if block:
                    self.block_input()
                    input_blocked = True
                if is_outer_operate:
                    self.cursor_position = GetCursorPos()
                    self._operating = True
                    cursor_saved = True
                reset_scene_executor = self._enter_reset_scene_without_check()
                result = fun()
            except Exception as e:
                logger.error("operate exception", e)
                raise
            finally:
                try:
                    try:
                        self._exit_reset_scene_without_check(reset_scene_executor)
                    finally:
                        if cursor_saved:
                            self._operating = False
                            # Real input must keep the physical cursor inside the
                            # cloud window. Restoring it to a pre-click position
                            # (often on the ok GUI) breaks the client's capture.
                            if restore_cursor and not _cloud_variant_selected():
                                self._restore_cursor()
                finally:
                    if input_blocked:
                        self.unblock_input()
            return result

    def _enter_reset_scene_without_check(self):
        device_manager = getattr(self.hwnd_window, "device_manager", None)
        executor = getattr(device_manager, "executor", None)
        if executor is None:
            return None

        if not hasattr(executor, "_nte_operate_reset_scene_depth"):
            executor._nte_operate_reset_scene_depth = 0

        if not hasattr(executor, "_nte_original_reset_scene"):
            executor._nte_original_reset_scene = executor.reset_scene

            def reset_scene(check_enabled=True):
                if executor._nte_operate_reset_scene_depth > 0:
                    check_enabled = False
                return executor._nte_original_reset_scene(check_enabled=check_enabled)

            executor.reset_scene = reset_scene

        executor._nte_operate_reset_scene_depth += 1
        return executor

    def _exit_reset_scene_without_check(self, executor):
        if executor is None:
            return
        executor._nte_operate_reset_scene_depth = max(
            0, executor._nte_operate_reset_scene_depth - 1
        )

    def _restore_cursor(self):
        time.sleep(0.035)
        try:
            self._cursor_sync.set_cursor_pos(self.cursor_position)
        except Exception as e:
            logger.error("restore cursor exception", e)

    def block_input(self):
        """Block physical mouse input only; keyboard input remains available.

        The cloud variant never installs this hook: its injected events are
        already allowed through (LLMHF_INJECTED), while the hook swallows the
        user's PHYSICAL mouse moves. A hook left behind after an aborted task
        looks exactly like a frozen cursor.
        """
        with self._input_lock:
            if _cloud_variant_selected():
                return
            if self._mouse_block_depth == 0:
                self._mouse_blocker.start()
            self._mouse_block_depth += 1

    def unblock_input(self):
        with self._input_lock:
            if _cloud_variant_selected():
                # Also clean up a leftover hook if the variant was switched
                # while a local-variant operate() was still on the stack.
                if self._mouse_block_depth:
                    self._mouse_block_depth = 0
                    try:
                        self._mouse_blocker.stop(raise_on_error=False)
                    except Exception as exc:
                        logger.error(f"cloud input: mouse hook cleanup failed: {exc}")
                return
            if self._mouse_block_depth == 0:
                return
            self._mouse_block_depth -= 1
            if self._mouse_block_depth == 0:
                self._mouse_blocker.stop()

    def move_mouse_relative(self, dx, dy):
        """
        Moves the mouse cursor relative to its current position using user32.SendInput.

        Args:
            dx: The number of pixels to move the mouse horizontally.
                (positive for right, negative for left).
            dy: The number of pixels to move the mouse vertically.
                (positive for down, negative for up).
        """
        if _cloud_variant_selected():
            # Real input is global: never move the cursor while the GUI owns
            # the foreground.
            if not self._cloud_prepare():
                return 0
        else:
            self._cursor_sync.mark_internal_move()
        mi = MOUSEINPUT(dx, dy, 0, 1, 0, None)
        i = INPUT(0, mi)  # type=0 indicates a mouse event
        return SendInput(1, ctypes.pointer(i), ctypes.sizeof(INPUT))

    def try_activate(self):
        now = clock.now()
        if self._activate_require:
            if not self.hwnd_window.is_foreground():
                super().try_activate()
                self._next_try_activate_at = now + self._ACTIVATE_REFRESH_INTERVAL
            self._activate_require = False
        elif now >= self._next_try_activate_at:
            super().try_activate()
            self._next_try_activate_at = now + self._ACTIVATE_REFRESH_INTERVAL
