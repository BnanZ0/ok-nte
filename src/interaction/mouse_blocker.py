import ctypes
import threading
from ctypes import wintypes

import win32con
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

_WH_MOUSE_LL = 14
_LLMHF_INJECTED = 0x01
_WM_MOUSEHWHEEL = 0x020E
_WM_XBUTTONDOWN = 0x020B
_WM_XBUTTONUP = 0x020C
_BUTTON_UP_KEYS = {
    win32con.WM_LBUTTONUP: win32con.VK_LBUTTON,
    win32con.WM_RBUTTONUP: win32con.VK_RBUTTON,
    win32con.WM_MBUTTONUP: win32con.VK_MBUTTON,
}
_BUTTON_KEYS = (*_BUTTON_UP_KEYS.values(), win32con.VK_XBUTTON1, win32con.VK_XBUTTON2)
_MOUSE_MESSAGES = {
    win32con.WM_MOUSEMOVE,
    win32con.WM_MOUSEWHEEL,
    _WM_MOUSEHWHEEL,
    win32con.WM_LBUTTONDOWN,
    win32con.WM_LBUTTONUP,
    win32con.WM_RBUTTONDOWN,
    win32con.WM_RBUTTONUP,
    win32con.WM_MBUTTONDOWN,
    win32con.WM_MBUTTONUP,
    _WM_XBUTTONDOWN,
    _WM_XBUTTONUP,
}


class _MouseHookData(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MouseInputBlocker:
    """Temporarily suppress physical mouse input without touching keyboard input."""

    START_TIMEOUT = 2.0
    STOP_TIMEOUT = 1.0

    def __init__(self):
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._thread = None
        self._thread_id = 0
        self._error = None
        self._closed = False
        self._held_buttons = set()
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._hook_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        self._configure_api()

    def _configure_api(self):
        signatures = {
            "SetWindowsHookExW": (
                [ctypes.c_int, self._hook_type, wintypes.HINSTANCE, wintypes.DWORD],
                wintypes.HHOOK,
            ),
            "UnhookWindowsHookEx": ([wintypes.HHOOK], wintypes.BOOL),
            "CallNextHookEx": (
                [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM],
                ctypes.c_ssize_t,
            ),
            "GetAsyncKeyState": ([ctypes.c_int], ctypes.c_short),
            "PeekMessageW": (
                [
                    ctypes.POINTER(wintypes.MSG),
                    wintypes.HWND,
                    wintypes.UINT,
                    wintypes.UINT,
                    wintypes.UINT,
                ],
                wintypes.BOOL,
            ),
            "GetMessageW": (
                [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT],
                ctypes.c_int,
            ),
            "PostThreadMessageW": (
                [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
                wintypes.BOOL,
            ),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._user32, name)
            function.argtypes = arguments
            function.restype = result
        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def start(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Mouse input blocker is closed")
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("Mouse input blocker is already running")
            self._ready.clear()
            self._stop_event.clear()
            self._error = None
            self._thread_id = 0
            self._thread = threading.Thread(target=self._worker, daemon=True, name="NTEMouseBlock")
            self._thread.start()
            if not self._ready.wait(self.START_TIMEOUT):
                self.stop(raise_on_error=False)
                raise RuntimeError("Mouse hook startup timed out")
            if self._error is not None:
                error = self._error
                self.stop(raise_on_error=False)
                raise RuntimeError("Could not install mouse input hook") from error
            if not self._thread.is_alive() or self._stop_event.is_set():
                self.stop(raise_on_error=False)
                raise RuntimeError("Mouse input hook stopped during startup")

    def stop(self, *, raise_on_error=True):
        # Release input immediately, even if the worker takes time to exit.
        self._stop_event.set()
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            if thread.is_alive() and self._thread_id:
                if not self._user32.PostThreadMessageW(self._thread_id, win32con.WM_QUIT, 0, 0):
                    if thread.is_alive():
                        logger.error(f"Could not wake mouse hook thread: {ctypes.WinError()}")
            thread.join(self.STOP_TIMEOUT)
            if thread.is_alive():
                raise RuntimeError("Mouse hook shutdown timed out; mouse input is released")
            self._thread = None
            if self._error is not None:
                logger.error(f"Mouse input hook failed: {self._error}")
                if raise_on_error:
                    raise RuntimeError("Mouse input hook failed") from self._error

    def close(self):
        with self._lock:
            self._closed = True
            self.stop(raise_on_error=False)

    def _should_block(self, message, data):
        if self._stop_event.is_set() or data.flags & _LLMHF_INJECTED:
            return False
        if message not in _MOUSE_MESSAGES:
            return False
        key = _BUTTON_UP_KEYS.get(message)
        if message == _WM_XBUTTONUP:
            key = win32con.VK_XBUTTON1 if data.mouseData >> 16 == 1 else win32con.VK_XBUTTON2
        if key in self._held_buttons:
            # A down delivered before blocking must still have its matching up.
            self._held_buttons.remove(key)
            return False
        return True

    def _hook_callback(self, code, message, address):
        if code >= 0:
            try:
                data = ctypes.cast(address, ctypes.POINTER(_MouseHookData)).contents
                if self._should_block(message, data):
                    return 1
            except Exception as error:
                self._error = error
                self._stop_event.set()
        return self._user32.CallNextHookEx(None, code, message, address)

    def _worker(self):
        hook = None
        # Keep the ctypes callback alive until the hook is removed.
        callback = self._hook_type(self._hook_callback)
        try:
            message = wintypes.MSG()
            self._user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            self._thread_id = self._kernel32.GetCurrentThreadId()
            hook = self._user32.SetWindowsHookExW(_WH_MOUSE_LL, callback, None, 0)
            if not hook:
                raise ctypes.WinError(ctypes.get_last_error())
            self._held_buttons = {
                key for key in _BUTTON_KEYS if self._user32.GetAsyncKeyState(key) & 0x8000
            }
            self._ready.set()
            while not self._stop_event.is_set():
                result = self._user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    if not self._stop_event.is_set():
                        raise RuntimeError("Mouse hook message loop stopped unexpectedly")
                    break
        except Exception as error:
            self._error = error
        finally:
            self._stop_event.set()
            if hook and not self._user32.UnhookWindowsHookEx(hook):
                self._error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
