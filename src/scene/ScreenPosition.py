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


