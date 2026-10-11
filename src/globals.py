import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event
from types import MethodType

from ok import Logger

from src.events import BotNotificationMessage, communicate
from src.runtime.services import RuntimeServices

logger = Logger.get_logger(__name__)


def _install_bot_notification_filter(main_window) -> None:
    """Forward external delivery only while handling an explicitly marked message."""
    if getattr(main_window, "_bot_notification_filter", None) is not None:
        return
    allowed = ContextVar("bot_notification_allowed", default=False)
    manager = main_window.notification_manager
    original_submit = manager.submit
    original_show = main_window.show_notification

    def submit(*args, **kwargs):
        if allowed.get():
            return original_submit(*args, **kwargs)
        return None

    def show_notification(_window, *args, **kwargs):
        message = args[0] if args else kwargs.get("message")
        token = allowed.set(isinstance(message, BotNotificationMessage))
        try:
            return original_show(*args, **kwargs)
        finally:
            allowed.reset(token)

    handler = MethodType(show_notification, main_window)
    manager.submit = submit
    communicate.notification.disconnect(original_show)
    main_window.show_notification = handler
    communicate.notification.connect(handler)
    main_window.destroyed.connect(lambda *_: communicate.notification.disconnect(handler))
    main_window._bot_notification_filter = handler


class Globals:
    def __init__(self, exit_event):
        self._thread_pool_executor_max_workers = 0
        self.thread_pool_executor = None
        self.thread_pool_exit_event = Event()
        self._periodic_tasks = {}
        self._periodic_tasks_lock = threading.Lock()
        self._runtime_services = RuntimeServices()
        exit_event.bind_stop(self)
        communicate.start_success.connect(self._start_runtime_services)

    def _start_runtime_services(self, *_):
        self._runtime_services.start()

    def on_show_main_window(self, main_window) -> None:
        from src.ui.foundation.dialogs import install_confirmation_handler
        from src.ui.foundation.overlay import install_overlay_window

        _install_bot_notification_filter(main_window)
        main_window.setMinimumSize(1200, 800)
        main_window._confirmation_handler = install_confirmation_handler(main_window)
        install_overlay_window(main_window)

    def stop(self):
        self._runtime_services.stop()
        self.shutdown_thread_pool_executor()

    def get_thread_pool_executor(self, max_workers=6):
        """
        获取全局执行器。
        如果请求的 max_workers 大于当前值，将安全地重建线程池。
        """
        if (
            self.thread_pool_executor is not None
            and max_workers > self._thread_pool_executor_max_workers
        ):
            logger.info(
                "thread pool max_workers not enough, reset max_workers"
                f" {self._thread_pool_executor_max_workers} -> {max_workers}"
            )
            self.shutdown_thread_pool_executor()

        if self.thread_pool_executor is None:
            logger.info(f"create thread pool executor, max_workers: {max_workers}")
            self.thread_pool_exit_event = Event()
            self.thread_pool_executor = ThreadPoolExecutor(max_workers=max_workers)
            self._thread_pool_executor_max_workers = max_workers

        return self.thread_pool_executor

    def shutdown_thread_pool_executor(self):
        if self.thread_pool_executor is not None:
            logger.info("Shutting down thread pool executor...")
            with self._periodic_tasks_lock:
                for record in self._periodic_tasks.values():
                    record["stop_event"].set()
                self._periodic_tasks.clear()
            self.thread_pool_exit_event.set()
            self.thread_pool_executor.shutdown(wait=False, cancel_futures=True)
            self.thread_pool_executor = None
            self._thread_pool_executor_max_workers = 0

    def _get_periodic_task_key(self, task):
        bound_self = getattr(task, "__self__", None)
        func = getattr(task, "__func__", task)
        func_name = getattr(func, "__name__", repr(task))

        if bound_self is not None:
            cls = bound_self.__class__
            return ("bound_method", cls.__module__, cls.__qualname__, func_name)

        return (
            "callable",
            getattr(func, "__module__", None),
            getattr(func, "__qualname__", func_name),
            None,
        )

    def _get_periodic_task_name(self, task):
        bound_self = getattr(task, "__self__", None)
        func = getattr(task, "__func__", task)
        func_name = getattr(func, "__name__", repr(task))
        if bound_self is None:
            return func_name
        return f"{bound_self.__class__.__name__}.{func_name}"

    def submit_periodic_task(self, delay, task, *args, **kwargs):
        """
        提交一个循环任务到线程池。
        如果要停止循环，任务函数应返回 False。

        :param task: 要执行的函数
        :param delay: 每次执行后的间隔时间（秒）
        :param args: 位置参数
        :param kwargs: 关键字参数
        """
        executor = self.get_thread_pool_executor()
        exit_event = self.thread_pool_exit_event
        task_key = self._get_periodic_task_key(task)
        task_name = self._get_periodic_task_name(task)
        task_stop_event = Event()

        with self._periodic_tasks_lock:
            old_record = self._periodic_tasks.get(task_key)
            if old_record is not None:
                logger.debug(f"Stopping previous periodic task {task_name}.")
                old_record["stop_event"].set()
                old_future = old_record.get("future")
                if old_future is not None:
                    old_future.cancel()

            self._periodic_tasks[task_key] = {
                "stop_event": task_stop_event,
                "future": None,
            }

        def loop_wrapper():
            logger.debug(f"Periodic task {task_name} started.")

            try:
                while not exit_event.is_set() and not task_stop_event.is_set():
                    should_stop = False
                    try:
                        if task(*args, **kwargs) is False:
                            should_stop = True
                    except Exception as e:
                        logger.error(f"Error in periodic task {task_name}: {e}")

                    if should_stop:
                        logger.debug(f"Periodic task {task_name} decided to stop.")
                        break

                    if task_stop_event.wait(timeout=delay) or exit_event.is_set():
                        logger.debug(f"Periodic task {task_name} received stop signal.")
                        break

                logger.debug(f"Periodic task {task_name} stopped.")
            finally:
                with self._periodic_tasks_lock:
                    current_record = self._periodic_tasks.get(task_key)
                    if (
                        current_record is not None
                        and current_record["stop_event"] is task_stop_event
                    ):
                        del self._periodic_tasks[task_key]

        future = executor.submit(loop_wrapper)
        with self._periodic_tasks_lock:
            current_record = self._periodic_tasks.get(task_key)
            if current_record is not None and current_record["stop_event"] is task_stop_event:
                current_record["future"] = future
        return future

    @property
    def openvino_model_async(self):
        return self._runtime_services.openvino_model_async

    @property
    def openvino_latency_async(self):
        return self._runtime_services.openvino_latency_async

    @property
    def openvino_latest_image(self):
        return self._runtime_services.openvino_latest_image

    def openvino_detect(
        self, image, sync=False, box=None, threshold=0.5, force=False, mask_regions=None
    ):
        return self._runtime_services.openvino_detect(
            image,
            sync=sync,
            box=box,
            threshold=threshold,
            force=force,
            mask_regions=mask_regions,
        )

    def openvino_clear_cache(self):
        self._runtime_services.openvino_clear_cache()

    @property
    def _openvino_model_async(self):
        return self._runtime_services._openvino_model_async

    @_openvino_model_async.setter
    def _openvino_model_async(self, detector) -> None:
        self._runtime_services._openvino_model_async = detector

    @property
    def openvino_available(self):
        return self._runtime_services.openvino_available
