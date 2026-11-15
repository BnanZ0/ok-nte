import csv
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from ok import TaskDisabledException

from src.combat.BaseCombatTask import BaseCombatTask
from src.Labels import Labels
from src.tasks.anomaly_rotation import RotationTarget, StaminaRotation, rounded_quota
from src.tasks.anomaly_statistics import FarmingStatistics
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
    CONF_ROTATION_SEQUENCE = "轮换队列"
    CONF_STRATEGY = "刷取策略"
    CONF_QUOTA = "每项最低体力"
    CONF_QUOTAS = "自定义体力额度"
    CONF_PREFER_DOUBLE = "优先双倍领取"
    CONF_ROTATION_STATE = "额度轮换进度"
    CONF_RESET_ROTATION = "下次运行重置轮换"
    CONF_STAMINA_MODE = "体力使用"
    CONF_STAMINA_LIMIT = "本次体力上限"
    CONF_ADVANCED = "进阶设置"
    CONF_TOTALS = "_刷取累计统计"
    STAMINA_ALL = "用完当前体力"
    STAMINA_LIMIT = "消耗指定体力"
    STAMINA_DAILY = "完成每日"
    STRATEGY_FIXED = "固定目标"
    STRATEGY_SAME_TYPE = "同类轮换"
    STRATEGY_CUSTOM = "自定义轮换"

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
    CONSOLE_COST = 60
    MAX_CLAIM_FAILURES = 3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.TASK_NAME
        self.description = "自动进行异象界域任务"
        self.setup_config(self)

    @classmethod
    def setup_config(cls, instance: "BaseNTETask", daily=False):
        if daily:
            cls.setup_daily_stamina_config(instance, reward_cost=cls.TASK_COST)
        instance.default_config.update(
            {
                cls.CONF_STRATEGY: cls.STRATEGY_FIXED,
                cls.CONF_TASK_TYPE: cls.TASK_EXP_COIN,
                cls.CONF_EXP_TARGET: cls.EXP_CHAR,
                cls.CONF_ABILITY_ID: 1,
                cls.CONF_ARC_ID: 1,
                cls.CONF_CONSOLE_ID: 1,
                cls.CONF_ROTATION_SEQUENCE: [],
                cls.CONF_STAMINA_MODE: cls.STAMINA_DAILY if daily else cls.STAMINA_ALL,
                cls.CONF_STAMINA_LIMIT: 180,
                cls.CONF_ADVANCED: False,
                cls.CONF_QUOTA: 0,
                cls.CONF_QUOTAS: "",
                cls.CONF_PREFER_DOUBLE: True,
                cls.CONF_RESET_ROTATION: False,
                cls.CONF_ROTATION_STATE: {},
                cls.CONF_TOTALS: {},
            }
        )
        if daily:
            instance.default_config.update(
                {
                    cls.CONF_CYCLEB_TASK_MODE: cls.CYCLE_NONE,
                    cls.CONF_CUSTOM_CYCLE: [],
                }
            )
        else:
            instance.add_claim_reward_count_config()

        fixed_targets = [
            cls.CONF_EXP_TARGET,
            cls.CONF_ABILITY_ID,
            cls.CONF_ARC_ID,
            cls.CONF_CONSOLE_ID,
        ]
        fixed_fields = [cls.CONF_TASK_TYPE, *fixed_targets]
        if daily:
            fixed_fields += [cls.CONF_CYCLEB_TASK_MODE, cls.CONF_CUSTOM_CYCLE]
        rotation_fields = [cls.CONF_QUOTA, cls.CONF_QUOTAS, cls.CONF_RESET_ROTATION]
        advanced_fields = [*rotation_fields, cls.CONF_PREFER_DOUBLE]
        if daily:
            advanced_fields += [cls.CONF_CYCLEB_TASK_MODE, cls.CONF_CUSTOM_CYCLE]
        else:
            advanced_fields.append(cls.CONF_CLAIM_REWARD_COUNT)

        instance.config_type.update(
            {
                cls.CONF_STRATEGY: {
                    "type": "drop_down",
                    "options": [cls.STRATEGY_FIXED, cls.STRATEGY_SAME_TYPE, cls.STRATEGY_CUSTOM],
                    "sub_configs": {
                        cls.STRATEGY_FIXED: fixed_fields,
                        cls.STRATEGY_SAME_TYPE: [cls.CONF_TASK_TYPE, *rotation_fields],
                        cls.STRATEGY_CUSTOM: [cls.CONF_ROTATION_SEQUENCE, *rotation_fields],
                    },
                },
                cls.CONF_TASK_TYPE: {
                    "type": "drop_down",
                    "options": cls.TASK_TYPES,
                    "sub_configs": cls.TASK_SUB_CONFIGS,
                },
                cls.CONF_EXP_TARGET: {"type": "drop_down", "options": cls.EXP_TARGET_OPTIONS},
                cls.CONF_ABILITY_ID: {
                    "min": cls.ABILITY_ID_RANGE[0],
                    "max": cls.ABILITY_ID_RANGE[1],
                },
                cls.CONF_ARC_ID: {"min": cls.ARC_ID_RANGE[0], "max": cls.ARC_ID_RANGE[1]},
                cls.CONF_CONSOLE_ID: {
                    "min": cls.CONSOLE_ID_RANGE[0],
                    "max": cls.CONSOLE_ID_RANGE[1],
                },
                cls.CONF_ROTATION_SEQUENCE: {
                    "options_available": cls.CYCLE_CUSTOM_OPTIONS,
                    "allow_duplication": False,
                },
                cls.CONF_STAMINA_MODE: {
                    "type": "drop_down",
                    "options": [cls.STAMINA_ALL, cls.STAMINA_LIMIT, cls.STAMINA_DAILY]
                    if daily
                    else [cls.STAMINA_ALL, cls.STAMINA_LIMIT],
                    "sub_configs": {
                        cls.STAMINA_LIMIT: cls.CONF_STAMINA_LIMIT,
                        **({cls.STAMINA_DAILY: cls.CONF_STAMINA_TARGET} if daily else {}),
                    },
                },
                cls.CONF_STAMINA_LIMIT: {"min": 1, "max": 100000},
                cls.CONF_ADVANCED: {"sub_configs": {True: advanced_fields}},
                cls.CONF_QUOTA: {"min": 0, "max": 100000},
                cls.CONF_ROTATION_STATE: {"hidden": True},
                cls.CONF_TOTALS: {"hidden": True},
            }
        )
        if daily:
            instance.config_type[cls.CONF_CYCLEB_TASK_MODE] = {
                "type": "drop_down",
                "options": [cls.CYCLE_NONE, cls.CYCLE_SUB_TASK, cls.CYCLE_CUSTOM],
                "sub_configs": {cls.CYCLE_CUSTOM: cls.CONF_CUSTOM_CYCLE},
            }
            instance.config_type[cls.CONF_CUSTOM_CYCLE] = {
                "options_available": cls.CYCLE_CUSTOM_OPTIONS,
                "allow_duplication": False,
            }

        instance.config_description.update(
            {
                cls.CONF_STRATEGY: (
                    "固定目标只刷一种材料; 同类轮换依序刷该类别; 自定义轮换按队列顺序刷。"
                ),
                cls.CONF_TASK_TYPE: "选择要刷取的类别",
                cls.CONF_EXP_TARGET: "选择奖励目标",
                cls.CONF_ABILITY_ID: cls.DESC_ID_RANGE_FMT.format(*cls.ABILITY_ID_RANGE),
                cls.CONF_ARC_ID: cls.DESC_ID_RANGE_FMT.format(*cls.ARC_ID_RANGE),
                cls.CONF_CONSOLE_ID: cls.DESC_ID_RANGE_FMT.format(*cls.CONSOLE_ID_RANGE),
                cls.CONF_ROTATION_SEQUENCE: "添加要刷取的材料并调整顺序, 刷完末项后回到首项。",
                cls.CONF_STAMINA_MODE: (
                    "用完当前体力会重新读取剩余体力, 不受每日完成状态影响。"
                    "不足一份奖励时结束, 不会自动使用体力药。"
                ),
                cls.CONF_STAMINA_LIMIT: "本次最多消耗的体力, 不够一份奖励的剩余额度不会使用。",
                cls.CONF_ADVANCED: "调整每项额度、双倍领取和轮换进度。",
                cls.CONF_QUOTA: (
                    "0表示每项刷一次双倍奖励的体力: 普通副本80, 空幕120。"
                    "其他值按单份消耗向上取整, 普通副本40, 空幕60。"
                ),
                cls.CONF_QUOTAS: (
                    "可留空。按队列顺序填写各项体力额度, 用英文逗号分隔; "
                    "例如120,120,80。数量须与队列一致, 0使用自动额度。"
                ),
                cls.CONF_PREFER_DOUBLE: "额度和剩余体力允许时优先双倍, 否则领取单倍。",
                cls.CONF_RESET_ROTATION: "下次从队列首项重新开始, 执行后自动关闭。",
            }
        )
        if daily:
            instance.config_description.update(
                {
                    cls.CONF_STAMINA_TARGET: (
                        "按今日累计消耗计算, 扣除已完成部分; 今日已消耗180体力或活跃度达100时跳过。"
                    ),
                    cls.CONF_CYCLEB_TASK_MODE: (
                        "仅用于固定目标: 本次日常成功结束后更新下次目标, 不在本次分配体力。"
                    ),
                    cls.CONF_CUSTOM_CYCLE: "固定目标的跨次循环序列, 不用于本次体力轮换。",
                }
            )
        else:
            instance.config_description[cls.CONF_CLAIM_REWARD_COUNT] = (
                "0不限制; 双倍按两份计算, 所有目标合计不超过此份数。"
            )

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.log_error("AnomalyTask Error", e)
        finally:
            self.exit_anomaly()

    @classmethod
    def reward_cost(cls, task_type):
        if task_type not in cls.TASK_TYPES:
            raise ValueError("无效的任务类型")
        return cls.CONSOLE_COST if task_type == cls.TASK_CONSOLE else cls.TASK_COST

    def rotation_enabled(self):
        return self.config.get(self.CONF_STRATEGY, self.STRATEGY_FIXED) != self.STRATEGY_FIXED

    def build_rotation(self):
        strategy = self.config.get(self.CONF_STRATEGY, self.STRATEGY_FIXED)
        if strategy == self.STRATEGY_SAME_TYPE:
            task_type = self.config.get(self.CONF_TASK_TYPE)
            if task_type not in self.TASKS_ID_RANGE:
                raise ValueError("请选择有效的材料类别")
            minimum, maximum = self.TASKS_ID_RANGE[task_type]
            options = [self.get_cycle_option(task_type, idx) for idx in range(minimum, maximum + 1)]
        elif strategy == self.STRATEGY_CUSTOM:
            options = self.config.get(self.CONF_ROTATION_SEQUENCE, [])
        else:
            raise ValueError("无效的刷取策略")
        if (
            not isinstance(options, list)
            or not options
            or not all(isinstance(option, str) for option in options)
        ):
            raise ValueError("请选择轮换队列中的材料")
        if len(options) != len(set(options)):
            raise ValueError("轮换序列不能为空或包含重复目标")
        minimum = self.config.get(self.CONF_QUOTA, 0)
        if type(minimum) is not int or not 0 <= minimum <= 100000:
            raise ValueError("每项体力额度须为0到100000的整数")
        raw_quotas = self.config.get(self.CONF_QUOTAS, "")
        if not isinstance(raw_quotas, str):
            raise ValueError("自定义体力额度须为英文逗号分隔的非负整数")
        raw_quotas = raw_quotas.strip()
        quotas = [minimum] * len(options)
        if raw_quotas:
            try:
                quotas = [int(value.strip()) for value in raw_quotas.split(",")]
            except ValueError:
                raise ValueError("自定义体力额度须为英文逗号分隔的非负整数") from None
            if len(quotas) != len(options):
                raise ValueError("自定义体力额度个数须与轮换目标个数一致")
        targets = []
        for option, quota in zip(options, quotas):
            if option not in self.CYCLE_OPTION_TO_TASK_ID or not 0 <= quota <= 100000:
                raise ValueError("轮换目标或体力额度无效")
            task_type, task_id = self.CYCLE_OPTION_TO_TASK_ID[option]
            cost = self.reward_cost(task_type)
            targets.append(
                RotationTarget(option, task_type, task_id, cost, rounded_quota(quota, cost))
            )
        reset = self.config.get(self.CONF_RESET_ROTATION, False)
        saved = None if reset else self.config.get(self.CONF_ROTATION_STATE)
        initial = options[0]
        rotation = StaminaRotation(targets, saved, initial)
        return rotation

    def resolve_stamina_target(self):
        mode = self.stamina_mode()
        if mode == self.STAMINA_ALL:
            return None
        if mode == self.STAMINA_LIMIT:
            limit = self.config.get(self.CONF_STAMINA_LIMIT)
            if type(limit) is not int or not 1 <= limit <= 100000:
                raise ValueError("本次体力上限须为1到100000的整数")
            return limit
        if mode != self.STAMINA_DAILY or self.CONF_STAMINA_TARGET not in self.config:
            raise ValueError("无效的体力使用方式")
        return super().resolve_stamina_target()

    def stamina_mode(self):
        default = (
            self.STAMINA_DAILY if self.CONF_STAMINA_TARGET in self.config else self.STAMINA_ALL
        )
        return self.config.get(self.CONF_STAMINA_MODE, default)

    @staticmethod
    def configuration_storage(config):
        if getattr(config, "config_file", None):
            return Path(config.config_file), None
        routine = getattr(config, "routine_task", None)
        store = getattr(routine, "routine_task_configs", None)
        if getattr(store, "config_file", None):
            return Path(store.config_file), config.task_id
        return None, None

    def persist_checkpoint(self, updates):
        # Save related progress/counts together, rather than once for each key.
        for key, value in updates.items():
            dict.__setitem__(self.config, key, deepcopy(value))
        if callable(getattr(self.config, "save_file", None)):
            self.config.save_file()
        path, task_id = self.configuration_storage(self.config)
        if path is not None:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if task_id is not None:
                    saved = saved[task_id]
                if any(saved.get(key) != value for key, value in updates.items()):
                    raise ValueError("Saved checkpoint does not match")
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise RuntimeError("领奖进度未能保存, 已停止任务, 请检查配置目录") from error

    def write_claim_log(self, option, units, cost):
        path, _ = self.configuration_storage(self.config)
        if path is None:
            return
        try:
            directory = path.parent.parent / "stats"
            directory.mkdir(parents=True, exist_ok=True)
            log = directory / "anomaly_claims.csv"
            new_file = not log.exists() or log.stat().st_size == 0
            with log.open("a", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                if new_file:
                    writer.writerow(["时间", "任务", "目标", "领奖倍数", "消耗体力"])
                writer.writerow(
                    [
                        datetime.now().astimezone().isoformat(),
                        type(self).__name__,
                        option,
                        units,
                        cost * units,
                    ]
                )
        except OSError:
            self.log_warning("领奖明细写入失败, 已保存累计统计")

    def do_run(self) -> bool | None:
        rotation = self.build_rotation() if self.rotation_enabled() else None
        self.ensure_main()
        stamina_target = self.resolve_stamina_target()
        if stamina_target == 0:
            return None
        reward_limit = self.config.get(self.CONF_CLAIM_REWARD_COUNT, 0) or None
        if reward_limit is not None and (type(reward_limit) is not int or reward_limit < 1):
            raise ValueError("领取奖励次数须为非负整数")
        round_up = self.stamina_mode() == self.STAMINA_DAILY
        fixed = None
        if rotation is None:
            task_type = self.config.get(self.CONF_TASK_TYPE)
            task_id = self.resolve_sub_id()
            option = self.get_cycle_option(task_type, task_id)
            fixed = RotationTarget(
                option, task_type, task_id, self.reward_cost(task_type), self.reward_cost(task_type)
            )
        else:
            self.persist_checkpoint(
                {self.CONF_ROTATION_STATE: rotation.snapshot(), self.CONF_RESET_ROTATION: False}
            )
        current_stats = FarmingStatistics()
        total_stats = FarmingStatistics(self.config.get(self.CONF_TOTALS))
        units = 0
        while stamina_target is None or current_stats.stamina < stamina_target:
            if reward_limit is not None and units >= reward_limit:
                break
            target = rotation.current if rotation is not None else fixed
            budget = rotation.remaining if rotation is not None else None
            if stamina_target is not None:
                remaining = stamina_target - current_stats.stamina
                budget = remaining if budget is None else min(budget, remaining)
            remaining_units = None if reward_limit is None else reward_limit - units

            def on_claim(claimed_units):
                nonlocal units
                current_stats.record(target.option, claimed_units, target.cost)
                total_stats.record(target.option, claimed_units, target.cost)
                units += claimed_units
                updates = {self.CONF_TOTALS: total_stats.snapshot()}
                if rotation is not None:
                    rotation.record_claim(claimed_units)
                    updates[self.CONF_ROTATION_STATE] = rotation.snapshot()
                self.persist_checkpoint(updates)
                self.write_claim_log(target.option, claimed_units, target.cost)
                self.info_set("本次已消耗体力", current_stats.stamina)
                self.info_set("累计已消耗体力", total_stats.stamina)
                summary = "\n".join(
                    f"{option}: 单倍{counts['single']}次, 双倍{counts['double']}次, "
                    f"{counts['stamina']}体力"
                    for option, counts in current_stats.targets.items()
                )
                self.info_set("本次领奖统计", summary)
                if rotation is not None:
                    self.info_set("下个目标", rotation.current.option)
                    self.info_set("轮换进度", f"{rotation.spent} / {rotation.current.quota} 体力")

            before = units
            success, completed = self.run_target(
                budget, remaining_units, on_claim, target=target, round_up=round_up
            )
            if type(completed) is not int or completed < 0 or units - before != completed:
                raise ValueError("领奖进度异常, 已停止任务")
            if not success:
                return False
            if completed == 0:
                return units > 0
        return units > 0

    def run_target(
        self, stamina_budget=None, reward_limit=None, on_claim=None, *, target=None, round_up=True
    ):
        """Run one quota batch; return (success, confirmed reward units)."""
        task_type = target.task_type if target else self.config.get(self.CONF_TASK_TYPE)
        idx = target.task_id - 1 if target else self.get_sub_idx()
        cost = self.reward_cost(task_type)
        self.info_set("任务类型", task_type)
        self.info_set("项目序号", f"第 {idx + 1} 个项目")
        self.info_set("单份奖励体力", cost)
        self.log_info(f"开始任务: {task_type}, 目标索引: {idx + 1}, 单份体力: {cost}")
        self.ensure_main()
        self.open_f1_domain_page()
        self.sleep(0.5)
        tab = {
            self.TASK_EXP_COIN: self.pos.panels.f1.domain.exp,
            self.TASK_ABILITY: self.pos.panels.f1.domain.skill,
            self.TASK_ARC: self.pos.panels.f1.domain.arc,
            self.TASK_CONSOLE: self.pos.panels.f1.domain.console,
        }[task_type]
        self.operate_click(*tab)
        self.sleep(0.5)
        stamina = self.get_stamina()
        if stamina < 0:
            raise ValueError("无法识别当前体力, 停止任务并保留进度")
        stamina_units = stamina // cost
        if stamina_budget is not None:
            budget_units = (
                (stamina_budget + cost - 1) // cost if round_up else stamina_budget // cost
            )
            stamina_units = min(stamina_units, budget_units)
        if reward_limit is not None:
            stamina_units = min(stamina_units, reward_limit)
        if stamina_units <= 0:
            self.log_info("当前目标可用体力不足一份奖励, 保留进度并结束")
            return True, 0

        btns = self.wait_until(
            lambda: self.find_confirms(self.box_of_screen(0.925, 0.190, 0.982, 0.760)), time_out=10
        )
        if not btns:
            return False, 0
        self.operate_click(min(btns, key=lambda box: box.y))
        self.click_traval_button()
        self.wait_in_team(time_out=300)
        self.enter_anomaly_from_interac(idx)

        completed = 0
        failures = 0
        while completed < stamina_units:
            double = (
                self.config.get(self.CONF_PREFER_DOUBLE, True) and stamina_units - completed >= 2
            )
            self.wait_until(self.find_exit, time_out=30)
            self.wait_in_team()
            self.sleep(1)
            claimed = self.do_combat_and_claim(double)
            if not claimed:
                failures += 1
                self.exit_anomaly()
                if failures >= self.MAX_CLAIM_FAILURES:
                    self.log_warning("连续领奖失败, 停止任务并保留进度", notify=True)
                    return False, completed
                self.enter_anomaly_from_interac(idx, retry=True)
                continue
            if (
                type(claimed) is not int
                or claimed not in (1, 2)
                or claimed > stamina_units - completed
            ):
                raise ValueError("领奖份数异常, 已停止任务")
            failures = 0
            completed += claimed
            if on_claim is not None:
                on_claim(claimed)
            self.info_set("已领取奖励份数", f"{completed} / {stamina_units}")
            self.sleep(2)
            if completed < stamina_units:
                self.wait_click_confirm(range=(0.6469, 0.8174, 0.7027, 0.9097))
        self.wait_click_confirm(range=(0.4074, 0.8174, 0.4578, 0.9056))
        self.log_info("当前目标额度执行完毕")
        return True, completed

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

        midpoint = self.width_of_screen(0.5)
        singles = [box for box in claims if box.x < midpoint]
        doubles = [box for box in claims if box.x >= midpoint]
        if double and doubles:
            box, units = max(doubles, key=lambda box: box.x), 2
        elif singles:
            box, units = min(singles, key=lambda box: box.x), 1
        else:
            raise ValueError("未识别到所需的领奖按钮, 停止任务以避免超额消耗")
        self.operate_click(box.copy(x_offset=box.width * 3))
        # Do not advance persisted progress merely because a click was sent.
        # A timeout here stops the task rather than retrying an uncertain claim.
        settled = self.wait_until(
            lambda: self.find_confirm(box=self.box_of_screen(0.4074, 0.8174, 0.4578, 0.9056)),
            time_out=20,
            settle_time=0.25,
        )
        if not settled:
            raise ValueError("未能确认领奖结算, 已停止任务并保留上次进度")
        return units

    def click_sub_idx(self, idx):
        y = 0.1715 + idx * (0.2806 - 0.1715)
        self.operate_click(0.0852, y)

    def get_sub_idx(self) -> int:
        """Return the selected sub-scene index, where index equals ID minus one."""
        return self.resolve_sub_id() - 1

    def resolve_sub_id(self) -> int:
        """Return the selected task ID from the UI-constrained configuration."""
        task_type = self.config.get(self.CONF_TASK_TYPE)
        if task_type not in self.TASK_SUB_CONFIGS:
            raise ValueError("无效的任务类型")
        config_value = self.config.get(self.TASK_SUB_CONFIGS[task_type])
        id_to_value = self.TASK_ID_TO_CONFIG_VALUE.get(task_type)
        if id_to_value:
            task_id = next((id for id, value in id_to_value.items() if value == config_value), None)
            if task_id is None:
                raise ValueError("无效的奖励目标")
            return task_id
        minimum, maximum = self.TASKS_ID_RANGE[task_type]
        if type(config_value) is not int or not minimum <= config_value <= maximum:
            raise ValueError("材料序号不在可选范围内")
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
        strategy = self.config.get(AnomalyTask.CONF_STRATEGY, AnomalyTask.STRATEGY_FIXED)
        if result is True and strategy == AnomalyTask.STRATEGY_FIXED:
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
