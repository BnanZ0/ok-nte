import ast
import re

from src.char.BaseChar import BaseChar
from src.char.custom.CustomCharManager import CustomCharManager
from typing import NamedTuple, Callable, List, Any

class Cmd(NamedTuple):
    name: str
    func: Callable[..., Any]
    params: str
    doc: str
    example: str

class CustomChar(BaseChar):
    """
    用户自定义的出招表角色。
    它从 CustomCharManager 获取出招表，并在 do_perform 中解析执行。
    """
    def __init__(self, task, index, char_name=None, confidence=1):
        super().__init__(task, index, char_name, confidence)
        self.manager = CustomCharManager()
        self.combo_name = ""
        self.combo_str = ""
        self.parsed_combo = []
        self._load_combo()

    def _load_combo(self):
        char_info = self.manager.get_character_info(self.char_name)
        if char_info:
            combo_name = char_info.get("combo_name", "")
            self.combo_name = combo_name
            self.combo_str = self.manager.get_combo(combo_name)
            self._compile_combo()
        else:
            self.logger.warning(f"No custom char info found for {self.char_name}")

    def do_perform(self):
        """覆盖默认战斗循环，执行解析出来的新出招"""
        if not self.parsed_combo:
            super().do_perform()  # 降级到默认
            return
            
        self._execute_parsed_combo()

    @classmethod
    def get_command_definitions(cls) -> List[Cmd]:
        # 统一在此处配置所有可用指令：指令名、对应内置函数
        # 这些文案在运行时不会变动：集中成“常量”避免静态扫描工具提示重复字面量，
        # 同时保持下方清单的直观可读性。
        PARAM_NONE = "无参数"
        PARAM_OPT_DURATION = "持续时间(s)，选填"
        PARAM_OPT_KEY = "按键，选填"
        PARAM_REQ_KEY = "按键，必填"
        DOC_MOUSE_BUTTON = "鼠标按键left、right、middle, 不填默认left"
        return [
            Cmd("skill", cls.click_skill, PARAM_NONE, "释放技能", "skill"),
            Cmd("ultimate", cls.click_ultimate, PARAM_NONE, "释放终结技", "ultimate"),
            Cmd("l_click", cls.smart_left_click, PARAM_OPT_DURATION, "鼠标左键。带参数则连点鼠标左键指定秒数，无参数为单次点按", "l_click, l_click(3)"),
            Cmd("r_click", cls.smart_right_click, PARAM_OPT_DURATION, "鼠标右键。带参数则连点鼠标右键指定秒数，无参数为单次点按", "r_click, r_click(2)"),
            Cmd("l_hold", cls.heavy_attack, PARAM_OPT_DURATION, "按住鼠标左键。带参数则指定秒数", "l_hold, l_hold(2)"),
            Cmd("r_hold", cls.hold_right_click, PARAM_OPT_DURATION, "按住鼠标右键。带参数则指定秒数", "r_hold, r_hold(2)"),
            Cmd("wait", cls.sleep, "等待时间(s)，必填", "休眠停顿等待指定时间", "wait(0.5)"),
            Cmd("jump", cls.jump, PARAM_NONE, "跳跃一下", "jump"),
            Cmd("walk", cls.walk, "按键方向、持续时间(s)，必填", "控制角色向指定方向行走", "walk(w, 0.2)"),
            Cmd("mousedown", cls.mousedown, PARAM_OPT_KEY, DOC_MOUSE_BUTTON, "mousedown, mousedown(left)"),
            Cmd("mouseup", cls.mouseup, PARAM_OPT_KEY, DOC_MOUSE_BUTTON, "mouseup, mouseup(right)"),
            Cmd("click", cls.command_click, PARAM_OPT_KEY, DOC_MOUSE_BUTTON, "click, click(middle)"),
            Cmd("keydown", cls.keydown, PARAM_REQ_KEY, "按下按键", "keydown(a)"),
            Cmd("keyup", cls.keyup, PARAM_REQ_KEY, "松开按键", "keyup(d)"),
            Cmd("keypress", cls.keypress, PARAM_REQ_KEY, "按下并松开按键", "keypress(f1)"),
        ]

    def _compile_combo(self):
        """将字符串代码预编译为可以直接执行的 [(target, args, kwargs, cmd)] 缓存结构"""
        self.parsed_combo = []
        if not self.combo_str:
            return

        # 仅在编译时提取别名映射即可，无需战斗中高频执行
        aliases = {cmd.name: cmd.func for cmd in self.get_command_definitions()}

        commands = []
        paren_level = 0
        current_cmd = []
        for char in self.combo_str:
            if char == '(':
                paren_level += 1
            elif char == ')':
                paren_level -= 1
            
            if char == ',' and paren_level == 0:
                cmd = "".join(current_cmd).strip()
                if cmd:
                    commands.append(cmd)
                current_cmd = []
            else:
                current_cmd.append(char)
        
        last_cmd = "".join(current_cmd).strip()
        if last_cmd:
            commands.append(last_cmd)

        for cmd in commands:
            # 检查是否有括号以解析参数
            match = re.match(r"([a-zA-Z_]+)(?:\((.*?)\))?", cmd)
            if not match:
                self.logger.error(f"Invalid combo command: {cmd}")
                continue

            func_name = match.group(1)
            args_str = match.group(2)

            # 获取目标（真实的函数对象，或者字符串别名）
            target = aliases.get(func_name, func_name)

            # 解析并预置参数
            args = []
            kwargs = {}
            if args_str:
                params = [p.strip() for p in args_str.split(",")]
                for p in params:
                    if not p:
                        continue
                    if "=" in p:
                        k, v = p.split("=", 1)
                        kwargs[k.strip()] = self._parse_val(v.strip())
                    else:
                        args.append(self._parse_val(p))

            # 存入执行缓存
            self.parsed_combo.append((func_name, target, args, kwargs, cmd))

    def _execute_parsed_combo(self):
        """战斗时极速遍历并执行已缓存的指令队列"""
        for func_name, target, args, kwargs, cmd in self.parsed_combo:
            try:
                if callable(target):
                    self.logger.debug(f"Executing Custom Combo Command: {func_name}(*{args}, **{kwargs})")
                    target(self, *args, **kwargs)
                else:
                    if hasattr(self, target):
                        func = getattr(self, target)
                        self.logger.debug(f"Executing Custom Combo Command: {target}(*{args}, **{kwargs})")
                        func(*args, **kwargs)
                    else:
                        self.logger.warning(f"Unknown command in combo: {target}")
            except Exception as e:
                self.logger.error(f"Error executing command '{cmd}': {e}")

            # 中途打断逻辑
            self.check_combat()

    def _parse_val(self, val_str):
        # 使用安全的 ast 解析字面量 (整数、浮点、布尔等)
        val_str = val_str.strip()
        if not val_str:
            return ""
        try:
            return ast.literal_eval(val_str)
        except (ValueError, SyntaxError):
            # 如果是无引号的裸写字符串如 "left"，直接当字符串兜底返回
            return val_str

    @classmethod
    def get_available_commands(cls):
        """
        手动定义对用户可视化/输入框提示的出招表指令及文档说明。
        """
        return cls.get_command_definitions()

    def jump(self):
        self.send_key("space")
        
    def smart_left_click(self, duration=None):
        if duration is None:
            self.normal_attack()
        else:
            self.continues_normal_attack(duration)

    def smart_right_click(self, duration=None):
        if duration is None:
            self.click(key="right")
        else:
            self.continues_right_click(duration)

    def hold_right_click(self, duration=0.01):
        self.click(key="right", down_time=duration)

    def walk(self, direction, duration):
        self.send_key(direction, down_time=duration)

    def mousedown(self, key="left"):
        self.task.mouse_down(key=key)

    def mouseup(self, key="left"):
        self.task.mouse_up(key=key)

    def command_click(self, key="left"):
        self.task.click(key=key)

    def keydown(self, key):
        self.task.send_key_down(key)

    def keyup(self, key):
        self.task.send_key_up(key)

    def keypress(self, key):
        self.task.send_key(key=key)
