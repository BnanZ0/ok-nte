"""通用配置读取: 任务配置值的类型解析与非法回退。

两种形态 (入口减负口径见 auction-notes 8):
- 模块函数 read_int / read_decimal: 无告警出口的一次性读取, 非法值一律
  静默回退默认值;
- ConfigReader: 绑定 config 与 log_warning 的告警感知读取器, 供能力模块
  在函数开头构造一次后多次读取, warn 文案经绑定的 log_warning 输出 ——
  log_warning 必传, 「传了 warn 却没有告警出口」的形态在构造上不存在。
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal


def _parse_int(config: dict, key: str, default: int) -> int:
    return int(config.get(key, default))


def _parse_decimal(config: dict, key: str, default: str) -> Decimal:
    return Decimal(str(config.get(key, default)))


def _parse_int_list(config: dict, key: str) -> list[int]:
    return [int(item) for item in config.get(key, [])]


def read_int(config: dict, key: str, default: int = 0) -> int:
    """读取整数配置, 非法时回退到默认值 (无告警出口, 需要告警用 ConfigReader)。"""
    try:
        return _parse_int(config, key, default)
    except (TypeError, ValueError):
        return default


def read_decimal(config: dict, key: str, default: str = "0") -> Decimal:
    """读取十进制配置, 非法时回退到默认值。

    用于参与 Decimal 运算的配置(加价数值): 配置原值本身就是文本框里的字符串,
    直接解析能保留用户填的全部精度, 而先经 float 中转再 str() 只剩 17 位有效数字。
    非法值一律回退默认值, 与 read_int 一样不抛异常 —— 调用方另有兜底。
    """
    try:
        return _parse_decimal(config, key, default)
    except (ArithmeticError, ValueError):
        return Decimal(default)


@dataclass(frozen=True)
class ConfigReader:
    """绑定 config 与 log_warning 的告警感知读取器。

    read_int / read_int_list 的 warn 文案经绑定的 log_warning 输出;
    read_decimal 与模块函数同口径, Decimal 解析异常一律静默回退。
    """

    config: dict
    log_warning: Callable[[str], None]

    def read_int(self, key: str, default: int = 0, *, warn: str | None = None) -> int:
        try:
            return _parse_int(self.config, key, default)
        except (TypeError, ValueError):
            if warn:
                self.log_warning(warn)
            return default

    def read_decimal(self, key: str, default: str = "0") -> Decimal:
        return read_decimal(self.config, key, default)

    def read_int_list(self, key: str, *, warn: str | None = None) -> list[int]:
        try:
            return _parse_int_list(self.config, key)
        except (TypeError, ValueError):
            if warn:
                self.log_warning(warn)
            return []
