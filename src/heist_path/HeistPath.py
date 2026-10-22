from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.tasks.AutoHeistTask import AutoHeistTask

    _HeistPathTaskProxy = AutoHeistTask
else:

    class _HeistPathTaskProxy:
        pass


class HeistPath(_HeistPathTaskProxy):
    """路径脚本基类。

    路径类通过 `__getattr__` 透传 `AutoHeistTask` 的能力，因此路径内可以直接调用
    任务上的移动、交互、识别和 heist helper 方法。
    """

    def __init__(self, task: AutoHeistTask):
        self.exit_state = {
            1: False,
            2: False,
            3: False,
            4: False,
        }
        self.task = task

    def __getattr__(self, name: str) -> Any:
        return getattr(self.task, name)

    def sleep(self, timeout):
        """路径专用 sleep。

        大部分时间交给任务 sleep, 以便周期检查继续运行; 最后一小段用忙等减少
        录制路线的时间误差。
        """
        timer = self.clock.timer(timeout=timeout, precise=True)
        if timeout > 0.02:
            self.task.sleep(timeout - 0.02)
        while not timer.expired():
            pass
        return True
