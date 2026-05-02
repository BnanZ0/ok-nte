import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ok import Logger

from src.sound_trigger.DodgeCounterTrigger import DodgeCounterTrigger
from src.sound_trigger.SoundListener import SoundListener

logger = Logger.get_logger(__name__)


class SoundCombatContext:
    _instance = None
    _lock = threading.Lock()
    _is_sound_action_busy = False
    _busy_lock = threading.Lock()
    _combat_interrupt = threading.Event()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        self._listener: Optional[SoundListener] = None
        self._trigger: Optional[DodgeCounterTrigger] = None
        self._thread_pool: Optional[ThreadPoolExecutor] = None
        self._context_lock = threading.Lock()
        self._is_active = False
        self._config = {}

    @classmethod
    def set_busy(cls):
        with cls._busy_lock:
            cls._is_sound_action_busy = True
        logger.debug("SoundCombatContext: Sound action busy")

    @classmethod
    def clear_busy(cls):
        with cls._busy_lock:
            cls._is_sound_action_busy = False
        logger.debug("SoundCombatContext: Sound action complete")

    @classmethod
    def enter_priority(cls):
        cls.set_busy()
        cls._combat_interrupt.set()
        logger.info("SoundCombatContext: Combat interrupt signal sent")

    @classmethod
    def exit_priority(cls):
        cls._combat_interrupt.clear()
        cls.clear_busy()

    @classmethod
    def should_interrupt_combat(cls):
        return cls._combat_interrupt.is_set()

    @classmethod
    def wait_for_sound_action_complete(cls, timeout=1.0):
        start = time.time()
        while cls._is_sound_action_busy:
            if time.time() - start > timeout:
                return False
            time.sleep(0.01)
        return True

    def setup(
        self,
        task,
        enable_sound_trigger: bool = True,
        sample_path: str = "./闪避波形.wav",
        counter_attack_sample_path: str = "./承轨反击波形.wav",
        threshold: float = 0.13,
        counter_attack_threshold: float = 0.12,
        thread_pool_size: int = 4,
        **kwargs,
    ):
        if not enable_sound_trigger:
            return

        if not (0.0 <= threshold <= 1.0):
            raise ValueError("threshold must be between 0.0 and 1.0")
        if not (0.0 <= counter_attack_threshold <= 1.0):
            raise ValueError("counter_attack_threshold must be between 0.0 and 1.0")
        if thread_pool_size < 1:
            raise ValueError("thread_pool_size must be a positive integer")

        with self._context_lock:
            if self._is_active:
                return

            self._config = {
                "sample_path": sample_path,
                "counter_attack_sample_path": counter_attack_sample_path,
                "threshold": threshold,
                "counter_attack_threshold": counter_attack_threshold,
                "thread_pool_size": thread_pool_size,
            }

            self._listener = SoundListener(
                sample_path=sample_path,
                counter_attack_sample_path=counter_attack_sample_path,
                threshold=threshold,
                counter_attack_threshold=counter_attack_threshold,
            )

            self._trigger = DodgeCounterTrigger(
                task=task,
            )

            self._listener.on_dodge_triggered = self._on_dodge_triggered
            self._listener.on_counter_triggered = self._on_counter_triggered

            self._thread_pool = ThreadPoolExecutor(
                max_workers=thread_pool_size, thread_name_prefix="SoundCombat"
            )

            self._is_active = True
            logger.info("SoundCombatContext initialized")

    def enter(self):
        if not self._is_active or not self._listener:
            return False

        try:
            self._listener.start()
            logger.info("SoundCombatContext entered - listener started")
            return True
        except Exception as e:
            logger.error(f"Failed to enter SoundCombatContext: {e}")
            return False

    def exit(self):
        if not self._is_active:
            return

        try:
            if self._listener:
                self._listener.stop()
            logger.info("SoundCombatContext exited - listener stopped")
        except Exception as e:
            logger.error(f"Error exiting SoundCombatContext: {e}")

    def execute_async(self, func, *args, **kwargs):
        if self._thread_pool and self._is_active:
            return self._thread_pool.submit(func, *args, **kwargs)
        logger.warning("Thread pool not available, executing synchronously")
        from concurrent.futures import Future

        future = Future()
        try:
            result = func(*args, **kwargs)
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)
        return future

    def _on_dodge_triggered(self):
        if self._trigger is None:
            logger.info("Dodge trigger ignored - trigger is None")
            return
        task = self._trigger.task
        if task and task.paused:
            logger.info("Dodge trigger ignored - task is paused")
            return
        logger.info("Dodge trigger callback invoked")
        if self._thread_pool is None:
            logger.error("Thread pool is None, cannot submit task")
            return
        try:
            logger.info(f"Submitting dodge to thread pool, pool: {self._thread_pool}")
            future = self._thread_pool.submit(self._trigger.execute_dodge)
            logger.info(f"Dodge task submitted, future: {future}")
        except Exception as e:
            logger.error(f"Failed to submit dodge task: {e}")

    def _on_counter_triggered(self):
        if self._trigger is None:
            logger.info("Counter trigger ignored - trigger is None")
            return
        task = self._trigger.task
        if task and task.paused:
            logger.info("Counter trigger ignored - task is paused")
            return
        logger.info("Counter trigger callback invoked")
        if self._thread_pool is None:
            logger.error("Thread pool is None, cannot submit task")
            return
        try:
            logger.info(f"Submitting counter to thread pool, pool: {self._thread_pool}")
            future = self._thread_pool.submit(self._trigger.execute_counter_attack)
            logger.info(f"Counter task submitted, future: {future}")
        except Exception as e:
            logger.error(f"Failed to submit counter task: {e}")

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def listener(self) -> Optional[SoundListener]:
        return self._listener

    @property
    def trigger(self) -> Optional[DodgeCounterTrigger]:
        return self._trigger

    @property
    def thread_pool(self) -> Optional[ThreadPoolExecutor]:
        return self._thread_pool

    def shutdown(self):
        with self._context_lock:
            if not self._is_active:
                return

            self.exit()

            if self._thread_pool:
                self._thread_pool.shutdown(wait=False, cancel_futures=True)
                self._thread_pool = None

            self._listener = None
            self._trigger = None
            self._is_active = False
            self._config = {}
            logger.info("SoundCombatContext shutdown complete")

    def __del__(self):
        if self._is_active:
            self.shutdown()
