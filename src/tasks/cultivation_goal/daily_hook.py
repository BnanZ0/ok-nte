"""插件形态专用: 在内存里把本任务挂成宿主日常任务的子项。

宿主的 DAILY_ROUTINE_ENTRIES 是模块级元组, 每次宿主更新都会被还原成官方版本,
所以不能靠改宿主文件来保留这个子项。这里在插件加载时把条目补回内存, 宿主文件零改动。

任何异常都只记一条 warning, 绝不影响插件主功能(独立任务仍可正常使用)。
"""

from __future__ import annotations

import importlib

TASK_ID = "cultivation_goal"
HOST_MODULE = "src.tasks.daily.DailyRoutineTask"


def _warn(message: str) -> None:
    try:
        from ok import logger

        logger.warning(message)
    except Exception:
        print(message)


def _refresh_existing_instance() -> None:
    """插件晚于宿主实例化时, 让已存在的日常任务立刻把新条目写进配置(UI 才看得到)。"""
    try:
        from ok import og

        executor = getattr(getattr(og, "task_manager", None), "task_executor", None)
        if executor is None:
            return
        for task in executor.get_all_tasks():
            if type(task).__name__ == "DailyRoutineTask":
                task.normalize_items()
                return
    except Exception:
        pass


def register_daily_routine_entry(task_class, module=None) -> bool:
    """把 task_class 注册成日常任务的第一项; 已存在则刷新条目后原地保留。

    放在第一项与历史行为一致: 体力容易溢出, 先消耗体力再跑其它日常。
    """
    try:
        host = module or importlib.import_module(HOST_MODULE)
        entry_cls = getattr(host, "DailyRoutineEntry")
        entries = tuple(getattr(host, "DAILY_ROUTINE_ENTRIES"))
        rest = tuple(entry for entry in entries if getattr(entry, "task_id", None) != TASK_ID)
        entry = entry_cls(
            task_id=TASK_ID,
            task_class=task_class,
            enabled_by_default=True,
            daily_config=True,
        )
        host.DAILY_ROUTINE_ENTRIES = (entry,) + rest
        _refresh_existing_instance()
        return True
    except Exception as error:
        _warn(f"注册日常任务子项失败(不影响独立任务): {error}")
        return False
