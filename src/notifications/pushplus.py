"""Deliver explicitly marked BOT notifications through PushPlus.

See https://www.pushplus.plus/doc/ for the service API.

Only :class:`src.events.BotNotificationMessage` values are eligible. Ordinary
framework and task notifications stay local, matching the external
notification boundary enforced by ``src.globals``.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Mapping
from typing import Any

import requests
from ok import ConfigOption, Icon, Logger, og
from ok.util.file import get_relative_path, read_json_file, write_json_file

from src.events import BotNotificationMessage, communicate
from src.utils import clock

logger = Logger.get_logger(__name__)

CONFIG_NAME = "PushPlus Notification"
NATIVE_CONFIG_NAME = "Notification"
CONF_ENABLE = "PushPlus Notification"
LEGACY_CONF_ENABLE = "Enable PushPlus"
CONF_TOKEN = "PushPlus Token"
CONF_TOPIC = "PushPlus Topic"
CONF_TEMPLATE = "PushPlus Template"
PUSHPLUS_ENDPOINT = "https://www.pushplus.plus/send"
DEFAULT_TITLE = "OK-NTE"
SUPPORTED_TEMPLATES = ("txt", "markdown", "html")
PUSHPLUS_DEFAULTS = {
    CONF_ENABLE: False,
    CONF_TOKEN: "",
    CONF_TOPIC: "",
    CONF_TEMPLATE: "txt",
}
PUSHPLUS_DESCRIPTIONS = {
    CONF_ENABLE: (
        "Send BOT notifications through PushPlus; ordinary task logs are never forwarded"
    ),
    CONF_TOKEN: "Token from https://www.pushplus.plus/ ; keep it private and do not share it",
    CONF_TOPIC: "Optional group code; leave empty to send to yourself",
    CONF_TEMPLATE: "Message template: txt, markdown, or html",
}
PUSHPLUS_CONFIG_TYPES = {
    CONF_TOKEN: {"type": "line_edit", "minimum_width": 360, "maximum_width": 520},
    CONF_TOPIC: {"type": "line_edit", "minimum_width": 240, "maximum_width": 360},
    CONF_TEMPLATE: {"type": "drop_down", "options": list(SUPPORTED_TEMPLATES)},
}


def merge_pushplus_option(option: ConfigOption) -> None:
    """Add PushPlus fields to a native Notification ConfigOption."""
    if option.config_type is None:
        option.config_type = {}
    for key, value in PUSHPLUS_DEFAULTS.items():
        option.default_config.setdefault(key, value)
        option.config_description.setdefault(key, PUSHPLUS_DESCRIPTIONS[key])
    option.config_type.setdefault(
        CONF_ENABLE,
        {"sub_configs": {True: [CONF_TOKEN, CONF_TOPIC, CONF_TEMPLATE]}},
    )
    for key, type_spec in PUSHPLUS_CONFIG_TYPES.items():
        option.config_type.setdefault(key, type_spec)


def create_pushplus_config_option() -> ConfigOption:
    """Build a fallback option for runtimes without the native Notification tab."""
    option = ConfigOption(
        CONFIG_NAME,
        dict(PUSHPLUS_DEFAULTS),
        description=(
            "Forward BOT notifications, such as daily routine reports, to WeChat "
            "through PushPlus. Ordinary logs and errors stay local."
        ),
        config_description=dict(PUSHPLUS_DESCRIPTIONS),
        config_type={},
        icon=Icon.RINGER,
    )
    merge_pushplus_option(option)
    return option


def install_pushplus_notification_options_patch() -> None:
    """Extend native Notification defaults before ok-script loads the config file."""
    try:
        from ok.util import GlobalConfig as global_config_module

        original = getattr(global_config_module, "create_notification_options", None)
        if not callable(original) or getattr(
            global_config_module, "_pushplus_options_patched", False
        ):
            return

        def create_notification_options():
            option = original()
            merge_pushplus_option(option)
            return option

        global_config_module.create_notification_options = create_notification_options
        global_config_module._pushplus_options_patched = True
    except Exception as exc:
        logger.warning(f"PushPlus notification options patch failed: {exc}")


def migrate_legacy_pushplus_config(config_folder: str = "configs") -> None:
    """Rename the old enable key before ok-script loads the Notification config."""
    try:
        path = get_relative_path(config_folder, f"{NATIVE_CONFIG_NAME}.json")
        data = read_json_file(path)
        if not isinstance(data, dict):
            return
        sentinel = object()
        enabled = data.pop(LEGACY_CONF_ENABLE, sentinel)
        if enabled is sentinel:
            return
        data.setdefault(CONF_ENABLE, bool(enabled))
        write_json_file(path, data)
    except Exception as exc:
        logger.warning(f"PushPlus config migration failed: {exc}")


def register_pushplus_notification_options(global_config=None):
    """Merge PushPlus fields into the native Notification config when available."""
    global_config = global_config or getattr(og, "global_config", None)
    if global_config is None:
        return None
    try:
        option = global_config.config_options.get(NATIVE_CONFIG_NAME)
        config = global_config.get_config(NATIVE_CONFIG_NAME)
    except Exception:
        option = None
        config = None
    if option is None or config is None:
        return global_config.get_config(create_pushplus_config_option())

    merge_pushplus_option(option)
    for key, value in PUSHPLUS_DEFAULTS.items():
        config.default.setdefault(key, value)
        config.setdefault(key, value)
    config.save_file()
    return config


class PushPlusError(RuntimeError):
    """Raised when PushPlus rejects a request or returns an invalid response."""


def pushplus_config() -> Mapping[str, Any]:
    """Return the current PushPlus config from Notification, or an empty mapping."""
    global_config = getattr(og, "global_config", None)
    if global_config is None:
        return {}
    for name in (NATIVE_CONFIG_NAME, CONFIG_NAME):
        try:
            return global_config.get_config(name)
        except Exception:
            continue
    logger.debug("PushPlus config is unavailable")
    return {}


def build_pushplus_payload(
    config: Mapping[str, Any] | None, title: str | None, content: str
) -> dict[str, str] | None:
    """Build a request payload, or return ``None`` when sending is disabled."""
    if not isinstance(config, Mapping) or not config.get(CONF_ENABLE):
        return None
    token = str(config.get(CONF_TOKEN) or "").strip()
    message = "" if content is None else str(content)
    if not token or not message.strip():
        return None
    template = str(config.get(CONF_TEMPLATE) or "txt").strip().lower()
    if template not in SUPPORTED_TEMPLATES:
        template = "txt"
    payload = {
        "token": token,
        "title": str(title or DEFAULT_TITLE),
        "content": message,
        "template": template,
    }
    topic = str(config.get(CONF_TOPIC) or "").strip()
    if topic:
        payload["topic"] = topic
    return payload


def _translated_notification(
    message: Any, title: Any, params: Mapping[str, Any] | None
) -> tuple[str | None, str]:
    """Match the text preparation done by the native notification path."""
    translate = getattr(getattr(og, "app", None), "tr", None)
    text = str(message)
    title_text = str(title) if title else None
    if callable(translate):
        try:
            text = str(translate(message))
            title_text = str(translate(title)) if title else None
        except Exception:
            pass
    if params:
        try:
            text = text.format(**params)
        except (KeyError, IndexError, ValueError):
            pass
    return title_text, text


def _http_send(endpoint: str, payload: Mapping[str, str], timeout: float) -> dict[str, Any]:
    response = requests.post(endpoint, json=dict(payload), timeout=timeout)
    response.raise_for_status()
    try:
        result = response.json()
    except ValueError as exc:
        raise PushPlusError("PushPlus returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise PushPlusError("PushPlus returned an unexpected response")
    if result.get("code") != 200:
        raise PushPlusError(
            f"PushPlus rejected the message: code={result.get('code')}, "
            f"msg={result.get('msg')}"
        )
    return result


class PushPlusNotifier:
    """Queue PushPlus requests and send them away from the UI thread."""

    def __init__(
        self,
        config_provider: Callable[[], Mapping[str, Any]] | None = None,
        *,
        endpoint: str = PUSHPLUS_ENDPOINT,
        timeout: float = 15.0,
        interval: float = 5.0,
        send: Callable[[Mapping[str, str]], Any] | None = None,
    ) -> None:
        self._config_provider = config_provider or pushplus_config
        self._endpoint = endpoint
        self._timeout = timeout
        self._interval = max(0.0, float(interval))
        self._send = send
        self._queue: queue.Queue[dict[str, str] | None] = queue.Queue()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._worker,
            name="PushPlusNotification",
            daemon=True,
        )
        self._thread.start()

    def submit(self, message: str, title: str | None = None) -> bool:
        """Queue a notification for background delivery."""
        if self._stopped.is_set():
            return False
        config = self._current_config()
        if config is None:
            return False
        payload = build_pushplus_payload(config, title, message)
        if payload is None:
            return False
        self._queue.put(payload)
        return True

    def send_now(
        self,
        message: str,
        title: str | None = None,
        *,
        config: Mapping[str, Any] | None = None,
    ) -> bool:
        """Send synchronously; used by tests and manual diagnostics."""
        current = self._current_config() if config is None else config
        if current is None:
            return False
        payload = build_pushplus_payload(current, title, message)
        if payload is None:
            return False
        return self._deliver(payload)

    def _current_config(self) -> Mapping[str, Any] | None:
        try:
            config = self._config_provider()
        except Exception as exc:
            logger.warning(f"PushPlus config is unavailable: {exc}")
            return None
        return config if isinstance(config, Mapping) else None

    def stop(self, wait: bool = False, timeout: float = 5.0) -> bool:
        """Stop the worker; matching the framework, do not wait by default."""
        if not self._stopped.is_set():
            self._stopped.set()
            self._queue.put(None)
        if wait and threading.current_thread() is not self._thread:
            self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def _worker(self) -> None:
        last_sent = clock.UNSET
        while True:
            payload = self._queue.get()
            try:
                if payload is None:
                    return
                if self._stopped.is_set():
                    continue
                if last_sent:
                    delay = self._interval - clock.elapsed(last_sent)
                    if delay > 0 and self._stopped.wait(delay):
                        continue
                self._deliver(payload)
            finally:
                if payload is not None:
                    last_sent = clock.now()
                self._queue.task_done()

    def _deliver(self, payload: Mapping[str, str]) -> bool:
        try:
            if self._send is None:
                _http_send(self._endpoint, payload, self._timeout)
            else:
                self._send(payload)
        except Exception as exc:
            logger.error("PushPlus notification failed", exc)
            return False
        return True


def install_pushplus_notifications(notifier: PushPlusNotifier) -> Callable[..., None]:
    """Forward only explicitly marked BOT notifications to PushPlus."""

    def handle_notification(
        message,
        title=None,
        error=False,
        tray=False,
        show_tab=None,
        params=None,
        images=None,
        **_kwargs,
    ):
        if not isinstance(message, BotNotificationMessage):
            return
        try:
            title_text, message_text = _translated_notification(message, title, params)
            notifier.submit(message_text, title=title_text)
        except Exception as exc:
            logger.warning(f"PushPlus notification dispatch failed: {exc}")

    communicate.notification.connect(handle_notification)
    return handle_notification


def uninstall_pushplus_notifications(handler: Callable[..., None]) -> None:
    """Remove a handler created by :func:`install_pushplus_notifications`."""
    communicate.notification.disconnect(handler)
