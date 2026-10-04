"""拍卖流程中的阻塞弹窗关闭: 获得物品类与提示类的识别与点击。

模块函数的第一个参数 task 是 AutoBidAuctionTask 实例: OCR/点击/日志等框架
API 经它访问, 本模块不持有任务对象。出价、出售、结算后、回场兜底各流程
共用这一个入口, 任务侧保留同名 delegate 作为装配点与测试 seam。
"""

import time

from src.tasks.auction.contracts import AuctionPopupOps
from src.tasks.auction.layout import RE_CONFIRM, RE_POPUP_CLOSE_HINT, AuctionBoxes

# 结算/出价路径上有两类弹窗, 关闭方式不同:
# - 提示类(入场费确认 / 异常出价 / 购买仪器组确认): 标题「提示」在屏幕中部,
#   确认与取消按钮在底部同一组坐标, 点确认按钮关闭.
# - 获得物品类(拍品到手后弹出, 库存满时叠加「少于200格」警告横幅,
#   ok_templates/69-71.png): 没有确认按钮, 底部提示「点击空白区域关闭」,
#   必须点提示条以外的空白区域.
# 单帧 ocr 会漏掉刚出现的弹窗, 必须带短超时轮询.
NOTICE_POPUP_TIMEOUT = 2


def dismiss_notice_popup(
    task: AuctionPopupOps,
    boxes: AuctionBoxes,
    deadline: float | None,
    reason: str,
    *,
    timeout: float | None = None,
) -> bool:
    """点掉挡在流程前面的弹窗, 命中返回 True。

    两类弹窗的关闭方式不同, 每帧各查一次、共享同一份预算:
    - 获得物品类(拍品到手, 库存满时叠加「少于200格」警告横幅): 没有「确认」
      按钮, 底部提示「点击空白区域关闭」, 必须点提示条以外的空白区域
      (popup_blank) —— 按确认按钮模板处理会等不到按钮, 弹窗残留挡住后续识别
      与低保金领取 (见 auction-notes 5.5);
    - 提示类(入场费确认 / 异常出价 / 购买仪器组确认): 确认按钮在同一组
      坐标(popup_ok), 点按钮关闭。

    单帧 ocr 会漏掉刚出现的弹窗(之后整条流程卡在弹窗上), 所以带短超时轮询;
    每次出价都要走一遍的热路径用 timeout 调小预算, 避免固定白等。
    """
    budget = NOTICE_POPUP_TIMEOUT if timeout is None else timeout
    budget = task._optional_timeout(deadline, budget)
    if budget is None:
        return False
    probe_deadline = time.monotonic() + budget
    while True:
        if task.ocr(box=boxes.popup_close_hint, match=RE_POPUP_CLOSE_HINT, log=False):
            task.log_info(f"检测到获得物品弹窗({reason}), 点击空白关闭")
            task.operate_click(boxes.popup_blank, after_sleep=0.5)
            return True
        if task.ocr(box=boxes.popup_ok, match=RE_CONFIRM, log=False):
            task.log_info(f"检测到提示弹窗({reason}), 点击确认")
            task.operate_click(boxes.popup_ok, after_sleep=0.3)
            return True
        if time.monotonic() >= probe_deadline:
            return False
        task.sleep(0.3)
