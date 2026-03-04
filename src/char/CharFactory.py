from typing import TYPE_CHECKING

import cv2
from typing_extensions import Any

from src.char.BaseChar import BaseChar
from src.char.Zero import Zero

if TYPE_CHECKING:
    from src.combat.BaseCombatTask import BaseCombatTask
    from ok import Box

char_dict: dict[str, dict[str, Any]] = {
    "char_default": {'cls': BaseChar},
    "char_zero": {'cls': Zero, 'cn_name': '零'},
}

char_names = char_dict.keys()


def get_char_by_pos(task: 'BaseCombatTask', box: 'Box', index: int, old_char: BaseChar | None):
    # Retrieve CustomCharManager and test match
    from src.char.custom.CustomCharManager import CustomCharManager
    from src.char.custom.CustomChar import CustomChar
    
    manager = CustomCharManager()
    feature_mat = box.crop_frame(task.frame)
    if feature_mat is not None and feature_mat.size > 0:
        
        # Fast path check: if we already have an old_char, see if the custom feature still matches it
        if old_char and old_char.confidence > 0.8:
            char_info = manager.get_character_info(old_char.char_name)
            if char_info:
                for fid in char_info.get("feature_ids", []):
                    saved_img = manager.load_feature_image(fid)
                    if saved_img is not None:
                        if saved_img.shape != feature_mat.shape:
                            resized_saved = cv2.resize(saved_img, (feature_mat.shape[1], feature_mat.shape[0]))
                        else:
                            resized_saved = saved_img
                        res = cv2.matchTemplate(feature_mat, resized_saved, cv2.TM_CCOEFF_NORMED)
                        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                        if max_val >= 0.8:
                            return old_char
        
        # Full DB Scan
        is_match, match_name, sim = manager.match_feature(feature_mat, threshold=0.8)
        if is_match and match_name:
            char_info = manager.get_character_info(match_name)
            combo_name = char_info.get("combo_name", "") if char_info else ""
            
            # Check if it's bound to a built-in Python script
            from src.ui.CharManagerTab import get_builtin_prefix
            import re
            
            if not combo_name:
                return BaseChar(task, index, char_name=match_name, confidence=sim)

            builtin_prefix = get_builtin_prefix()
            if combo_name.startswith(builtin_prefix):
                # Format is "[内置代码] 零 (char_zero)", we extract "char_zero"
                match = re.search(r'\(([^)]+)\)$', combo_name)
                if match:
                    builtin_key = match.group(1).strip()
                else:
                    builtin_key = combo_name.replace(builtin_prefix, "").strip()
                    
                if builtin_key in char_dict:
                    cls: 'BaseChar' = char_dict[builtin_key].get('cls', BaseChar)
                    return cls(task, index, char_name=match_name, confidence=sim)
            
            # Otherwise return default parsed CustomChar
            return CustomChar(task, index, char_name=match_name, confidence=sim)
    task.log_info(f"No match found for char {index + 1} set as default char")
    return BaseChar(task, index, char_name="unknown")

def get_char_feature_by_pos(task: 'BaseCombatTask', index):
    box = task.get_box_by_name(f'box_char_{index + 1}')
    return box.crop_frame(task.frame)

def is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False
