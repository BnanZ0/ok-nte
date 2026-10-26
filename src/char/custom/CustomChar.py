import ast
from typing import Any, Callable, List, NamedTuple

from ok import TaskDisabledException

from src.char.BaseChar import BaseChar
from src.char.custom.CustomCharManager import CustomCharManager
from src.combat.planner import (
    Planner,
    RoleProfile,
)


class Cmd(NamedTuple):
    name: str
    func: Callable[..., Any]
    params: str
    doc: str
    example: str
    if_capable: bool = False


_RETURN_SIGNAL = object()


class CustomChar(BaseChar):
    """
    用户自定义的出招表角色。
    它从 CustomCharManager 获取出招表，并作为 planner 动作执行。
    """

    def __init__(self, task, index, char_id="", impl_id: str = "", confidence=1):
        super().__init__(task, index, char_id, confidence)
        self.manager = CustomCharManager()
        self.impl_id = impl_id
        self.combo_str = ""
        self.parsed_combo = []
        self._held_keys = set()
        self._held_mouse_buttons = set()
        self._load_combo()

    @property
    def name(self):
        """获取角色类名作为其名称。

        Returns:
            str: 角色类名字符串。
        """
        return super().name + "_" + str(self.index)

    def _load_combo(self):
        if self.impl_id:
            self.combo_str = self.manager.get_combo(self.impl_id)
            self._compile_combo()
        else:
            self.logger.warning(f"No custom char info found for {self.ufn_name}")

    def describe_role(self):
        return RoleProfile(role=Planner.Role.SUB_DPS)

    def combat_plan(self):
        if not self.parsed_combo:
            return super().combat_plan()

        plan = self.plan()
        tags = {Planner.ActionTag.LEGACY_COMBO, Planner.ActionTag.DAMAGE}
        reason = "legacy combo ready"
        if self.skill_available():
            tags.add(Planner.ActionTag.SKILL_ACTION)
            reason = "legacy combo skill available"
        if self.ultimate_available():
            tags.add(Planner.ActionTag.ULTIMATE_ACTION)

        plan.register(
            self.execute_legacy_combo_action,
            name="legacy_combo",
            tags=tags,
            slot=Planner.ActionSlot.LEGACY_COMBO,
            reason=reason,
        )
        return plan

    def execute_legacy_combo_action(self):
        self._execute_parsed_combo()
        return True

    @classmethod
    def get_command_definitions(cls) -> List[Cmd]:
        # 统一在此处配置所有可用指令：指令名、对应内置函数
        PARAM_NONE = "无参数"
        PARAM_OPT_DURATION = "持续时间(s)，选填"
        PARAM_OPT_KEY = "按键，选填"
        PARAM_REQ_KEY = "按键，必填"
        DOC_MOUSE_BUTTON = "鼠标按键left、right、middle, 不填默认left"
        return [
            Cmd(
                "skill",
                cls.custom_click_skill,
                "按下秒数, 默认0.01秒",
                "释放技能",
                "skill, skill(0.5)",
                True,
            ),
            Cmd("ultimate", cls.click_ultimate, PARAM_NONE, "释放终结技", "ultimate", True),
            Cmd("arc", cls.click_arc, PARAM_NONE, "释放弧盘技能", "arc", False),
            Cmd(
                "l_click",
                cls.smart_left_click,
                PARAM_OPT_DURATION,
                "鼠标左键。带参数则连点鼠标左键指定秒数，无参数为单次点按",
                "l_click, l_click(3)",
            ),
            Cmd(
                "r_click",
                cls.smart_right_click,
                PARAM_OPT_DURATION,
                "鼠标右键。带参数则连点鼠标右键指定秒数，无参数为单次点按",
                "r_click, r_click(2)",
            ),
            Cmd(
                "l_hold",
                cls.heavy_attack,
                PARAM_OPT_DURATION,
                "按住鼠标左键。带参数则指定秒数",
                "l_hold, l_hold(2)",
            ),
            Cmd(
                "r_hold",
                cls.hold_right_click,
                PARAM_OPT_DURATION,
                "按住鼠标右键。带参数则指定秒数",
                "r_hold, r_hold(2)",
            ),
            Cmd("wait", cls.sleep, "等待时间(s)，必填", "休眠停顿等待指定时间", "wait(0.5)"),
            Cmd("jump", cls.jump, PARAM_NONE, "跳跃一下", "jump"),
            Cmd(
                "walk",
                cls.walk,
                "按键方向、持续时间(s)，必填",
                "控制角色向指定方向行走",
                "walk(w, 0.2)",
            ),
            Cmd(
                "mousedown",
                cls.mousedown,
                PARAM_OPT_KEY,
                DOC_MOUSE_BUTTON,
                "mousedown, mousedown(left)",
            ),
            Cmd("mouseup", cls.mouseup, PARAM_OPT_KEY, DOC_MOUSE_BUTTON, "mouseup, mouseup(right)"),
            Cmd(
                "click", cls.command_click, PARAM_OPT_KEY, DOC_MOUSE_BUTTON, "click, click(middle)"
            ),
            Cmd("keydown", cls.keydown, PARAM_REQ_KEY, "按下按键", "keydown(a)"),
            Cmd("keyup", cls.keyup, PARAM_REQ_KEY, "松开按键", "keyup(d)"),
            Cmd("keypress", cls.keypress, PARAM_REQ_KEY, "按下并松开按键", "keypress(f1)"),
        ]

    def _compile_combo(self):
        """将字符串代码预编译为可以直接执行的 [(target, args, kwargs, cmd)] 缓存结构"""
        self.parsed_combo = []
        if not self.combo_str:
            return

        parsed_combo, error = self.compile_combo_text(self.combo_str)
        if error:
            self.logger.error(f"Syntax error parsing combo '{self.combo_str}': {error}")
            return
        if not parsed_combo:
            self.logger.warning("Parsed combo is empty")
        self.parsed_combo = parsed_combo

    @staticmethod
    def _node_loc(node) -> str:
        line = getattr(node, "lineno", None)
        col = getattr(node, "col_offset", None)
        if line is None:
            return ""
        col_num = (col + 1) if isinstance(col, int) else 1
        return f"line {line}, column {col_num}"

    @staticmethod
    def _syntax_error_text(error: SyntaxError) -> str:
        line = error.lineno or 1
        col = error.offset or 1
        return f"line {line}, column {col}: {error.msg}"

    @classmethod
    def _parse_node_value(cls, node):
        try:
            return True, ast.literal_eval(node), ""
        except (ValueError, SyntaxError):
            if isinstance(node, ast.Name):
                return True, node.id, ""
            return False, None, f"{cls._node_loc(node)}: unsupported value expression"

    @classmethod
    def _resolve_target(cls, func_name: str, aliases: dict[str, Callable[..., Any]]):
        target = aliases.get(func_name, func_name)
        if not callable(target) and not hasattr(cls, func_name):
            return None
        return target

    @classmethod
    def _parse_if_statement(
        cls,
        node: ast.If,
        combo_str: str,
        aliases: dict[str, Callable[..., Any]],
        if_capable_map: dict[str, bool],
    ):
        cond_node = node.test
        cond_cmd, err = cls._parse_command_node(
            cond_node,
            combo_str=combo_str,
            aliases=aliases,
            if_capable_map=if_capable_map,
        )
        if err or not cond_cmd:
            return None, err

        cond_name = cond_cmd[0]
        if not if_capable_map.get(cond_name, False):
            return (
                None,
                f"{cls._node_loc(cond_node)}: command '{cond_name}' is not enabled as if condition",
            )

        then_cmds, err = cls._parse_if_branch(
            node.body,
            combo_str,
            aliases,
            if_capable_map,
        )
        if err:
            return None, err
        else_cmds, err = cls._parse_if_branch(
            node.orelse,
            combo_str,
            aliases,
            if_capable_map,
            allow_empty=True,
        )
        if err:
            return None, err

        cmd_text = ast.get_source_segment(combo_str, node) or "if"
        return (
            "if",
            cls._execute_if_statement,
            [cond_cmd, then_cmds, else_cmds],
            {},
            cmd_text,
        ), None

    @classmethod
    def _parse_if_branch(
        cls,
        statements,
        combo_str: str,
        aliases: dict[str, Callable[..., Any]],
        if_capable_map: dict[str, bool],
        allow_empty: bool = False,
    ):
        if not statements and allow_empty:
            return [], None
        if len(statements) != 1:
            return None, "if branches must contain one command list or return"

        statement = statements[0]
        if isinstance(statement, ast.Return):
            if statement.value is not None:
                return None, f"{cls._node_loc(statement)}: return does not accept a value"
            return [("return", cls._return_combo, [], {}, "return")], None
        if not isinstance(statement, ast.Expr):
            return None, f"{cls._node_loc(statement)}: unsupported if branch syntax"

        nodes = (
            statement.value.elts if isinstance(statement.value, ast.Tuple) else [statement.value]
        )
        commands = []
        for node in nodes:
            command, err = cls._parse_command_node(
                node,
                combo_str=combo_str,
                aliases=aliases,
                if_capable_map=if_capable_map,
            )
            if err:
                return None, err
            commands.append(command)
        return commands, None

    @classmethod
    def _parse_command_node(
        cls,
        node,
        combo_str: str,
        aliases: dict[str, Callable[..., Any]],
        if_capable_map: dict[str, bool],
    ):
        func_name = ""
        args = []
        kwargs = {}

        if isinstance(node, ast.Name):
            func_name = node.id
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return None, f"{cls._node_loc(node)}: unsupported callable expression"
            func_name = node.func.id

            for arg in node.args:
                ok, value, err = cls._parse_node_value(arg)
                if not ok:
                    return None, err
                args.append(value)

            for kw in node.keywords:
                if kw.arg is None:
                    return None, f"{cls._node_loc(kw)}: **kwargs syntax is not supported"
                ok, value, err = cls._parse_node_value(kw.value)
                if not ok:
                    return None, err
                kwargs[kw.arg] = value
        else:
            return None, f"{cls._node_loc(node)}: unsupported syntax '{type(node).__name__}'"

        if not func_name:
            return None, f"{cls._node_loc(node)}: command name is required"

        target = cls._resolve_target(func_name, aliases)
        if target is None:
            return None, f"{cls._node_loc(node)}: unknown command '{func_name}'"

        cmd_text = ast.get_source_segment(combo_str, node) or func_name
        return (func_name, target, args, kwargs, cmd_text), None

    @classmethod
    def compile_combo_text(cls, combo_str: str):
        """
        Compile combo text into executable tuples.
        Returns (parsed_combo, error_message). error_message is None when valid.
        """
        parsed_combo = []
        if not combo_str or not combo_str.strip():
            return parsed_combo, None

        command_defs = cls.get_command_definitions()
        aliases = {cmd.name: cmd.func for cmd in command_defs}
        if_capable_map = {cmd.name: cmd.if_capable for cmd in command_defs}

        try:
            tree = ast.parse(combo_str)
        except SyntaxError as error:
            return [], cls._syntax_error_text(error)

        for stmt in tree.body:
            if isinstance(stmt, ast.If):
                parsed_command, err = cls._parse_if_statement(
                    stmt,
                    combo_str=combo_str,
                    aliases=aliases,
                    if_capable_map=if_capable_map,
                )
                if err:
                    return [], err
                parsed_combo.append(parsed_command)
                continue
            if isinstance(stmt, ast.Return):
                return [], f"{cls._node_loc(stmt)}: return is only allowed inside if or else"
            if not isinstance(stmt, ast.Expr):
                return [], f"{cls._node_loc(stmt)}: only commands and if statements are allowed"

            expr = stmt.value
            nodes = expr.elts if isinstance(expr, ast.Tuple) else [expr]
            for node in nodes:
                parsed_command, err = cls._parse_command_node(
                    node,
                    combo_str=combo_str,
                    aliases=aliases,
                    if_capable_map=if_capable_map,
                )
                if err:
                    return [], err
                parsed_combo.append(parsed_command)

        return parsed_combo, None

    @classmethod
    def validate_combo_syntax(cls, combo_str: str):
        _, error = cls.compile_combo_text(combo_str)
        return error is None, error

    def _execute_parsed_combo(self):
        """战斗时极速遍历并执行已缓存的指令队列"""
        try:
            for command in self.parsed_combo:
                try:
                    result = self._execute_compiled_command(command)
                    if result is _RETURN_SIGNAL:
                        return
                except TaskDisabledException:
                    raise
                except Exception as e:
                    cmd = command[4] if len(command) >= 5 else "unknown"
                    self.logger.error(f"Error executing command '{cmd}'", e)

                # 中途打断逻辑
                self.check_combat()
        finally:
            self._release_held_mouse_buttons()
            self._release_held_keys()

    def _release_held_keys(self):
        """释放本次自定义连招通过 keydown 按住, 但未显式 keyup 的按键。"""
        for key in tuple(self._held_keys):
            try:
                self.task.send_key_up(key)
            except Exception as e:
                self.logger.error(f"Failed to release custom combo key '{key}'", e)
            else:
                self._held_keys.discard(key)

    def _release_held_mouse_buttons(self):
        """释放本次自定义连招通过 mousedown 按住, 但未显式 mouseup 的鼠标键。"""
        for key in tuple(self._held_mouse_buttons):
            try:
                self.task.mouse_up(key=key)
            except Exception as e:
                self.logger.error(f"Failed to release custom combo mouse button '{key}'", e)
            else:
                self._held_mouse_buttons.discard(key)

    def _execute_compiled_command(self, command):
        func_name, target, args, kwargs, _ = command
        if callable(target):
            self.logger.debug(f"Executing Custom Combo Command: {func_name}(*{args}, **{kwargs})")
            return target(self, *args, **kwargs)

        if hasattr(self, target):
            func = getattr(self, target)
            self.logger.debug(f"Executing Custom Combo Command: {target}(*{args}, **{kwargs})")
            return func(*args, **kwargs)

        self.logger.warning(f"Unknown command in combo: {target}")
        return None

    @staticmethod
    def _return_combo(_self):
        return _RETURN_SIGNAL

    def _execute_if_statement(self, condition_cmd, then_cmds, else_cmds):
        cond_result = self._execute_compiled_command(condition_cmd)
        if not isinstance(cond_result, bool):
            self.logger.warning(
                f"if condition command '{condition_cmd[0]}' returned non-bool value, treat as False"
            )
            cond_result = False

        branch = then_cmds if cond_result else else_cmds
        for command in branch:
            result = self._execute_compiled_command(command)
            if result is _RETURN_SIGNAL:
                return _RETURN_SIGNAL
        return cond_result

    @classmethod
    def get_available_commands(cls):
        """
        手动定义对用户可视化/输入框提示的出招表指令及文档说明。
        """
        return cls.get_command_definitions()

    @staticmethod
    def get_combo_syntax_guide() -> str:
        return (
            "▶ 【 if | else | return 】\n"
            "    • 【 if 】\n"
            "        ◦ 参数: 条件, 分支命令, 必填\n"
            "        ◦ 说明: 仅支持可用作条件的指令, 如 ultimate 或 skill(0.5)\n"
            "        ◦ 示例: if ultimate: skill\n\n"
            "    • 【 else 】\n"
            "        ◦ 参数: 分支命令, 必填\n"
            "        ◦ 说明: 必须紧跟 if; if 条件为假时执行此分支\n"
            "        ◦ 示例: else: r_click\n\n"
            "    • 【 return 】\n"
            "        ◦ 参数: 无参数\n"
            "        ◦ 说明: 只能作为 if 或 else 分支的唯一动作, 用于结束后续出招\n"
            "        ◦ 示例: if ultimate: return\n\n"
            "    • 流程组合示例:\n"
            "        l_click(0.5), jump\n"
            "        if skill(0.5): l_click(2), wait(0.1)\n"
            "        else: r_click\n"
            "        arc, wait(0.2)"
        )

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
        self._held_mouse_buttons.add(key)

    def mouseup(self, key="left"):
        self.task.mouse_up(key=key)
        self._held_mouse_buttons.discard(key)

    def command_click(self, key="left"):
        self.task.click(key=key)

    def keydown(self, key):
        self.task.send_key_down(key)
        self._held_keys.add(key)

    def keyup(self, key):
        self.task.send_key_up(key)
        self._held_keys.discard(key)

    def keypress(self, key):
        self.task.send_key(key=key)

    def custom_click_skill(self, down_time=0.01) -> bool:
        return self.click_skill(down_time=down_time)
