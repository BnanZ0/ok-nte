from ok import Box


class ScreenPosition:
    """
    根据屏幕宽高生成各个位置的 Box。
    支持：
        - 固定位置（top_left、top_right 等）
        - 自定义 Box
        - 百分比/比例 Box
    使用 to_x / to_y 参数。
    """

    def __init__(self, parent):
        self.parent = parent  # parent 必须有 .width 和 .height

    # ---------- 固定位置 ----------
    @property
    def top_left(self) -> Box:
        return Box(x=0, y=0, to_x=self.parent.width // 2, to_y=self.parent.height // 2)

    @property
    def top_right(self) -> Box:
        return Box(x=self.parent.width // 2, y=0, to_x=self.parent.width, to_y=self.parent.height // 2)

    @property
    def bottom_left(self) -> Box:
        return Box(x=0, y=self.parent.height // 2, to_x=self.parent.width // 2, to_y=self.parent.height)

    @property
    def bottom_right(self) -> Box:
        return Box(x=self.parent.width // 2, y=self.parent.height // 2, to_x=self.parent.width, to_y=self.parent.height)

    @property
    def left(self) -> Box:
        return Box(x=0, y=0, to_x=self.parent.width // 2, to_y=self.parent.height)

    @property
    def right(self) -> Box:
        return Box(x=self.parent.width // 2, y=0, to_x=self.parent.width, to_y=self.parent.height)

    @property
    def top(self) -> Box:
        return Box(x=0, y=0, to_x=self.parent.width, to_y=self.parent.height // 2)

    @property
    def bottom(self) -> Box:
        return Box(x=0, y=self.parent.height // 2, to_x=self.parent.width, to_y=self.parent.height)

    @property
    def center(self) -> Box:
        return Box(x=self.parent.width // 4, y=self.parent.height // 4,
                   to_x=self.parent.width * 3 // 4, to_y=self.parent.height * 3 // 4)

    def _scale_box(self, x: int, y: int, w: int, h: int, ref_width: int = 2560, ref_height: int = 1440) -> Box:
        """将基于参考分辨率(2560x1440)的bbox缩放到当前屏幕分辨率"""
        scale_x = self.parent.width / ref_width
        scale_y = self.parent.height / ref_height
        return Box(
            x=int(x * scale_x),
            y=int(y * scale_y),
            to_x=int((x + w) * scale_x),
            to_y=int((y + h) * scale_y)
        )

    @property
    def dialog_icon_box(self) -> Box:
        """对话框右上角图标组box"""
        return self._scale_box(2164, 67, 2497 - 2164, 106 - 67)
