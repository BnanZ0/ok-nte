"""资产历史记录: 把每轮观测到的资产数值追加到本地 JSONL 文件。

拍卖任务每轮结束回到主界面时会 OCR 一次资产数值, 原本只写进日志。日志按大小滚动
清理, 历史观测会随之丢失, 也无法直接看出「这一轮赚了多少」。这里把同一份观测追加
到一条独立的时间序列文件, 供 ``tools/asset_report.py`` 生成趋势报告。

落盘格式为 JSONL(每行一个 JSON 对象), 而不是 CSV 或整体 JSON:
- 追加即可, 不需要读回整个文件, 任务每轮写一行的开销可以忽略;
- 进程被强杀或掉线中断时, 已写入的行仍然完整可读, 不会像整体 JSON 那样整个文件报废。

写入失败一律吞掉并只记 debug 日志: 记录资产是附加观测, 不能因为磁盘满、目录只读
之类的本地问题把已经成功结算的拍卖轮次判成失败。
"""

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# 默认落盘位置。data/ 已在 .gitignore 中, 属于本地生成数据, 不进仓库。
DEFAULT_HISTORY_PATH = Path("data") / "asset_history.jsonl"

# 来源标记: 便于日后加入其它界面(出价面板等)的观测而不混淆。
SOURCE_MAIN_SCREEN = "main_screen"


@dataclass(frozen=True)
class AssetRecord:
    """单条资产观测。

    timestamp 为 Unix 秒, 便于读取时按天聚合; time_text 是本地时间的可读形式,
    用来看「几点测的」而不必再做时区换算。
    """

    timestamp: float
    time_text: str
    value: int
    round_index: int = 0
    source: str = SOURCE_MAIN_SCREEN
    # 相对上一条记录的增减。首条记录为 None(没有可比的前值)。
    delta: int | None = None

    @classmethod
    def create(
        cls,
        value: int,
        *,
        round_index: int = 0,
        source: str = SOURCE_MAIN_SCREEN,
        delta: int | None = None,
    ) -> "AssetRecord":
        now = time.time()
        return cls(
            timestamp=now,
            time_text=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            value=value,
            round_index=round_index,
            source=source,
            delta=delta,
        )


class AssetHistoryRecorder:
    """把资产观测追加写入 JSONL 文件, 并维护「上一条数值」用于计算增减。"""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else DEFAULT_HISTORY_PATH
        # 上一次成功写入的数值, 用于算 delta。进程重启后由 load() 补齐。
        self._last_value: int | None = None
        self._loaded = False

    @property
    def last_value(self) -> int | None:
        return self._last_value

    def load(self) -> list[AssetRecord]:
        """读取全部历史记录, 同时把最后一条的数值设为 delta 基准。

        文件不存在时返回空列表。损坏的行(半截 JSON、手改坏了)直接跳过: 一行坏掉
        不该让整个历史不可读。
        """
        records = read_records(self.path)
        self._last_value = records[-1].value if records else None
        self._loaded = True
        return records

    def record(
        self,
        value: int,
        *,
        round_index: int = 0,
        source: str = SOURCE_MAIN_SCREEN,
    ) -> AssetRecord | None:
        """追加一条观测, 返回写入的记录; 写入失败时返回 None。

        首次写入前会惰性读一次已有文件, 保证跨进程重启的 delta 仍然正确。
        """
        if not self._loaded:
            self.load()

        delta = None if self._last_value is None else value - self._last_value
        record = AssetRecord.create(
            value, round_index=round_index, source=source, delta=delta
        )
        if self._append(record):
            self._last_value = value
            return record
        return None

    def _append(self, record: AssetRecord) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(asdict(record), ensure_ascii=False)
            # 先编码再以二进制追加: 避免 Windows 上文本模式的换行转换, 保证一行一条。
            with open(self.path, "ab") as f:
                f.write(line.encode("utf-8") + os.linesep.encode("utf-8"))
            return True
        except OSError:
            # 附加观测失败不影响主流程, 由调用方决定是否提示。
            return False


def read_records(path: Path | str | None = None) -> list[AssetRecord]:
    """读取 JSONL 历史文件, 跳过无法解析或缺少必填字段的行。"""
    target = Path(path) if path is not None else DEFAULT_HISTORY_PATH
    try:
        raw = target.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return []

    records: list[AssetRecord] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        record = _parse_line(stripped)
        if record is not None:
            records.append(record)
    return records


def _parse_line(line: str) -> AssetRecord | None:
    try:
        payload = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    value = payload.get("value")
    if not isinstance(value, int) or isinstance(value, bool):
        return None

    timestamp = payload.get("timestamp")
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
        # 没有时间戳的记录无法参与趋势分析, 直接丢弃。
        return None

    time_text = payload.get("time_text")
    if not isinstance(time_text, str):
        time_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))

    round_index = payload.get("round_index")
    if not isinstance(round_index, int) or isinstance(round_index, bool):
        round_index = 0

    delta = payload.get("delta")
    if not isinstance(delta, int) or isinstance(delta, bool):
        delta = None

    source = payload.get("source")
    if not isinstance(source, str) or not source:
        source = SOURCE_MAIN_SCREEN

    return AssetRecord(
        timestamp=float(timestamp),
        time_text=time_text,
        value=value,
        round_index=round_index,
        source=source,
        delta=delta,
    )
