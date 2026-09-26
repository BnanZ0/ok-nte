from dataclasses import dataclass

from ok import BaseTask


@dataclass
class RoundState:
    total: int = 0
    index: int = 0
    success_count: int = 0
    failed_count: int = 0
    stop_requested: bool = False

    def reset(self, total: int):
        self.total = total
        self.index = 0
        self.success_count = 0
        self.failed_count = 0
        self.stop_requested = False

    @property
    def completed_count(self) -> int:
        return self.success_count + self.failed_count

    @property
    def has_active_round(self) -> bool:
        return self.index > self.completed_count

    @property
    def has_remaining_rounds(self) -> bool:
        return not self.stop_requested and (
            self.has_active_round or self.total == 0 or self.completed_count < self.total
        )

    @property
    def total_text(self) -> str:
        return "∞" if self.total == 0 else str(self.total)

    @property
    def info_text(self) -> str:
        return f"{self.index} / {self.total_text}"

    def begin_next_round(self) -> bool:
        if self.has_active_round or not self.has_remaining_rounds:
            return False
        self.index += 1
        return True


class RoundMixin(BaseTask):
    CONF_ROUNDS = "循环次数"
    INFO_ROUND = "轮次"
    INFO_SUCCESS_COUNT = "成功次数"
    INFO_FAILED_COUNT = "失败次数"
    INFO_FAILED_REASON = "失败原因"
    INFO_STOP_REASON = "停止原因"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._round_state = RoundState()

    def configured_rounds(self, default=0) -> int:
        """读取统一的循环次数配置: 0 表示无限运行。"""
        value = self.config.get(self.CONF_ROUNDS, None)
        if value is None:
            value = default
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return max(0, int(default))

    def add_rounds_config(self, default=0):
        self.default_config.update({self.CONF_ROUNDS: default})
        self.config_description.update({self.CONF_ROUNDS: "设置为0则一直运行"})

    def start_rounds(self):
        """初始化统一轮次状态, 并输出任务开始信息。"""
        self._round_state.reset(self.configured_rounds())
        self.info_set(self.INFO_ROUND, "")
        self.info_set(self.INFO_SUCCESS_COUNT, 0)
        self.info_set(self.INFO_FAILED_COUNT, 0)
        self.info_set(self.INFO_FAILED_REASON, None)
        self.info_set(self.INFO_STOP_REASON, None)
        self.log_info(f"开始{self.name}, 共 {self._round_state.total_text} 轮")

    def begin_round(self) -> bool:
        """开始下一轮, 并在运行中同步最新循环次数配置。"""
        state = self._round_state
        previous_total = state.total
        state.total = self.configured_rounds()
        if state.stop_requested:
            return False
        if state.has_active_round:
            if state.total != previous_total:
                self.info_set(self.INFO_ROUND, state.info_text)
            return True
        if not state.begin_next_round():
            return False
        self.info_set(self.INFO_ROUND, state.info_text)
        self.log_round_info("开始")
        return True

    def has_remaining_rounds(self) -> bool:
        """判断当前轮次完成后是否仍可继续运行。"""
        state = self._round_state
        if state.stop_requested:
            return False
        state.total = self.configured_rounds()
        return state.has_remaining_rounds

    def stop_rounds(self, reason: str | None = None) -> None:
        """请求停止轮次任务, 后续轮次检查会立即返回 False。"""
        self._round_state.stop_requested = True
        if reason:
            self.info_set(self.INFO_STOP_REASON, reason)
            self.log_round_info(f"收到停止请求: {reason}")

    @property
    def current_round(self) -> int:
        return self._round_state.index

    def add_success(self, count: int = 1) -> int:
        """记录已完成的成功轮次, 并返回累计成功数。"""
        state = self._round_state
        state.success_count += count
        self.info_set(self.INFO_SUCCESS_COUNT, state.success_count)
        return state.success_count

    def add_failed(self, reason: str | None = None, count: int = 1) -> int:
        """记录失败轮次, 必要时更新失败原因并输出轮次错误日志。"""
        state = self._round_state
        state.failed_count += count
        self.info_set(self.INFO_FAILED_COUNT, state.failed_count)
        if reason:
            self.info_set(self.INFO_FAILED_REASON, reason)
            self.log_round_info(f"失败：{reason}", error=True)
        else:
            self.log_round_info("失败", error=True)
        return state.failed_count

    def log_round_info(self, message: str, *, error: bool = False):
        """输出带当前轮次前缀的日志, 供轮次任务和其子流程统一使用。"""
        round_index = self._round_state.index
        prefix = f"第 {round_index} 轮: " if round_index else ""
        if error:
            self.log_error(f"{prefix}{message}")
        else:
            self.log_info(f"{prefix}{message}")

    def finish_rounds(self, *, notify: bool = True):
        """输出统一的轮次汇总日志。"""
        state = self._round_state
        self.log_info(
            f"{self.name}结束, 成功 {state.success_count}/{state.total_text}",
            notify=notify,
        )
