from ok import TaskDisabledException, WaitFailedException

from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.tasks.RecordTask import RecordTask
from src.ui.task_icons import Icon

INST = (
    "功能说明：本功能仅负责『自动退出关卡』与『重新开启关卡』的点击循环，"
    "不包含任何局内的制作食物或招待客人操作。\n\n"
    "使用方法：\n"
    "1. 确保您已配置好游戏内的挂机流派。\n"
    "2. 打开店长特供选好你的目标关卡。\n"
    "3. 点击[开始]\n\n"
    "使用录制功能：\n"
    "1. 站在咖啡店可进行 F 交互的位置。\n"
    "2. 启用[使用录制功能]。首次启动需录制目标，点击[开始]后请跟随指示操作。"
)

EN_INST = (
    "Feature notes: This feature only handles the click loop for automatically exiting the stage and restarting the stage."  # noqa: E501
    "It does not perform any in-stage food preparation or customer service actions.\n\n"
    "How to use:\n"
    "1. Make sure you have configured your in-game AFK build.\n"
    "2. Open Owner Selection and select your target level.\n"
    "3. Click [Start].\n\n"
    "Using the recording feature:\n"
    "1. Stand at the cafe where the F interaction is available.\n"
    "2. Enable [Use recording feature]. On first use, click [Start], then follow the prompts to record the target level."  # noqa: E501
)

RECORD_INS = (
    "记录点击目标关卡的操作，分为两个步骤：\n"
    "1. 使用滚轮滚动至[目标关卡]可见 (若不需要则点击[目标关卡])\n"
    "2. 点击目标关卡\n\n"
    "※ 请勿点击[开始营业]"
)

EN_RECORD_INS = (
    "Record the actions used to select the target level in two steps:\n"
    "1. Scroll until [Target Level] is visible (if it already is, click [Target Level]).\n"
    "2. Click [Target Level]\n\n"
    "※ Do not click [Start Business]."
)


class OwnerSelectionTask(NTEOneTimeTask, RecordTask):
    CONF_ROB = "抢钱流"
    CONF_USE_RECORD = "使用录制功能"

    REVENUE_CHECK_INTERVAL = 1.0  # OCR 检测营业额间隔（秒）
    CLICK_INTERVAL = 1  # 步骤3点击间隔（秒）
    CONTROL_TIMEOUT = 120  # 单轮玩法最长等待（秒）

    POS_START = (0.8957, 0.9326)  # 开始玩法按钮
    POS_TAP = (0.0496, 0.4125)  # 循环点击目标
    OCR_BOX = (0.7977, 0.0882, 0.9711, 0.1257)  # 营业额 OCR 区域
    POS_CLOSE = (0.0230, 0.0361)  # 关闭结果界面
    POS_CONFIRM = (0.5984, 0.7764)  # 结算确认

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "店长特供"
        self.description = "自动循环进出关卡（需配合游戏内挂机流派使用）"
        self.instructions = INST if self.is_chinese() else EN_INST
        self.group_name = "都市闲趣"
        self.group_icon = Icon.GAME
        self.add_rounds_config()
        self.default_config.update(
            {
                self.CONF_ROB: False,
                self.CONF_USE_RECORD: False,
            }
        )
        self.config_description.update(
            {
                self.CONF_USE_RECORD: "启用后录制并回放选择目标关卡的操作",
            }
        )
        self.config_type.update(
            {
                self.CONF_USE_RECORD: {
                    "sub_configs": {
                        True: [
                            self.CONF_RESET_RECORD,
                        ]
                    },
                },
            }
        )

    def run(self):
        super().run()
        try:
            return self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.screenshot("shop_special_unexpected_exception")
            self.log_error("OwnerSelection error", e)
            raise

    def show_notification(self):
        if self.config.get(self.CONF_USE_RECORD, False):
            super().show_notification()

    def do_run(self):
        if self.config.get(self.CONF_ROB):
            if not self.confirm_mouse_control_warning(
                mode=self.tr(self.CONF_ROB), close_delay_seconds=2
            ):
                return
        self.start_rounds()

        self.wait_until(
            lambda: not self.is_in_team(),
            pre_action=lambda: self.send_key("f", interval=1),
            settle_time=0.5,
            time_out=10,
            raise_if_not_found=True,
        )

        while self.begin_round():
            if self.run_round():
                self.add_success()

        self.finish_rounds()

    def run_round(self) -> bool:
        # 步骤1：按 F 进入店长特供页面
        self.info_set("当前阶段", "进入店长特供")
        timer = self.clock.timer(timeout=60)
        while not timer.expired():
            if self.find_confirm(box=self.box_of_screen(0.922, 0.889, 0.969, 0.972)):
                break
            if self.find_confirm(box=self.box_of_screen(0.514, 0.740, 0.582, 0.822)):
                self.operate_click(0.788, 0.183, after_sleep=1)
        else:
            raise WaitFailedException()
        self.sleep(1)
        if self.config.get(self.CONF_USE_RECORD, False):
            record_instruction = RECORD_INS if self.is_chinese() else EN_RECORD_INS
            self.record_or_replay_operations(2, instruction_text=record_instruction)
            self.sleep(1)
        # 步骤2：点击开始玩法
        self.info_set("当前阶段", "开始玩法")
        self.wait_click_confirm(range=(0.922, 0.889, 0.969, 0.972))
        # 步骤3：循环点击 + OCR 检测营业额
        self.info_set("当前阶段", "营业中")
        if not self.run_until_target_revenue():
            return self._fail_round("shop_revenue_timeout", "营业额未在超时内达标")

        # 步骤4：关闭结果界面 → 结算确认
        self.info_set("当前阶段", "结算确认")
        self.wait_click_confirm(
            pre_action=lambda: self.operate_click(
                *self.POS_CLOSE, action_name="settle_reward", interval=1
            ),
            range=(0.629, 0.734, 0.688, 0.819),
            time_out=60,
        )
        self.sleep(0.5)
        self.info_set("当前阶段", "本轮完成")
        return True

    # 工具方法
    def run_until_target_revenue(self) -> bool:
        timer = self.clock.timer(timeout=self.CONTROL_TIMEOUT)

        self.log_info("开始营业循环")
        while not timer.expired():
            if self.config.get(self.CONF_ROB, True):
                self.operate_click(
                    *self.POS_TAP, interval=self.CLICK_INTERVAL, restore_cursor=False
                )

            if self._check_revenue_reached():
                self.log_info("营业额已达标，退出营业循环")
                return True
            self.sleep(0.01)

        self.log_error("营业额检测超时")
        return False

    def _check_revenue_reached(self) -> bool:
        box = self.box_of_screen(0.9484, 0.1660, 0.9555, 0.1771, name="star")
        return self.calculate_color_percentage(yellow_star_color, box) > 0.1

    def _fail_round(self, reason: str, message: str) -> bool:
        self.screenshot(f"{reason}_{self.current_round}")
        self.add_failed(message)
        return False


yellow_star_color = {
    "r": (250, 255),
    "g": (200, 220),
    "b": (50, 80),
}
