"""拍卖流程与框架交互的收口: 原子点击序列的钩子挂起与滚动坐标换算。

TaskExecutor.sleep 会把长 sleep 切成不超过 sleep_check_interval 的片段, 每片段
回调一次 task.sleep_check(), 而本任务的钩子会点击月卡弹窗; operate_click 的尾部
又是一次 self.sleep(after_sleep), 于是「清空→逐位输入」「放弃→放弃确认」
「品质圆点逐次点击」这类必须连续完成的序列中间都可能被插进一次弹窗点击
(背景见 auction-notes 5.8)。atomic_sequence 把序列期间的 sleep_check_interval
置 -1(框架约定的禁用值), 结束后恢复, 让弹窗兜底退回到序列边界之外。

BaseNTETask.scroll 对相对坐标只做 float 夹逼换算, 不走 click_relative /
box_of_screen 的 out_of_ratio 分支, 超宽屏下落点偏移 (背景见 auction-notes 5.8);
scroll_screen 用 box_of_screen 同口径先换算成绝对像素再滚动。
"""

from contextlib import contextmanager


@contextmanager
def atomic_sequence(task, *, reason: str):
    """在必须连续完成的点击序列期间挂起 sleep_check 钩子。

    进入时把 task.sleep_check_interval 置 -1, 退出时(含异常路径)恢复原值。
    只包「必须连续」的点击段; 观察窗与长等待不要包进来 —— 挂起期间弹窗无人
    处理, 序列会失败重试, 属可接受降级。
    """
    previous = task.sleep_check_interval
    task.sleep_check_interval = -1
    task.log_debug(f"进入原子序列: {reason}")
    try:
        yield
    finally:
        task.sleep_check_interval = previous
        task.log_debug(f"退出原子序列: {reason}")


def scroll_screen(task, x, y, count):
    """按 box_of_screen 同口径把相对坐标换算成绝对像素后滚动。

    换算得到的 int 坐标会绕过 BaseNTETask.scroll 的 float 夹逼分支, 原样透传。
    box_of_screen 第 3/4 个位置参数是矩形「到点」而非宽高, 必须省略用默认值
    构造「从落点出发」的有效 Box 再取左上角; 误传 0 会得到负宽高使 Box 构造
    抛 ValueError (见 auction-notes 5.8)。
    """
    point = task.box_of_screen(x, y)
    task.scroll(point.x, point.y, count)
