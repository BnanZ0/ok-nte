"""Real Win32 input injection for the cloud (streaming) game window.

Why this module exists
----------------------
The cloud client (NTECloudGame.exe / Qt51517QWindowIcon) does not consume
window messages. PostMessage / SendMessage WM_KEYDOWN and WM_LBUTTONDOWN are
accepted by the Qt shell but are never serialized to the remote game, because
the client collects input from the system input queue (low-level hooks / raw
input). Verified with the standalone probes in ``input_probe/``:

    SendInput(wVk=vk, wScan=0, flags=0)        -> works
    SendInput(wScan + KEYEVENTF_SCANCODE)       -> ignored
    pydirectinput (scan codes)                  -> ignored
    PostMessage / SendMessageTimeout           -> ignored
    SetCursorPos + SendInput(LEFTDOWN/LEFTUP)  -> works for many UI clicks

Note: SetCursorPos does not generate a mouse-move event. Cloud hover behavior
is still under real-hardware investigation, so this module deliberately does
not synthesize mouse moves.

A second, mouse-only constraint: after the window loses foreground the client
consumes the FIRST physical click to re-capture the mouse (the system cursor
flashes and no in-game action happens). probe_11 (one click, no effect),
probe_12 (two clicks, normal attack), probe_13 (one click, zero keyboard
events, cursor flashes and no attack) established this. The warm-up state
machine lives in NTEInteraction, which owns the focus-loss signal.

Implementation constraints
--------------------------
* A PRIVATE ``ctypes.WinDLL("user32")`` is required. ``ctypes.windll.user32``
  is a shared cached object; setting ``SendInput.argtypes`` on it corrupts
  pydirectinput, which uses the same function object with its own struct
  (observed as "expected LP_INPUT instance instead of LP_Input" in probe_04).
* ``wVk`` must be set and ``wScan`` must be 0. Pure scan-code events are
  dropped by the cloud client.

Foreground vs. the ok GUI
-------------------------
Clicking a task in the ok GUI leaves the GUI in the foreground, and Windows'
foreground lock refuses a plain SetForegroundWindow. ``try_set_foreground``
uses the ALT trick + AttachThreadInput (verified in input_probe/_common.py),
and ``point_belongs_to_window`` guards the injected center click so it can
never land on a GUI button. ``click_at`` guarantees a matching button-up,
because a stuck physical button/key after an aborted task is indistinguishable
from a frozen cursor to the user.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui
import win32process

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800

CURSOR_SHOWING = 0x00000001

CLICK_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

# Keys a physical keyboard reports with the extended-key bit. Matching the real
# hardware here matters for a client that forwards low-level keyboard events.
_EXTENDED_KEYS = frozenset(
    {
        win32con.VK_UP,
        win32con.VK_DOWN,
        win32con.VK_LEFT,
        win32con.VK_RIGHT,
        win32con.VK_HOME,
        win32con.VK_END,
        win32con.VK_PRIOR,
        win32con.VK_NEXT,
        win32con.VK_INSERT,
        win32con.VK_DELETE,
        win32con.VK_RCONTROL,
        win32con.VK_RMENU,
        win32con.VK_NUMLOCK,
        win32con.VK_DIVIDE,
        win32con.VK_SNAPSHOT,
    }
)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)

SendInput = _user32.SendInput
SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
SendInput.restype = wintypes.UINT

_GetCursorInfo = _user32.GetCursorInfo
_GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
_GetCursorInfo.restype = wintypes.BOOL

_AttachThreadInput = _user32.AttachThreadInput
_AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_AttachThreadInput.restype = wintypes.BOOL


def send_input(inp: INPUT) -> int:
    """Insert one INPUT into the system queue; 1 means success."""
    return int(SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))


def keyboard_input(vk: int, flags: int = 0) -> INPUT:
    """Virtual-key event. wScan stays 0 on purpose (scan-code events are dropped)."""
    return INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUTUNION(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags)),
    )


def mouse_input(flags: int, data: int = 0, dx: int = 0, dy: int = 0) -> INPUT:
    return INPUT(
        type=INPUT_MOUSE,
        u=_INPUTUNION(mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags)),
    )


def key_event(vk: int, up: bool = False) -> int:
    """Send one real key down/up. This is the confirmed-working cloud path."""
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in _EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    return send_input(keyboard_input(vk, flags))


def mouse_event(flags: int, data: int = 0) -> int:
    """Send one real mouse button/wheel event at the current cursor position."""
    return send_input(mouse_input(flags, data=data))


def xbutton_event(button: int, up: bool = False) -> int:
    """Send mouse4 / mouse5 (XBUTTON1 / XBUTTON2) down or up."""
    return mouse_event(MOUSEEVENTF_XUP if up else MOUSEEVENTF_XDOWN, data=button)


def jiggle_mouse(dx: int = 4, dy: int = 0) -> int:
    """Small symmetric relative nudge at the current position.

    A manual move wakes the cloud client's hover state; a SetCursorPos jump
    does not. Move left/up then right/down by the same amount so the net
    position is unchanged, while emitting two real MOUSEEVENTF_MOVE events.

    Deliberately NOT a path computed from GetCursorPos(): if the client is in
    relative mode, the system cursor and the game pointer differ and copying
    that delta makes the game pointer drift.

    Returns 1 when both events were sent, 0 on failure.
    """
    dx = abs(int(dx))
    dy = abs(int(dy))
    if dx == 0 and dy == 0:
        return 1
    first = send_input(mouse_input(MOUSEEVENTF_MOVE, dx=-dx, dy=-dy))
    time.sleep(0.03)
    second = send_input(mouse_input(MOUSEEVENTF_MOVE, dx=dx, dy=dy))
    return 1 if first == 1 and second == 1 else 0


def window_center(hwnd) -> tuple[int, int]:
    """Screen coordinates of a window center, used by the warm-up click."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return (left + right) // 2, (top + bottom) // 2


def raise_window(hwnd) -> None:
    """Lift the game above the terminal/GUI in Z-order, without resizing it.

    The injected warm-up click must land on the game window, not on whatever
    window is covering it. This is exactly what probe_12 did before click #1.
    """
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.BringWindowToTop(hwnd)
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_TOP,
        0,
        0,
        0,
        0,
        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
    )


def is_foreground(hwnd) -> bool:
    """Raw foreground check; the interaction prefers HwndWindow.is_foreground()."""
    return bool(win32gui.IsWindowVisible(hwnd) and win32gui.GetForegroundWindow() == hwnd)


def cursor_showing() -> bool:
    """Diagnostic only: a visible system cursor means the client has not captured it."""
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    if not _GetCursorInfo(ctypes.byref(info)):
        return False
    return bool(info.flags & CURSOR_SHOWING)


# ------------------------------------------------------------------ activation
# Windows' foreground lock refuses a plain SetForegroundWindow when the caller
# did not receive the last physical input. Running a task from the GUI or from
# Git Bash leaves the GUI/terminal in front, so the verified workarounds from
# input_probe/_common.py are used here:
#   1) ALT trick  -> a simulated ALT press counts as user input
#   2) AttachThreadInput -> join the foreground/target threads, then retry
# A plain injected center click additionally unlocks the lock, but it is only
# safe once the click target is known to belong to the game (see
# point_belongs_to_window): otherwise it would hit the GUI and could disable
# the running task. This exact failure was observed in the anomaly-domain log.
def foreground_info() -> tuple[int, str]:
    """Return (hwnd, title) of the current foreground window, for diagnostics."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        return hwnd, win32gui.GetWindowText(hwnd)
    except Exception:
        return 0, ""


def try_set_foreground(hwnd) -> bool:
    """Force the cloud window into the foreground with ALT + AttachThreadInput.

    Mirrors the activation used by the standalone probes (focus/_try_set_foreground),
    which successfully took the foreground away from Git Bash and the ok GUI.
    Never raises: failures are reported to the caller as False.
    """
    if not hwnd:
        return False
    try:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
    except Exception:
        return False

    # -- 1) ALT trick -------------------------------------------------------
    try:
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    except Exception:
        # The GUI may refuse the call; step 2 handles that case.
        pass
    time.sleep(0.15)
    try:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
    except Exception:
        return False

    # -- 2) AttachThreadInput ----------------------------------------------
    attached = []
    cur_tid = win32api.GetCurrentThreadId()
    try:
        fg = win32gui.GetForegroundWindow()
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
        tgt_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        for tid in (fg_tid, tgt_tid):
            if tid and tid != cur_tid and _AttachThreadInput(cur_tid, tid, True):
                attached.append(tid)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    finally:
        for tid in attached:
            try:
                _AttachThreadInput(cur_tid, tid, False)
            except Exception:
                pass
    time.sleep(0.15)
    try:
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def point_belongs_to_window(point, hwnd) -> bool:
    """True only when a screen point lands on hwnd's own process.

    Guards the warm-up click: after the GUI steals the foreground, an
    unguarded center click could land on a GUI button (observed: the running
    onetime task got disabled right after the warm-up click). Qt child windows
    share the process id, so this accepts those too.
    """
    try:
        target = win32gui.WindowFromPoint(tuple(point))
        if not target:
            return False
        _, target_pid = win32process.GetWindowThreadProcessId(target)
        _, game_pid = win32process.GetWindowThreadProcessId(hwnd)
        return bool(target_pid and game_pid and target_pid == game_pid)
    except Exception:
        return False


def set_cursor_pos(position) -> None:
    """Move the physical cursor WITHOUT going through CursorSync.

    CursorSync exists to restore the cursor while the local game runs in the
    background. Under real input it can pull the cursor away from the cloud
    window, so cloud input must bypass it entirely.

    This only moves the system cursor; it does NOT enqueue a mouse-move event.
    No simulated move is sent here while the cloud hover behavior is being
    investigated on real hardware.
    """
    win32api.SetCursorPos(tuple(position))


def get_cursor_pos() -> tuple[int, int] | None:
    """Diagnostic only: where Windows actually placed the physical cursor.

    Streaming clients that run in relative-mouse mode lock the cursor to the
    window center and ignore SetCursorPos. Comparing this with the requested
    click point tells us whether a missed UI click is a coordinate problem or
    the client's mouse capture.
    """
    try:
        x, y = win32api.GetCursorPos()
        return int(x), int(y)
    except Exception:
        return None


def click_at(point, hold: float = 0.30) -> tuple[int, int]:
    """One real left click at a screen point, with a guaranteed button-up.

    A missing LEFTUP leaves the physical mouse button held down, which is one
    of the ways the user can end up with an unusable cursor after a task stops.
    The finally block releases the button even if the hold is interrupted.
    Returns (SendInput result for down, SendInput result for up).
    """
    set_cursor_pos(point)
    time.sleep(0.05)
    down = mouse_event(MOUSEEVENTF_LEFTDOWN)
    try:
        time.sleep(hold)
    finally:
        up = mouse_event(MOUSEEVENTF_LEFTUP)
    return down, up


def wheel_event(amount: int) -> int:
    """Real vertical wheel event. mouseData is an unsigned DWORD delta."""
    data = (win32con.WHEEL_DELTA * int(amount)) & 0xFFFFFFFF
    return mouse_event(MOUSEEVENTF_WHEEL, data=data)
