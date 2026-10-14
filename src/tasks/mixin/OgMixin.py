import ctypes
import inspect
from typing import Any

import win32api
import win32con
import win32gui
import win32process
from ok import BaseTask, Logger, og
from ok.core.config_schema import resolve_config_type

logger = Logger.get_logger(__name__)


class OgMixin(BaseTask):
    # Any accommodates upstream BaseTask's inferred None return type in multiple inheritance.
    def validate_config(self, key, value) -> Any:
        type_spec = self.config_type.get(key)
        if resolve_config_type(type_spec, self.default_config.get(key)) == "drop_down":
            options = type_spec.get("options")
            if isinstance(options, (list, tuple)) and value not in options:
                return f"Invalid value for {key}: {value!r}. Expected one of {options!r}."
        if isinstance(type_spec, dict) and isinstance(value, (int, float)):
            minimum = type_spec.get("min")
            maximum = type_spec.get("max")
            if minimum is not None and value < minimum:
                return f"Invalid value for {key}: {value!r}. Expected >= {minimum}."
            if maximum is not None and value > maximum:
                return f"Invalid value for {key}: {value!r}. Expected <= {maximum}."

    def sync_config(self, config=None):
        """同步保存配置, 并刷新对应的配置 UI。"""
        target_config = config if config is not None else self.config
        if hasattr(target_config, "save_file"):
            target_config.save_file()
        self._refresh_config_ui(target_config)

    def _refresh_config_ui(self, config):
        """刷新指定配置对应的 UI 界面"""
        main_window = self._get_main_window()
        if main_window is None:
            return

        for widget in self._iter_config_ui_widgets(main_window):
            if (
                getattr(widget, "config", None) is config
                and hasattr(widget, "update_config")
                and self.can_be_called_without_args(widget.update_config)
            ):
                widget.update_config()

    @staticmethod
    def can_be_called_without_args(func):
        """判断一个函数/方法是否可以在不传入任何参数的情况下调用"""
        sig = inspect.signature(func)
        for param in sig.parameters.values():
            if param.default == inspect.Parameter.empty and param.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                return False
        return True

    @staticmethod
    def _get_main_window():
        main_window = getattr(og, "main_window", None)
        if main_window is not None:
            return main_window

        app = getattr(og, "app", None)
        if app is None:
            return None
        return getattr(app, "main_window", None)

    @staticmethod
    def _iter_config_ui_widgets(main_window):
        """Iterate task/global config widgets without depending on one tab path."""
        roots = [
            getattr(main_window, "onetime_tab", None),
            getattr(main_window, "trigger_tab", None),
            getattr(main_window, "setting_tab", None),
        ]
        roots.extend(getattr(main_window, "grouped_task_tabs", []) or [])
        roots.extend(getattr(main_window, "global_config_tabs", []) or [])
        roots.extend((getattr(main_window, "imported_tabs", {}) or {}).values())

        seen = set()
        stack = [root for root in roots if root is not None]
        while stack:
            widget = stack.pop()
            widget_id = id(widget)
            if widget_id in seen:
                continue
            seen.add(widget_id)
            yield widget

            for attr in ("card_widgets", "config_groups"):
                stack.extend(getattr(widget, attr, []) or [])

            children = getattr(widget, "children", None)
            if callable(children):
                stack.extend(children())

    def is_foreground(self):
        """
        检查窗口是否在最前端。
        """
        if not self.hwnd:
            return False
        return self.hwnd.is_foreground()

    def bring_to_front(self, after_sleep=0):
        """
        强制将窗口带到最前端。
        """
        if not self.hwnd:
            self.log_warning("bring_to_front skipped: hwnd_window unavailable")
            return False
        hwnd = self.hwnd.hwnd

        if self.is_foreground():
            self.log_info(f"bring_to_front {hwnd} already is foreground")
            return True

        self.log_info(f"try bring_to_front {hwnd}")

        current_thread_id = 0
        target_thread_id = 0
        foreground_thread_id = 0
        attached_target = False
        attached_foreground = False

        try:
            current_thread_id = win32api.GetCurrentThreadId()
            target_thread_id, _ = win32process.GetWindowThreadProcessId(hwnd)
            foreground_hwnd = win32gui.GetForegroundWindow()
            if foreground_hwnd:
                foreground_thread_id, _ = win32process.GetWindowThreadProcessId(foreground_hwnd)

            if target_thread_id and target_thread_id != current_thread_id:
                attached_target = bool(
                    ctypes.windll.user32.AttachThreadInput(
                        current_thread_id, target_thread_id, True
                    )
                )
            if (
                foreground_thread_id
                and foreground_thread_id != current_thread_id
                and foreground_thread_id != target_thread_id
            ):
                attached_foreground = bool(
                    ctypes.windll.user32.AttachThreadInput(
                        current_thread_id, foreground_thread_id, True
                    )
                )

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            self.sleep(0.1)
            if self.is_foreground():
                self.log_info(f"bring_to_front {hwnd} succeeded")
                self.sleep(after_sleep)
                return True
            self.log_info(f"bring_to_front {hwnd} did not keep foreground")
            return False
        except Exception as e:
            logger.debug(f"bring_to_front failed: {e}")
            return False
        finally:
            if attached_foreground:
                ctypes.windll.user32.AttachThreadInput(
                    current_thread_id, foreground_thread_id, False
                )
            if attached_target:
                ctypes.windll.user32.AttachThreadInput(current_thread_id, target_thread_id, False)
