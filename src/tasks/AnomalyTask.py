from ok import TaskDisabledException

from src.combat.BaseCombatTask import BaseCombatTask
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.BaseStaminaTask import BaseStaminaTask
from src.utils.i18n_format import register_i18n_format


class AnomalyTask(BaseStaminaTask, BaseCombatTask):
    TASK_NAME = "异象界域"

    # --- 配置项键名 ---
    CONF_TASK_TYPE = "任务类型"
    CONF_EXP_TARGET = "具体奖励目标"
    CONF_ABILITY_ID = "异能材料序号"
    CONF_ARC_ID = "弧盘材料序号"
    CONF_CONSOLE_ID = "空幕序号"
    CONF_CYCLEB_TASK_MODE = "循环模式"
    CONF_CUSTOM_CYCLE = "循环序列"

    # --- 循环模式 ---
    CYCLE_NONE = "停用"
    CYCLE_SUB_TASK = "自动循环序号/目标"
    CYCLE_CUSTOM = "自定义循环"

    # --- 任务类型选项 ---
    TASK_EXP_COIN = "经验与甲硬币"
    TASK_ABILITY = "异能升级材料"
    TASK_ARC = "弧盘突破材料"
    TASK_CONSOLE = "空幕"

    # --- 经验子场景选项 ---
    EXP_CHAR = "角色经验"
    EXP_ARC = "弧盘经验"
    EXP_COIN = "甲硬币"

    # --- 任务配置结构 ---
    TASK_SUB_CONFIGS = {
        TASK_EXP_COIN: CONF_EXP_TARGET,
        TASK_ABILITY: CONF_ABILITY_ID,
        TASK_ARC: CONF_ARC_ID,
        TASK_CONSOLE: CONF_CONSOLE_ID,
    }
    TASK_TYPES = list(TASK_SUB_CONFIGS)

    # --- 任务 ID (1-based) ---
    EXP_COIN_ID_RANGE = (1, 3)
    ABILITY_ID_RANGE = (1, 5)
    ARC_ID_RANGE = (1, 5)
    CONSOLE_ID_RANGE = (1, 6)
    TASKS_ID_RANGE = {
        TASK_EXP_COIN: EXP_COIN_ID_RANGE,
        TASK_ABILITY: ABILITY_ID_RANGE,
        TASK_ARC: ARC_ID_RANGE,
        TASK_CONSOLE: CONSOLE_ID_RANGE,
    }

    TASK_ID_TO_CONFIG_VALUE = {
        TASK_EXP_COIN: {
            1: EXP_CHAR,
            2: EXP_ARC,
            3: EXP_COIN,
        },
    }
    EXP_TARGET_OPTIONS = list(TASK_ID_TO_CONFIG_VALUE[TASK_EXP_COIN].values())

    # --- 字串格式 ---
    CYCLE_CUSTOM_OPTION_FMT = "{task}: {id}"
    DESC_ID_RANGE_FMT = "选择列表中的第几个项目 ({}-{})"

    # --- 自定义循环选项 ---
    NUMERIC_ID_TASK_TYPES = [TASK_ABILITY, TASK_ARC, TASK_CONSOLE]
    CYCLE_OPTION_TO_TASK_ID = {}
    for _id, _option in TASK_ID_TO_CONFIG_VALUE[TASK_EXP_COIN].items():
        CYCLE_OPTION_TO_TASK_ID[_option] = (TASK_EXP_COIN, _id)
    for _task_type in NUMERIC_ID_TASK_TYPES:
        _min_id, _max_id = TASKS_ID_RANGE[_task_type]
        for _id in range(_min_id, _max_id + 1):
            _option = CYCLE_CUSTOM_OPTION_FMT.format(task=_task_type, id=_id)
            CYCLE_OPTION_TO_TASK_ID[_option] = (_task_type, _id)
    CYCLE_CUSTOM_OPTIONS = list(CYCLE_OPTION_TO_TASK_ID)
    del _id, _max_id, _min_id, _option, _task_type

    # --- 任务消耗体力 ---
    TASK_COST = 40

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.TASK_NAME
        self.description = "自动进行异象界域任务"
        self.setup_config(self)

    # ruff: disable[E501]
    @classmethod
    def setup_config(cls, instance: "BaseNTETask", daily=False):
        """
        初始化任务配置或日常任务的配置结构。
        """
        config_updates = {}
        if daily:
            cls.setup_daily_stamina_config(instance, reward_cost=cls.TASK_COST)
        config_updates.update(
            {
                cls.CONF_TASK_TYPE: cls.TASK_EXP_COIN,
                cls.CONF_EXP_TARGET: cls.EXP_CHAR,
                cls.CONF_ABILITY_ID: 1,
                cls.CONF_ARC_ID: 1,
                cls.CONF_CONSOLE_ID: 1,
            }
        )
        if daily:
            config_updates.update(
                {
                    cls.CONF_CYCLEB_TASK_MODE: cls.CYCLE_NONE,
                    cls.CONF_CUSTOM_CYCLE: [],
                }
            )
        instance.default_config.update(config_updates)
        instance.config_type.update(
            {
                cls.CONF_ABILITY_ID: {
                    "min": cls.ABILITY_ID_RANGE[0],
                    "max": cls.ABILITY_ID_RANGE[1],
                },
                cls.CONF_ARC_ID: {
                    "min": cls.ARC_ID_RANGE[0],
                    "max": cls.ARC_ID_RANGE[1],
                },
                cls.CONF_CONSOLE_ID: {
                    "min": cls.CONSOLE_ID_RANGE[0],
                    "max": cls.CONSOLE_ID_RANGE[1],
                },
                cls.CONF_TASK_TYPE: {
                    "type": "drop_down",
                    "options": cls.TASK_TYPES,
                    "sub_configs": cls.TASK_SUB_CONFIGS,
                },
                cls.CONF_EXP_TARGET: {
                    "type": "drop_down",
                    "options": cls.EXP_TARGET_OPTIONS,
                },
                cls.CONF_CUSTOM_CYCLE: {
                    "options_available": cls.CYCLE_CUSTOM_OPTIONS,
                    "allow_duplication": False,
                },
                cls.CONF_CYCLEB_TASK_MODE: {
                    "type": "drop_down",
                    "options": [
                        cls.CYCLE_NONE,
                        cls.CYCLE_SUB_TASK,
                        cls.CYCLE_CUSTOM,
                    ],
                    "sub_configs": {
                        cls.CYCLE_CUSTOM: cls.CONF_CUSTOM_CYCLE,
                    },
                },
            }
        )
        instance.config_description.update(
            {
                cls.CONF_TASK_TYPE: "选择要进行的任务类型",
                cls.CONF_EXP_TARGET: "选择经验与甲硬币任务的具体奖励目标",
                cls.CONF_ABILITY_ID: cls.DESC_ID_RANGE_FMT.format(*cls.ABILITY_ID_RANGE),
                cls.CONF_ARC_ID: cls.DESC_ID_RANGE_FMT.format(*cls.ARC_ID_RANGE),
                cls.CONF_CONSOLE_ID: cls.DESC_ID_RANGE_FMT.format(*cls.CONSOLE_ID_RANGE),
            }
        )
        if daily:
            instance.config_description.update(
                {
                    cls.CONF_TASK_TYPE: (
                        "本次仅刷所选类型及其目标; 成功结束后才按循环模式更新下次目标。"
                    ),
                    cls.CONF_CYCLEB_TASK_MODE: (
                        "每次日常仅刷当前目标, 本次不在多个目标间分配体力。\n"
                        "停用: 下次仍刷当前目标。\n"
                        "自动循环序号/目标: 成功结束后切到同类型的下一个序号或奖励目标, 末项后回到首项。\n"
                        "自定义循环: 成功结束后按下方序列更新下次目标; 失败或跳过时不切换。"
                    ),
                    cls.CONF_CUSTOM_CYCLE: (
                        "按列表顺序轮换, 末项后回到首项; 每次日常只刷一个目标, 下次运行才刷下一个。\n"
                        "数字是副本序号, 不是次数; 例如「异能升级材料: 1」表示该类型的第1项。\n"
                        "首次仍刷上方当前目标; 若其不在序列中, 成功后将下次目标设为序列首项; 空序列不切换。"
                    ),
                }
            )
        if not daily:
            instance.add_claim_reward_count_config()

    # ruff: enable[E501]

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.log_error("AnomalyTask Error", e)

    def do_run(self) -> bool | None:
        task_type = self.config.get(self.CONF_TASK_TYPE)
        idx = self.get_sub_idx()

        # 记录当前执行状态
        self.info_set("任务类型", task_type)
        if task_type == self.TASK_EXP_COIN:
            self.info_set("奖励目标", self.config.get(self.CONF_EXP_TARGET))
        else:
            self.info_set("项目序号", f"第 {idx + 1} 个项目")

        self.log_info(f"开始任务: {task_type}, 目标索引: {idx + 1}")

        # 共同操作 1
        self.ensure_main()
        stamina_target = self.resolve_stamina_target()
        if stamina_target == 0:
            return None

        self.open_f1_domain_page()

        self.sleep(0.5)

        # 不同操作 1: 选择任务类型
        self.log_info(f"切换至任务页签: {task_type}")
        if task_type == self.TASK_EXP_COIN:
            self.operate_click(*self.pos.panels.f1.domain.exp)
        elif task_type == self.TASK_ABILITY:
            self.operate_click(*self.pos.panels.f1.domain.skill)
        elif task_type == self.TASK_ARC:
            self.operate_click(*self.pos.panels.f1.domain.arc)
        elif task_type == self.TASK_CONSOLE:
            self.operate_click(*self.pos.panels.f1.domain.console)

        self.sleep(0.5)

        stamina = self.get_stamina()

        if stamina < self.TASK_COST:
            self.log_warning("体力不足，退出任务", notify=True)
            return False

        # 共同操作 2
        self.log_info("正在传送至目标地点")
        btns = self.wait_until(
            lambda: self.find_confirms(self.box_of_screen(0.925, 0.190, 0.982, 0.760)), time_out=10
        )
        if not btns:
            self.log_warning("未找到副本按钮, 退出任务", notify=True)
            return False

        btn = min(btns, key=lambda x: x.y)
        self.operate_click(btn)
        self.click_traval_button()
        self.wait_in_team(time_out=300)

        stamina_units = stamina // self.TASK_COST
        if stamina_target is not None:
            target_units = (stamina_target + self.TASK_COST - 1) // self.TASK_COST
            stamina_units = min(stamina_units, target_units)
            self.info_set("体力消耗目标", stamina_target)
        reward_count = self.config.get(self.CONF_CLAIM_REWARD_COUNT, 0)
        if reward_count > 0:
            stamina_units = min(stamina_units, reward_count)
        double_count = stamina_units // 2
        single_count = stamina_units % 2
        self.log_info(f"双倍次数: {double_count}, 单倍次数: {single_count}")

        self.enter_anomaly_from_interac(idx)

        total_count = double_count + single_count
        completed_count = 0
        while completed_count < total_count:
            double = completed_count < double_count
            self.wait_until(self.find_exit, time_out=30)
            self.wait_in_team()
            self.sleep(1)
            if not self.do_combat_and_claim(double):
                self.log_warning("本次未成功领取奖励，退出副本后重试当前目标")
                self.exit_anomaly()
                self.enter_anomaly_from_interac(idx, retry=True)
                continue
            completed_count += 1
            self.sleep(2)
            if completed_count < total_count:
                self.wait_click_confirm(range=(0.6469, 0.8174, 0.7027, 0.9097))
        self.wait_click_confirm(range=(0.4074, 0.8174, 0.4578, 0.9056))
        self.log_info("任务执行完毕")
        return True

    def enter_anomaly_from_interac(self, idx, retry=False):
        self.log_info("寻路至交互点并触发交互")
        direction = "s" if retry else "w"
        self.walk_until_interac(direction=direction, raise_if_not_found=True)
        self.wait_until(
            lambda: not self.find_interac(),
            post_action=lambda: self.send_interac(handle_claim=False),
            time_out=10,
            settle_time=0.5,
        )

        self.wait_until(lambda: self.find_one(Labels.stamina_icon), settle_time=0.5, time_out=10)

        # 不同操作 2: 选择对应序号的项目
        self.log_info(f"选择项目序号: {idx + 1}")
        self.click_sub_idx(idx)
        self.sleep(0.25)

        # 共同操作 3
        self.log_info("进入副本并等待")
        self.wait_until(
            lambda: not self.find_one(Labels.stamina_icon),
            pre_action=lambda: self.operate_click(0.8008, 0.9042),
            time_out=10,
        )

    def do_combat_and_claim(self, double: bool):
        self.log_info("开始执行战斗流程")
        self.walk_until_combat(run=True, delay=1)
        self.combat_once()

        self.log_info("战斗结束，正在前往领取奖励")

        def action():
            self.walk_to_treasure()
            self.send_interac(handle_claim=False)
            return self.find_all_claim()

        self.rotate_and_find_treasure()
        claims = self.retry_on_action(action)
        if not claims:
            return False

        if double:
            box = max(claims, key=lambda x: x.x)
        else:
            box = min(claims, key=lambda x: x.x)
        btn = box.copy(x_offset=box.width * 3)
        self.operate_click(btn)
        return True

    def click_sub_idx(self, idx):
        y = 0.1715 + idx * (0.2806 - 0.1715)
        self.operate_click(0.0852, y)

    def get_sub_idx(self) -> int:
        """Return the selected sub-scene index, where index equals ID minus one."""
        return self.resolve_sub_id() - 1

    def resolve_sub_id(self) -> int:
        """Return the selected task ID from the UI-constrained configuration."""
        task_type = self.config.get(self.CONF_TASK_TYPE)
        config_value = self.config.get(self.TASK_SUB_CONFIGS.get(task_type))
        id_to_value = self.TASK_ID_TO_CONFIG_VALUE.get(task_type)
        if id_to_value:
            return next(id for id, value in id_to_value.items() if value == config_value)
        return config_value

    def set_task_type_and_id(self, task_type: str, task_id: int) -> None:
        """Set a task and valid ID generated by the configured cycle."""
        config_key = self.TASK_SUB_CONFIGS[task_type]
        id_to_value = self.TASK_ID_TO_CONFIG_VALUE.get(task_type)
        self.config[self.CONF_TASK_TYPE] = task_type  # type: ignore
        self.config[config_key] = id_to_value[task_id] if id_to_value else task_id  # type: ignore

    def get_next_sub_id(self) -> int:
        """获取下一个任务 ID (1-based)"""
        task_id = self.resolve_sub_id()
        task_type = self.config.get(self.CONF_TASK_TYPE)
        min_id, max_id = self.TASKS_ID_RANGE[task_type]
        return min_id + (task_id - min_id + 1) % (max_id - min_id + 1)

    def on_daily_end(self, result: bool | None) -> None:
        """Advance the configured cycle after a successful daily execution."""
        if result is True:
            self.shift_id()

    def shift_id(self):
        """Advance the daily task according to its configured cycle mode."""
        if not self.config:
            return
        shift_handler = {
            self.CYCLE_SUB_TASK: self.shift_sub_task_id,
            self.CYCLE_CUSTOM: self.shift_custom_cycle,
        }.get(self.config.get(self.CONF_CYCLEB_TASK_MODE))
        if shift_handler:
            shift_handler()

    def shift_sub_task_id(self):
        """Advance to the next ID of the current task type."""
        task_type = self.config.get(self.CONF_TASK_TYPE)
        next_task_id = self.get_next_sub_id()
        self.set_task_type_and_id(task_type, next_task_id)
        self.sync_config()

    def shift_custom_cycle(self):
        """Advance to the next user-selected custom-cycle option."""
        cycle: list = self.config.get(self.CONF_CUSTOM_CYCLE, [])
        if not cycle:
            self.log_warning("自定义任务循环为空, 不切换任务")
            return
        task_type = self.config.get(self.CONF_TASK_TYPE)
        task_id = self.resolve_sub_id()
        option = self.get_cycle_option(task_type, task_id)
        if option in cycle:
            current_index = cycle.index(option)
            next_option = cycle[(current_index + 1) % len(cycle)]
        else:
            next_option = cycle[0]
        next_task = self.CYCLE_OPTION_TO_TASK_ID.get(next_option)
        if next_task is None:
            self.log_warning("无法解析下一个自定义循环任务")
            return

        next_task_type, next_task_id = next_task
        self.set_task_type_and_id(next_task_type, next_task_id)
        self.sync_config()
        self.log_info(f"下一个任务设为 {next_task_type} {next_task_id}")

    def get_cycle_option(self, task_type: str, task_id: int):
        """Return the custom-cycle label for a task ID, if it is available."""
        return next(
            (
                option
                for option, cycle_task in self.CYCLE_OPTION_TO_TASK_ID.items()
                if cycle_task == (task_type, task_id)
            ),
            None,
        )


register_i18n_format(
    AnomalyTask.DESC_ID_RANGE_FMT,
)
register_i18n_format(
    AnomalyTask.CYCLE_CUSTOM_OPTION_FMT,
    translated_fields=frozenset({"task"}),
    allowed_values={"task": AnomalyTask.NUMERIC_ID_TASK_TYPES},
    translate_template=False,
)
