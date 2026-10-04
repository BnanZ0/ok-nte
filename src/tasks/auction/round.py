"""单轮拍卖的控制流与显式阶段转换。

两层状态刻意分开: `AuctionRoundPhase` 是只能经合法转换推进的控制流阶段,
识别到的界面 (`AuctionState`) 是业务动作产出的事件。界面识别, 超时与掉线
回场都留在动作里; 状态机只判定转换合法性, runner 负责编排。
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ok import WaitFailedException

from src.tasks.auction.layout import AuctionBoxes, AuctionState


class AuctionRoundPhase(Enum):
    """单轮拍卖的控制流阶段, 与识别到的界面状态相互独立。"""

    MATCHING = "matching"
    CONFIRMING = "confirming"
    BIDDING = "bidding"
    SETTLING = "settling"
    COMPLETE = "complete"


class AuctionRoundStateMachine:
    """按合法转换推进单轮拍卖的阶段。"""

    MAX_CONFIRM_ATTEMPTS = 2

    def __init__(self) -> None:
        self.phase = AuctionRoundPhase.MATCHING
        self.confirm_attempts = 0

    def on_match(self, screen: AuctionState) -> AuctionRoundPhase:
        """把匹配阶段识别到的界面路由到确认, 出价或结算。"""
        self._require_phase(AuctionRoundPhase.MATCHING)
        if screen is AuctionState.CONFIRM:
            self.confirm_attempts += 1
            return self._move(AuctionRoundPhase.CONFIRMING)
        if screen is AuctionState.BID:
            return self._move(AuctionRoundPhase.BIDDING)
        if screen is AuctionState.SKIP:
            return self._move(AuctionRoundPhase.SETTLING)
        raise WaitFailedException(f"匹配阶段返回了无法处理的界面状态: {screen!r}")

    def on_confirmation(self, succeeded: bool) -> AuctionRoundPhase:
        """确认成功进入出价; 失败时回匹配阶段重试一次。"""
        self._require_phase(AuctionRoundPhase.CONFIRMING)
        if succeeded:
            return self._move(AuctionRoundPhase.BIDDING)
        if self.confirm_attempts < self.MAX_CONFIRM_ATTEMPTS:
            return self._move(AuctionRoundPhase.MATCHING)
        raise WaitFailedException("确认阶段连续失败")

    def on_bidding_complete(self) -> AuctionRoundPhase:
        self._require_phase(AuctionRoundPhase.BIDDING)
        return self._move(AuctionRoundPhase.SETTLING)

    def on_settlement_complete(self) -> AuctionRoundPhase:
        """结算阶段结束, 进入 COMPLETE。

        COMPLETE 只表示「单轮控制流交还调用方」, 不表达拍卖是否成功:
        settle 动作的返回值 (True 拍卖结束 / False 结算时又出现出价界面) 由
        runner 原样返回, 任务层据此记成功或失败, 状态机不承载这一差异。
        """
        self._require_phase(AuctionRoundPhase.SETTLING)
        return self._move(AuctionRoundPhase.COMPLETE)

    def _move(self, next_phase: AuctionRoundPhase) -> AuctionRoundPhase:
        allowed = {
            AuctionRoundPhase.MATCHING: {
                AuctionRoundPhase.CONFIRMING,
                AuctionRoundPhase.BIDDING,
                AuctionRoundPhase.SETTLING,
            },
            AuctionRoundPhase.CONFIRMING: {
                AuctionRoundPhase.MATCHING,
                AuctionRoundPhase.BIDDING,
            },
            AuctionRoundPhase.BIDDING: {AuctionRoundPhase.SETTLING},
            AuctionRoundPhase.SETTLING: {AuctionRoundPhase.COMPLETE},
            AuctionRoundPhase.COMPLETE: set(),
        }
        if next_phase not in allowed[self.phase]:
            raise RuntimeError(
                f"非法拍卖阶段转换: {self.phase.value} -> {next_phase.value}"
            )
        self.phase = next_phase
        return self.phase

    def _require_phase(self, expected: AuctionRoundPhase) -> None:
        if self.phase is not expected:
            raise RuntimeError(
                f"拍卖阶段事件不匹配: 当前 {self.phase.value}, 预期 {expected.value}"
            )


@dataclass(frozen=True)
class AuctionRoundActions:
    """执行单轮所需的显式界面与业务动作。"""

    match: Callable[[AuctionBoxes, float], AuctionState]
    confirm: Callable[[AuctionBoxes, float], bool]
    wait_bid_screen: Callable[[AuctionBoxes, float], None]
    bid: Callable[[AuctionBoxes, float], None]
    settle: Callable[[AuctionBoxes, float], bool]
    set_phase: Callable[[str], None]
    warn: Callable[[str], None]


class AuctionRoundRunner:
    """按拍卖轮次状态机驱动界面业务动作。"""

    def __init__(self, actions: AuctionRoundActions) -> None:
        self.actions = actions

    def run(self, boxes: AuctionBoxes, deadline: float) -> bool:
        machine = AuctionRoundStateMachine()
        while machine.phase is not AuctionRoundPhase.COMPLETE:
            if machine.phase is AuctionRoundPhase.MATCHING:
                screen = self.actions.match(boxes, deadline)
                phase = machine.on_match(screen)
                if phase is AuctionRoundPhase.CONFIRMING:
                    self.actions.set_phase("确认中")
                elif phase is AuctionRoundPhase.BIDDING:
                    self.actions.set_phase("出价中")
                else:
                    self.actions.set_phase("结算中")
            elif machine.phase is AuctionRoundPhase.CONFIRMING:
                confirmed = self.actions.confirm(boxes, deadline)
                next_phase = machine.on_confirmation(confirmed)
                if next_phase is AuctionRoundPhase.BIDDING:
                    self.actions.set_phase("出价中")
                    self.actions.wait_bid_screen(boxes, deadline)
                elif next_phase is AuctionRoundPhase.MATCHING:
                    # warn 挂在状态机实际做出的「回匹配」转换上: 重试与否、
                    # 重试上限都由 on_confirmation 单点判定, 这里不重复条件。
                    self.actions.warn("确认阶段未完成, 回到匹配阶段重试, 不重置单轮超时")
                    # 初始「匹配中」由任务侧在启动 runner 前写入, 重试回匹配时
                    # 界面阶段要由这里补写, 否则面板仍显示「确认中」.
                    self.actions.set_phase("匹配中")
            elif machine.phase is AuctionRoundPhase.BIDDING:
                self.actions.bid(boxes, deadline)
                machine.on_bidding_complete()
                self.actions.set_phase("结算中")
            elif machine.phase is AuctionRoundPhase.SETTLING:
                finished = self.actions.settle(boxes, deadline)
                machine.on_settlement_complete()
                return finished
        # 不可达守卫: while 条件永不以 COMPLETE 复检(SETTLING 分支必然 return),
        # 保留作显式类型收尾, 真走到说明状态机转换被破坏。
        raise RuntimeError("拍卖轮次在没有结算结果时结束")
