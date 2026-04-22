from src.tasks.BaseNTETask import BaseNTETask
from qfluentwidgets import FluentIcon
from datetime import datetime


class DailyTask(BaseNTETask):
    """日常任务骨架执行器（纯结构版）"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ===== 基础信息 =====
        self.name = "日常任务"
        self.description = "收菜(暂不可用)"
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
        try:
            self.log_info("开始执行日常任务...", notify=True)

            """
            此处添加任务配置与任务执行函数映射，同时决定任务顺序
            """
            tasks = [("日常子项1", self.daily_1), ("日常子项2", self.daily_2)]

            # 初始化任务列表
            self._reset_task_status(tasks)

            for key, func in tasks:
                self.execute_task(key, func)

            # ===== 输出结果 =====
            self._print_result()

        except Exception as e:
            self._handle_exception(e)

    def execute_task(self, key, func):
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
        self.task_status = {"success": [], "failed": [], "skipped": [], "pending": [t[0] for t in tasks]}

    def _print_result(self):
        self.info_set("success", f"{self.task_status['success']}")
        self.info_set("failed", f"{self.task_status['failed']}")
        self.info_set("skipped", f"{self.task_status['skipped']}")

    def _handle_exception(self, e):
        self.screenshot(f"{datetime.now().strftime('%Y%m%d')}_exception")

        if self.current_task_key:
            self.info_set("当前失败任务", self.current_task_key)
        self._print_result()
        raise e

    """
    以下为各子项任务函数示例，实际使用时请替换为具体实现
    """

    def daily_1(self): ...

    def daily_2(self): ...
