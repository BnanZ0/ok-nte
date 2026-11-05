import cv2
import numpy as np
from ok import TaskDisabledException

from src.combat.BaseCombatTask import BaseCombatTask
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.BaseStaminaTask import BaseStaminaTask


class AnomalyHunter(BaseStaminaTask, BaseCombatTask):
    # --- 配置项键名 ---
    CONF_HUNTER_TARGET = "追猎目标"
    CONF_TEAM_INDEX = "战斗队伍方案"

    # --- 编队界面坐标 (相对比例, 适配任意分辨率) ---
    TEAM_CIRCLE_X = 0.0643
    TEAM_CIRCLE_Y0 = 0.2525
    TEAM_CIRCLE_STEP = 0.0845

    # --- 战斗队伍方案选项 ---
    TEAM_NONE = "不切换"
    TEAM_OPTIONS = ["不切换", "队伍1", "队伍2", "队伍3", "队伍4", "队伍5", "队伍6"]

    # --- 追猎目标选项 ---
    TARGET_SOUND_KING = "音霸魔王"
    TARGET_HEADLESS_RIDER = "无首铁驭"
    TARGET_SERENITY = "塞润尼缇"
    TARGET_BLACK_BOOK = "黑之书"
    TARGET_SEA_PRISONER = "海囚"
    TARGET_NEST_BIRD = "围巢鸟"
    TARGET_SPOTTED_BUTTERFLY = "斑蝶"

    WALK_METHOD = {
        TARGET_HEADLESS_RIDER: [["w", 0.5], ["w", "d"]],
        TARGET_SPOTTED_BUTTERFLY: [["s"]],
    }

    HUNTER_TARGETS = [
        TARGET_SOUND_KING,
        TARGET_HEADLESS_RIDER,
        TARGET_SERENITY,
        TARGET_BLACK_BOOK,
        TARGET_SEA_PRISONER,
        TARGET_NEST_BIRD,
        TARGET_SPOTTED_BUTTERFLY,
    ]

    DEFAULT_TREASURE_FEATURES = [
        Labels.boss_treasure,
    ]
    BOSS_TREASURE_THRESHOLD = 0.7
    BOSS_TREASURE_ONCE_SEARCH_TIME = 2
    BOSS_TREASURE_WALK_TIMEOUT = 15

    TASK_COST = 60
    MAX_CONSECUTIVE_FAILURES = 3
    HUNTER_TRAVEL_X = 0.867
    HUNTER_TRAVEL_Y_START = 0.262
    HUNTER_NEXT_PAGE_TRAVEL_Y_START = 0.468
    HUNTER_TRAVEL_Y_STEP = 0.148
    HUNTER_FIRST_PAGE_SIZE = 4

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "异象追猎"
        self.description = "自动进行异象追猎任务"
        self.setup_config(self)

    @classmethod
    def setup_config(cls, instance: "BaseNTETask", daily=False):
        """初始化异象追猎配置。"""
        if daily:
            cls.setup_daily_stamina_config(instance, reward_cost=cls.TASK_COST)
        instance.default_config.update(
            {
                cls.CONF_HUNTER_TARGET: cls.TARGET_SOUND_KING,
                cls.CONF_TEAM_INDEX: cls.TEAM_OPTIONS[1],
            }
        )

        instance.config_type.update(
            {
                cls.CONF_HUNTER_TARGET: {
                    "type": "drop_down",
                    "options": cls.HUNTER_TARGETS,
                },
                cls.CONF_TEAM_INDEX: {
                    "type": "drop_down",
                    "options": cls.TEAM_OPTIONS,
                },
            }
        )
        if not daily:
            instance.add_claim_reward_count_config()

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error("AnomalyHunter Error", e)
        finally:
            self.exit_anomaly()

    def switch_team_from_config(self):
        """按配置切换战斗队伍方案 (编队界面, L 键)。"""
        team_cfg = self.config.get(self.CONF_TEAM_INDEX, self.TEAM_NONE)
        if not team_cfg or team_cfg == self.TEAM_NONE:
            return
        try:
            idx = int(str(team_cfg).replace("队伍", ""))
        except ValueError:
            self.log_warning(f"无效的队伍方案配置: {team_cfg}, 跳过切队")
            return
        if idx < 1 or idx > 6:
            self.log_warning(f"无效的队伍编号: {idx}, 跳过切队")
            return
        try:
            self.switch_team_preset(idx)
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error("切换队伍方案失败", e)
            self.send_key("esc")

    def switch_team_preset(self, idx: int) -> bool:
        """在编队界面切换到第 idx 个队伍方案 (1-6), 返回是否成功。"""
        self.info_set("切换队伍", f"队伍{idx}")
        opened = False
        for _ in range(3):
            self.send_key("l", action_name="open_team_ui")
            if self.wait_until(
                lambda: self.ocr(0.0, 0.0, 0.3, 0.15, match="编队"),
                time_out=6,
                raise_if_not_found=False,
            ):
                opened = True
                break
            self.send_key("esc")
            self.sleep(1)
        if not opened:
            self.log_warning("无法打开编队界面, 跳过切队", notify=True)
            return False
        self.sleep(1)

        # 1. 点左侧队伍编号圆点
        y = self.TEAM_CIRCLE_Y0 + (idx - 1) * self.TEAM_CIRCLE_STEP
        self.click_relative(self.TEAM_CIRCLE_X, y, after_sleep=1.5)

        # 2. 点底部"选择队伍"按钮 (OCR 定位, 失败则退回固定坐标)
        btn = self.ocr(0.18, 0.80, 0.62, 0.96, match="选择队伍")
        if btn:
            self.click_box(btn[0], after_sleep=1.5)
        else:
            self.log_warning("未找到选择队伍按钮, 使用固定坐标")
            self.click_relative(0.306, 0.88, after_sleep=1.5)

        # 3. 处理可能的确认弹窗
        confirm = self.ocr(0.25, 0.40, 0.75, 0.80, match="确认")
        if confirm:
            self.log_info("检测到确认弹窗, 点击确认")
            self.click_box(confirm[0], after_sleep=1.5)

        # 4. 验证顶部标题已是目标队伍
        ok = self.wait_until(
            lambda: self.ocr(0.38, 0.02, 0.62, 0.14, match=f"队伍{idx}"),
            time_out=5,
            raise_if_not_found=False,
        )
        self.send_key("esc")
        self.sleep(1.5)
        self.ensure_main(esc=False, in_world=True, time_out=15)
        if ok:
            self.log_info(f"已切换到队伍{idx}")
        else:
            self.log_warning(f"切换队伍{idx}未确认成功, 请检查编队界面", notify=True)
        return bool(ok)

    def do_run(self) -> bool | None:
        target = self.normalize_target(
            self.config.get(self.CONF_HUNTER_TARGET, self.TARGET_SOUND_KING)
        )
        target_idx = self.get_target_idx(target)
        self.info_set("追猎目标", target)
        self.log_info(f"开始异象追猎任务: {target}, 目标索引: {target_idx}")

        self.ensure_main()
        stamina_target = self.resolve_stamina_target()
        if stamina_target == 0:
            return None
        # 仅在确有体力目标时才切队, 避免目标已完成时改动玩家当前队伍
        self.switch_team_from_config()

        self.open_hunter_page()

        self.sleep(0.5)

        stamina = self.get_stamina()

        if stamina < self.TASK_COST:
            self.log_warning("体力不足，退出异象追猎任务", notify=True)
            return False

        stamina_units = stamina // self.TASK_COST
        if stamina_target is not None:
            target_units = (stamina_target + self.TASK_COST - 1) // self.TASK_COST
            stamina_units = min(stamina_units, target_units)
            self.info_set("体力消耗目标", stamina_target)
        reward_count = self.config.get(self.CONF_CLAIM_REWARD_COUNT, 0)
        if reward_count > 0:
            stamina_units = min(stamina_units, reward_count)

        if stamina_units <= 0:
            self.log_warning("没有可执行的异象追猎目标，退出任务", notify=True)
            return False

        self.info_set("计划次数", stamina_units)
        success_count = 0
        failed_count = 0
        consecutive_failures = 0
        attempt_count = 0
        ret = True
        while success_count < stamina_units:
            attempt_count += 1
            self.info_set("当前目标", target)
            self.info_set("当前次数", f"{success_count + 1} / {stamina_units}")
            self.info_set("尝试次数", attempt_count)
            self.log_info(f"准备挑战异象追猎目标: {target}")

            self.start_hunter_attempt(target, target_idx, reopen_page=attempt_count > 1)
            self.wait_in_team(settle_time=0.25)

            if self.do_combat_and_claim():
                success_count += 1
                consecutive_failures = 0
            else:
                failed_count += 1
                consecutive_failures += 1
                self.log_warning(
                    f"异象追猎连续失败 {consecutive_failures}/{self.MAX_CONSECUTIVE_FAILURES}"
                )

            self.exit_anomaly()

            if consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                self.log_warning("连续失败已达上限，将传送最近的电话亭传送点", notify=True)
                ret = False
                break

        self.log_info("异象追猎任务完成，尝试传送到最近的电话亭")
        self.sleep(0.5)
        self.click_nearest_map_teleport()
        self.sleep(0.5)
        self.ensure_main()
        self.log_warning(
            f"异象追猎执行结果: 成功次数: {success_count},"
            f"失败次数: {failed_count}，共计消耗体力: {success_count * self.TASK_COST}"
        )
        return ret

    def start_hunter_attempt(self, target: str, target_idx: int, reopen_page=False):
        if reopen_page:
            self.open_hunter_page()
        self.travel_to_hunter_target(target_idx)
        self.enter_hunter(target)

    def open_hunter_page(self):
        self.ensure_main()
        self.log_info("打开F1面板并切换至异象追猎页签")
        self.open_f1_domain_page()
        self.sleep(0.5)
        self.operate_click(*self.pos.panels.f1.domain.hunter)
        self.sleep(0.5)

    def normalize_target(self, target: str) -> str:
        if target not in self.HUNTER_TARGETS:
            self.log_warning(f"未知追猎目标: {target}，默认执行第一个目标")
            return self.TARGET_SOUND_KING
        return target

    def get_target_idx(self, target: str):
        target = self.normalize_target(target)
        return self.HUNTER_TARGETS.index(target)

    def travel_to_hunter_target(self, target_idx: int):
        self.log_info(f"正在选择第 {target_idx} 个异象追猎目标并前往传送")
        page_idx = target_idx
        y_start = self.HUNTER_TRAVEL_Y_START
        if target_idx >= self.HUNTER_FIRST_PAGE_SIZE:
            self.turn_to_next_hunter_page()
            page_idx = target_idx - self.HUNTER_FIRST_PAGE_SIZE
            y_start = self.HUNTER_NEXT_PAGE_TRAVEL_Y_START

        y = y_start + page_idx * self.HUNTER_TRAVEL_Y_STEP
        self.operate_click(self.HUNTER_TRAVEL_X, y)
        self.click_traval_button()
        self.wait_in_team(time_out=300)

    def turn_to_next_hunter_page(self):
        self.log_info("异象追猎目标位于下一页，执行翻页")
        self.operate(
            lambda: self.scroll_relative(0.5, 0.5, -40),
            block=True,
        )
        self.sleep(0.5)

    def enter_hunter(self, target: str):
        self.sleep(3)
        self.walk_until_interac_or_combat(script=self.WALK_METHOD.get(target, ["w"]))
        if self.is_in_team() and self.find_interac():
            self.wait_until(
                lambda: not self.is_in_team(),
                pre_action=lambda: self.send_interac(handle_claim=False),
                time_out=5,
                raise_if_not_found=False,
            )

    def walk_until_interac_or_combat(
        self, script=[["w"]], time_out=10, run=False, raise_if_not_found=False
    ):
        def cond():
            return self.find_interac() or self.in_combat()

        if cond():
            return True
        direction = script[-1]
        ret = False
        try:
            self.middle_click(after_sleep=0.2)
            for step in script[:-1]:
                key = step[0]
                down_time = step[1]
                if isinstance(key, str) and isinstance(down_time, float):
                    self.send_key(key, down_time=down_time)
                    self.sleep(0.1)
            for key in direction:
                self.send_key_down(key)
            if run:
                self.sleep(0.1)
                self.send_key("lshift")
            ret = bool(
                self.wait_until(
                    cond,
                    time_out=time_out,
                    raise_if_not_found=raise_if_not_found,
                )
            )
        finally:
            for key in direction:
                self.send_key_up(key)
        return ret

    def find_boss_treasure(self):
        for feature_name in self.DEFAULT_TREASURE_FEATURES:
            mat = self.get_feature_by_name(feature_name).mat
            mat = cv2.cvtColor(mat, cv2.COLOR_BGR2GRAY)
            mat_h, mat_w = mat.shape[:2]
            start_scale = 1.0
            template = mat
            for scale in np.arange(start_scale, 0.8, -0.1):
                if scale < start_scale:
                    template = cv2.resize(
                        mat,
                        (int(mat_w * scale), int(mat_h * scale)),
                        interpolation=cv2.INTER_NEAREST,
                    )
                if result := self.find_one(
                    feature_name=feature_name,
                    template=template,
                    box=self.pos.screen.main_viewport.to_box(),
                    threshold=self.BOSS_TREASURE_THRESHOLD,
                    frame_processor=lambda frame: cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                ):
                    return result

    def rotate_and_find_treasure(self):
        return self.rotate_and_find(self.find_boss_treasure, self.is_boss)

    def walk_to_boss_treasure(self):
        if self.rotate_and_find_treasure():
            self.log_warning("前往BOSS宝箱中")
            if self.walk_to_box(
                self.find_boss_treasure,
                time_out=self.BOSS_TREASURE_WALK_TIMEOUT,
                end_condition=self.find_interac,
                y_offset=0.1,
                x_threshold=0.15,
            ):
                return True
            self.log_warning("前往BOSS宝箱超时, 判定为失败")
        return False

    def is_claim_btn_ready(self):
        return self.find_confirm(
            box=self.pos.screen.main_viewport.to_box(),
            threshold=0.7,
        )

    def exit_reward_interaction(self):
        self.send_key("esc")
        self.sleep(1)
        self.operate_click(0.609, 0.659, after_sleep=2)

    def do_combat_and_claim(self):
        self.log_info("战斗前检查是否有上次未领取的BOSS宝箱")
        if self.rotate_and_find_treasure():
            self.log_info("发现BOSS宝箱, 跳过战斗")
        else:
            self.log_info("未发现BOSS宝箱, 调用战斗模块")
            if self.walk_until_combat(run=True, delay=1):
                self.combat_once(retarget_turn=False)
            else:
                return False

        self.log_info("调用领取BOSS宝箱模块")

        if not self.walk_to_boss_treasure():
            return

        self.log_info("正在领取交互中")

        if self.wait_until(
            self.is_claim_btn_ready,
            pre_action=lambda: self.send_interac(handle_claim=False),
            raise_if_not_found=False,
            time_out=5,
        ):
            self.log_info("发现奖励领取页面，领取奖励")
            if self.wait_until(
                self.is_in_team,
                pre_action=lambda: self.operate_click(0.609, 0.659, after_sleep=2),
            ):
                return True
