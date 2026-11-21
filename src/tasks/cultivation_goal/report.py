"""培养目标刷本运行报告: 逐轮写 JSONL, 不依赖 ok 框架, 便于离线复盘。"""

from __future__ import annotations

import argparse
import json
import os
import threading
import uuid
from datetime import datetime

DEFAULT_PATH = os.path.join("logs", "cultivation_goal.jsonl")
_LOCK = threading.Lock()


class RunReport:
    """逐轮写 JSONL，作为框架主日志之外的证据链。"""

    def __init__(self, path: str = DEFAULT_PATH, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        # 同一实例多次启动也可能落在同一秒, 追加随机段保证会话 ID 唯一。
        self.session = (
            datetime.now().strftime("%Y%m%d-%H%M%S")
            + f"-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )

    def emit(self, event: str, **fields) -> dict:
        record = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "session": self.session,
            "event": event,
            **fields,
        }
        if self.enabled:
            with _LOCK:
                parent = os.path.dirname(os.path.abspath(self.path))
                os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record


def row_snapshot(rows) -> list[dict]:
    result = []
    for row in rows or ():
        result.append(
            {
                "label": row.label,
                "cost": row.cost,
                "landing": row.landing,
                "ratios": [f"{r.owned}/{r.required}" for r in row.ratios] if row.ratios else None,
                "satisfied": row.satisfied,
            }
        )
    return result


def read_records(path: str = DEFAULT_PATH, session: str | None = None,
                 all_sessions: bool = False) -> list[dict]:
    if not os.path.isfile(path):
        return []
    records = []
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.readlines()
    for index, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            # 只容忍文件末尾被截断的最后一行; 中间损坏的记录必须显式报错。
            if index == len(lines) - 1:
                print(f"警告: 报告末尾记录被截断, 已忽略: {line[:80]}")
            else:
                raise
    if session:
        return [r for r in records if r.get("session") == session]
    if all_sessions:
        return records
    if not records:
        return []
    last = records[-1].get("session")
    return [r for r in records if r.get("session") == last]


def format_records(records: list[dict]) -> str:
    lines = []
    for record in records:
        ts, event, rnd = record.get("ts", ""), record.get("event", ""), record.get("round", "-")
        if event == "start":
            lines.append(f"[{ts}] 启动 session={record.get('session')} 今日体力目标={record.get('daily_target')} 周本剩余={record.get('weekly_remaining')}")
        elif event == "round":
            gains = record.get("gains") or {}
            gaps = record.get("gaps") or {}

            def brief(item):
                if item["satisfied"]:
                    mark = "✓"
                elif item["satisfied"] is False:
                    mark = "✗"
                else:
                    mark = "?"
                text = f"{item['label']} {' '.join(item['ratios']) if item['ratios'] else '<读不出>'} {mark}"
                gain = gains.get(item["label"])
                if gain is not None:
                    text += f" +{gain}"
                if gaps.get(item["label"]):
                    text += f" 缺{gaps[item['label']]}"
                return text

            rows = " | ".join(brief(item) for item in record.get("rows", []))
            lines.append(
                f"[{ts}] #{rnd} 体力={record.get('stamina')} 生效={record.get('effective_stamina')} 周本={record.get('weekly_remaining')} | {rows} "
                f"→ {record.get('action')} ({record.get('reason')})"
            )
        elif event == "landing":
            lines.append(
                f"[{ts}] #{rnd} 落点 {record.get('kind')} 屏幕文字: {(record.get('text') or '<空>')[:140]}"
            )
        elif event == "action":
            lines.append(f"[{ts}] #{rnd} 执行 {record.get('action')} {record.get('label', '')} 成功={record.get('ok')} {record.get('detail', '')}")
        elif event == "loot":
            items = record.get("items") or []
            detail = " | ".join(
                f"{item.get('dungeon') or item.get('key')} ×{item.get('count')}"
                f" ({item.get('inliers')}点)"
                for item in items
            ) if items else "<该界面材料不在模板库内>"
            lines.append(f"[{ts}] 奖励清单({record.get('tag')}) {detail}")
        elif event == "stop":
            lines.append(
                f"[{ts}] 结束 原因={record.get('reason')} 轮数={record.get('rounds')} "
                f"周本={record.get('weekly_done')} 材料={record.get('material_done')} 空幕={record.get('console_done')}"
            )
        else:
            lines.append(f"[{ts}] {event} {json.dumps({k: v for k, v in record.items() if k not in ('ts', 'session', 'event')}, ensure_ascii=False)}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="查看培养目标刷本运行报告")
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument("--session", help="只看指定 session, 默认最后一次")
    parser.add_argument("--all", action="store_true", help="显示所有 session")
    parser.add_argument("--raw", action="store_true", help="输出原始 JSONL")
    parser.add_argument("--last", type=int, default=0, help="只显示最后 N 条")
    args = parser.parse_args(argv)

    # 报告文件只允许在默认 logs 目录内, 防 CLI 参数被诱导读任意路径。
    base = os.path.realpath(os.path.dirname(DEFAULT_PATH) or ".")
    resolved = os.path.realpath(args.path)
    if not resolved.startswith(base + os.sep):
        # 错误提示只给相对目录, 不把安装路径写进 stderr。
        parser.error("--path 只能指向 logs 目录内的报告文件")

    records = read_records(args.path, session=args.session, all_sessions=args.all)
    if args.last:
        records = records[-args.last:]
    if args.raw:
        for record in records:
            print(json.dumps(record, ensure_ascii=False))
    elif records:
        print(format_records(records))
    else:
        print(f"没有找到运行报告: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
