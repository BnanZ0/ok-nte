from datetime import datetime

from qfluentwidgets import FluentIcon

from ok import TaskDisabledException, find_color_rectangles
from src import text_white_color
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.utils import image_utils as iu


class DailyTask(BaseNTETask):
    """日常任务执行器"""

    DEFAULT_MOVE = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.description = "暂不可用"
        self.icon = FluentIcon.SYNC
        self.support_schedule_task = False
        self.task_status = {"success": [], "failed": [], "skipped": [], "pending": []}

        self.default_config.update(
            {
                "领取邮件": True,
                "完成每日活跃度": True,
                "领取活跃度奖励": True,
                "领取环期任务奖励": True,
            }
        )
        self.current_task_key = None
        self.add_exit_after_config()

    def run(self):
        try:
            self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self._handle_exception(e)

    def do_run(self):
        """执行日常任务主流程"""
        self.log_info("开始执行日常任务")

        tasks = [
            ("领取邮件", self.claim_mail),
            ("完成每日活跃度", self.complete_daily_activities),
            ("领取活跃度奖励", self.claim_activity_rewards),
            ("领取环期任务奖励", self.claim_battle_pass_rewards),
        ]

        self._reset_task_status(tasks)

        for key, func in tasks:
            self.execute_task(key, func)

        self.ensure_main()
        self._print_result()

    def execute_task(self, key, func):
        """执行单个子任务。

        Args:
            key (str): 任务名称
            func (Callable): 任务执行函数

        根据配置决定是否跳过，并记录执行结果。
        """

        self.task_status["pending"].remove(key)

        # 开关控制
        if not self.config.get(key, False):
            self.task_status["skipped"].append(key)
            return

        self.current_task_key = key
        self.log_info(f"开始任务: {key}")

        self.ensure_main()

        result = func()

        if result is False:
            self.task_status["failed"].append(key)
            self.screenshot(f"fail_{key}")
            self.log_info(f"任务失败: {key}")
            return

        self.task_status["success"].append(key)
        self.log_info(f"任务完成: {key}")
        self.current_task_key = None

    def _reset_task_status(self, tasks):
        """重置任务状态。

        Args:
            tasks (list): [(key, func)] 任务列表
        """
        self.task_status = {
            "success": [],
            "failed": [],
            "skipped": [],
            "pending": [t[0] for t in tasks],
        }

    def _print_result(self):
        """输出任务执行结果。"""
        self.info_set("success", f"{self.task_status['success']}")
        self.info_set("failed", f"{self.task_status['failed']}")
        self.info_set("skipped", f"{self.task_status['skipped']}")

    def _handle_exception(self, e):
        """处理执行异常并记录状态。

        Args:
            e (Exception): 捕获到的异常
        """
        self.screenshot(f"{datetime.now().strftime('%Y%m%d')}_exception")

        if self.current_task_key:
            self.info_set("当前失败任务", self.current_task_key)
        self._print_result()
        raise e

    def _open_mail_panel(self):
        """打开mail panel。

        Returns:
            bool: True 表示成功，False 表示失败
        """
        self.log_info("正在打开邮件面板")
        self.openESCpanel()
        self.click(0.8707, 0.8736, after_sleep=1)
        result = self.wait_panel(Labels.mail_panel)
        if not result:
            self.log_error("无法找到邮件面板", notify=True)
            raise Exception("can't find mail panel")
        return result

    def claim_mail(self):
        """领取邮件"""
        self.log_info("正在领取邮件奖励")
        self._open_mail_panel()
        self.click(0.1289, 0.9299)
        self.sleep(1)
        return True

    def complete_daily_activities(self):
        """执行操作完成每日活跃度"""
        # TODO: 待实现具体逻辑
        self.log_info("正在执行每日活跃度任务 (TODO)")
        return True

    def claim_activity_rewards(self):
        """领取活跃度奖励"""
        self.log_info("正在领取活跃度奖励")
        self.openF1panel()
        self.click(0.0551, 0.3833)
        self.wait_panel(Labels.f1_activity_panel)
        if self.find_one(Labels.f1_activity_mission):
            self.click(0.2348, 0.7653)
            self.sleep(2)

        if target := self._get_activity_reward_box():
            self.click(target)
            self.sleep(1)
        else:
            self.log_error("无法找到活跃度奖励领取框")
            return False
        return True

    def _get_activity_reward_box(self):
        target = None
        box = self.get_box_by_name(Labels.box_f1_activity_reward)
        mask = iu.binarize_bgr_by_brightness(self.frame, threshold=245, to_bgr=False)
        mask = iu.dilate_mask(mask, kernel_size=7, to_bgr=True)
        reward_boxes = find_color_rectangles(
            mask, color_range=text_white_color, min_width=10, min_height=10, box=box, threshold=0.6
        )
        if reward_boxes:
            target = max(reward_boxes, key=lambda x: x.x)
            self.draw_boxes(boxes=target)
        return target

    def claim_battle_pass_rewards(self):
        """领取环期任务奖励"""
        self.log_info("正在领取环期任务奖励")
        self.openF2panel()
        self.click(0.6934, 0.8229)
        self.sleep(1)
        self.click(0.0570, 0.3451)
        self.wait_panel(Labels.f2_mission_panel)
        self.click(0.8777, 0.8187)
        self.sleep(1)
        return True
