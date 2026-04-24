import json
import os
import uuid
from threading import Lock, RLock
from typing import TYPE_CHECKING

import cv2
import numpy as np
from ok import Logger

from src.char.custom.BuiltinComboRegistry import BuiltinComboRegistry
from src.Labels import Labels

if TYPE_CHECKING:
    from src.combat.BaseCombatTask import BaseCombatTask

logger = Logger.get_logger(__name__)

CUSTOM_CHARS_DIR = "custom_chars"
FEATURES_DIR = os.path.join(CUSTOM_CHARS_DIR, "features")
DB_PATH = os.path.join(CUSTOM_CHARS_DIR, "db.json")
DB_SCHEMA_VERSION = 4


class CustomCharManager:
    _instance = None
    _lock = Lock()
    CUSTOM_COMBO_PREFIX = "custom:"

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(CustomCharManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if hasattr(self, "initialized") and self.initialized:
            return
        self._data_lock = RLock()
        os.makedirs(FEATURES_DIR, exist_ok=True)
        self.db = self._default_db()
        self._feature_cache = {}
        self._cache_mask = None
        self._cache_scr_w = -1
        self._cache_scr_h = -1
        self._cache_fids = set()
        self.load_db()
        self.migrate_db_schema()
        self.validate_db()
        self.initialized = True

    @staticmethod
    def _default_fixed_team():
        return {"enabled": False, "slots": [{"char_name": "", "combo_ref": ""} for _ in range(4)]}

    @classmethod
    def _normalize_fixed_team_slot(cls, slot) -> dict:
        slot = slot if isinstance(slot, dict) else {}
        char_name = cls._normalize_char_name(slot.get("char_name", ""))
        combo_ref = cls.to_combo_ref(str(slot.get("combo_ref", "") or "").strip())
        if not char_name:
            combo_ref = ""
        return {
            "char_name": char_name,
            "combo_ref": combo_ref,
        }

    @classmethod
    def _normalize_fixed_team_config(cls, config) -> dict:
        normalized = cls._default_fixed_team()
        if not isinstance(config, dict):
            return normalized

        normalized["enabled"] = bool(config.get("enabled", False))
        raw_slots = config.get("slots", [])
        if isinstance(raw_slots, list):
            for i in range(min(4, len(raw_slots))):
                normalized["slots"][i] = cls._normalize_fixed_team_slot(raw_slots[i])
        return normalized

    @staticmethod
    def _default_db():
        return {
            "schema_version": DB_SCHEMA_VERSION,
            "combos": {},
            "characters": {},
            "features": {},
            "fixed_team": CustomCharManager._default_fixed_team(),
        }

    @staticmethod
    def get_builtin_prefix():
        return BuiltinComboRegistry._legacy_prefix()

    @staticmethod
    def to_combo_ref(combo_label: str) -> str:
        return BuiltinComboRegistry.to_ref(combo_label)

    @staticmethod
    def to_combo_label(combo_ref: str) -> str:
        return BuiltinComboRegistry.to_label(combo_ref)

    @staticmethod
    def get_builtin_key(combo_ref: str) -> str | None:
        key = BuiltinComboRegistry.ref_to_key(combo_ref)
        if key and key in dict(BuiltinComboRegistry._get_builtin_entries()):
            return key
        return None

    @staticmethod
    def is_builtin_combo(combo_ref: str) -> bool:
        return CustomCharManager.get_builtin_key(combo_ref) is not None

    @classmethod
    def _to_custom_combo_key(cls, combo_key: str, existing_keys: set[str]) -> str:
        base = f"{cls.CUSTOM_COMBO_PREFIX}{combo_key}"
        candidate = base
        suffix = 2
        while candidate in existing_keys:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _normalize_char_name(char_name) -> str:
        return str(char_name or "").strip()

    def _character_name_from_record(self, char_id: str, char_data: dict) -> str:
        name = self._normalize_char_name(char_data.get("name", ""))
        if name:
            return name
        fallback = self._normalize_char_name(char_id)
        return fallback or "unnamed"

    def _find_character_id_by_name(self, char_name: str) -> str | None:
        target = self._normalize_char_name(char_name)
        if not target:
            return None
        for char_id, char_data in self.db.get("characters", {}).items():
            if not isinstance(char_data, dict):
                continue
            if self._character_name_from_record(char_id, char_data) == target:
                return char_id
        return None

    def _generate_character_id(self) -> str:
        while True:
            char_id = f"char_{uuid.uuid4().hex}"
            if char_id not in self.db["characters"]:
                return char_id

    def load_db(self):
        loaded = self._default_db()
        if os.path.exists(DB_PATH):
            try:
                with open(DB_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        loaded["schema_version"] = data.get("schema_version", 0)
                        loaded["combos"] = data.get("combos", loaded["combos"])
                        loaded["characters"] = data.get("characters", loaded["characters"])
                        loaded["features"] = data.get("features", loaded["features"])
                        loaded["fixed_team"] = data.get("fixed_team", loaded["fixed_team"])
            except Exception as e:
                logger.error("Failed to load custom char DB", e)
        self.db = loaded

    def validate_db(self):
        with self._data_lock:
            modified = False

            if not isinstance(self.db.get("characters"), dict):
                self.db["characters"] = {}
                modified = True

            if not isinstance(self.db.get("features"), dict):
                self.db["features"] = {}
                modified = True

            fixed_team = self._normalize_fixed_team_config(self.db.get("fixed_team"))
            if fixed_team != self.db.get("fixed_team"):
                self.db["fixed_team"] = fixed_team
                modified = True

            for char_id, char_data in self.db["characters"].items():
                if not isinstance(char_data, dict):
                    self.db["characters"][char_id] = {
                        "name": self._normalize_char_name(char_id) or "unnamed",
                        "combo_ref": "",
                        "feature_ids": [],
                    }
                    char_data = self.db["characters"][char_id]
                    modified = True

                feature_ids = char_data.get("feature_ids", [])
                if not isinstance(feature_ids, list):
                    feature_ids = []
                    modified = True
                valid_fids = []
                for fid in feature_ids:
                    path = os.path.join(FEATURES_DIR, f"{fid}.png")
                    if os.path.exists(path):
                        valid_fids.append(fid)
                    else:
                        modified = True
                char_data["feature_ids"] = valid_fids

            # Keep db.features in sync with actual image assets on disk.
            for fid in list(self.db["features"].keys()):
                path = os.path.join(FEATURES_DIR, f"{fid}.png")
                if not os.path.exists(path):
                    del self.db["features"][fid]
                    modified = True

            if modified:
                self._invalidate_feature_cache()
                self.save_db()

    def save_db(self):
        with self._data_lock:
            try:
                self.db["schema_version"] = DB_SCHEMA_VERSION
                temp_path = DB_PATH + ".tmp"
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self.db, f, indent=4, ensure_ascii=False)
                os.replace(temp_path, DB_PATH)
            except Exception as e:
                logger.error("Failed to save custom char DB", e)

    def _invalidate_feature_cache(self):
        self._feature_cache.clear()
        self._cache_scr_w = -1
        self._cache_scr_h = -1
        self._cache_fids = set()

    def migrate_db_schema(self):
        with self._data_lock:
            modified = False
            source_schema_version = self.db.get("schema_version", 0)
            try:
                source_schema_version = int(source_schema_version)
            except (TypeError, ValueError):
                source_schema_version = 0
                modified = True
            use_legacy_combo_name = source_schema_version < 4

            if not isinstance(self.db.get("combos"), dict):
                self.db["combos"] = {}
                modified = True
            if not isinstance(self.db.get("characters"), dict):
                self.db["characters"] = {}
                modified = True
            if not isinstance(self.db.get("features"), dict):
                self.db["features"] = {}
                modified = True

            normalized_combos = {}
            combo_key_remap = {}
            for combo_ref, combo_content in self.db.get("combos", {}).items():
                combo_key = str(combo_ref).strip()
                if not combo_key:
                    modified = True
                    continue

                # Custom combo keys that resolve to builtin refs are ambiguous.
                # Remap them into an explicit custom namespace.
                if self.is_builtin_combo(combo_key):
                    remapped_key = self._to_custom_combo_key(
                        combo_key, set(normalized_combos.keys())
                    )
                    combo_key_remap[combo_key] = remapped_key
                    combo_key = remapped_key
                    modified = True

                normalized_combos[combo_key] = combo_content
            if normalized_combos != self.db["combos"]:
                self.db["combos"] = normalized_combos
                modified = True

            normalized_characters = {}
            used_names = set()
            legacy_id_index = 1

            def next_legacy_id():
                nonlocal legacy_id_index
                while True:
                    candidate = f"char_{legacy_id_index:04d}"
                    legacy_id_index += 1
                    if candidate not in normalized_characters:
                        return candidate

            for raw_char_id, raw_char_data in self.db.get("characters", {}).items():
                source_data = raw_char_data if isinstance(raw_char_data, dict) else {}
                if not isinstance(raw_char_data, dict):
                    modified = True

                record = dict(source_data)

                char_name = self._normalize_char_name(record.get("name", raw_char_id))
                if not char_name:
                    char_name = "unnamed"
                    modified = True

                unique_name = char_name
                suffix = 2
                while unique_name in used_names:
                    unique_name = f"{char_name}_{suffix}"
                    suffix += 1
                if unique_name != char_name:
                    modified = True
                used_names.add(unique_name)

                combo_ref_raw = str(record.get("combo_ref", "") or "").strip()
                if not combo_ref_raw and use_legacy_combo_name:
                    combo_ref_raw = str(record.get("combo_name", "") or "").strip()
                combo_raw = combo_ref_raw
                combo_ref = self.to_combo_ref(combo_raw)
                if combo_ref in combo_key_remap:
                    combo_ref = combo_key_remap[combo_ref]
                elif combo_raw in combo_key_remap:
                    combo_ref = combo_key_remap[combo_raw]
                feature_ids = record.get("feature_ids", [])
                if not isinstance(feature_ids, list):
                    feature_ids = []
                    modified = True

                record["name"] = unique_name
                record["combo_ref"] = combo_ref
                if "combo_name" in record:
                    del record["combo_name"]
                    modified = True
                record["feature_ids"] = feature_ids

                raw_char_id = str(raw_char_id).strip()
                if "name" in source_data and raw_char_id:
                    char_id = raw_char_id
                else:
                    # Legacy schema: key was character name; migrate to stable ID.
                    char_id = next_legacy_id()
                    modified = True

                while char_id in normalized_characters:
                    char_id = next_legacy_id()
                    modified = True

                if record != source_data:
                    modified = True
                if raw_char_id != char_id:
                    modified = True

                normalized_characters[char_id] = record

            if normalized_characters != self.db["characters"]:
                self.db["characters"] = normalized_characters
                modified = True

            if self.db.get("schema_version") != DB_SCHEMA_VERSION:
                self.db["schema_version"] = DB_SCHEMA_VERSION
                modified = True

            if modified:
                self.save_db()

    # Backward-compatible alias for legacy callers.
    def migrate_combo_references(self):
        self.migrate_db_schema()

    def add_combo(self, combo_ref, content):
        """添加或更新出招表"""
        with self._data_lock:
            combo_ref = self.to_combo_ref(combo_ref)
            if not combo_ref or self.is_builtin_combo(combo_ref):
                return
            self.db["combos"][combo_ref] = content
            self.save_db()

    def delete_combo(self, combo_ref):
        """删除出招表"""
        with self._data_lock:
            combo_ref = self.to_combo_ref(combo_ref)
            deleted = False
            if combo_ref in self.db["combos"]:
                del self.db["combos"][combo_ref]
                deleted = True
            fixed_team = self._normalize_fixed_team_config(self.db.get("fixed_team"))
            fixed_team_changed = False
            for slot in fixed_team["slots"]:
                if slot["combo_ref"] == combo_ref:
                    slot["combo_ref"] = ""
                    fixed_team_changed = True
            if fixed_team_changed:
                self.db["fixed_team"] = fixed_team
            if deleted or fixed_team_changed:
                self.save_db()

    def is_custom_combo_exist(self, combo_ref):
        """判断出招表是否存在"""
        with self._data_lock:
            return combo_ref in self.db["combos"]

    def get_combo(self, combo_ref):
        """获取出招表"""
        with self._data_lock:
            if combo_ref in self.db["combos"]:
                return self.db["combos"].get(combo_ref, "")
            if self.is_builtin_combo(combo_ref):
                return ""
            return self.db["combos"].get(combo_ref, "")

    def get_all_combos(self):
        with self._data_lock:
            combos = list(self.db["combos"].keys())
            combos.extend([label for _, label in BuiltinComboRegistry.iter_builtin_pairs()])
            return combos

    def get_all_combo_items(self):
        """
        Return combo options as (label, ref) tuples for UI binding.
        """
        with self._data_lock:
            items = [(name, name) for name in self.db["combos"].keys()]
            items.extend(
                [
                    (label, combo_ref)
                    for combo_ref, label in BuiltinComboRegistry.iter_builtin_pairs()
                ]
            )
            return items

    def add_character(self, char_name, combo_ref):
        """添加或更新角色属性 (不包含特征图)"""
        with self._data_lock:
            char_name = self._normalize_char_name(char_name)
            combo_ref = self.to_combo_ref(combo_ref)
            if not char_name:
                return
            char_id = self._find_character_id_by_name(char_name)
            if char_id is None:
                char_id = self._generate_character_id()
                self.db["characters"][char_id] = {
                    "name": char_name,
                    "combo_ref": combo_ref,
                    "feature_ids": [],
                }
            else:
                self.db["characters"][char_id]["name"] = char_name
                self.db["characters"][char_id]["combo_ref"] = combo_ref
            self._invalidate_feature_cache()
            self.save_db()

    def delete_character(self, char_name):
        """删除角色及其所有特征图，不影响出招表"""
        with self._data_lock:
            char_name = self._normalize_char_name(char_name)
            char_id = self._find_character_id_by_name(char_name)
            if char_id is None:
                return
            feature_ids = self.db["characters"][char_id].get("feature_ids", [])
            for fid in feature_ids:
                self.delete_feature_image(fid)
            del self.db["characters"][char_id]
            fixed_team = self._normalize_fixed_team_config(self.db.get("fixed_team"))
            fixed_team_changed = False
            for slot in fixed_team["slots"]:
                if slot["char_name"] == char_name:
                    slot["char_name"] = ""
                    slot["combo_ref"] = ""
                    fixed_team_changed = True
            if fixed_team_changed:
                self.db["fixed_team"] = fixed_team
            self._invalidate_feature_cache()
            self.save_db()

    def rename_character(self, old_name: str, new_name: str) -> bool:
        with self._data_lock:
            old_name = self._normalize_char_name(old_name)
            new_name = self._normalize_char_name(new_name)
            if not old_name or not new_name:
                return False
            if old_name == new_name:
                return True
            old_char_id = self._find_character_id_by_name(old_name)
            if old_char_id is None:
                return False
            duplicate_char_id = self._find_character_id_by_name(new_name)
            if duplicate_char_id is not None and duplicate_char_id != old_char_id:
                return False

            self.db["characters"][old_char_id]["name"] = new_name
            fixed_team = self._normalize_fixed_team_config(self.db.get("fixed_team"))
            fixed_team_changed = False
            for slot in fixed_team["slots"]:
                if slot["char_name"] == old_name:
                    slot["char_name"] = new_name
                    fixed_team_changed = True
            if fixed_team_changed:
                self.db["fixed_team"] = fixed_team
            self._invalidate_feature_cache()
            self.save_db()
            return True

    def add_feature_to_character(self, char_name, image_mat, width=0, height=0):
        """为角色保存一张截图并关联特征 UUID"""
        with self._data_lock:
            char_name = self._normalize_char_name(char_name)
            if not char_name:
                return ""
            fid = f"feat_{uuid.uuid4().hex}"
            self.save_feature_image(fid, image_mat)

            if "features" not in self.db:
                self.db["features"] = {}
            self.db["features"][fid] = {"width": width, "height": height}

            char_id = self._find_character_id_by_name(char_name)
            if char_id is None:
                char_id = self._generate_character_id()
                self.db["characters"][char_id] = {
                    "name": char_name,
                    "combo_ref": "",
                    "feature_ids": [],
                }

            if "feature_ids" not in self.db["characters"][char_id]:
                self.db["characters"][char_id]["feature_ids"] = []

            self.db["characters"][char_id]["feature_ids"].append(fid)
            self._invalidate_feature_cache()
            self.save_db()
            return fid

    def remove_feature_from_character(self, char_name, feature_id):
        """从角色中移除某个特征"""
        with self._data_lock:
            char_id = self._find_character_id_by_name(char_name)
            if char_id is None:
                return
            feature_ids = self.db["characters"][char_id].get("feature_ids", [])
            if feature_id in feature_ids:
                feature_ids.remove(feature_id)
                self.delete_feature_image(feature_id)
                self._invalidate_feature_cache()
                self.save_db()

    def save_feature_image(self, feature_id, image_mat):
        """保存特征图"""
        path = os.path.join(FEATURES_DIR, f"{feature_id}.png")
        ok = cv2.imwrite(path, image_mat)
        if not ok:
            raise IOError(f"Failed to write feature image: {path}")

    def delete_feature_image(self, feature_id):
        """删除特征图文件并移除 DB 内独立的特征分辨率记录"""
        with self._data_lock:
            if "features" in self.db and feature_id in self.db["features"]:
                del self.db["features"][feature_id]
            path = os.path.join(FEATURES_DIR, f"{feature_id}.png")
            if os.path.exists(path):
                os.remove(path)

    def load_feature_image(self, feature_id):
        """读取特征图以及其原始分辨率"""
        path = os.path.join(FEATURES_DIR, f"{feature_id}.png")
        if os.path.exists(path):
            mat = cv2.imread(path)
            with self._data_lock:
                feat_info = self.db.get("features", {}).get(feature_id, {})
            w = feat_info.get("width", 0)
            h = feat_info.get("height", 0)
            return mat, w, h
        return None, 0, 0

    def match_feature(self, task: "BaseCombatTask", new_image_mat, threshold=0.6, target_char=None):
        """比对新截图与所有数据库内特征图，返回(是/否匹配, 匹配到的角色名, 相似度)"""
        current_scr_h, current_scr_w = task.height, task.width

        with self._data_lock:
            character_snapshot = {}
            for char_id, char_data in self.db["characters"].items():
                if not isinstance(char_data, dict):
                    continue
                char_name = self._character_name_from_record(char_id, char_data)
                character_snapshot[char_name] = list(char_data.get("feature_ids", []))
            current_fids = set()
            for feature_ids in character_snapshot.values():
                current_fids.update(feature_ids)

            need_rebuild = (
                self._cache_scr_w != current_scr_w
                or self._cache_scr_h != current_scr_h
                or self._cache_fids != current_fids
            )
            if need_rebuild:
                self._feature_cache.clear()
                self._cache_scr_w = current_scr_w
                self._cache_scr_h = current_scr_h
                self._cache_fids = current_fids

        if need_rebuild:
            rebuilt_cache = {}
            for char_name, feature_ids in character_snapshot.items():
                rebuilt_cache[char_name] = {}
                for fid in feature_ids:
                    saved_img, w, h = self.load_feature_image(fid)
                    if saved_img is not None:
                        if w and h and (w != current_scr_w or h != current_scr_h):
                            scale_x = current_scr_w / w
                            scale_y = current_scr_h / h
                            scale = min(scale_x, scale_y)
                            save_h, save_w = saved_img.shape[:2]
                            new_w = max(1, round(save_w * scale))
                            new_h = max(1, round(save_h * scale))
                            resized_saved = cv2.resize(saved_img, (new_w, new_h))
                        else:
                            scale = 1
                            resized_saved = saved_img
                        logger.debug(
                            f"loaded {char_name} resized width {current_scr_w} / "
                            f"original_width:{w}, scale_x:{scale}"
                        )
                        rebuilt_cache[char_name][fid] = resized_saved
            with self._data_lock:
                self._feature_cache = rebuilt_cache
                box = task.get_box_by_name(Labels.box_char_1)
                self._cache_mask = (
                    create_ellipse_mask(box.width, box.height, box.width * 0.4, box.height * 0.4)
                    if box
                    else None
                )

        with self._data_lock:
            cache_snapshot = {
                char_name: dict(features) for char_name, features in self._feature_cache.items()
            }

        best_match_char = None
        best_similarity = 0.0

        for char_name, cached_features in cache_snapshot.items():
            if target_char and char_name != target_char:
                continue
            for fid, cached_mat in cached_features.items():
                # show_masked_template(cached_mat, self._cache_mask)  # Debug
                mask = None
                if self._cache_mask is not None:
                    if cached_mat.shape[0:2] == self._cache_mask.shape[0:2]:
                        mask = self._cache_mask
                    else:
                        mask = cv2.resize(
                            self._cache_mask,
                            (cached_mat.shape[1], cached_mat.shape[0]),
                            interpolation=cv2.INTER_NEAREST,
                        )

                # Ensure cached_mat is not statically larger than new_image_mat
                if (
                    cached_mat.shape[0] > new_image_mat.shape[0]
                    or cached_mat.shape[1] > new_image_mat.shape[1]
                ):
                    ch = min(cached_mat.shape[0], new_image_mat.shape[0])
                    cw = min(cached_mat.shape[1], new_image_mat.shape[1])
                    cached_mat = cached_mat[:ch, :cw]
                    if mask is not None:
                        mask = mask[:ch, :cw]

                # 裁切掉边缘的几个像素，给予 matchTemplate 在 new_image_mat 内部滑动的冗余空间
                # 这样可以解决跨分辨率时因为引擎级渲染和坐标四舍五入造成的 1-2 像素错位导致的断崖式掉率
                margin = 2
                if cached_mat.shape[0] > margin * 2 and cached_mat.shape[1] > margin * 2:
                    cached_mat = cached_mat[margin:-margin, margin:-margin]
                    if mask is not None:
                        mask = mask[margin:-margin, margin:-margin]

                res = cv2.matchTemplate(new_image_mat, cached_mat, cv2.TM_CCOEFF_NORMED, mask=mask)
                res[np.isinf(res)] = 0
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                if max_val > best_similarity:
                    best_similarity = max_val
                    best_match_char = char_name

        if best_similarity >= threshold:
            return True, best_match_char, best_similarity
        return False, None, best_similarity

    def get_all_characters(self):
        """获取所有角色数据"""
        with self._data_lock:
            characters = {}
            for char_id, char_data in self.db["characters"].items():
                if isinstance(char_data, dict):
                    out = dict(char_data)
                    char_name = self._character_name_from_record(char_id, char_data)
                    out["name"] = char_name
                    characters[char_name] = out
                else:
                    char_name = self._normalize_char_name(char_id)
                    if char_name:
                        characters[char_name] = char_data
            return characters

    def get_character_combo_ref(self, char_name: str) -> str:
        info = self.get_character_info(char_name) or {}
        return self.to_combo_ref(info.get("combo_ref", ""))

    def get_character_combo_label(self, char_name: str) -> str:
        return self.to_combo_label(self.get_character_combo_ref(char_name))

    def get_character_info(self, char_name):
        with self._data_lock:
            char_id = self._find_character_id_by_name(char_name)
            if char_id is None:
                return None
            char_info = self.db["characters"].get(char_id, None)
            if isinstance(char_info, dict):
                combo_ref = self.to_combo_ref(char_info.get("combo_ref", ""))
                out = dict(char_info)
                out["name"] = self._character_name_from_record(char_id, char_info)
                out["combo_ref"] = combo_ref
                return out
            return char_info

    def get_fixed_team(self):
        with self._data_lock:
            fixed_team = self._normalize_fixed_team_config(self.db.get("fixed_team"))
            return {
                "enabled": fixed_team["enabled"],
                "slots": [dict(slot) for slot in fixed_team["slots"]],
            }

    def set_fixed_team(self, enabled: bool, slots):
        with self._data_lock:
            self.db["fixed_team"] = self._normalize_fixed_team_config(
                {
                    "enabled": enabled,
                    "slots": slots,
                }
            )
            self.save_db()

    def clear_fixed_team(self):
        with self._data_lock:
            self.db["fixed_team"] = self._default_fixed_team()
            self.save_db()


def create_ellipse_mask(w, h, rx, ry):
    # 1. 创建全黑图像
    mask = np.zeros((h, w), dtype=np.uint8)

    # 2. 强制将所有数值转换为整数，避免类型错误
    # center 要求是 (int, int)
    # axes 要求是 (int, int)
    center = (int(w // 2), int(h // 2))
    axes = (int(rx), int(ry))

    # 3. 使用规范的参数格式
    # 必须保证是 (img, center, axes, angle, startAngle, endAngle, color, thickness)
    cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)

    return mask


def show_masked_template(cached_mat, _cache_mask):
    # 1. 获取目标尺寸 (以 cached_mat 为准)
    h, w = cached_mat.shape[:2]

    # 2. 确保 mask 是 2 维的 (如果有可能是 3 维的，去掉通道)
    if len(_cache_mask.shape) == 3:
        mask = _cache_mask[:, :, 0]
    else:
        mask = _cache_mask.copy()

    # 3. 强制调整 mask 尺寸以匹配 cached_mat
    if mask.shape != (h, w):
        print(f"警告：尺寸不匹配！Mat: {h}x{w}, Mask: {mask.shape}。正在强制 resize...")
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

    # 4. 确保类型是 uint8
    mask = mask.astype(np.uint8)

    # 5. 执行位运算
    result = cv2.bitwise_and(cached_mat, cached_mat, mask=mask)
    result = cv2.resize(result, (w * 5, h * 5), interpolation=cv2.INTER_NEAREST)
    unmasked = cv2.resize(cached_mat, (w * 5, h * 5), interpolation=cv2.INTER_NEAREST)
    # 显示
    cv2.imshow("Masked Result", result)
    cv2.imshow("unMasked Result", unmasked)
    cv2.waitKey(0)
