import json
import os
import shutil
import uuid
from threading import Lock, RLock

from src.char.custom.CustomCharDbMigrator import CustomCharDbMigrator, MigrationContext

DB_SCHEMA_VERSION = 9


class CustomCharDb:
    """Process-wide owner of custom character persistence and current-schema invariants."""

    _instance = None
    _instance_lock = Lock()

    def __new__(cls, db_path: str, features_dir: str, context: MigrationContext, logger=None):
        with cls._instance_lock:
            if cls._instance is None or cls._instance.db_path != db_path:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str, features_dir: str, context: MigrationContext, logger=None):
        if getattr(self, "_initialized", False):
            return
        self.db_path = db_path
        self.features_dir = features_dir
        self.context = context
        self.logger = logger
        self._lock = RLock()
        self._data = self._default_data()
        self._initialized = True
        self.reload()

    @classmethod
    def reset_instance(cls):
        with cls._instance_lock:
            cls._instance = None

    @staticmethod
    def _as_text(value) -> str:
        return "" if value is None else str(value)

    @classmethod
    def _is_blank_text(cls, value) -> bool:
        return cls._as_text(value).strip() == ""

    @staticmethod
    def _default_fixed_team() -> dict:
        return {"enabled": False, "slots": [{"char_id": "", "impl_id": ""} for _ in range(4)]}

    @staticmethod
    def _new_preset_id() -> str:
        return f"preset_{uuid.uuid4().hex}"

    @classmethod
    def _default_team_preset(cls, preset_id: str, name: str) -> dict:
        return {
            "id": preset_id,
            "name": name,
            "slots": [{"char_id": "", "impl_id": ""} for _ in range(4)],
            "is_fixed": False,
        }

    @classmethod
    def _normalize_team_slot(cls, slot) -> dict:
        slot = slot if isinstance(slot, dict) else {}
        char_id = cls._as_text(slot.get("char_id", "")).strip()
        impl_id = cls._as_text(slot.get("impl_id", "")).strip()
        return {"char_id": char_id, "impl_id": impl_id}

    @classmethod
    def _normalize_team_presets(cls, presets, valid_char_ids, is_valid_impl) -> list[dict]:
        if not isinstance(presets, list):
            return []

        normalized = []
        used_ids = set()
        used_names = set()
        has_fixed_preset = False
        for raw_preset in presets:
            if not isinstance(raw_preset, dict):
                continue

            preset_id = cls._as_text(raw_preset.get("id", "")).strip()
            if not preset_id or preset_id in used_ids:
                preset_id = cls._new_preset_id()
            used_ids.add(preset_id)

            name = cls._unique_name(cls._as_text(raw_preset.get("name", "")), used_names)
            preset = cls._default_team_preset(preset_id, name)
            raw_slots = raw_preset.get("slots", [])
            seen_char_ids = set()
            if isinstance(raw_slots, list):
                for index in range(min(4, len(raw_slots))):
                    slot = cls._normalize_team_slot(raw_slots[index])
                    char_id = slot["char_id"]
                    impl_id = slot["impl_id"]
                    if impl_id and not is_valid_impl(impl_id):
                        slot["impl_id"] = ""
                    if char_id:
                        if char_id not in valid_char_ids or char_id in seen_char_ids:
                            slot["char_id"] = ""
                        else:
                            seen_char_ids.add(char_id)
                    preset["slots"][index] = slot

            preset["is_fixed"] = bool(raw_preset.get("is_fixed", False)) and not has_fixed_preset
            has_fixed_preset = has_fixed_preset or preset["is_fixed"]
            normalized.append(preset)
        return normalized

    @classmethod
    def _default_data(cls) -> dict:
        return {
            "schema_version": DB_SCHEMA_VERSION,
            "combos": {},
            "characters": {},
            "features": {},
            "team_presets": [],
        }

    @classmethod
    def _character_name_from_record(cls, char_id: str, char_data: dict) -> str:
        name = cls._as_text(char_data.get("name", "")).strip()
        if not cls._is_blank_text(name):
            return name
        fallback = cls._as_text(char_id).strip()
        return fallback if not cls._is_blank_text(fallback) else "unnamed"

    @classmethod
    def _unique_name(cls, name: str, used_names: set[str]) -> str:
        name = name.strip()
        if cls._is_blank_text(name):
            name = "unnamed"
        candidate = name
        suffix = 2
        while candidate in used_names:
            candidate = f"{name}_{suffix}"
            suffix += 1
        used_names.add(candidate)
        return candidate

    def _read_raw_data(self) -> dict:
        """Read JSON only; migration and normalization have separate responsibilities."""
        if not os.path.exists(self.db_path):
            return self._default_data()

        try:
            with open(self.db_path, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                return data
            if self.logger:
                self.logger.warning("Custom char DB root must be an object; using defaults")
        except Exception as error:
            if self.logger:
                self.logger.error("Failed to load custom char DB", error)
        return self._default_data()

    def _backup_before_migration(self) -> bool:
        """Preserve a pre-migration source database once, before saving a migration."""
        if not os.path.exists(self.db_path):
            return True

        backup_path = f"{self.db_path}.pre-v{DB_SCHEMA_VERSION}.bak"
        if os.path.exists(backup_path):
            return True

        try:
            shutil.copy2(self.db_path, backup_path)
            return True
        except Exception as error:
            if self.logger:
                self.logger.error("Failed to back up custom char DB before migration", error)
            return False

    def _save_locked(self):
        try:
            self._data["schema_version"] = DB_SCHEMA_VERSION
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            temp_path = f"{self.db_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(self._data, file, indent=4, ensure_ascii=False)
            os.replace(temp_path, self.db_path)
        except Exception as error:
            if self.logger:
                self.logger.error("Failed to save custom char DB", error)
            else:
                raise

    def _normalize_current_data(self) -> bool:
        """Normalize the current v7 record shape without interpreting historical fields."""
        db = self._data
        modified = False

        if not isinstance(db.get("combos"), dict):
            db["combos"] = {}
            modified = True

        valid_combos = {}
        for combo_id, combo_data in db["combos"].items():
            combo_id = self._as_text(combo_id).strip()
            if self._is_blank_text(combo_id) or self.context.is_builtin_impl(combo_id):
                modified = True
                continue
            if isinstance(combo_data, dict):
                combo_name = self._as_text(combo_data.get("name", combo_id)).strip()
                content = self._as_text(combo_data.get("content", ""))
            else:
                combo_name = combo_id
                content = self._as_text(combo_data)
                modified = True
            if self._is_blank_text(combo_name):
                combo_name = combo_id
                modified = True
            valid_combos[combo_id] = {"name": combo_name, "content": content}
        if valid_combos != db["combos"]:
            db["combos"] = valid_combos
            modified = True

        if not isinstance(db.get("characters"), dict):
            db["characters"] = {}
            modified = True
        if not isinstance(db.get("features"), dict):
            db["features"] = {}
            modified = True

        if "fixed_team" in db:
            db.pop("fixed_team")
            modified = True

        used_names = set()
        for char_id, char_data in db["characters"].items():
            if not isinstance(char_data, dict):
                db["characters"][char_id] = {
                    "name": self._unique_name(self._as_text(char_id), used_names),
                    "impl_id": "",
                    "feature_ids": [],
                }
                modified = True
                continue

            char_name = self._unique_name(
                self._character_name_from_record(char_id, char_data), used_names
            )
            if char_data.get("name") != char_name:
                char_data["name"] = char_name
                modified = True

            impl_id = self._as_text(char_data.get("impl_id", "")).strip()
            if (
                impl_id
                and not self.context.is_builtin_impl(impl_id)
                and impl_id not in db["combos"]
                and not impl_id.startswith("external:")
            ):
                impl_id = ""
                modified = True
            if impl_id != char_data.get("impl_id", ""):
                char_data["impl_id"] = impl_id
                modified = True

            feature_ids = char_data.get("feature_ids", [])
            if not isinstance(feature_ids, list):
                feature_ids = []
                modified = True
            valid_feature_ids = []
            for feature_id in feature_ids:
                path = os.path.join(self.features_dir, f"{feature_id}.png")
                if os.path.exists(path):
                    valid_feature_ids.append(feature_id)
                else:
                    modified = True
            char_data["feature_ids"] = valid_feature_ids

        for feature_id in list(db["features"]):
            path = os.path.join(self.features_dir, f"{feature_id}.png")
            if not os.path.exists(path):
                del db["features"][feature_id]
                modified = True

        is_registered_impl = self.context.is_registered_impl or self.context.is_builtin_impl

        def is_valid_impl(impl_id: str) -> bool:
            return impl_id in db["combos"] or is_registered_impl(impl_id)

        normalized_presets = self._normalize_team_presets(
            db.get("team_presets"), set(db["characters"]), is_valid_impl
        )
        if normalized_presets != db.get("team_presets"):
            db["team_presets"] = normalized_presets
            modified = True

        return modified

    def save(self):
        with self._lock:
            self._save_locked()

    def reload(self):
        with self._lock:
            self._data = self._read_raw_data()
            migration = CustomCharDbMigrator(self.context, DB_SCHEMA_VERSION).migrate(self._data)
            self._data = migration.db
            for diagnostic in migration.diagnostics:
                if self.logger:
                    self.logger.warning(diagnostic)
            if migration.needs_backup and not self._backup_before_migration():
                raise RuntimeError("Failed to create custom character database migration backup")
            if migration.modified:
                self._save_locked()
            if self._normalize_current_data():
                self._save_locked()

    def find_character_id_by_name(self, char_name: str) -> str | None:
        target = self._as_text(char_name).strip()
        if self._is_blank_text(target):
            return None
        with self._lock:
            for char_id, char_data in self._data["characters"].items():
                if (
                    isinstance(char_data, dict)
                    and self._character_name_from_record(char_id, char_data) == target
                ):
                    return char_id
        return None

    def _generate_character_id(self) -> str:
        while True:
            char_id = f"char_{uuid.uuid4().hex}"
            if char_id not in self._data["characters"]:
                return char_id

    def _generate_combo_id(self) -> str:
        while True:
            combo_id = f"combo_{uuid.uuid4().hex}"
            if combo_id not in self._data["combos"] and not self.context.is_builtin_impl(combo_id):
                return combo_id

    def find_combo_id_by_name(self, combo_name: str) -> str:
        combo_name = self._as_text(combo_name).strip()
        if self._is_blank_text(combo_name):
            return ""
        with self._lock:
            for combo_id, combo_data in self._data["combos"].items():
                if isinstance(combo_data, dict) and combo_data.get("name") == combo_name:
                    return combo_id
        return ""

    def add_combo(self, combo_name: str, content: str, combo_id: str | None = None) -> str:
        combo_name = self._as_text(combo_name).strip()
        if self._is_blank_text(combo_name):
            return ""
        with self._lock:
            combo_id = (
                combo_id or self.find_combo_id_by_name(combo_name) or self._generate_combo_id()
            )
            if self.context.is_builtin_impl(combo_id):
                return ""
            self._data["combos"][combo_id] = {"name": combo_name, "content": self._as_text(content)}
            self._save_locked()
            return combo_id

    def update_combo(self, combo_id: str, content: str, combo_name: str | None = None) -> bool:
        combo_id = self._as_text(combo_id)
        with self._lock:
            record = self._data["combos"].get(combo_id)
            if not isinstance(record, dict) or self.context.is_builtin_impl(combo_id):
                return False
            if combo_name is not None and not self._is_blank_text(combo_name):
                record["name"] = self._as_text(combo_name).strip()
            record["content"] = self._as_text(content)
            self._save_locked()
            return True

    def delete_combo(self, combo_id: str):
        with self._lock:
            if self._remove_combos_and_preset_references({self._as_text(combo_id)}):
                self._save_locked()

    def _remove_combos_and_preset_references(self, impl_ids: set[str]) -> bool:
        changed = False
        for impl_id in impl_ids:
            if self._data["combos"].pop(impl_id, None) is not None:
                changed = True
        for preset in self._data["team_presets"]:
            for slot in preset["slots"]:
                if slot["impl_id"] in impl_ids:
                    slot["impl_id"] = ""
                    changed = True
        return changed

    def delete_external_impl_references(self, impl_ids: set[str]) -> bool:
        """Clear deleted external implementations and report changed character bindings."""
        with self._lock:
            changed = self._remove_combos_and_preset_references(impl_ids)
            characters_changed = False
            for record in self._data["characters"].values():
                if record["impl_id"] in impl_ids:
                    record["impl_id"] = ""
                    characters_changed = True
            if changed or characters_changed:
                self._save_locked()
            return characters_changed

    def has_custom_combo(self, combo_id: str) -> bool:
        with self._lock:
            return self._as_text(combo_id) in self._data["combos"]

    def has_impl_id(self, impl_id: str) -> bool:
        is_registered = self.context.is_registered_impl or self.context.is_builtin_impl
        return is_registered(impl_id) or self.has_custom_combo(impl_id)

    def get_combo(self, combo_id: str) -> str:
        with self._lock:
            combo_data = self._data["combos"].get(self._as_text(combo_id))
            return (
                self._as_text(combo_data.get("content", "")) if isinstance(combo_data, dict) else ""
            )

    def get_custom_combo_name(self, combo_id: str) -> str:
        with self._lock:
            combo_data = self._data["combos"].get(self._as_text(combo_id))
            return (
                self._as_text(combo_data.get("name", combo_id))
                if isinstance(combo_data, dict)
                else ""
            )

    def get_custom_combo_items(self) -> list[tuple[str, str]]:
        with self._lock:
            return [
                (self._as_text(data.get("name", combo_id)), combo_id)
                for combo_id, data in self._data["combos"].items()
                if isinstance(data, dict)
            ]

    def create_character(self, char_name: str, impl_id: str) -> str:
        char_name = self._as_text(char_name).strip()
        impl_id = self._as_text(impl_id)
        if self._is_blank_text(char_name):
            return ""
        with self._lock:
            existing_id = self.find_character_id_by_name(char_name)
            if existing_id:
                return existing_id
            if impl_id and not self.has_impl_id(impl_id):
                impl_id = ""
            char_id = self._generate_character_id()
            self._data["characters"][char_id] = {
                "name": char_name,
                "impl_id": impl_id,
                "feature_ids": [],
            }
            self._save_locked()
            return char_id

    def update_character(self, char_id: str, char_name=None, impl_id=None) -> bool:
        with self._lock:
            record = self._data["characters"].get(char_id)
            if not isinstance(record, dict):
                return False
            if char_name is not None:
                char_name = self._as_text(char_name).strip()
                if self._is_blank_text(char_name):
                    return False
                existing_id = self.find_character_id_by_name(char_name)
                if existing_id and existing_id != char_id:
                    return False
                record["name"] = char_name
            if impl_id is not None:
                impl_id = self._as_text(impl_id)
                record["impl_id"] = impl_id if not impl_id or self.has_impl_id(impl_id) else ""
            self._save_locked()
            return True

    def delete_character(self, char_id: str) -> list[str]:
        with self._lock:
            record = self._data["characters"].pop(char_id, None)
            if not isinstance(record, dict):
                return []
            feature_ids = list(record.get("feature_ids", []))
            for feature_id in feature_ids:
                self._data["features"].pop(feature_id, None)
            for preset in self._data["team_presets"]:
                for slot in preset["slots"]:
                    if slot["char_id"] == char_id:
                        slot["char_id"] = ""
            self._save_locked()
            return feature_ids

    def add_feature(self, char_id: str, feature_id: str, width=0, height=0) -> bool:
        with self._lock:
            record = self._data["characters"].get(char_id)
            if not isinstance(record, dict):
                return False
            self._data["features"][feature_id] = {"width": width, "height": height}
            record.setdefault("feature_ids", []).append(feature_id)
            self._save_locked()
            return True

    def remove_feature(self, char_id: str, feature_id: str) -> bool:
        with self._lock:
            record = self._data["characters"].get(char_id)
            if not isinstance(record, dict) or feature_id not in record.get("feature_ids", []):
                return False
            record["feature_ids"].remove(feature_id)
            self._data["features"].pop(feature_id, None)
            self._save_locked()
            return True

    def get_feature_ids(self) -> list[str]:
        with self._lock:
            feature_ids = set(self._data["features"].keys())
            for char_data in self._data["characters"].values():
                if isinstance(char_data, dict):
                    feature_ids.update(char_data.get("feature_ids", []))
            return list(feature_ids)

    def get_feature_info(self, feature_id: str) -> dict:
        with self._lock:
            feature_info = self._data["features"].get(feature_id, {})
            return dict(feature_info) if isinstance(feature_info, dict) else {}

    def get_character_feature_snapshot(self) -> dict[str, list[str]]:
        with self._lock:
            return {
                char_id: list(char_data.get("feature_ids", []))
                for char_id, char_data in self._data["characters"].items()
                if isinstance(char_data, dict)
            }

    def get_character_records(self) -> dict[str, dict]:
        with self._lock:
            return {
                char_id: dict(char_data)
                for char_id, char_data in self._data["characters"].items()
                if isinstance(char_data, dict)
            }

    def get_character_record(self, char_id: str) -> dict | None:
        with self._lock:
            record = self._data["characters"].get(char_id)
            return dict(record) if isinstance(record, dict) else None

    def get_team_presets(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": preset["id"],
                    "name": preset["name"],
                    "slots": [dict(slot) for slot in preset["slots"]],
                    "is_fixed": preset["is_fixed"],
                }
                for preset in self._data["team_presets"]
            ]

    def create_team_preset(self, name: str) -> dict:
        with self._lock:
            used_names = {preset["name"] for preset in self._data["team_presets"]}
            preset = self._default_team_preset(
                self._new_preset_id(), self._unique_name(self._as_text(name), used_names)
            )
            self._data["team_presets"].append(preset)
            self._save_locked()
            return {**preset, "slots": [dict(slot) for slot in preset["slots"]]}

    def update_team_preset(self, preset_id: str, name=None, slots=None) -> bool:
        with self._lock:
            preset = next(
                (preset for preset in self._data["team_presets"] if preset["id"] == preset_id), None
            )
            if preset is None:
                return False

            new_name = preset["name"]
            if name is not None:
                new_name = self._as_text(name).strip()
                if not new_name or any(
                    other["id"] != preset_id and other["name"] == new_name
                    for other in self._data["team_presets"]
                ):
                    return False

            normalized_slots = None
            if slots is not None:
                if not isinstance(slots, list):
                    return False
                seen_char_ids = set()
                normalized_slots = []
                for index in range(4):
                    raw_slot = slots[index] if index < len(slots) else {}
                    slot = self._normalize_team_slot(raw_slot)
                    char_id = slot["char_id"]
                    impl_id = slot["impl_id"]
                    if impl_id and not self.has_impl_id(impl_id):
                        return False
                    if char_id and (
                        char_id not in self._data["characters"] or char_id in seen_char_ids
                    ):
                        return False
                    if char_id:
                        seen_char_ids.add(char_id)
                    normalized_slots.append(slot)

            if name is not None:
                preset["name"] = new_name
            if normalized_slots is not None:
                preset["slots"] = normalized_slots

            self._save_locked()
            return True

    def delete_team_preset(self, preset_id: str) -> bool:
        with self._lock:
            presets = self._data["team_presets"]
            for index, preset in enumerate(presets):
                if preset["id"] == preset_id:
                    presets.pop(index)
                    self._save_locked()
                    return True
            return False

    def apply_team_preset(self, preset_id: str, fixed=False) -> list[str] | None:
        with self._lock:
            preset = next(
                (preset for preset in self._data["team_presets"] if preset["id"] == preset_id), None
            )
            if preset is None:
                return None

            applied_char_ids = []
            for slot in preset["slots"]:
                char_id = slot["char_id"]
                impl_id = slot["impl_id"]
                if char_id and impl_id and char_id in self._data["characters"]:
                    self._data["characters"][char_id]["impl_id"] = impl_id
                    applied_char_ids.append(char_id)

            if fixed:
                for item in self._data["team_presets"]:
                    item["is_fixed"] = item["id"] == preset_id
            self._save_locked()
            return applied_char_ids

    def clear_fixed_team_preset(self) -> bool:
        with self._lock:
            changed = False
            for preset in self._data["team_presets"]:
                if preset["is_fixed"]:
                    preset["is_fixed"] = False
                    changed = True
            if changed:
                self._save_locked()
            return changed

    def get_fixed_team(self) -> dict:
        """Return the fixed team preset, if one is selected."""
        with self._lock:
            preset = next(
                (preset for preset in self._data["team_presets"] if preset["is_fixed"]), None
            )
            if preset is None:
                return self._default_fixed_team()
            return {"enabled": True, "slots": [dict(slot) for slot in preset["slots"]]}
