import time

import win32api
import win32con
from ok.device.intercation import PostMessageInteraction
from ok.util.logger import Logger
from win32api import GetCursorPos, SetCursorPos

logger = Logger.get_logger(__name__)


class NTEInteraction(PostMessageInteraction):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_position = None

    def click(
        self, x=-1, y=-1, move_back=False, name=None, down_time=0.001, move=False, key="left"
    ):
        self.try_activate()
        if x < 0:
            click_pos = win32api.MAKELONG(
                round(self.capture.width * 0.5), round(self.capture.height * 0.5)
            )
        else:
            if move:
                self.cursor_position = GetCursorPos()
                abs_x, abs_y = self.capture.get_abs_cords(x, y)
                win32api.SetCursorPos((abs_x, abs_y))
                time.sleep(0.001)
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
        if x >= 0 and move:
            time.sleep(0.1)
            SetCursorPos(self.cursor_position)
