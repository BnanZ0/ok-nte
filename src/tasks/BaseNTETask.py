import cv2
import numpy as np

from ok import BaseTask
from src.scene.NTEScene import NTEScene
from src.Labels import Labels


class BaseNTETask(BaseTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scene: NTEScene | None = None
        self.key_config = self.get_global_config('Game Hotkey Config')
        self._logged_in = False

    def in_team(self):
        c1 = self.find_one(Labels.char_1_text, threshold=0.8, frame_processor=isolate_white_text_to_black)
        c2 = self.find_one(Labels.char_2_text, threshold=0.8, frame_processor=isolate_white_text_to_black)
        c3 = self.find_one(Labels.char_3_text, threshold=0.8, frame_processor=isolate_white_text_to_black)
        c4 = self.find_one(Labels.char_4_text, threshold=0.8, frame_processor=isolate_white_text_to_black)
        arr = [c1, c2, c3, c4]
        # logger.debug(f'in_team check {arr}')
        current = -1
        exist_count = 0
        for i in range(len(arr)):
            if arr[i] is None:
                if current == -1:
                    current = i
            else:
                exist_count += 1
        if exist_count > 0:
            self._logged_in = True
            return True, current, exist_count + 1
        else:
            return False, -1, exist_count + 1

    def in_team_and_world(self):
        in_team, _, _ = self.in_team()
        in_world = True
        return in_team and in_world


lower_white = np.array([244, 244, 244], dtype=np.uint8)
upper_white = np.array([255, 255, 255], dtype=np.uint8)
black = np.array([0, 0, 0], dtype=np.uint8)
lower_white_none_inclusive = np.array([190, 190, 190], dtype=np.uint8)

def isolate_white_text_to_black(cv_image):
    """
    Converts pixels in the near-white range (244-255) to black,
    and all others to white.
    Args:
        cv_image: Input image (NumPy array, BGR).
    Returns:
        Black and white image (NumPy array), where matches are black.
    """
    match_mask = cv2.inRange(cv_image, black, lower_white_none_inclusive)
    output_image = cv2.cvtColor(match_mask, cv2.COLOR_GRAY2BGR)

    return output_image