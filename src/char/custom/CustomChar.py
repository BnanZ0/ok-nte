import re

from src.char.BaseChar import BaseChar
from src.char.custom.CustomCharManager import CustomCharManager

class CustomChar(BaseChar):
    """
    用户自定义的出招表角色。
    它从 CustomCharManager 获取出招表，并在 do_perform 中解析执行。
    """
    def __init__(self, task, index, char_name=None, confidence=1):
        super().__init__(task, index, char_name, confidence)
        self.manager = CustomCharManager()
        self.combo_str = ""
        self._load_combo()

    def _load_combo(self):
        char_info = self.manager.get_character_info(self.char_name)
        if char_info:
            combo_name = char_info.get("combo_name", "")
            self.combo_str = self.manager.get_combo(combo_name)
        else:
            self.logger.warning(f"No custom char info found for {self.char_name}")

    def do_perform(self):
        """覆盖默认战斗循环，执行解析出来的新出招"""
        if not self.combo_str:
            super().do_perform()  # 降级到默认
            return
            
        self._execute_combo(self.combo_str)
        self.switch_next_char()

    def _execute_combo(self, combo_str):
        """解析如 `intro, e, wait(0.5), q, a(3), z` 的指令集。"""
        # 常见映射快捷方式
        aliases = {
            "e": "click_skill",
            "q": "click_ultimate",
            "z": "heavy_attack",
            "intro": "wait_intro",
            "a": "continues_normal_attack",
            "wait": "sleep"
        }

        commands = [cmd.strip() for cmd in combo_str.split(",") if cmd.strip()]
        for cmd in commands:
            # 检查是否有括号以解析参数
            match = re.match(r"([a-zA-Z_]+)(?:\((.*?)\))?", cmd)
            if not match:
                self.logger.error(f"Invalid combo command: {cmd}")
                continue

            func_name = match.group(1)
            args_str = match.group(2)

            # 解析别名
            func_name = aliases.get(func_name, func_name)

            # 查找同名方法
            if hasattr(self, func_name):
                func = getattr(self, func_name)
                # 解析参数
                args = []
                kwargs = {}
                if args_str:
                    params = [p.strip() for p in args_str.split(",")]
                    for p in params:
                        if "=" in p:
                            k, v = p.split("=", 1)
                            kwargs[k.strip()] = self._parse_val(v.strip())
                        else:
                            args.append(self._parse_val(p))
                
                self.logger.debug(f"Executing Custom Combo Command: {func_name}(*{args}, **{kwargs})")
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    self.logger.error(f"Error executing command '{cmd}': {e}")
            else:
                self.logger.warning(f"Unknown command in combo: {func_name}")

            # 如果中途人物被切走或死亡，可以加上打断逻辑
            self.check_combat()

    def _parse_val(self, val_str):
        # 简单将字符串参数解析为 int, float, bool, 否则原样返回
        val_str = val_str.strip()
        if val_str.lower() == "true":
            return True
        if val_str.lower() == "false":
            return False
        try:
            if "." in val_str:
                return float(val_str)
            return int(val_str)
        except ValueError:
            # 去除引号 (如果用户写了 "string")
            if (val_str.startswith('"') and val_str.endswith('"')) or (val_str.startswith("'") and val_str.endswith("'")):
                val_str = val_str[1:-1]
            return val_str

    @classmethod
    def get_available_commands(cls):
        """反射获取 BaseChar 中对用户可用的方法和文档。用于 UI 提示。"""
        # 定义需要排除的内置方法或不想暴露的方法前缀
        exclude_prefix = ("do_", "get_", "count_", "check_", "has_", "is_", "alert_", "wait_until", "reset_state", "send_")
        allowed_methods = []

        for method_name in dir(BaseChar):
            if method_name.startswith("_"):
                continue
            if any(method_name.startswith(p) for p in exclude_prefix):
                continue
            
            func = getattr(BaseChar, method_name)
            if callable(func):
                doc = func.__doc__ or "无描述"
                # 只截取文档第一行
                doc = doc.strip().split("\n")[0]
                allowed_methods.append({
                    "name": method_name,
                    "doc": doc
                })
                
        # 加几个别名的文档
        allowed_methods.insert(0, {"name": "wait", "doc": "休眠等待，例如 wait(0.5) 表示停顿 0.5 秒"})
        allowed_methods.insert(0, {"name": "z", "doc": "长按重击 (等同于 heavy_attack)"})
        allowed_methods.insert(0, {"name": "a(持续秒数)", "doc": "连续普通攻击，例如 a(3) 表示连A 3秒 (等同于 continues_normal_attack(3))"})
        allowed_methods.insert(0, {"name": "q", "doc": "释放共鸣解放大招 (等同于 click_ultimate)"})
        allowed_methods.insert(0, {"name": "e", "doc": "释放共鸣技能 (等同于 click_skill)"})
        allowed_methods.insert(0, {"name": "intro", "doc": "等待角色入场动画，如果你把这个角色放在 QTE / 连携出场时，必须加这个 (等同于 wait_intro)"})
        
        return allowed_methods
