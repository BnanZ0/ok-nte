"""结算后回到主界面的观测与可选动作编排。"""

from collections.abc import Callable
from dataclasses import dataclass

from ok import TaskDisabledException, WaitFailedException

from src.tasks.auction.layout import AuctionBoxes, PostRoundState


@dataclass(frozen=True)
class PostRoundActions:
    """结算后编排与界面/领域服务之间的窄动作接口。"""

    remaining_timeout: Callable[[float | None, float], float | None]
    confirm_main_screen: Callable[[AuctionBoxes, float], bool]
    dismiss_notice: Callable[[AuctionBoxes, float, str], bool]
    uses_collection_sell: Callable[[], bool]
    detect_inventory_full: Callable[[AuctionBoxes, float], bool | None]
    observe_asset: Callable[[AuctionBoxes, float], int | None]
    welfare_enabled: Callable[[], bool]
    claim_welfare: Callable[[AuctionBoxes, float, int | None], bool]
    set_state: Callable[[PostRoundState], None]
    result_value: Callable[[], int | None]
    close_popup: Callable[[AuctionBoxes], bool]
    log_info: Callable[[str], None]
    log_warning: Callable[[str], None]


class AuctionPostRoundCoordinator:
    """确认回到主界面后收集观测, 再执行可选的领取。"""

    def __init__(self, actions: PostRoundActions) -> None:
        self.actions = actions

    def observe(
        self, boxes: AuctionBoxes, deadline: float, *, inventory_full_timeout: float
    ) -> None:
        """仅在确认回到拍卖主界面后执行结算后工作。"""
        # 可选预算耗尽返回 None: 不能把 None/0 透传给框架的等待 API.
        timeout = self.actions.remaining_timeout(deadline, 5)
        if timeout is None:
            return
        if not self.actions.confirm_main_screen(boxes, timeout):
            self.actions.log_warning("主界面「即刻落槌」标题未识别, 跳过本轮结算后观测")
            return

        self.actions.log_info("主界面确认完成, 开始结算后观测")
        self.run(boxes, deadline, inventory_full_timeout=inventory_full_timeout)

    def run(self, boxes: AuctionBoxes, deadline: float, *, inventory_full_timeout: float) -> None:
        """观测满仓与资产, 按需领取低保金, 然后写回本轮状态。"""
        # 库存不足提示条位于屏幕中部, 会被低保金弹窗和满仓提示遮挡, 必须先兜掉弹窗
        # 再做满仓观测, 否则满仓永远检测不到; 这里的顺序不能调换.
        self.actions.dismiss_notice(boxes, deadline, "结算后主界面")

        # 未检测到一律保持 None(而不是 False): None 表示「本轮未测出结论」, 轮次末尾
        # 的出售流程会在时间充裕时补测一次; 写成 False 等于宣称「确定没满仓」,
        # 会把满仓静默漏掉.
        inventory_full: bool | None = None
        if self.actions.uses_collection_sell():
            budget = self.actions.remaining_timeout(deadline, inventory_full_timeout)
            inventory_full = (
                None if budget is None else self.actions.detect_inventory_full(boxes, budget)
            )

        asset_value = self.actions.observe_asset(boxes, deadline)
        # 领取结果不参与出售决策: 出售看的是「今日低保是否领完」(弹窗读数维护的
        # 任务级状态), 不是「本轮有没有领到」; 这里只负责把领取流程走完, 观测和
        # 轮次末尾的出售各动手一次, 否则同一轮会卖两次.
        if self.actions.welfare_enabled():
            try:
                self.actions.claim_welfare(boxes, deadline, asset_value)
            except TaskDisabledException:
                raise
            except WaitFailedException as error:
                # 领取是可选的收尾动作, 单轮时间用尽时只跳过本次领取: 让异常传播会把
                # 已经结算成功的轮次判成失败, 还会跳过下面 observed=True 的写回,
                # 连带跳过轮次末尾的出售.
                self.actions.log_warning(f"低保金领取超时, 跳过本次领取: {error}")

        self.actions.set_state(
            PostRoundState(
                inventory_full=inventory_full,
                observed=True,
                result_value=self.actions.result_value(),
            )
        )
        # 收尾兜底: close_popup 的实现是「检测到弹窗才点击」, 没有弹窗时零点击,
        # 是否真的关掉了什么由它自己记录, 这里不再输出固定日志.
        self.actions.close_popup(boxes)
