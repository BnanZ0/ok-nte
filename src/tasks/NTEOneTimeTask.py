from ok import PostMessageInteraction, TaskDisabledException
from ok.device.intercation import PynputInteraction

_CLOUD_CAPTURE_WAIT_SECONDS = 12
_CLOUD_CAPTURE_POLL_SECONDS = 0.5
_CLOUD_CAPTURE_REBUILD_INTERVAL = 2.0


def _cloud_window_selected():
    """Best-effort cloud-variant check; a patch failure must never break tasks."""
    try:
        from src.patches.window_variants_patch import is_cloud_window_selected

        return bool(is_cloud_window_selected())
    except Exception:
        return False


def _cloud_launch_blocked():
    """Safety net for scheduled/CLI starts that bypass StartController.start()."""
    try:
        from src.patches.cloud_launch_guard import should_block_cloud_launch

        return bool(should_block_cloud_launch())
    except Exception:
        return False


class NTEOneTimeTask:

    def run(self, *args, **kwargs):
        # Cloud games are billed by playtime and are never auto-started. If the
        # client is missing, cancel this task instead of rebuilding capture or
        # sending real input. Self-disable first: TaskExecutor only `continue`s
        # on TaskDisabledException, which would otherwise retry forever.
        if _cloud_window_selected() and _cloud_launch_blocked():
            log_error = getattr(self, "log_error", None)
            if not callable(log_error):
                log_error = self.log_warning
            log_error("Cloud game window is not open; disabling this task")
            disable = getattr(self, "disable", None)
            if callable(disable):
                disable()
            raise TaskDisabledException("Cloud game window is not open")

        if not self.scene.game_capture_ready() or not self.executor.connected():
            cloud = _cloud_window_selected()
            if not (cloud and self._wait_for_cloud_capture_ready()):
                if cloud:
                    self.log_warning(
                        "Game launch or capture initialization is incomplete; "
                        "skipping task"
                    )
                else:
                    self.log_warning(
                        "Game launch or capture initialization is incomplete; "
                        "disabling this task to stop the retry loop"
                    )
                    # TaskExecutor catches TaskDisabledException with ``continue``
                    # and then immediately re-selects any still-enabled one-time
                    # task, so raising without disabling retries forever. The local
                    # PC variant reaches this when LauncherTask aborts early, for
                    # example when the app is not running as administrator. The
                    # cloud helper already disables on timeout.
                    disable = getattr(self, "disable", None)
                    if callable(disable):
                        disable()
                raise TaskDisabledException("Game capture is not ready")
        if isinstance(self.executor.interaction, PostMessageInteraction):
            self.executor.interaction.activate()
        elif isinstance(self.executor.interaction, PynputInteraction):
            self.bring_to_front()
        self.sleep(0.5)
        self.set_check_monthly_card()
        return super().run(*args, **kwargs)

    def _wait_for_cloud_capture_ready(self, time_out=_CLOUD_CAPTURE_WAIT_SECONDS):
        """Give a cloud capture a bounded chance to come up before giving up.

        TaskExecutor catches TaskDisabledException with ``continue`` but does
        NOT disable the task, and ``next_task()`` immediately returns any still
        enabled one-time task. A cloud window whose WGC capture never started
        therefore produces an endless "skipping task" retry loop. Here we wait
        briefly, retry the capture rebuild, and self-disable on timeout so the
        loop terminates with a clear error instead of spinning forever.
        """
        timer = self.clock.timer(timeout=time_out)
        last_rebuild = None
        rebuilds = 0
        while not timer.expired():
            if self.executor.connected():
                # LauncherTask owns game_capture_ready. If it marked the run
                # not-ready before WGC came up, this second chance must also
                # restore the flag; otherwise the task guard can never pass.
                self.scene.set_game_capture_ready(True)
                log_info = getattr(self, "log_info", None)
                if callable(log_info):
                    log_info(f"Cloud capture became ready after {rebuilds} rebuild(s)")
                return True

            manager = getattr(self.executor, "device_manager", None)
            method = getattr(manager, "capture_method", None) if manager else None
            needs_rebuild = manager is not None and (
                method is None or not self._method_connected(method)
            )
            now = self.clock.now()
            if needs_rebuild and (
                last_rebuild is None
                or now - last_rebuild >= _CLOUD_CAPTURE_REBUILD_INTERVAL
            ):
                last_rebuild = now
                rebuilds += 1
                try:
                    manager.use_windows_capture()
                except Exception as exc:
                    self.log_warning(f"Cloud capture rebuild #{rebuilds} failed: {exc}")
            self.sleep(_CLOUD_CAPTURE_POLL_SECONDS)

        self.scene.set_game_capture_ready(False)
        log_error = getattr(self, "log_error", None)
        if not callable(log_error):
            log_error = self.log_warning
        log_error(
            "Cloud capture did not become ready; disabling this task to stop "
            f"the retry loop (rebuilds={rebuilds})"
        )
        disable = getattr(self, "disable", None)
        if callable(disable):
            disable()
        return False

    @staticmethod
    def _method_connected(method):
        try:
            return bool(method.connected())
        except Exception:
            return False
