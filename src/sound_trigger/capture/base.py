# ============================================================================
# Common base for audio capture sources.
#
# A source runs a producer thread that pushes mono float32 @48kHz chunks into a
# bounded queue. `start()` blocks briefly until capture is live or has failed.
# ============================================================================
from __future__ import annotations

import queue
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

import numpy as np
from ok import Logger

from src.utils import clock

logger = Logger.get_logger(__name__)

CAPTURE_SAMPLE_RATE = 48000
PushFn = Callable[[np.ndarray], None]


class AudioCaptureSource(ABC):
    sample_rate = CAPTURE_SAMPLE_RATE

    def __init__(self, queue_max: int = 4):
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=queue_max)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._failed = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def _produce(self, push: PushFn) -> None:
        """Capture loop: push mono float32 @48kHz chunks until stopped."""

    def start(self, ready_timeout: float = 5.0) -> bool:
        if self._thread is not None:
            return self._ready.is_set()
        self._thread = threading.Thread(
            target=self._run,
            name=f"AudioCapture-{self.name}",
            daemon=True,
        )
        self._thread.start()

        timer = clock.timer(timeout=ready_timeout)
        while not timer.expired():
            if self._ready.is_set():
                return True
            if self._failed.is_set():
                return False
            if self._stop.is_set():
                return self._ready.is_set()
            time.sleep(0.02)
        return self._ready.is_set()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def read(self, timeout: float = 0.5) -> Optional[np.ndarray]:
        try:
            latest = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

        while True:
            try:
                latest = self._queue.get_nowait()
            except queue.Empty:
                return latest

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> Optional[BaseException]:
        return self._error

    def _mark_ready(self) -> None:
        self._ready.set()

    def _push(self, chunk: np.ndarray) -> None:
        if chunk is None or len(chunk) == 0:
            return
        chunk = np.ascontiguousarray(chunk, dtype=np.float32)
        while True:
            try:
                self._queue.put_nowait(chunk)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass

    def _run(self) -> None:
        try:
            self._produce(self._push)
        except Exception as exc:
            self._error = exc
            logger.error(f"Audio capture source '{self.name}' failed: {exc}")
        finally:
            self._failed.set()
