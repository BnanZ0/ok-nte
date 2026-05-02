import threading
import time
from typing import Optional

import librosa
import numpy as np
import soundcard as sc
from ok import Logger
from scipy.signal import butter, correlate, filtfilt
from sklearn.preprocessing import scale

logger = Logger.get_logger(__name__)


class SoundListener:
    used_sr = 32000
    used_channel = 2
    chunk_size = 1600
    sample_len = 0.2
    detection_interval = 0.1
    log_interval = 20

    degree = 4
    cut_off = 1000

    def __init__(
        self,
        sample_path: str = "./闪避波形.wav",
        counter_attack_sample_path: str = "./承轨反击波形.wav",
        threshold: float = 0.13,
        counter_attack_threshold: float = 0.12,
        expansion_ratio: float = 1.0,
        is_allow_successive_trigger: bool = False,
    ):
        self.sample_path = sample_path
        self.counter_attack_sample_path = counter_attack_sample_path
        self.threshold = threshold
        self.counter_attack_threshold = counter_attack_threshold
        self.expansion_ratio = expansion_ratio
        self.is_allow_successive_trigger = is_allow_successive_trigger

        self._running = False
        self._listener_thread: Optional[threading.Thread] = None
        self._last_trigger_time = 0.0
        self._trigger_interval = 0.5

        self._sample_waveform = None
        self._counter_sample_waveform = None
        self._b = None
        self._a = None

        self.on_dodge_triggered = None
        self.on_counter_triggered = None

        self._load_samples()

    def _load_samples(self):
        try:
            self._sample_waveform, sample_rate = librosa.load(self.sample_path)
            self._sample_waveform = librosa.resample(self._sample_waveform, orig_sr=sample_rate, target_sr=self.used_sr)

            self._b, self._a = butter(self.degree, self.cut_off, btype='highpass', output='ba', fs=self.used_sr)
            self._sample_waveform = self._filtering(self._sample_waveform)

            if self.counter_attack_sample_path:
                self._counter_sample_waveform, counter_rate = librosa.load(self.counter_attack_sample_path)
                self._counter_sample_waveform = librosa.resample(self._counter_sample_waveform, orig_sr=counter_rate, target_sr=self.used_sr)
                self._counter_sample_waveform = self._filtering(self._counter_sample_waveform)

            logger.info(f"Sound samples loaded: {self.used_sr}Hz")
        except Exception as e:
            logger.error(f"Failed to load sound samples: {e}")

    def _filtering(self, waveform):
        return filtfilt(self._b, self._a, waveform)

    def matching(self, stream_waveform: np.ndarray, sample_waveform: np.ndarray):
        stream_waveform = self._filtering(stream_waveform)

        norm_stream_waveform = scale(stream_waveform, with_mean=False)
        norm_sample_waveform = scale(sample_waveform, with_mean=False)

        if norm_stream_waveform.shape[0] > norm_sample_waveform.shape[0]:
            correlation = correlate(norm_stream_waveform, norm_sample_waveform, mode='same', method='fft') / norm_stream_waveform.shape[0]
        else:
            correlation = correlate(norm_sample_waveform, norm_stream_waveform, mode='same', method='fft') / norm_sample_waveform.shape[0]

        max_corr = np.max(correlation) * self.expansion_ratio

        return max_corr

    def start(self):
        if self._running:
            logger.warning("SoundListener already running")
            return
        self._running = True
        self._listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listener_thread.start()
        logger.info("SoundListener started successfully")

    def stop(self):
        logger.info(f"SoundListener stop called, current running: {self._running}")
        self._running = False
        if self._listener_thread:
            self._listener_thread.join(timeout=2.0)
        logger.info("SoundListener stopped")

    def _listen_loop(self):
        try:
            logger.info("Initializing audio loopback device...")
            
            default_speaker = sc.default_speaker()
            logger.info(f"Default speaker: {default_speaker.name}")
            
            loopback = sc.get_microphone(id=str(default_speaker.name), include_loopback=True)
            logger.info(f"Using loopback device: {loopback.name}")

            audio_instance = loopback.recorder(samplerate=self.used_sr, channels=self.used_channel)

            check_count = 0
            with audio_instance as audio_recorder:
                logger.info("Audio recording started, monitoring for triggers...")
                
                max_samples = int(self.used_sr * self.sample_len)
                chunks_per_interval = int(self.used_sr * self.detection_interval / self.chunk_size)
                new_samples_per_interval = chunks_per_interval * self.chunk_size

                ring_buffer = np.zeros(max_samples * 2, dtype=np.float64)
                buffer_pos = 0
                total_written = 0

                while self._running:
                    current_frame = np.empty(new_samples_per_interval, dtype=np.float64)
                    idx = 0
                    for _ in range(chunks_per_interval):
                        stream_data = audio_recorder.record(numframes=self.chunk_size)
                        read_chunks = librosa.to_mono(stream_data.T)
                        current_frame[idx:idx + self.chunk_size] = read_chunks
                        idx += self.chunk_size

                    end_pos = buffer_pos + new_samples_per_interval
                    if end_pos <= max_samples * 2:
                        ring_buffer[buffer_pos:end_pos] = current_frame
                    else:
                        first_part = max_samples * 2 - buffer_pos
                        ring_buffer[buffer_pos:] = current_frame[:first_part]
                        ring_buffer[:end_pos - max_samples * 2] = current_frame[first_part:]

                    buffer_pos = end_pos % (max_samples * 2)
                    total_written += new_samples_per_interval

                    if total_written >= max_samples:
                        if buffer_pos >= max_samples:
                            window = ring_buffer[buffer_pos - max_samples:buffer_pos]
                        else:
                            window = np.concatenate([
                                ring_buffer[-(max_samples - buffer_pos):],
                                ring_buffer[:buffer_pos]
                            ])

                        dodge_score = self.matching(window, self._sample_waveform)
                        counter_score = 0.0
                        if self._counter_sample_waveform is not None:
                            counter_score = self.matching(window, self._counter_sample_waveform)

                        self._check_triggers(dodge_score, counter_score)

                        check_count += 1
                        if check_count % self.log_interval == 0:
                            logger.info(f"Audio monitoring - dodge_score: {dodge_score:.4f} (threshold: {self.threshold}), counter_score: {counter_score:.4f} (threshold: {self.counter_attack_threshold})")
        except Exception as e:
            logger.error(f"Listener error: {e}", exc_info=True)
        finally:
            self._running = False
            logger.info("Audio listener stopped")

    def _check_triggers(self, dodge_score, counter_score):
        now = time.time()
        if not self.is_allow_successive_trigger and now - self._last_trigger_time < self._trigger_interval:
            return

        if dodge_score > 0 and dodge_score > self.threshold:
            if self.on_dodge_triggered:
                logger.info(f"Dodge TRIGGERED! score: {dodge_score:.4f}, threshold: {self.threshold}")
                self.on_dodge_triggered()
                self._last_trigger_time = now
                return

        if counter_score > 0 and counter_score > self.counter_attack_threshold:
            if self.on_counter_triggered:
                logger.info(f"Counter attack TRIGGERED! score: {counter_score:.4f}, threshold: {self.counter_attack_threshold}")
                self.on_counter_triggered()
                self._last_trigger_time = now
