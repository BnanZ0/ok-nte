# ============================================================================
# This file is derived from the ZZZSoundTrigger project.
# Original Author: ImLaoBJie
# Repository: https://github.com/ImLaoBJie/ZZZSoundTrigger
# License: GNU General Public License v3.0 (GPL-3.0)
#
# This file has been modified for integration into the ok-nte project.
# ============================================================================
import threading
import time
from typing import Callable, Optional

from ok import Logger

from src.utils import clock

logger = Logger.get_logger(__name__)


class DodgeCounterTrigger:
    clock = clock

    def __init__(
        self,
        task,
        dodge_action: Optional[Callable] = None,
        counter_action: Optional[Callable] = None,
    ):
        self.task = task
        self.set_actions(dodge_action, counter_action)

        self._is_executing = False
        self._execute_lock = threading.Lock()
        self._last_dodge_time = self.clock.UNSET
        self._last_counter_time = self.clock.UNSET
        self._min_dodge_interval = 0.3
        self._min_counter_interval = 1.0

    def set_actions(
        self,
        dodge_action: Optional[Callable] = None,
        counter_action: Optional[Callable] = None,
    ):
        self.dodge_action = dodge_action or self._default_dodge_action
        self.counter_action = counter_action or self._default_counter_action

    def execute_dodge(self):
        now = self.clock.now()
        if now - self._last_dodge_time < self._min_dodge_interval:
            logger.debug(f"Dodge skipped, too soon: {now - self._last_dodge_time:.3f}s")
            return

        with self._execute_lock:
            if self._is_executing:
                return
            self._is_executing = True

        try:
            logger.info("Executing dodge")
            self.dodge_action()
            self._last_dodge_time = now
            logger.info("Dodge executed successfully")
        except Exception as e:
            logger.error("Dodge execution error", e)
        finally:
            self._is_executing = False

    def execute_counter_attack(self):
        now = self.clock.now()
        if now - self._last_counter_time < self._min_counter_interval:
            return

        with self._execute_lock:
            if self._is_executing:
                return
            self._is_executing = True

        try:
            logger.info("Executing counter attack")
            self.counter_action()
            self._last_counter_time = now
            logger.info("Counter attack executed successfully")
        except Exception as e:
            logger.error("Counter execution error", e)
        finally:
            self._is_executing = False

    def _default_dodge_action(self):
        logger.info("Dodge sequence: Left Shift")
        try:
            self.task.send_key_down("d")
            time.sleep(0.02)
            self.task.send_key("lshift")
            time.sleep(0.02)
        finally:
            self.task.send_key_up("d")
            time.sleep(0.02)
        self.task.send_key("lshift")
        time.sleep(0.02)

    def _default_counter_action(self):
        logger.info("Counter attack sequence: Left mouse")
        self.task.click()
        time.sleep(0.02)
