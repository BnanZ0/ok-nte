from typing import TypedDict

from ok import BaseScene, Logger

from src.utils import clock

logger = Logger.get_logger(__name__)


class _TeamRecord(TypedDict):
    state: bool | None
    timestamp: clock.TimePoint


class NTEScene(BaseScene):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._is_in_team = None
        self._in_combat = None
        self.cd_refreshed = False
        self._ocr_warm_up = False
        self._is_in_team_record: _TeamRecord = {"state": None, "timestamp": clock.UNSET}
        self._logged_in = False
        self._game_capture_ready = False
        self._health_snapshot = None

    def reset(self):
        self._is_in_team = None
        self._in_combat = None
        self.cd_refreshed = False

    def logged_in(self):
        return self._logged_in

    def set_logged_in(self, value=True):
        self._logged_in = value

    def game_capture_ready(self):
        return self._game_capture_ready

    def set_game_capture_ready(self, value: bool):
        self._game_capture_ready = value

    def in_combat(self):
        return self._in_combat

    def set_in_combat(self):
        self._in_combat = True
        return True

    def set_not_in_combat(self):
        self._in_combat = False
        return False

    def is_in_team(self, fun):
        if self._is_in_team is None:
            self._is_in_team = fun()
            if self._is_in_team is not self._is_in_team_record.get("state"):
                self._is_in_team_record["state"] = self._is_in_team
                self._is_in_team_record["timestamp"] = clock.now()
        return self._is_in_team

    def get_is_in_team_record(self) -> tuple[bool | None, clock.TimePoint]:
        return self._is_in_team_record["state"], self._is_in_team_record["timestamp"]

    def health_snapshot(self, image=None):
        import numpy as np
        if isinstance(image, np.ndarray):
            self._health_snapshot = image
        return self._health_snapshot

    def clear_health_snapshot(self):
        self._health_snapshot = None
