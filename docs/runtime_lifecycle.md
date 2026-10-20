# Runtime COM lifecycle

`Globals` constructs `RuntimeServices` during the application's main-thread
initialization. The constructor imports `comtypes` before any sound worker starts.
Keep this construction on the main thread. `start()` may subsequently run from a
Web server thread, including the server used by a WebView shell.

The installed comtypes 1.4.16 automatically initializes COM on the thread that
first imports it and registers a process-exit cleanup callback. Python executes
that callback on the main thread. First importing comtypes from `SoundContextInit`
would initialize the worker but uninitialize the main thread at exit. That can
invalidate the main thread's existing WinRT apartment before cached activation
factories are released, causing an access violation after shutdown logs finish.

Importing comtypes during main-thread runtime construction gives its implicit
initialization and exit callback the same owner. This preparation is lightweight;
sample loading, librosa, model initialization and capture loops stay on workers.
Do not clear framework caches, remove third-party exit handlers, or force the
process to exit as a substitute for correct thread ownership.

The audio producer separately initializes MTA COM on its own thread. It enters
its cleanup scope only after successful initialization and balances that call
with `CoUninitialize` on the same thread. Initialization failures propagate to
the capture source's existing error handling instead of advertising readiness.

Microsoft documents that each successful COM initialization, including S_FALSE,
must have a corresponding uninitialization on that thread:
[Initializing COM](https://learn.microsoft.com/en-us/windows/win32/learnwin32/initializing-the-com-library)
and [CoInitializeEx](https://learn.microsoft.com/en-us/windows/win32/api/combaseapi/nf-combaseapi-coinitializeex).

The process-level regression test imports audio on a worker while retaining a
WinRT factory, then checks actual interpreter exit. It starts no capture stream
or automation task. Recheck this boundary when upgrading comtypes or ok-script.
