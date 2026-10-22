# ============================================================================
# WASAPI per-process loopback capture.
#
# Captures only the target process tree using Windows'
# ActivateAudioInterfaceAsync + AUDIOCLIENT_ACTIVATION_PARAMS process loopback,
# then delivers mono float32 chunks in memory. No WAV files are created.
# ============================================================================
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Optional

import comtypes
import numpy as np
from comtypes import COMMETHOD, GUID, COMObject, IUnknown
from comtypes.hresult import S_OK
from ok import Logger

from src.sound_trigger.capture.base import CAPTURE_SAMPLE_RATE, AudioCaptureSource, PushFn
from src.sound_trigger.capture.process_resolver import (
    name_set,
    process_is_alive,
    resolve_target_pid,
)
from src.utils import clock
from src.utils.log_gate import LogGate

logger = Logger.get_logger(__name__)

CAPTURE_CHANNELS = 2
# Windows process loopback is documented for Windows 10 Build 20348+, but it is
# available on many 20H1/19041 systems in practice. Keep the broader gate and let
# activation fail with a searchable HRESULT on machines where the API is absent.
# https://learn.microsoft.com/windows/win32/api/audioclientactivationparams/ns-audioclientactivationparams-audioclient_activation_params
MIN_PROCESS_LOOPBACK_BUILD = 19041
PROCESS_WAIT_INTERVAL = 0.5
PROCESS_CHECK_INTERVAL = 1.0
MAX_ACTIVATION_FAILURES = 5

# Windows SDK: audioclientactivationparams.h
# https://learn.microsoft.com/windows/win32/api/audioclientactivationparams/ne-audioclientactivationparams-audioclient_activation_type
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
# Windows SDK: PROCESS_LOOPBACK_MODE. The enum values are declared in order, so
# INCLUDE_TARGET_PROCESS_TREE is 0 and EXCLUDE_TARGET_PROCESS_TREE is 1.
# https://learn.microsoft.com/windows/win32/api/audioclientactivationparams/ne-audioclientactivationparams-process_loopback_mode
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1

# Windows PROPVARIANT VT_BLOB, used by ActivateAudioInterfaceAsync to receive an
# AUDIOCLIENT_ACTIVATION_PARAMS blob.
VT_BLOB = 65
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"

AUDCLNT_SHAREMODE_SHARED = 0
# Windows SDK: Audiosessiontypes.h AUDCLNT_STREAMFLAGS_XXX constants.
# LOOPBACK opens a capture buffer for rendered audio, EVENTCALLBACK lets WASAPI
# signal us when packets are ready, and AUTOCONVERTPCM lets the audio engine
# convert from the device mix format to our requested PCM format.
# https://learn.microsoft.com/windows/win32/coreaudio/audclnt-streamflags-xxx-constants
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
INIT_STREAM_FLAGS = (
    AUDCLNT_STREAMFLAGS_LOOPBACK
    | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
    | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
)

AUDCLNT_BUFFERFLAGS_SILENT = 0x2

# Windows SDK: mmreg.h wave format tags.
# https://learn.microsoft.com/windows/win32/api/mmreg/ns-mmreg-waveformatex
WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

# REFERENCE_TIME is in 100 ns units. 50,000 = 5 ms, 200,000 = 20 ms.
LOW_LATENCY_BUFFER_HNS = 50_000
FALLBACK_BUFFER_HNS = 200_000

REFERENCE_TIME = ctypes.c_longlong


# Windows SDK: AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS.
# https://learn.microsoft.com/windows/win32/api/audioclientactivationparams/ns-audioclientactivationparams-audioclient_process_loopback_params
class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [
        ("TargetProcessId", wintypes.DWORD),
        ("ProcessLoopbackMode", ctypes.c_int),
    ]


class _APUnion(ctypes.Union):
    _fields_ = [("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]


# Windows SDK: AUDIOCLIENT_ACTIVATION_PARAMS.
# The C struct contains an activation type plus a union. Currently the union only
# carries process-loopback params for our use case.
# https://learn.microsoft.com/windows/win32/api/audioclientactivationparams/ns-audioclientactivationparams-audioclient_activation_params
class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("ActivationType", ctypes.c_int),
        ("u", _APUnion),
    ]


class _BLOB(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", ctypes.c_void_p)]


class PROPVARIANT_BLOB(ctypes.Structure):
    _fields_ = [
        ("vt", wintypes.USHORT),
        ("wReserved1", wintypes.USHORT),
        ("wReserved2", wintypes.USHORT),
        ("wReserved3", wintypes.USHORT),
        ("blob", _BLOB),
    ]


# Windows SDK: WAVEFORMATEX. _pack_=1 matches the 18-byte C layout.
# https://learn.microsoft.com/windows/win32/api/mmreg/ns-mmreg-waveformatex
class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


# Windows SDK: WAVEFORMATEXTENSIBLE. Used first because it precisely describes
# stereo float32 with an explicit channel mask.
# https://learn.microsoft.com/windows/win32/api/mmreg/ns-mmreg-waveformatextensible
class WAVEFORMATEXTENSIBLE(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("Format", WAVEFORMATEX),
        ("wValidBitsPerSample", wintypes.WORD),
        ("dwChannelMask", wintypes.DWORD),
        ("SubFormat", GUID),
    ]


KSDATAFORMAT_SUBTYPE_IEEE_FLOAT = GUID("{00000003-0000-0010-8000-00AA00389B71}")
# SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT.
SPEAKER_FRONT_LEFT_RIGHT = 0x3


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _methods_ = (
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "GetBuffer",
            (["out"], ctypes.POINTER(ctypes.c_void_p), "ppData"),
            (["out"], ctypes.POINTER(wintypes.UINT), "pNumFramesToRead"),
            (["out"], ctypes.POINTER(wintypes.DWORD), "pdwFlags"),
            (["out"], ctypes.POINTER(ctypes.c_ulonglong), "pu64DevicePosition"),
            (["out"], ctypes.POINTER(ctypes.c_ulonglong), "pu64QPCPosition"),
        ),
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "ReleaseBuffer",
            (["in"], wintypes.UINT, "NumFramesRead"),
        ),
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "GetNextPacketSize",
            (["out"], ctypes.POINTER(wintypes.UINT), "pNumFramesInNextPacket"),
        ),
    )


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
    _methods_ = (
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "Initialize",
            (["in"], ctypes.c_int, "ShareMode"),
            (["in"], wintypes.DWORD, "StreamFlags"),
            (["in"], REFERENCE_TIME, "hnsBufferDuration"),
            (["in"], REFERENCE_TIME, "hnsPeriodicity"),
            (["in"], ctypes.POINTER(WAVEFORMATEX), "pFormat"),
            (["in"], ctypes.POINTER(GUID), "AudioSessionGuid"),
        ),
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "GetBufferSize",
            (["out"], ctypes.POINTER(wintypes.UINT), "pNumBufferFrames"),
        ),
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "GetStreamLatency",
            (["out"], ctypes.POINTER(REFERENCE_TIME), "phnsLatency"),
        ),
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "GetCurrentPadding",
            (["out"], ctypes.POINTER(wintypes.UINT), "pNumPaddingFrames"),
        ),
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "IsFormatSupported",
            (["in"], ctypes.c_int, "ShareMode"),
            (["in"], ctypes.POINTER(WAVEFORMATEX), "pFormat"),
            (["out"], ctypes.POINTER(ctypes.POINTER(WAVEFORMATEX)), "ppClosestMatch"),
        ),
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "GetMixFormat",
            (["out"], ctypes.POINTER(ctypes.POINTER(WAVEFORMATEX)), "ppDeviceFormat"),
        ),
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "GetDevicePeriod",
            (["out"], ctypes.POINTER(REFERENCE_TIME), "phnsDefaultDevicePeriod"),
            (["out"], ctypes.POINTER(REFERENCE_TIME), "phnsMinimumDevicePeriod"),
        ),
        COMMETHOD([], comtypes.HRESULT, "Start"),
        COMMETHOD([], comtypes.HRESULT, "Stop"),
        COMMETHOD([], comtypes.HRESULT, "Reset"),
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "SetEventHandle",
            (["in"], wintypes.HANDLE, "eventHandle"),
        ),
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "GetService",
            (["in"], ctypes.POINTER(GUID), "riid"),
            (["out"], ctypes.POINTER(ctypes.POINTER(IAudioCaptureClient)), "ppv"),
        ),
    )


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = (
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "GetActivateResult",
            (["out"], ctypes.POINTER(ctypes.c_int), "activateResult"),
            (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "activatedInterface"),
        ),
    )


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = (
        COMMETHOD(
            [],
            comtypes.HRESULT,
            "ActivateCompleted",
            (
                ["in"],
                ctypes.POINTER(IActivateAudioInterfaceAsyncOperation),
                "activateOperation",
            ),
        ),
    )


class IAgileObject(IUnknown):
    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_ = ()


_mmdevapi = ctypes.WinDLL("mmdevapi.dll")
_ActivateAudioInterfaceAsync = _mmdevapi.ActivateAudioInterfaceAsync
_ActivateAudioInterfaceAsync.restype = ctypes.c_long
_ActivateAudioInterfaceAsync.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(GUID),
    ctypes.c_void_p,
    ctypes.POINTER(IActivateAudioInterfaceCompletionHandler),
    ctypes.POINTER(ctypes.POINTER(IActivateAudioInterfaceAsyncOperation)),
]

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_CreateEventW = _kernel32.CreateEventW
_CreateEventW.restype = wintypes.HANDLE
_CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_WaitForSingleObject = _kernel32.WaitForSingleObject
_WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_WaitForSingleObject.restype = wintypes.DWORD
_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]


def _hresult_hex(value: int) -> str:
    return f"0x{int(value) & 0xFFFFFFFF:08X}"


def _exception_hresult(exc: BaseException) -> Optional[int]:
    value = getattr(exc, "hresult", None)
    if isinstance(value, int):
        return value
    if exc.args and isinstance(exc.args[0], int):
        return exc.args[0]
    return None


def _describe_exception(exc: BaseException) -> str:
    hresult = _exception_hresult(exc)
    if hresult is None:
        return f"{type(exc).__name__}: {exc}"
    return f"{type(exc).__name__} HRESULT={_hresult_hex(hresult)}: {exc}"


class _ActivateHandler(COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self):
        super().__init__()
        import threading

        self.completed = threading.Event()
        self.activate_hr: Optional[int] = None
        self.audio_client = None

    def IActivateAudioInterfaceCompletionHandler_ActivateCompleted(self, this, operation):
        try:
            hr, unk = operation.GetActivateResult()
            self.activate_hr = int(hr)
            if hr >= 0 and unk:
                self.audio_client = unk.QueryInterface(IAudioClient)
        except Exception as exc:
            self.activate_hr = getattr(exc, "hresult", -1) or -1
        finally:
            self.completed.set()
        return S_OK


def _activate_audio_client(pid: int, include_tree: bool, timeout_s: float = 5.0):
    params = AUDIOCLIENT_ACTIVATION_PARAMS()
    params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
    params.ProcessLoopbackParams.TargetProcessId = pid
    params.ProcessLoopbackParams.ProcessLoopbackMode = (
        PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
        if include_tree
        else PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE
    )

    propvar = PROPVARIANT_BLOB()
    propvar.vt = VT_BLOB
    propvar.blob.cbSize = ctypes.sizeof(params)
    propvar.blob.pBlobData = ctypes.cast(ctypes.byref(params), ctypes.c_void_p)

    handler = _ActivateHandler()
    operation = ctypes.POINTER(IActivateAudioInterfaceAsyncOperation)()
    iid_audioclient = IAudioClient._iid_

    hr = _ActivateAudioInterfaceAsync(
        VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        ctypes.byref(iid_audioclient),
        ctypes.byref(propvar),
        handler,
        ctypes.byref(operation),
    )
    if hr < 0:
        raise OSError(f"ActivateAudioInterfaceAsync failed: {_hresult_hex(hr)}")

    if not handler.completed.wait(timeout_s):
        raise TimeoutError("ActivateCompleted did not fire within timeout")
    if handler.activate_hr is None or handler.activate_hr < 0:
        code = handler.activate_hr or 0
        raise OSError(f"process-loopback activation failed: {_hresult_hex(code)}")
    if handler.audio_client is None:
        raise OSError("activation returned no IAudioClient")
    _ = params
    return handler.audio_client


def _make_float_extensible_format() -> WAVEFORMATEXTENSIBLE:
    wfx = WAVEFORMATEXTENSIBLE()
    fmt = wfx.Format
    fmt.wFormatTag = WAVE_FORMAT_EXTENSIBLE
    fmt.nChannels = CAPTURE_CHANNELS
    fmt.nSamplesPerSec = CAPTURE_SAMPLE_RATE
    fmt.wBitsPerSample = 32
    fmt.nBlockAlign = fmt.nChannels * fmt.wBitsPerSample // 8
    fmt.nAvgBytesPerSec = fmt.nSamplesPerSec * fmt.nBlockAlign
    fmt.cbSize = 22
    wfx.wValidBitsPerSample = 32
    wfx.dwChannelMask = SPEAKER_FRONT_LEFT_RIGHT
    wfx.SubFormat = KSDATAFORMAT_SUBTYPE_IEEE_FLOAT
    return wfx


def _make_float_format() -> WAVEFORMATEX:
    fmt = WAVEFORMATEX()
    fmt.wFormatTag = WAVE_FORMAT_IEEE_FLOAT
    fmt.nChannels = CAPTURE_CHANNELS
    fmt.nSamplesPerSec = CAPTURE_SAMPLE_RATE
    fmt.wBitsPerSample = 32
    fmt.nBlockAlign = fmt.nChannels * fmt.wBitsPerSample // 8
    fmt.nAvgBytesPerSec = fmt.nSamplesPerSec * fmt.nBlockAlign
    fmt.cbSize = 0
    return fmt


def _make_pcm16_format() -> WAVEFORMATEX:
    fmt = WAVEFORMATEX()
    fmt.wFormatTag = WAVE_FORMAT_PCM
    fmt.nChannels = CAPTURE_CHANNELS
    fmt.nSamplesPerSec = CAPTURE_SAMPLE_RATE
    fmt.wBitsPerSample = 16
    fmt.nBlockAlign = fmt.nChannels * fmt.wBitsPerSample // 8
    fmt.nAvgBytesPerSec = fmt.nSamplesPerSec * fmt.nBlockAlign
    fmt.cbSize = 0
    return fmt


_FORMAT_TIERS = (
    (_make_float_extensible_format, True, 32),
    (_make_float_format, True, 32),
    (_make_pcm16_format, False, 16),
)


def _to_mono(raw: bytes, is_float: bool) -> np.ndarray:
    if is_float:
        inter = np.frombuffer(raw, dtype=np.float32)
    else:
        inter = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if inter.size == 0:
        return np.zeros(0, dtype=np.float32)
    mono = (inter[0::CAPTURE_CHANNELS] + inter[1::CAPTURE_CHANNELS]) * np.float32(0.5)
    np.clip(mono, -1.0, 1.0, out=mono)
    return np.ascontiguousarray(mono, dtype=np.float32)


class ProcessLoopbackSource(AudioCaptureSource):
    used_sr = CAPTURE_SAMPLE_RATE
    used_channel = CAPTURE_CHANNELS

    def __init__(self, process_name, include_process_tree: bool = True):
        super().__init__()
        self.process_name = process_name
        self._display_name = ",".join(sorted(name_set(process_name))) or "?"
        self.include_process_tree = include_process_tree
        self._stream_started_once = False
        self._log_gate = LogGate(logger)

    @property
    def name(self) -> str:
        return f"process-loopback({self._display_name})"

    def _produce(self, push: PushFn) -> None:
        if not _capability_available():
            raise RuntimeError("WASAPI per-process loopback is not available on this system")

        # Only enter the cleanup scope after COM initialization succeeds.
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            self._mark_ready()
            failure_streak = 0
            missing_log_key = "missing_audio_process"
            while not self._stop.is_set():
                pid = resolve_target_pid(self.process_name)
                if pid is None:
                    self._log_gate.info(
                        f"Waiting for audio process {self.process_name}...",
                        interval=None,
                        changed=True,
                        key=missing_log_key,
                    )
                    if self._stop.wait(PROCESS_WAIT_INTERVAL):
                        return
                    continue

                self._log_gate.reset(missing_log_key)
                logger.info(f"Process loopback capture source: {self.process_name} pid={pid}")
                try:
                    self._run_loopback_stream(pid, push)
                    failure_streak = 0
                except Exception as exc:
                    if self._stream_started_once:
                        logger.warning(
                            f"Process loopback stream pid={pid} dropped; reconnecting: {exc}"
                        )
                        failure_streak = 0
                    else:
                        failure_streak += 1
                        logger.warning(
                            "Process loopback activation failed "
                            f"({failure_streak}/{MAX_ACTIVATION_FAILURES}) for "
                            f"{self.process_name} pid={pid}: {exc}"
                        )
                        if failure_streak >= MAX_ACTIVATION_FAILURES:
                            raise
                    if self._stop.wait(PROCESS_WAIT_INTERVAL):
                        return
        finally:
            comtypes.CoUninitialize()

    def _run_loopback_stream(self, pid: int, push: PushFn) -> None:
        audio_client, block_align, is_float = self._activate_and_initialize(pid)
        h_event = _CreateEventW(None, False, False, None)
        if not h_event:
            raise ctypes.WinError(ctypes.get_last_error())

        try:
            audio_client.SetEventHandle(h_event)
            capture = audio_client.GetService(ctypes.byref(IAudioCaptureClient._iid_))
            audio_client.Start()
            try:
                self._stream_started_once = True
                next_check = clock.now() + PROCESS_CHECK_INTERVAL
                while not self._stop.is_set():
                    now = clock.now()
                    if now >= next_check:
                        if not process_is_alive(pid, self.process_name):
                            logger.info(f"{self.process_name} pid={pid} ended; will rebind")
                            return
                        next_check = now + PROCESS_CHECK_INTERVAL

                    next_frames = capture.GetNextPacketSize()
                    if next_frames == 0:
                        _WaitForSingleObject(h_event, 20)
                        continue

                    while next_frames > 0 and not self._stop.is_set():
                        data_ptr, frames, flags, _devpos, _qpc = capture.GetBuffer()
                        try:
                            if frames > 0:
                                if (flags & AUDCLNT_BUFFERFLAGS_SILENT) or not data_ptr:
                                    mono = np.zeros(frames, dtype=np.float32)
                                else:
                                    raw = ctypes.string_at(data_ptr, frames * block_align)
                                    mono = _to_mono(raw, is_float)
                                push(mono)
                        finally:
                            capture.ReleaseBuffer(frames)
                        next_frames = capture.GetNextPacketSize()
            finally:
                try:
                    audio_client.Stop()
                except Exception:
                    pass
        finally:
            _CloseHandle(h_event)

    def _activate_and_initialize(self, pid: int):
        last_error: Optional[BaseException] = None
        for make_format, is_float, bits in _FORMAT_TIERS:
            block_align = CAPTURE_CHANNELS * bits // 8
            for buffer_hns in (LOW_LATENCY_BUFFER_HNS, FALLBACK_BUFFER_HNS):
                audio_client = _activate_audio_client(pid, self.include_process_tree)
                fmt = make_format()
                fmt_ptr = ctypes.cast(ctypes.byref(fmt), ctypes.POINTER(WAVEFORMATEX))
                try:
                    audio_client.Initialize(
                        AUDCLNT_SHAREMODE_SHARED,
                        INIT_STREAM_FLAGS,
                        REFERENCE_TIME(buffer_hns),
                        REFERENCE_TIME(0),
                        fmt_ptr,
                        None,
                    )
                    logger.info(
                        "Process loopback initialized: "
                        f"{'float' if is_float else 'pcm16'} buffer={buffer_hns // 10_000}ms"
                    )
                    return audio_client, block_align, is_float
                except Exception as exc:
                    last_error = exc
                    logger.debug(
                        "IAudioClient.Initialize failed for "
                        f"{'float' if is_float else 'pcm16'} "
                        f"buffer={buffer_hns // 10_000}ms: {_describe_exception(exc)}"
                    )
                    del audio_client
        detail = _describe_exception(last_error) if last_error else "no error detail"
        raise OSError(f"IAudioClient.Initialize failed for all formats: {detail}")


def _capability_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        build = sys.getwindowsversion().build
    except Exception:
        return False
    if build < MIN_PROCESS_LOOPBACK_BUILD:
        return False
    try:
        ctypes.WinDLL("mmdevapi.dll")
    except Exception:
        return False
    return True
