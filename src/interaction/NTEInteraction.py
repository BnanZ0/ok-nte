from ok.device.intercation import PostMessageInteraction
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)


class NTEInteraction(PostMessageInteraction):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_position = None

    def click(
        self, x=-1, y=-1, move_back=False, name=None, down_time=0.001, move=False, key="left"
    ):
        # 使用 PostMessageInteraction 的后台点击逻辑，避免真实鼠标移动
        return super().click(
            x=x,
            y=y,
            move_back=move_back,
            name=name,
            down_time=down_time,
            move=move,
            key=key,
        )
