"""用线上日志的真实读数序列回放估价稳定判定, 对比修复前后的行为。

日志里每局「出价面板打开 -> 读到估价」的完整读数序列是现成的回归素材:
`数字面板加载完成` 到 `当前估价读数稳定` 之间的每条 `当前估价 OCR` 都带毫秒时间戳。

用法: ./.venv/Scripts/python.exe tests/support/estimate_replay.py [日志路径]
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

PANEL = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*数字面板加载完成")
# 读数记录有两种: 正常读数报「解析值: N」, 解析失败报「解析值: None」; 修复后的估价读取还会把
# 「千位分隔符前缺数字」的残缺读数提前返回, 改记「视为残缺读数」这条, 没有解析值字段。
# 两种残缺形态都必须收进 reads —— 判定里它们都代表「没有带来更完整的信息」, 丢掉会让回放少算
# 一次「连续无新信息」, 采信时刻和结果都可能与真机不一致。
READ = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*当前估价 OCR: '(.*?)', 解析值: (\d+|None)"
)
PARTIAL = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*当前估价 OCR: '(.*?)', 千位分隔符前缺数字"
)
# 采信点有两种日志: 连续读到同一个完整数值时报「读数稳定」, 只有 1~2 次有效读数
# (其余帧未读出或为残缺值)时改报告警 —— 两者都代表这一帧的读数被采用, 都要认。
STABLE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*"
    r"当前估价(?:读数稳定|仅 \d+ 次有效读数).*(\d+)"
)

# 与 AutoBidAuctionTask 的常量保持一致。故意硬编码而不是 import: 导入 src.tasks 会触发
# 框架初始化并回写用户的 configs/*.json。
REQUIRED = 3
OBSERVE = 4.0


def _ts(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S,%f").timestamp()


def load_rounds(path: Path) -> list[dict]:
    rounds: list[dict] = []
    current: dict | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = PANEL.match(line)
        if match:
            if current is not None:
                rounds.append(current)
            current = {"panel": _ts(match.group(1)), "reads": [], "stable": None}
            continue
        if current is None:
            continue
        match = READ.match(line)
        if match:
            value = None if match.group(3) == "None" else int(match.group(3))
            current["reads"].append((_ts(match.group(1)), match.group(2), value))
            continue
        match = PARTIAL.match(line)
        if match:
            # 旧逻辑没有残缺标志, 会把 ',544' 解析成 544 并照常参与稳定判定, 所以这里按旧规则
            # 存成数字; simulate_after 从 raw 的逗号前缀认出它是残缺读数, 改按 None 处理。
            digits = re.sub(r"[^\d]", "", match.group(2))
            current["reads"].append(
                (_ts(match.group(1)), match.group(2), int(digits) if digits else None)
            )
            continue
        match = STABLE.match(line)
        if match:
            current["stable"] = int(match.group(2))
    if current is not None:
        rounds.append(current)
    return [r for r in rounds if r["reads"]]


def simulate_before(reads, required: int = REQUIRED, skip_zero: bool = True):
    """修复前: 连续 N 次值相同即采用。"""
    last: int | None = None
    same = 0
    for ts, _raw, value in reads:
        if skip_zero and value == 0:
            continue
        same = same + 1 if value == last else 1
        last = value
        if same >= required:
            return value, ts
    return last, None


def simulate_after(
    reads, required: int = REQUIRED, skip_zero: bool = True, observe: float = OBSERVE
):
    """修复后: 只在位数不减少的读数里取最新值, 且至少观察 observe 秒。

    返回 (值, 采信时刻, 是否真的采信)。采信时刻为 None 表示日志序列结束时仍未采信,
    真机上会继续重读 —— 所以「未采信」是修复生效的标志, 不是失败。
    """
    last: int | None = None
    same = 0
    first_seen: float | None = None
    for ts, raw, value in reads:
        # 残缺读数以 raw 为准: 存进来的是旧规则解析出的数字, 不能直接当读数用。
        partial = raw.lstrip("：:").startswith(",")
        if partial:
            value = None
        if value is None:
            if last is not None:
                same += 1
        elif skip_zero and value == 0:
            pass
        elif last is None or (len(str(value)) >= len(str(last)) and value != last):
            if first_seen is None:
                first_seen = ts
            last, same = value, 1
        else:
            same += 1
        if (
            last is not None
            and same >= required
            and first_seen is not None
            and ts - first_seen >= observe
        ):
            return last, ts, True
    return last, None, False


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/ok-script.log")
    rounds = load_rounds(path)
    print(f"日志: {path}  出价面板轮次: {len(rounds)}  最短观察窗口: {OBSERVE}s\n")

    held = agreed = timed_out = stalled = 0
    for index, item in enumerate(rounds, 1):
        before, at_before = simulate_before(item["reads"])
        after, at_after, accepted = simulate_after(item["reads"])
        when = f"@+{at_after - item['panel']:.2f}s" if at_after else "日志内未采信"

        if at_before is None:
            # 旧逻辑跑满超时也没采信。新逻辑可能已经读出正确值, 也可能同样没等到 —— 两者要
            # 分开计数, 否则汇总里的「新逻辑读出」会把「两逻辑都没读到」也算进去。
            if accepted:
                timed_out += 1
                note = "  <== 旧逻辑超时未稳定"
            else:
                stalled += 1
                note = "  <== 两逻辑都未采信"
            print(
                f"[{index:>3}] {len(item['reads']):>2} 次读数 | 旧 未稳定(超时) | "
                f"新 {after} {when}{note}"
            )
            continue

        # 旧逻辑采信的那一刻, 新逻辑是否也会采信?
        early = [r for r in item["reads"] if r[0] <= at_before]
        if simulate_after(early)[2]:
            agreed += 1
            verdict = "两逻辑一致"
        else:
            held += 1
            verdict = "新逻辑仍在等"
        old_at = at_before - item["panel"]
        print(
            f"[{index:>3}] {len(item['reads']):>2} 次读数 | 旧 {before} @+{old_at:.2f}s"
            f" | 新 {after} {when} | {verdict}"
        )

    print(
        f"\n旧逻辑采信而新逻辑仍在等 (可能采信了残缺值): {held}"
        f"\n旧逻辑超时、新逻辑读出: {timed_out}"
        f"\n两逻辑都未采信: {stalled}"
        f"\n两逻辑一致: {agreed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
