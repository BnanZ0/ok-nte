"""拍卖表情领域: 出价确认成功后的表情发送动作。

模块函数的第一个参数 task 是 AutoBidAuctionTask 实例: 点击与日志经它访问,
允许访问面由 contracts.AuctionEmoteOps 窄协议声明。是否发送由任务侧
_send_emote_once 按「表情包」勾选判定, 本模块只承载动作序列与行为常量。

发送是「点表情按钮打开菜单, 再点菜单第一格」的两击序列, 用 atomic_sequence
挂起 sleep_check 钩子, 避免月卡弹窗插进两击之间; 坐标与时序沿革见
auction-notes 4。
"""

from src.tasks.auction.contracts import AuctionEmoteOps
from src.tasks.auction.interaction import atomic_sequence
from src.tasks.auction.layout import EMOTE_BTN, EMOTE_FIRST

# 菜单动画等待; 时序沿用 2026-10-02 删除前的原实现, 未经实机复验 (见 auction-notes 4)。
EMOTE_MENU_OPEN_DELAY = 0.8
EMOTE_SEND_DELAY = 0.5


def send_first_emote(task: AuctionEmoteOps) -> None:
    """发送表情菜单中的第一个表情 (收藏的第一个表情包)。"""
    with atomic_sequence(task, reason="表情发送"):
        task.operate_click(*EMOTE_BTN, after_sleep=EMOTE_MENU_OPEN_DELAY)
        task.operate_click(*EMOTE_FIRST, after_sleep=EMOTE_SEND_DELAY)
    task.log_info("表情包发送完成")
