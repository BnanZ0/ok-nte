from contextlib import suppress

from ok import PostMessageInteraction, TaskDisabledException
from ok.device.intercation import PynputInteraction

_CAPTURE_SKIP_BACKOFF_CAP = 10.0


class NTEOneTimeTask:

    def run(self, *args, **kwargs):
        if not self.scene.game_capture_ready() or not self.executor.connected():
            self.log_warning("Game launch or capture initialization is incomplete; skipping task")
            # ok-script's executor re-picks the same enabled onetime task
            # immediately after TaskDisabledException. Back off so waiting for
            # game launch/capture does not turn into a hot loop that floods
            # the log and freezes the UI. The counter resets once capture is
            # ready, so unattended runs keep working after the game starts.
            count = getattr(self, "_capture_skip_count", 0) + 1
            self._capture_skip_count = count
            with suppress(Exception):
                self.sleep(min(1.0 * (count - 1), _CAPTURE_SKIP_BACKOFF_CAP))
            raise TaskDisabledException("Game capture is not ready")
        self._capture_skip_count = 0
        if isinstance(self.executor.interaction, PostMessageInteraction):
            self.executor.interaction.activate()
        elif isinstance(self.executor.interaction, PynputInteraction):
            self.bring_to_front()
        self.sleep(0.5)
        self.set_check_monthly_card()
        return super().run(*args, **kwargs)
