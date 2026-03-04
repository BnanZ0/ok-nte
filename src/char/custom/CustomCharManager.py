import json
import os
import uuid
from threading import Lock

import cv2
from ok import Logger, og

logger = Logger.get_logger(__name__)

CUSTOM_CHARS_DIR = "custom_chars"
FEATURES_DIR = os.path.join(CUSTOM_CHARS_DIR, "features")
DB_PATH = os.path.join(CUSTOM_CHARS_DIR, "db.json")


class CustomCharManager:
    _instance = None
    _lock = Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(CustomCharManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if hasattr(self, 'initialized') and self.initialized:
            return
        os.makedirs(FEATURES_DIR, exist_ok=True)
        self.db = {
            "combos": {},
            "characters": {}
        }
        self.load_db()
        self.validate_db()
        self.initialized = True

    def load_db(self):
        if os.path.exists(DB_PATH):
            try:
                with open(DB_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.db["combos"] = data.get("combos", {})
                    self.db["characters"] = data.get("characters", {})
            except Exception as e:
                logger.error(f"Failed to load custom char DB: {e}")

    def validate_db(self):
        modified = False
        for char_name, char_data in self.db["characters"].items():
            valid_fids = []
            for fid in char_data.get("feature_ids", []):
                path = os.path.join(FEATURES_DIR, f"{fid}.png")
                if os.path.exists(path):
                    valid_fids.append(fid)
                else:
                    modified = True
            char_data["feature_ids"] = valid_fids
            
        if modified:
            self.save_db()

    def save_db(self):
        try:
            with open(DB_PATH, "w", encoding="utf-8") as f:
                json.dump(self.db, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save custom char DB: {e}")

    def add_combo(self, combo_name, content):
        """添加或更新出招表"""
        self.db["combos"][combo_name] = content
        self.save_db()

    def delete_combo(self, combo_name):
        """删除出招表"""
        if combo_name in self.db["combos"]:
            del self.db["combos"][combo_name]
            self.save_db()

    def get_combo(self, combo_name):
        """获取出招表"""
        return self.db["combos"].get(combo_name, "")

    def get_all_combos(self):
        combos = list(self.db["combos"].keys())
        # Append built-in scripts
        try:
            from src.char.CharFactory import char_dict
            from src.ui.CharManagerTab import get_builtin_prefix
            prefix = get_builtin_prefix()
            for c_name in char_dict.keys():
                if c_name != "char_default":
                    if og.app.locale.name() == "zh_CN" and c_name in char_dict and "cn_name" in char_dict[c_name]:
                        display_name = char_dict[c_name]["cn_name"]
                        combos.append(f"{prefix}{display_name} ({c_name})")
                    else:
                        combos.append(f"{prefix}{c_name}")
        except ImportError:
            pass
        return combos

    def add_character(self, char_name, combo_name):
        """添加或更新角色属性 (不包含特征图)"""
        if char_name not in self.db["characters"]:
            self.db["characters"][char_name] = {
                "combo_name": combo_name,
                "feature_ids": []
            }
        else:
            self.db["characters"][char_name]["combo_name"] = combo_name
        self.save_db()

    def delete_character(self, char_name):
        """删除角色及其所有特征图，不影响出招表"""
        if char_name in self.db["characters"]:
            feature_ids = self.db["characters"][char_name].get("feature_ids", [])
            for fid in feature_ids:
                self.delete_feature_image(fid)
            del self.db["characters"][char_name]
            self.save_db()

    def add_feature_to_character(self, char_name, image_mat):
        """为角色保存一张截图并关联特征 UUID"""
        fid = f"feat_{uuid.uuid4().hex}"
        self.save_feature_image(fid, image_mat)
        
        if char_name not in self.db["characters"]:
            self.db["characters"][char_name] = {
                "combo_name": "",
                "feature_ids": []
            }
            
        if "feature_ids" not in self.db["characters"][char_name]:
            self.db["characters"][char_name]["feature_ids"] = []
            
        self.db["characters"][char_name]["feature_ids"].append(fid)
        self.save_db()
        return fid

    def remove_feature_from_character(self, char_name, feature_id):
        """从角色中移除某个特征"""
        if char_name in self.db["characters"]:
            feature_ids = self.db["characters"][char_name].get("feature_ids", [])
            if feature_id in feature_ids:
                feature_ids.remove(feature_id)
                self.delete_feature_image(feature_id)
                self.save_db()

    def save_feature_image(self, feature_id, image_mat):
        """保存特征图"""
        path = os.path.join(FEATURES_DIR, f"{feature_id}.png")
        cv2.imwrite(path, image_mat)

    def delete_feature_image(self, feature_id):
        """删除特征图文件"""
        path = os.path.join(FEATURES_DIR, f"{feature_id}.png")
        if os.path.exists(path):
            os.remove(path)

    def load_feature_image(self, feature_id):
        """读取特征图"""
        path = os.path.join(FEATURES_DIR, f"{feature_id}.png")
        if os.path.exists(path):
            return cv2.imread(path)
        return None

    def match_feature(self, new_image_mat, threshold=0.8):
        """比对新截图与所有数据库内特征图，返回(是/否匹配, 匹配到的角色名, 相似度)"""
        best_match_char = None
        best_similarity = 0.0

        for char_name, char_data in self.db["characters"].items():
            for fid in char_data.get("feature_ids", []):
                saved_img = self.load_feature_image(fid)
                if saved_img is not None:
                    # Resize to ensure matchTemplate works if slightly different
                    if saved_img.shape != new_image_mat.shape:
                        resized_saved = cv2.resize(saved_img, (new_image_mat.shape[1], new_image_mat.shape[0]))
                    else:
                        resized_saved = saved_img
                    
                    # Compute similarity using matchTemplate (Normalized Cross Correlation)
                    res = cv2.matchTemplate(new_image_mat, resized_saved, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                    if max_val > best_similarity:
                        best_similarity = max_val
                        best_match_char = char_name

        if best_similarity >= threshold:
            return True, best_match_char, best_similarity
        return False, None, best_similarity

    def get_all_characters(self):
        """获取所有角色数据"""
        return self.db["characters"]

    def get_character_info(self, char_name):
        return self.db["characters"].get(char_name, None)
