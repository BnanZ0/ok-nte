from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass

from ok import TaskDisabledException
from ok.util.config import Config

from src.tasks.AnomalyHunter import AnomalyHunter
from src.tasks.AnomalyTask import AnomalyTask
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.daily.CinemaDateTask import CinemaDateTask
from src.tasks.daily.CoffeeTask import CoffeeTask
from src.tasks.daily.DailyActivityTask import DailyActivityTask
from src.tasks.daily.DailyClaimTask import DailyClaimTask
from src.tasks.daily.FountainTask import FountainTask
from src.tasks.daily.FurnitureTask import FurnitureTask
from src.tasks.daily.GiftTask import GiftTask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask

__all__ = ["DailyRoutineEntry", "DailyRoutineTask", "selection_is_complete"]


@dataclass(frozen=True)
class DailyRoutineEntry:
    task_id: str
    task_class: type
    enabled_by_default: bool = False
    exclusive_group: str | None = None
    daily_config: bool = False


DAILY_ROUTINE_ENTRIES = (
    DailyRoutineEntry("daily_anomaly", AnomalyTask, True, "daily_anomaly", True),
    DailyRoutineEntry("daily_anomaly_hunter", AnomalyHunter, False, "daily_anomaly", True),
    DailyRoutineEntry("coffee", CoffeeTask),
    DailyRoutineEntry("daily_claim", DailyClaimTask, True),
    DailyRoutineEntry("cinema_date", CinemaDateTask),
    DailyRoutineEntry("fountain", FountainTask),
    DailyRoutineEntry("furniture", FurnitureTask),
    DailyRoutineEntry("gift", GiftTask),
    DailyRoutineEntry("daily_activity", DailyActivityTask),
)


def selection_is_complete(items, entries) -> bool:
    if not items:
        return False
    enabled_groups = set()
    for item in items:
        entry = entries[item["id"]]
        if entry.exclusive_group:
            if item["enabled"]:
                enabled_groups.add(entry.exclusive_group)
        elif not item["enabled"]:
            return False
    return all(
        entry.exclusive_group is None or entry.exclusive_group in enabled_groups
        for entry in entries.values()
    )


def selected_routine_tasks(routine_task):
    tasks = []
    for item in routine_task.normalize_items():
        if not item["enabled"]:
            continue
        if task := routine_task.task_for_id(item["id"]):
            tasks.append(task)
    return tasks


def routine_has_active_tasks(tasks):
    return any(task.enabled or task.running for task in tasks)


def start_routine_tasks(start_controller, routine_task):
    return start_controller.do_start(routine_task)


@dataclass
class _DailyTaskSchema:
    default_config: dict
    config_description: dict
    config_type: dict


class _DailyTaskConfig(dict):
    """A task config view persisted in the daily routine task-config store."""

    def __init__(self, routine_task, task_id, task, default_config):
        self.routine_task = routine_task
        self.task_id = task_id
        self.task = task
        self.default = deepcopy(default_config)
        values = deepcopy(self.default)
        stored_values = routine_task.routine_task_configs.get(task_id)
        if isinstance(stored_values, dict):
            for key, value in stored_values.items():
                if key in values and isinstance(value, type(values[key])):
                    values[key] = deepcopy(value)
        super().__init__(values)
        if isinstance(stored_values, dict) and list(stored_values) != list(self):
            self.save_file()

    def get_default(self, key):
        return self.default.get(key)

    def has_user_config(self):
        return any(not key.startswith("_") for key in self)

    def __setitem__(self, key, value):
        if self.get(key) == value:
            return
        super().__setitem__(key, value)
        self.save_file()

    def update(self, *args, **kwargs):
        values = dict(*args, **kwargs)
        if all(self.get(key) == value for key, value in values.items()):
            return
        super().update(values)
        self.save_file()

    def reset_to_default(self):
        if dict(self) == self.default:
            return
        super().clear()
        super().update(deepcopy(self.default))
        self.save_file()

    def save_file(self):
        self.routine_task.routine_task_configs[self.task_id] = deepcopy(dict(self))


class DailyRoutineTask(NTEOneTimeTask, BaseNTETask):
    CONF_ITEMS = "Routine Items"
    TASK_CONFIGS_FILE_NAME = "DailyRoutineTaskConfigs"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.support_schedule_task = True
        self.show_in_task_tab = False
        self.visible = True
        self.task_status = {"success": [], "failed": [], "skipped": [], "pending": []}
        self.current_task_key = None
        self._active_routine_task = None
        self.routine_task_configs = None
        self.default_config[self.CONF_ITEMS] = self.default_items()
        self.config_description[self.CONF_ITEMS] = "日常任务中的任务顺序和启用状态"
        self.config_type[self.CONF_ITEMS] = {"hidden": True}
        self.add_exit_after_config()

    @staticmethod
    def default_items():
        return [
            {"id": entry.task_id, "enabled": entry.enabled_by_default}
            for entry in DAILY_ROUTINE_ENTRIES
        ]

    @staticmethod
    def default_task_configs():
        return {entry.task_id: {} for entry in DAILY_ROUTINE_ENTRIES}

    @staticmethod
    def entries_by_id():
        return {entry.task_id: entry for entry in DAILY_ROUTINE_ENTRIES}

    def on_create(self):
        self.routine_task_configs = Config(
            self.TASK_CONFIGS_FILE_NAME,
            self.default_task_configs(),
        )
        self.normalize_items()

    def normalize_items(self):
        entries = self.entries_by_id()
        raw_items = self.config.get(self.CONF_ITEMS, [])
        raw_item_list = raw_items if isinstance(raw_items, list) else []
        normalized = []
        seen = set()
        for item in raw_item_list:
            if not isinstance(item, dict):
                continue
            task_id = item.get("id")
            if task_id not in entries or task_id in seen:
                continue
            normalized.append({"id": task_id, "enabled": bool(item.get("enabled", False))})
            seen.add(task_id)
        for entry in DAILY_ROUTINE_ENTRIES:
            if entry.task_id not in seen:
                normalized.append({"id": entry.task_id, "enabled": entry.enabled_by_default})

        enabled_groups = set()
        for item in normalized:
            entry = entries[item["id"]]
            if item["enabled"] and entry.exclusive_group:
                if entry.exclusive_group in enabled_groups:
                    item["enabled"] = False
                else:
                    enabled_groups.add(entry.exclusive_group)
        if raw_items != normalized:
            self.config[self.CONF_ITEMS] = deepcopy(normalized)  # pyright: ignore[reportOptionalSubscript]
        return normalized

    def set_items(self, items):
        self.config[self.CONF_ITEMS] = deepcopy(items)  # pyright: ignore[reportOptionalSubscript]
        return self.normalize_items()

    def set_item_enabled(self, task_id, enabled):
        entries = self.entries_by_id()
        if task_id not in entries:
            return self.normalize_items()

        items = self.normalize_items()
        for item in items:
            if item["id"] == task_id:
                item["enabled"] = enabled
            elif enabled and entries[task_id].exclusive_group:
                other = entries[item["id"]]
                if other.exclusive_group == entries[task_id].exclusive_group:
                    item["enabled"] = False
        return self.set_items(items)

    def set_all_available_items_selected(self, selected):
        items = self.normalize_items()
        available_ids = {item["id"] for item in items if self.task_for_id(item["id"]) is not None}
        if not selected:
            return self.set_items(
                [
                    {
                        "id": item["id"],
                        "enabled": False if item["id"] in available_ids else item["enabled"],
                    }
                    for item in items
                ]
            )

        entries = self.entries_by_id()
        selected_groups = {
            entries[item["id"]].exclusive_group: item["id"]
            for item in items
            if item["id"] in available_ids
            and item["enabled"]
            and entries[item["id"]].exclusive_group
        }
        assigned_groups = set()
        for item in items:
            if item["id"] not in available_ids:
                continue
            group = entries[item["id"]].exclusive_group
            if not group:
                item["enabled"] = True
            elif group not in assigned_groups:
                item["enabled"] = selected_groups.get(group, item["id"]) == item["id"]
                assigned_groups.add(group)
            else:
                item["enabled"] = False
        return self.set_items(items)

    def set_available_item_order(self, ordered_task_ids):
        items = self.normalize_items()
        available_ids = {item["id"] for item in items if self.task_for_id(item["id"]) is not None}
        if set(ordered_task_ids) != available_ids or len(ordered_task_ids) != len(available_ids):
            return items

        items_by_id = {item["id"]: item for item in items}
        hidden_by_position = [[] for _ in range(len(ordered_task_ids) + 1)]
        visible_position = 0
        for item in items:
            if item["id"] in available_ids:
                visible_position += 1
            else:
                hidden_by_position[visible_position].append(item)

        reordered = []
        for index, task_id in enumerate(ordered_task_ids):
            reordered.extend(hidden_by_position[index])
            reordered.append(items_by_id[task_id])
        reordered.extend(hidden_by_position[-1])
        return self.set_items(reordered)

    def reset_items(self):
        return self.set_items(self.default_items())

    def task_for_id(self, task_id):
        entry = self.entries_by_id().get(task_id)
        return self.get_task_by_class(entry.task_class) if entry is not None else None

    def daily_task_schema(self, task_id, task):
        entry = self.entries_by_id()[task_id]
        setup_config = getattr(entry.task_class, "setup_config", None)
        if callable(setup_config):
            schema = _DailyTaskSchema({}, {}, {})
            setup_config(schema, daily=entry.daily_config)
            return schema
        return _DailyTaskSchema(
            deepcopy(task.default_config),
            deepcopy(task.config_description),
            deepcopy(task.config_type),
        )

    def daily_task_config(self, task_id, task, schema=None):
        if schema is None:
            schema = self.daily_task_schema(task_id, task)
        return _DailyTaskConfig(self, task_id, task, schema.default_config)

    @contextmanager
    def daily_task_card_context(self, task_id, task):
        schema = self.daily_task_schema(task_id, task)
        original_config = task.config
        original_default = task.default_config
        original_description = task.config_description
        original_type = task.config_type
        task.config = self.daily_task_config(task_id, task, schema)
        task.default_config = schema.default_config
        task.config_description = schema.config_description
        task.config_type = schema.config_type
        try:
            yield task
        finally:
            task.config = original_config
            task.default_config = original_default
            task.config_description = original_description
            task.config_type = original_type

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            raise
        except Exception as error:
            self.screenshot("daily_routine_unexpected_exception")
            if self.current_task_key:
                self.info_set("当前失败任务", self.current_task_key)
            self._print_result()
            self.log_error("DailyRoutineTask error", error)
            raise

    def do_run(self) -> bool:
        self.scene.set_logged_in(False)
        items = self.normalize_items()
        selected = [item for item in items if item["enabled"]]
        if not selected:
            self.log_info("日常任务没有已选任务，跳过执行")
            return True
        tasks = [self.task_for_id(item["id"]) for item in selected]
        if routine_has_active_tasks([task for task in tasks if task is not None]):
            self.log_warning("日常任务中的任务已在运行或排队，跳过重复入队")
            return False
        self._reset_task_status(items)
        self.log_info("开始执行日常任务")
        for item in items:
            self._execute_routine_item(item)
        self.ensure_main()
        self._print_result()
        self.log_info("结束执行日常任务")
        return not self.task_status["failed"]

    def _execute_routine_item(self, item):
        task_id = item["id"]
        self.task_status["pending"].remove(task_id)
        task = self.task_for_id(task_id)
        if not item["enabled"]:
            self.task_status["skipped"].append(task_id)
            return
        if task is None:
            self.task_status["skipped"].append(task_id)
            self.log_info(f"任务不支持当前语言，跳过: {task_id}")
            return

        self.current_task_key = task_id
        self.info_set("当前任务", task.name)
        self.log_info(f"开始任务: {task.name}")
        self.ensure_main()
        try:
            with self._active_task_context(task_id, task):
                result = task.do_run()
                entry = self.entries_by_id()[task_id]
                if result and entry.daily_config and (shift_id := getattr(task, "shift_id", None)):
                    shift_id(task)
        except TaskDisabledException:
            raise
        except Exception as error:
            self.log_error(f"任务运行失败: {task.name}", error)
            result = False

        if not result:
            self.task_status["failed"].append(task_id)
            self.screenshot(f"daily_routine_fail_{task_id}")
            self.log_info(f"任务失败: {task.name}")
            return

        self.task_status["success"].append(task_id)
        self.current_task_key = None
        self.log_info(f"任务完成: {task.name}")

    def _reset_task_status(self, items):
        self.task_status = {
            "success": [],
            "failed": [],
            "skipped": [],
            "pending": [item["id"] for item in items],
        }

    def _print_result(self):
        results = {
            status: [self._task_display_name(task_id) for task_id in task_ids]
            for status, task_ids in self.task_status.items()
            if status in ("success", "failed", "skipped")
        }
        self.info_set("success", f"{results['success']}")
        self.info_set("failed", f"{results['failed']}")
        self.info_set("skipped", f"{results['skipped']}")
        result = "\n".join(
            (
                f"success: {results['success']}",
                f"failed: {results['failed']}",
                f"skipped: {results['skipped']}",
            )
        )
        self.log_info(result, notify=True)

    def _task_display_name(self, task_id):
        task = self.task_for_id(task_id)
        if task is None:
            return task_id
        return self.tr(task.name) if getattr(self, "_app", None) is not None else task.name

    @contextmanager
    def _active_task_context(self, task_id, task):
        previous_task = self._active_routine_task
        previous_interval = self.sleep_check_interval
        original_config = task.config
        self._active_routine_task = task
        self.sleep_check_interval = task.sleep_check_interval
        task.config = self.daily_task_config(task_id, task)
        try:
            yield task
        finally:
            task.config = original_config
            self._active_routine_task = previous_task
            self.sleep_check_interval = previous_interval

    def sleep_check(self):
        if self._active_routine_task is not None:
            return self._active_routine_task.sleep_check()
        return super().sleep_check()
