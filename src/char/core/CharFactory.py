import math
from typing import TYPE_CHECKING

from src.char.BaseChar import BaseChar
from src.char.core.CharRegistry import char_registry

if TYPE_CHECKING:
    import numpy as np
    from ok import Box

    from src.char.custom.CustomCharManager import CustomCharManager
    from src.combat.BaseCombatTask import BaseCombatTask


def get_char_implementation_class(impl_id: str):
    entry = char_registry.get(impl_id)
    return entry.char_cls if entry else None


def iter_char_implementations():
    return char_registry.get_all()


def _build_char_instance(
    task,
    index,
    char_id,
    sim,
    manager: "CustomCharManager",
    impl_id_override: str | None = None,
):
    from src.char.custom.CustomChar import CustomChar

    char_info = manager.get_character_info_by_id(char_id)
    impl_id = impl_id_override
    if impl_id is None:
        impl_id = char_info["impl_id"] if char_info else ""
    char_name = char_info["char_name"] if char_info else manager.get_impl_name(impl_id)

    resolved = False
    if not impl_id:
        instance = BaseChar(task, index, char_id=char_id or "unknown", confidence=sim)
    elif char_class := get_char_implementation_class(impl_id):
        instance = char_class(task, index, char_id=char_id, confidence=sim)
        resolved = True
    elif manager.is_custom_combo_exist(impl_id):
        instance = CustomChar(task, index, char_id=char_id, impl_id=impl_id, confidence=sim)
        resolved = True
    else:
        task.log_warning(f"Unknown character implementation '{impl_id}', using BaseChar")
        instance = BaseChar(task, index, char_id=char_id or "unknown", confidence=sim)

    instance.char_name = char_name
    instance.impl_id = impl_id if resolved else ""
    return instance


def get_char_by_id(
    task: "BaseCombatTask",
    index: int,
    char_id: str,
    confidence=1,
    impl_id: str | None = None,
):
    from src.char.custom.CustomCharManager import CustomCharManager

    manager = CustomCharManager()
    return _build_char_instance(
        task,
        index,
        char_id,
        confidence,
        manager,
        impl_id_override=impl_id,
    )


def get_char_by_impl_id(task: "BaseCombatTask", index: int, impl_id: str, confidence=1):
    """Create a character implementation for a preset slot without a character record."""
    return get_char_by_id(task, index, "", confidence=confidence, impl_id=impl_id)


def get_char_by_pos(task: "BaseCombatTask", box: "Box", index: int, old_char: BaseChar | None):
    # Retrieve CustomCharManager and test match
    from src.char.custom.CustomCharManager import CustomCharManager

    manager = CustomCharManager()
    cropped = box.crop_frame(task.frame)
    # Fast path check: if we already have an old_char, specifically test its matching only
    if old_char and old_char.confidence > 0.8:
        is_match, match_id, sim = manager.match_feature(task, cropped, target_char=old_char.char_id)
        if is_match and match_id == old_char.char_id:
            return _build_char_instance(task, index, match_id, sim, manager)

    # Perform Full DB Scan using the memory-cached match_feature
    is_match, match_id, sim = manager.match_feature(task, cropped)

    if is_match and match_id:
        return _build_char_instance(task, index, match_id, sim, manager)

    task.log_info(f"No match found for char {index + 1} set as default char")
    return BaseChar(task, index, char_id="unknown")


def get_char_feature_by_pos(
    task: "BaseCombatTask", index, frame=None, scale_box=1.0
) -> tuple["np.ndarray", int, int]:
    """
    Get the feature image of the character at the given position.

    Args:
        task: The combat task.
        index: The index of the character.

    Returns:
        A tuple containing the feature image, width, and height.
    """
    if frame is None:
        frame = task.frame
    box = task.get_char_box(index)
    if not math.isclose(scale_box, 1.0):
        box = box.scale(scale_box, scale_box)
    return box.crop_frame(frame), task.width, task.height


def is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False
