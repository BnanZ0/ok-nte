from src.tasks.BaseNTETask import BaseNTETask
from qfluentwidgets import FluentIcon
from datetime import datetime


class DailyTask(BaseNTETask):
    """日常任务骨架执行器（纯结构版）"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ===== 基础信息 =====
        self.name = "日常任务"
        self.description = "暂不可用"
        self.icon = FluentIcon.SYNC

        # ===== 能力开关 =====
        self.support_schedule_task = True

        # ===== 任务状态管理 =====
        # pending: 尚未执行的任务
        # success: 执行成功
        # failed: 执行失败
        # skipped: 被配置跳过（未执行）
        self.task_status = {"success": [], "failed": [], "skipped": [], "pending": []}

        # ===== 配置 =====
        self.default_config.update({"日常子项1": True, "日常子项2": True})
        self.current_task_key = None  # 当前执行的任务
        self.add_exit_after_config()

    # ==============================
    # 主流程
    # ==============================
    def run(self):
        """执行日常任务主流程。

        初始化任务列表，依次执行子任务，并输出最终结果。
        """
        try:
            self.log_info("开始执行日常任务...", notify=True)

            # 定义任务列表，格式为 [(任务配置名称, 任务函数)]
            tasks = [
                ("日常子项1", self.daily_1), 
                ("日常子项2", self.daily_2)
            ]

            self._reset_task_status(tasks)

            for key, func in tasks:
                self.execute_task(key, func)

            self._print_result()

        except Exception as e:
            self._handle_exception(e)

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

        # self.ensure_main()  # 每轮任务前确保在主界面

        result = func()

        if result is False:
            self.task_status["failed"].append(key)
            self.screenshot(f"fail_{key}")
            self.log_info(f"任务失败: {key}")
            return

        self.task_status["success"].append(key)
        self.current_task_key = None

    def _reset_task_status(self, tasks):
        """重置任务状态。

        Args:
            tasks (list): [(key, func)] 任务列表
        """
        self.task_status = {"success": [], "failed": [], "skipped": [], "pending": [t[0] for t in tasks]}

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

    """
    以下为各子项任务函数示例，实际使用时请替换为具体实现
    """

    def daily_1(self):
        """日常子任务1（占位）。

        Returns:
            bool: True 表示成功，False 表示失败
        """
        ...

    def daily_2(self):
        """日常子任务2（占位）。

        Returns:
            bool: True 表示成功，False 表示失败
        """
        ...
