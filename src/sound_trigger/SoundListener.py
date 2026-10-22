# ============================================================================
# This file is derived from the ZZZSoundTrigger project.
# Original Author: ImLaoBJie
# Repository: https://github.com/ImLaoBJie/ZZZSoundTrigger
# License: GNU General Public License v3.0 (GPL-3.0)
#
# This file has been modified for integration into the ok-nte project.
# ============================================================================
import threading
import warnings
from concurrent.futures import CancelledError
from typing import Optional, cast

import librosa
import numpy as np
from ok import Logger
from scipy.signal import butter, correlate, filtfilt

from src.sound_trigger.capture import MODE_PROCESS, AudioCaptureSource, create_capture_source
from src.sound_trigger.capture.base import CAPTURE_SAMPLE_RATE
from src.utils import clock
from src.utils.log_gate import LogGate

warnings.filterwarnings("ignore", message="data discontinuity in recording")

logger = Logger.get_logger(__name__)


class SoundListener:
    used_sr = CAPTURE_SAMPLE_RATE
    sample_len = 0.2
    detection_interval = 0.025
    log_interval = 20.0
    restart_interval = 1.0
    default_process_name = "HTGame.exe"

    degree = 4
    cut_off = 1000

    def __init__(
        self,
        sample_path: str,
        counter_attack_sample_path: str,
        threshold: float = 0.13,
        counter_attack_threshold: float = 0.12,
        expansion_ratio: float = 1.0,
        is_allow_successive_trigger: bool = False,
        process_name: str = default_process_name,
        initialization_stop_event: Optional[threading.Event] = None,
    ):
        self.sample_path = sample_path
        self.counter_attack_sample_path = counter_attack_sample_path
        self.threshold = threshold
        self.counter_attack_threshold = counter_attack_threshold
        self.expansion_ratio = expansion_ratio
        self.is_allow_successive_trigger = is_allow_successive_trigger
        self.process_name = process_name
        self._initialization_stop_event = initialization_stop_event

        self.is_computation_required = None
        self._running = False
        self._state_lock = threading.Lock()
        self._stop_event: Optional[threading.Event] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._last_trigger_time = clock.UNSET
        self._trigger_interval = 0.25

        self._sample_waveform = None
        self._counter_sample_waveform = None
        self._b = None
        self._a = None

        self.on_dodge_triggered = None
        self.on_counter_triggered = None
        self._capture: Optional[AudioCaptureSource] = None
        self._log_gate = LogGate(logger)

        self._load_samples()

    def _load_samples(self):
        try:
            self._check_initialization_stopped()
            self._b, self._a = cast(
                tuple[np.ndarray, np.ndarray],
                butter(
                    self.degree,
                    self.cut_off,
                    btype="highpass",
                    output="ba",
                    fs=self.used_sr,
                ),
            )

            self._sample_waveform = self._normalize_waveform(
                self._load_and_cache(self.sample_path)
            )
            if self.counter_attack_sample_path:
                self._counter_sample_waveform = self._normalize_waveform(
                    self._load_and_cache(self.counter_attack_sample_path)
                )

            self._check_initialization_stopped()
            logger.info(f"Sound samples loaded: {self.used_sr}Hz")
        except CancelledError:
            raise
        except Exception as e:
            # A dependency may finish importing only after shutdown has begun.
            # Treat that as cancellation instead of a broken sample file.
            self._check_initialization_stopped()
            message = (
                "Failed to load sound samples: "
                f"dodge={self.sample_path}, counter={self.counter_attack_sample_path}: {e}"
            )
            logger.error(message)
            raise RuntimeError(message) from e

    def _check_initialization_stopped(self):
        if self._initialization_stop_event is not None and self._initialization_stop_event.is_set():
            raise CancelledError("Sound initialization stopped")

    def _load_and_cache(self, path: str):
        import os

        self._check_initialization_stopped()
        cache_path = f"{path}_{self.used_sr}_{self.degree}_{self.cut_off}.npy"

        if os.path.exists(cache_path) and os.path.exists(path):
            if os.path.getmtime(cache_path) > os.path.getmtime(path):
                return np.load(cache_path)
        if not os.path.exists(path):
            raise FileNotFoundError(path)

        waveform, _ = librosa.load(path, sr=self.used_sr)
        self._check_initialization_stopped()
        waveform = self._filtering(waveform)
        self._check_initialization_stopped()
        np.save(cache_path, waveform)
        return waveform

    def _filtering(self, waveform):
        return filtfilt(self._b, self._a, waveform)

    @staticmethod
    def _normalize_waveform(waveform: np.ndarray) -> np.ndarray:
        std = np.std(waveform)
        if std == 0:
            return waveform
        return waveform / std

    def _prepare_stream_waveform(self, stream_waveform: np.ndarray) -> np.ndarray:
        return self._normalize_waveform(self._filtering(stream_waveform))

    def matching(self, stream_waveform: np.ndarray, sample_waveform: np.ndarray):
        norm_stream_waveform = self._prepare_stream_waveform(stream_waveform)
        return self._match_normalized(norm_stream_waveform, sample_waveform)

    def _match_normalized(self, norm_stream_waveform: np.ndarray, norm_sample_waveform: np.ndarray):
        if norm_stream_waveform.shape[0] > norm_sample_waveform.shape[0]:
            correlation = (
                correlate(norm_stream_waveform, norm_sample_waveform, mode="same", method="fft")
                / norm_stream_waveform.shape[0]
            )
        else:
            correlation = (
                correlate(norm_sample_waveform, norm_stream_waveform, mode="same", method="fft")
                / norm_sample_waveform.shape[0]
            )

        max_corr = np.max(correlation) * self.expansion_ratio

        return max_corr

    def start(self):
        with self._state_lock:
            if self._running:
                logger.warning("SoundListener already running")
                return True

            self._running = True
            self._stop_event = threading.Event()
            self._listener_thread = threading.Thread(
                target=self._listen_loop,
                args=(self._stop_event,),
                daemon=True,
            )
            try:
                self._listener_thread.start()
            except Exception as error:
                self._running = False
                self._stop_event = None
                self._listener_thread = None
                logger.error(f"Failed to start SoundListener thread: {error}")
                return False

        logger.info("SoundListener started successfully")
        return True

    def stop(self):
        logger.info(f"SoundListener stop called, current running: {self._running}")
        with self._state_lock:
            self._running = False
            stop_event = self._stop_event
            capture = self._capture
            listener_thread = self._listener_thread

        if stop_event:
            stop_event.set()
        if capture:
            capture.stop()
        if listener_thread and listener_thread is not threading.current_thread():
            listener_thread.join(timeout=2.0)
        logger.info("SoundListener stopped")

    @property
    def is_running(self):
        stop_event = self._stop_event
        return (
            self._running
            and (stop_event is None or not stop_event.is_set())
            and self._listener_thread is not None
            and self._listener_thread.is_alive()
        )

    def _listen_loop(self, stop_event: threading.Event):
        try:
            while self._should_run(stop_event):
                try:
                    self._listen_once(stop_event)
                except Exception as e:
                    logger.error("Listener error", e)
                finally:
                    self._stop_capture()

                if self._should_run(stop_event):
                    logger.warning(
                        f"Audio listener loop stopped unexpectedly; restarting in "
                        f"{self.restart_interval:.2f}s"
                    )
                    stop_event.wait(self.restart_interval)
        finally:
            with self._state_lock:
                if self._stop_event is stop_event:
                    self._running = False
            logger.info("Audio listener stopped")

    def _should_run(self, stop_event: threading.Event) -> bool:
        return self._running and not stop_event.is_set()

    def _stop_capture(self):
        if self._capture is None:
            return
        try:
            self._capture.stop()
        except Exception as e:
            logger.warning(f"Failed to stop WASAPI process capture: {e}")
        finally:
            self._capture = None

    def _listen_once(self, stop_event: threading.Event):
        logger.info(f"Initializing WASAPI process audio capture for {self.process_name}...")

        max_samples = int(self.used_sr * self.sample_len)
        samples_per_check = max(1, int(self.used_sr * self.detection_interval))
        ring_buffer = np.zeros(max_samples * 2, dtype=np.float64)
        buffer_pos = 0
        total_written = 0
        samples_since_check = 0

        while self._should_run(stop_event):
            if self._capture is None or not self._capture.is_alive():
                if self._capture is not None:
                    logger.warning(
                        f"WASAPI process capture stopped; restarting: {self._capture.error}"
                    )
                    self._capture.stop()
                self._capture = create_capture_source(
                    MODE_PROCESS,
                    process_name=self.process_name,
                )
                if not self._capture.start():
                    logger.warning(
                        "WASAPI process capture not ready for {}: {}".format(
                            self.process_name,
                            self._capture.error,
                        )
                    )
                    self._capture.stop()
                    self._capture = None
                    stop_event.wait(1.0)
                    continue
                logger.info(f"Using audio capture source: {self._capture.name}")

            current_frame = self._capture.read(timeout=0.2)
            if current_frame is None or current_frame.size == 0:
                continue

            if current_frame.shape[0] >= ring_buffer.shape[0]:
                current_frame = current_frame[-ring_buffer.shape[0] :]

            end_pos = buffer_pos + current_frame.shape[0]
            if end_pos <= ring_buffer.shape[0]:
                ring_buffer[buffer_pos:end_pos] = current_frame
            else:
                first_part = ring_buffer.shape[0] - buffer_pos
                ring_buffer[buffer_pos:] = current_frame[:first_part]
                ring_buffer[: end_pos - ring_buffer.shape[0]] = current_frame[first_part:]

            buffer_pos = end_pos % ring_buffer.shape[0]
            total_written += current_frame.shape[0]
            samples_since_check += current_frame.shape[0]

            if total_written < max_samples or samples_since_check < samples_per_check:
                continue

            samples_since_check = 0
            if buffer_pos >= max_samples:
                window = ring_buffer[buffer_pos - max_samples : buffer_pos]
            else:
                window = np.concatenate(
                    [
                        ring_buffer[-(max_samples - buffer_pos) :],
                        ring_buffer[:buffer_pos],
                    ]
                )

            if self.is_computation_required and not self.is_computation_required():
                continue

            norm_window = self._prepare_stream_waveform(window)
            dodge_score = self._match_normalized(norm_window, self._sample_waveform)
            counter_score = 0.0
            if self._counter_sample_waveform is not None:
                counter_score = self._match_normalized(
                    norm_window,
                    self._counter_sample_waveform,
                )

            self._check_triggers(dodge_score, counter_score)

            # self._draw_debug_visual(dodge_score, counter_score)

            self._log_gate.info(
                "Audio monitoring - dodge_score: {:.4f} (threshold: {}), "
                "counter_score: {:.4f} (threshold: {})".format(
                    dodge_score,
                    self.threshold,
                    counter_score,
                    self.counter_attack_threshold,
                ),
                interval=self.log_interval,
                key="audio_monitoring",
            )

    def _check_triggers(self, dodge_score, counter_score):
        now = clock.now()
        if (
            not self.is_allow_successive_trigger
            and now - self._last_trigger_time < self._trigger_interval
        ):
            return

        if dodge_score > 0 and dodge_score > self.threshold:
            if self.on_dodge_triggered:
                logger.info(
                    "Dodge TRIGGERED! score: {:.4f}, threshold: {}".format(
                        dodge_score,
                        self.threshold,
                    )
                )
                self.on_dodge_triggered()
                self._last_trigger_time = now
                return

        if counter_score > 0 and counter_score > self.counter_attack_threshold:
            if self.on_counter_triggered:
                logger.info(
                    "Counter attack TRIGGERED! score: {:.4f}, threshold: {}".format(
                        counter_score,
                        self.counter_attack_threshold,
                    )
                )
                self.on_counter_triggered()
                self._last_trigger_time = now

    def _draw_debug_visual(self, dodge_score, counter_score):
        if not hasattr(self, "_visual_queue"):
            import queue

            self._visual_queue = queue.Queue(maxsize=1)
            self._mouse_x = -1

            def on_mouse(event, x, y, flags, param):
                if event == 0:  # cv2.EVENT_MOUSEMOVE
                    self._mouse_x = x

            def visual_worker():
                logger.info("Debug visual thread started")
                import cv2

                window_name = "Sound Listener Debug Wave"
                cv2.namedWindow(window_name)
                cv2.setMouseCallback(window_name, on_mouse)

                while self._running:
                    try:
                        # Timeout should be similar to detection_interval
                        d, c = self._visual_queue.get(timeout=0.1)
                        self._last_received_d, self._last_received_c = d, c
                        self._do_draw_debug_visual(d, c, update_history=True)
                    except Exception:
                        if self._running:
                            # If no data, redraw last state without updating history
                            last_d = getattr(self, "_last_received_d", 0.0)
                            last_c = getattr(self, "_last_received_c", 0.0)
                            self._do_draw_debug_visual(last_d, last_c, update_history=False)
                        continue
                try:
                    cv2.destroyAllWindows()
                except Exception:
                    pass
                logger.info("Debug visual thread stopped")

            threading.Thread(target=visual_worker, daemon=True).start()

        self._last_d, self._last_c = dodge_score, counter_score

        try:
            # Use put_nowait to ensure we never block the audio loop
            # If the visual thread is slow, we just skip frames
            self._visual_queue.put_nowait((dodge_score, counter_score))
        except Exception:
            pass

    def _do_draw_debug_visual(self, dodge_score, counter_score, update_history=True):
        try:
            import cv2
            import numpy as np
        except ImportError:
            return

        if not hasattr(self, "_debug_history"):
            self._debug_history = {"dodge": [], "counter": []}
            self._max_history = 300
            self._debug_history["dodge"] = [0.0] * self._max_history
            self._debug_history["counter"] = [0.0] * self._max_history

        # Update history only if new data arrived
        if update_history:
            self._debug_history["dodge"].append(dodge_score)
            self._debug_history["counter"].append(counter_score)
            if len(self._debug_history["dodge"]) > self._max_history:
                self._debug_history["dodge"].pop(0)
                self._debug_history["counter"].pop(0)

        # Canvas settings
        width, height = 800, 400
        canvas = np.zeros((height, width, 3), dtype=np.uint8)

        # Draw grid and background
        canvas[:] = (20, 20, 20)
        for i in range(1, 10):
            y = int(height * i / 10)
            cv2.line(canvas, (0, y), (width, y), (40, 40, 40), 1)
        for i in range(1, 20):
            x = int(width * i / 20)
            cv2.line(canvas, (x, 0), (x, height), (30, 30, 30), 1)

        # Scale function
        max_val = max(0.5, self.threshold * 1.5, self.counter_attack_threshold * 1.5)

        def get_y(val):
            return int(height - (val / max_val) * height * 0.8) - 20

        # Draw thresholds
        d_y = get_y(self.threshold)
        c_y = get_y(self.counter_attack_threshold)
        cv2.line(canvas, (0, d_y), (width, d_y), (50, 50, 180), 1, cv2.LINE_AA)
        cv2.line(canvas, (0, c_y), (width, c_y), (50, 180, 50), 1, cv2.LINE_AA)

        # Draw waves
        points_d = []
        points_c = []
        for i in range(self._max_history):
            x = int(i * (width / (self._max_history - 1)))
            points_d.append([x, get_y(self._debug_history["dodge"][i])])
            points_c.append([x, get_y(self._debug_history["counter"][i])])

        cv2.polylines(
            canvas, [np.array(points_d, np.int32)], False, (255, 100, 100), 2, cv2.LINE_AA
        )
        cv2.polylines(
            canvas, [np.array(points_c, np.int32)], False, (100, 255, 100), 2, cv2.LINE_AA
        )

        # Mouse interaction: Timeline and Values
        if hasattr(self, "_mouse_x") and 0 <= self._mouse_x < width:
            mx = self._mouse_x
            idx = int(mx * (self._max_history - 1) / width)
            if 0 <= idx < self._max_history:
                d_val = self._debug_history["dodge"][idx]
                c_val = self._debug_history["counter"][idx]

                # Draw vertical timeline line
                cv2.line(canvas, (mx, 0), (mx, height), (150, 150, 150), 1, cv2.LINE_AA)

                # Display detailed values at cursor
                info_text = f"T-{self._max_history - idx} | D: {d_val:.4f} | C: {c_val:.4f}"
                cv2.putText(
                    canvas,
                    info_text,
                    (min(mx + 10, width - 250), 100),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (220, 220, 220),
                    1,
                    cv2.LINE_AA,
                )

        # Text labels
        cv2.putText(
            canvas,
            f"Dodge: {dodge_score:.3f} (T: {self.threshold:.3f})",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 100, 100),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            f"Counter: {counter_score:.3f} (T: {self.counter_attack_threshold:.3f})",
            (10, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (100, 255, 100),
            2,
            cv2.LINE_AA,
        )

        cv2.imshow("Sound Listener Debug Wave", canvas)
        cv2.waitKey(1)
