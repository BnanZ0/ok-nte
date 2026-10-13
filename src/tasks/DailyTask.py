from contextlib import contextmanager
from datetime import datetime
from typing import Callable, Iterator, List, Optional, Tuple, Type, TypeVar, cast

from ok import CannotFindException, TaskDisabledException, find_color_rectangles

from src import text_white_color
from src.Labels import Labels
from src.tasks.AnomalyTask import AnomalyTask
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.BaseStaminaTask import BaseStaminaTask
from src.tasks.daily.CinemaDateTask import CinemaDateTask
from src.tasks.daily.CoffeeTask import CoffeeTask
from src.tasks.daily.FountainTask import FountainTask
from src.tasks.daily.FurnitureTask import FurnitureTask
from src.tasks.daily.GiftTask import GiftTask
from src.utils import image_utils as iu

WorkingTaskT = TypeVar("WorkingTaskT", bound=BaseNTETask)


class DailyTask(BaseStaminaTask):
    """日常任务执行器"""

    # --- 配置项键名 ---
    CONF_TASK = "副本类型"
    TASK_NONE = "不执行"
    TASK = [TASK_NONE, AnomalyTask.TASK_NAME]

    CONF_CLAIM_MAIL = "领取邮件"
    CONF_COMPLETE_DAILY = "完成每日活跃度"
    CONF_CLAIM_ACTIVITY = "领取活跃度奖励"
    CONF_CLAIM_BP = "领取环期任务奖励"
    CONF_COFFEE_TASK = "一咖舍任务"
    CONF_CINEMA_DATE = "影院约会"
    CONF_FOUNTAIN_SIGN = "喷泉签到"
    CONF_FURNITURE = "异象家具"
    CONF_GIFT = "羁遇赠礼"

    CINEMA_DATE_TARGET = "约会目标"
    DAILY_STAMINA_TARGET = BaseStaminaTask.CONF_STAMINA_TARGET

    # --- 一咖舍任务选项 ---
    COFFEE_MODE_CLAIM_AND_RESTOCK = "领取/补货一咖舍"
    COFFEE_MODE_AUTO = "运行一咖舍自动化"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.support_schedule_task = True
        self.task_status = {"success": [], "failed": [], "skipped": [], "pending": []}
        self.working_task: Optional[BaseNTETask] = None

        AnomalyTask.setup_config(self, daily=True)
        self.default_config.update(
            {
                self.CONF_TASK: self.TASK[1],
                self.CONF_COFFEE_TASK: self.TASK_NONE,
                self.CONF_CINEMA_DATE: False,
                self.CINEMA_DATE_TARGET: "",
                self.CONF_FOUNTAIN_SIGN: self.TASK_NONE,
                self.CONF_FURNITURE: False,
                self.CONF_GIFT: False,
            }
        )
        self.config_description.update(
            {
                self.CONF_COFFEE_TASK: "选择日常任务中的一咖舍处理方式",
            }
        )
        coffee_options = [self.TASK_NONE, self.COFFEE_MODE_CLAIM_AND_RESTOCK]
        # 一咖舍自动化页面 OCR 仅匹配简体中文; 在非 zh_CN 下不向用户暴露自动化选项.
        if self.get_app_locale() == "zh_CN":
            coffee_options.append(self.COFFEE_MODE_AUTO)
        self.config_type.update(
            {
                self.CONF_TASK: {
                    "type": "drop_down",
                    "options": self.TASK,
                    "sub_configs": {
                        self.TASK[1]: [
                            AnomalyTask.CONF_TASK_TYPE,
                            AnomalyTask.CONF_CYCLEB_TASK_MODE,
                            self.DAILY_STAMINA_TARGET,
                        ],
                    },
                },
                self.CONF_COFFEE_TASK: {
                    "type": "drop_down",
                    "options": coffee_options,
                },
                self.CONF_CINEMA_DATE: {
                    "sub_configs": {
                        True: [
                            self.CINEMA_DATE_TARGET,
                        ]
                    },
                },
                self.CONF_FOUNTAIN_SIGN: {
                    "type": "drop_down",
                    "options": [
                        self.TASK_NONE,
                        FountainTask.SIGN_MODE_SIGN,
                        FountainTask.SIGN_MODE_COIN,
                    ],
                },
            }
        )

        self.current_task_key = None
        self.add_exit_after_config()

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self._handle_exception(e)

    def do_run(self):
        """执行日常任务主流程"""
        self.scene.set_logged_in(False)
        self.ensure_main()
        self.log_info("开始执行日常任务")

        tasks: List[Tuple[str, bool, Callable]] = [
            (
                self.CONF_CLAIM_MAIL,
                self._task_enabled(self.CONF_CLAIM_MAIL, True),
                self.claim_mail,
            ),
            (
                self.CONF_COMPLETE_DAILY,
                self._task_enabled(self.CONF_COMPLETE_DAILY, True),
                self.complete_daily_activities,
            ),
            (
                self.CONF_COFFEE_TASK,
                self._task_enabled(self.CONF_COFFEE_TASK, self.TASK_NONE, self.TASK_NONE),
                self.run_coffee_task,
            ),
            (
                self.CONF_CLAIM_ACTIVITY,
                self._task_enabled(self.CONF_CLAIM_ACTIVITY, True),
                self.claim_activity_rewards,
            ),
            (
                self.CONF_CLAIM_BP,
                self._task_enabled(self.CONF_CLAIM_BP, True),
                self.claim_battle_pass_rewards,
            ),
            (
                self.CONF_CINEMA_DATE,
                self._task_enabled(self.CONF_CINEMA_DATE, False),
                self.run_cinema_task,
            ),
            (
                self.CONF_FOUNTAIN_SIGN,
                self._task_enabled(self.CONF_FOUNTAIN_SIGN, self.TASK_NONE, self.TASK_NONE),
                self.run_fountain_sign_task,
            ),
            (
                self.CONF_FURNITURE,
                self._task_enabled(self.CONF_FURNITURE, False),
                self.run_furniture_task,
            ),
            (
                self.CONF_GIFT,
                self._task_enabled(self.CONF_GIFT, False),
                self.run_gift_task,
            ),
        ]

        self._reset_task_status(tasks)

        for task in tasks:
            self.execute_task(*task)

        self.ensure_main()
        self._print_result()
        self.log_info("结束执行日常任务", notify=True)

    def _task_enabled(self, key, default, not_equal=None):
        value = self.config.get(key, default)
        if isinstance(value, bool):
            return value
        elif not_equal and value != not_equal:
            return True
        return False

    def execute_task(self, key, enabled, func):
        """执行单个子任务。

        Args:
            key (str): 任务名称
            enabled (bool): 是否执行
            func (Callable): 任务执行函数

        根据配置决定是否跳过，并记录执行结果。
        """

        self.task_status["pending"].remove(key)

        if not enabled:
            self.task_status["skipped"].append(key)
            return

        self.current_task_key = key
        self.log_info(f"开始任务: {key}")

        self.ensure_main()

        try:
            result = func()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error(f"任务: {key} 运行失败", e)
            result = False

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

        def action():
            self.openESCpanel()
            self.operate_click(*self.pos.panels.esc.mail)
            self.sleep(0.5)
            return self.wait_panel(Labels.mail_panel)

        self.log_info("正在打开邮件面板")
        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("无法找到邮件面板", notify=True)
            raise CannotFindException("can't find mail panel")
        return result

    def claim_mail(self):
        """领取邮件"""
        self.log_info("正在领取邮件奖励")
        self._open_mail_panel()
        self.operate_click(0.1289, 0.9299)
        self.sleep(1)
        return True

    def run_coffee_task(self):
        mode = self.config.get(self.CONF_COFFEE_TASK)
        match mode:
            case self.COFFEE_MODE_AUTO:
                with self.set_working_task(CoffeeTask) as task:
                    task.do_run()
            case self.COFFEE_MODE_CLAIM_AND_RESTOCK:
                self.claim_coffee()

    def complete_daily_activities(self):
        """执行操作完成每日活跃度"""
        self.log_info("正在执行每日活跃度任务")
        task_name = self.config.get(self.CONF_TASK)
        if task_name == AnomalyTask.TASK_NAME:
            with self.set_working_task(AnomalyTask, config=self.config) as task:
                result = task.do_run()
                task.on_daily_end(result)
                return result is not False
        return False

    @contextmanager
    def set_working_task(self, cls: Type[WorkingTaskT], config=None) -> Iterator[WorkingTaskT]:
        old_working_task = self.working_task
        old_sleep_check_interval = self.sleep_check_interval
        working_task = cast(WorkingTaskT, self.get_task_by_class(cls))
        old_task_info = working_task.info
        old_task_config = working_task.config
        self.working_task = working_task
        self.working_task.info = self.info
        self.sleep_check_interval = working_task.sleep_check_interval
        if config is not None:
            working_task.config = config
        try:
            yield working_task
        finally:
            self.working_task.info = old_task_info
            self.working_task.config = old_task_config
            self.working_task = old_working_task
            self.sleep_check_interval = old_sleep_check_interval

    def sleep_check(self):
        if self.working_task:
            return self.working_task.sleep_check()
        return super().sleep_check()

    def claim_activity_rewards(self, in_panel=False):
        """领取活跃度奖励"""
        self.log_info("正在领取活跃度奖励")
        if not in_panel and not self.open_activity_panel():
            return False
        if self.find_one(Labels.f1_activity_mission):
            self.operate_click(0.2348, 0.7653)
            self.sleep(2)

        if target := self._get_activity_reward_box():
            self.wait_until(
                lambda: not self._get_activity_reward_box(),
                pre_action=lambda: self.operate_click(target, interval=1),
            )
            self.sleep(1)
        else:
            self.log_error("无法找到活跃度奖励领取框")
            return False
        return True

    def _get_activity_reward_box(self):
        target = None
        box = self.get_box_by_name(Labels.box_f1_activity_reward)
        mask = iu.binarize_bgr_by_brightness(self.frame, threshold=245, to_bgr=False)
        mask = iu.morphology_mask(mask, kernel_size=7, to_bgr=True)
        reward_boxes = find_color_rectangles(
            mask, color_range=text_white_color, min_width=10, min_height=10, box=box, threshold=0.6
        )
        if reward_boxes:
            target = max(reward_boxes, key=lambda x: x.x)
            self.draw_boxes(boxes=target)
        return target

    def claim_battle_pass_rewards(self):
        """领取环期任务奖励"""

        def action():
            self.openF2panel()
            self.operate_click(*self.pos.panels.f2.mission)
            self.sleep(0.5)
            return self.wait_panel(Labels.f2_mission_panel)

        self.log_info("正在领取环期任务奖励")
        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("无法找到环期任务面板")
            return False
        self.operate_click(0.8777, 0.8187)
        self.sleep(1)
        self.operate_click(0.0570, 0.2333)
        self.sleep(1)
        self.operate_click(0.6934, 0.8229)
        self.sleep(1)
        return True

    def claim_coffee(self):
        """领取一咖舍奖励"""

        def action():
            self.openF5panel()
            self.operate_click(*self.pos.panels.f5.coffee)
            self.sleep(0.5)
            return self.wait_panel(Labels.f5_coffee_panel)

        self.log_info("正在领取一咖舍奖励")
        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("无法找到一咖舍面板")
            return False
        self.sleep(1)

        # 提取收益
        self.wait_until(
            lambda: not self.find_one(Labels.f5_coffee_panel),
            pre_action=lambda: self.operate_click(0.188, 0.877, interval=1),
            time_out=10,
        )
        self.sleep(1)
        self.wait_until(
            lambda: self.find_one(Labels.f5_coffee_panel),
            pre_action=lambda: self.operate_click(0.072, 0.886, interval=1),
            time_out=10,
            settle_time=0.5,
        )
        self.sleep(1)

        # 进入补货
        self.wait_until(
            lambda: not self.find_one(Labels.f5_coffee_panel),
            pre_action=lambda: self.operate_click(0.115, 0.530, interval=1),
            time_out=10,
            settle_time=0.5,
        )
        self.sleep(1)

        # 补货
        self.operate_click(0.340, 0.785)  # 24hr
        self.sleep(1)
        self.operate_click(0.717, 0.787)  # 补货
        self.sleep(1)
        self.operate_click(0.595, 0.776)  # 送货上门
        self.sleep(1)
        self.operate_click(0.600, 0.656)  # 确认
        return True

    def run_fountain_sign_task(self):
        sign_mode = self.config.get(self.CONF_FOUNTAIN_SIGN)
        with self.set_working_task(FountainTask) as task:
            return task.do_run(sign_mode)

    def run_gift_task(self):
        with self.set_working_task(GiftTask) as task:
            summary = task.run_gifts()
        return len(summary["failed"]) == 0

    def run_furniture_task(self):
        with self.set_working_task(FurnitureTask) as task:
            return task.do_run()

    def run_cinema_task(self):
        with self.set_working_task(CinemaDateTask) as task:
            return task.do_run(self.config.get(self.CINEMA_DATE_TARGET, ""))
