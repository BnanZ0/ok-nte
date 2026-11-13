import re
import time
import unicodedata
from datetime import date, datetime

from ok import (
    Box,
    CannotFindException,
    TaskDisabledException,
    WaitFailedException,
    og,
)

from src.combat.BaseCombatTask import BaseCombatTask, TeamSurvivalStatus
from src.Labels import Labels
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.utils.game_templates import WEEKLYBOSS_ITEM


class AnomalyPilgrimage(NTEOneTimeTask, BaseCombatTask):
    """Cycle weekly targets within the reward and stamina budgets."""

    TASK_NAME = "异象巡礼"
    CONF_TARGET = "目标"
    CONF_START_DATE = "轮换起始日期"
    CONF_DAILY_START_DAY = "每周开始运行日"
    WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    WEEKLY_REWARD_LIMIT = 3
    STAMINA_COST = 60
    MAX_COMBAT_RETRIES = 3
    MAX_LIST_SCROLLS = 20
    SCROLL_STEP = 12
    SCROLL_SETTLE_TIME = 0.3
    MATCH_THRESHOLD = 0.7
    REMAINING_ATTEMPTS_BOX = (0.409, 0.206, 0.668, 0.254)
    CHALLENGE_CONFIRM_BOX = (0.879, 0.860, 0.941, 0.953)
    TEAM_CONFIRM_BOX = (0.341, 0.883, 0.407, 0.973)
    REWARD_CLAIM_BOX = (0.438, 0.615, 0.595, 0.685)
    WIPE_RETRY_BOX = (0.591, 0.700, 0.685, 0.790)
    WIPE_EXIT_BOX = (0.415, 0.700, 0.503, 0.790)
    REWARD_RETRY_BOX = (0.620, 0.825, 0.750, 0.905)
    REWARD_EXIT_BOX = (0.380, 0.825, 0.499, 0.905)
    REMAINING_ATTEMPTS_RE = re.compile(r"(?<![\d.+-])([0-3])\s*/\s*3(?![\d.])")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.TASK_NAME
        self.description = "按目标清单顺序进行异象巡礼"
        self.setup_config(self)

    @classmethod
    def setup_config(cls, instance: "BaseCombatTask", daily=False):
        instance.default_config.update({cls.CONF_TARGET: []})
        instance.config_type.update(
            {
                cls.CONF_TARGET: {
                    "options_available": list(WEEKLYBOSS_ITEM.list_names()),
                    "allow_duplication": True,
                },
            }
        )
        instance.config_description.update(
            {cls.CONF_TARGET: "按清单顺序循环, 末项后回到首项; 重复选择表示重复挑战同一目标"}
        )
        if daily:
            instance.default_config.update(
                {cls.CONF_DAILY_START_DAY: "周五", cls.CONF_START_DATE: ""}
            )
            instance.config_type.update(
                {cls.CONF_DAILY_START_DAY: {"type": "drop_down", "options": cls.WEEKDAYS}}
            )
            instance.config_description.update(
                {
                    cls.CONF_DAILY_START_DAY: "仅在所选星期至周日运行, 本周更早的日期会跳过。",
                    cls.CONF_START_DATE: (
                        "可选, 格式 YYYY-MM-DD; 每满7天按清单推进3项, 本周已领取次数也计入进度。"
                        "留空则每次从清单首项开始循环。"
                    ),
                }
            )

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            raise
        except Exception as error:
            self.log_error("异象巡礼任务失败", error)
            raise

    def do_run(self) -> bool | None:
        """Run the affordable queue and count only confirmed reward claims."""
        today = datetime.now().astimezone().date()
        start_day = self.config.get(self.CONF_DAILY_START_DAY)
        if start_day is not None and today.weekday() < self.WEEKDAYS.index(start_day):
            return None
        try:
            targets = self.resolve_targets()
            offset = self.rotation_offset(today)
            self.info_set("完成次数", 0)
            self.info_set("失败目标", [])
            completed_count = 0
            failed_targets: list[str] = []
            self.open_target_list()
            remaining = self.read_remaining_attempts()
            stamina = self.get_stamina()
            if stamina < 0:
                raise CannotFindException("无法识别巡礼可用体力")
            self.info_set("本周剩余次数", remaining)
            limit = min(remaining, stamina // self.STAMINA_COST)
            self.info_set("计划挑战次数", limit)
            self.info_set("消耗体力", 0)
            if limit == 0:
                self.log_info("巡礼剩余次数或体力不足, 停止任务")
                return False
            offset = 0 if offset is None else offset + self.WEEKLY_REWARD_LIMIT - remaining
            queue = [targets[(offset + index) % len(targets)] for index in range(limit)]
            while queue:
                target = queue.pop(0)
                self.info_set(self.CONF_TARGET, target)
                if not self.enter_target(target):
                    self.log_warning("未能进入巡礼关卡, 停止任务")
                    return False
                if not self.run_target_combat(target):
                    failed_targets.append(target)
                    self.info_set("失败目标", list(failed_targets))
                    queue = [pending for pending in queue if pending != target]
                    self.log_warning(f"巡礼目标重试耗尽, 本轮跳过该目标: {target}")
                else:
                    if not self.claim_target_reward():
                        self.log_warning("未能确认巡礼领奖成功, 停止任务")
                        return False
                    completed_count += 1
                    self.info_set("完成次数", completed_count)
                    self.info_set("本周剩余次数", remaining - completed_count)
                    self.info_set("消耗体力", completed_count * self.STAMINA_COST)
                if queue:
                    if not self.return_to_target_list():
                        self.log_warning("未能返回巡礼列表, 停止任务")
                        return False
                elif not self.exit_target():
                    return False
            if failed_targets:
                self.log_warning("异象巡礼目标清单已处理, 部分目标挑战失败")
            else:
                self.log_info("异象巡礼目标清单已完成")
            return not failed_targets
        finally:
            if og.my_app is not None:
                og.my_app.release_openvino_model("general")

    def open_target_list(self) -> None:
        self.ensure_main()
        self.open_f1_domain_page()
        self.operate_click(*self.pos.panels.f1.domain.pilgrimage, after_sleep=1.5)

    def enter_target(self, target: str) -> bool:
        """Find the target in the open list, reopening it before each retry."""

        def action(count):
            try:
                if count > 1:
                    self.open_target_list()
                match = self.find_pilgrimage_target(target)
                _, y = match.center()
                x, _ = self.pos.panels.f1.domain.goto
                self.operate_click(x, y / self.height, after_sleep=0.5)
                return self.click_traval_button(raise_if_not_found=False)
            except (CannotFindException, WaitFailedException):
                self.log_warning(f"未能传送至巡礼目标, 重试导航: {target}")
                return False

        if not self.retry_on_action(action):
            return False
        if not self.wait_in_team(time_out=300):
            return False
        return self.enter_target_panel()

    def enter_target_panel(self) -> bool:
        """Open the interaction panel and confirm both steps before waiting for the arena."""

        def merged_action(interac):
            self.send_key_down("lalt")
            time.sleep(0.1)
            self.click(interac, move=True)
            time.sleep(0.1)
            self.send_key_up("lalt")

        def click_interac():
            if interacs := self.find_interac():
                interac = min(interacs, key=lambda x: x.y)
                self.operate(lambda: merged_action(interac), block=True)
                self.sleep(1)

        def action():
            try:
                if self.find_one(Labels.stamina_icon):
                    return True
                self.walk_until_interac(direction="w", raise_if_not_found=True)
                self.sleep(1)
                self.wait_until(
                    lambda: not self.find_interac(),
                    post_action=click_interac,
                    time_out=10,
                    settle_time=0.5,
                )
                return self.wait_until(
                    lambda: self.find_one(Labels.stamina_icon), settle_time=0.5, time_out=10
                )
            except (CannotFindException, WaitFailedException):
                self.log_warning("未能确认巡礼进入面板, 重试交互")
                return False

        if not self.retry_on_action(action, reset_action=self.ensure_main):
            return False
        if not self.wait_click_confirm(range=self.CHALLENGE_CONFIRM_BOX):
            return False
        if not self.wait_click_confirm(range=self.TEAM_CONFIRM_BOX):
            return False
        return bool(self.wait_in_team(time_out=300))

    def run_target_combat(self, target: str) -> bool:
        """Retry team wipes in the arena with a fresh budget for each encounter."""
        for retry in range(self.MAX_COMBAT_RETRIES + 1):
            self.info_set("重新挑战次数", retry)
            if retry:
                self.log_info(f"重新挑战巡礼目标 {retry}/{self.MAX_COMBAT_RETRIES}: {target}")
                if not self.retry_target(target):
                    raise CannotFindException("未能确认巡礼重新挑战成功")
            status = self.fight_target_once()
            if status is not TeamSurvivalStatus.WIPED:
                return True
        return False

    def fight_target_once(self) -> TeamSurvivalStatus:
        """Walk to the encounter, verify combat began and let the combat helper take over."""
        self.walk_until_combat(direction="w", raise_if_not_found=True)
        return self.combat_once(wait_combat_time=60)

    def claim_target_reward(self) -> bool:
        """Claim once and verify success, rather than treating a click as success."""

        claim_box = self.box_of_screen(*self.REWARD_CLAIM_BOX, hcenter=True)

        def action():
            if self.find_confirm(box=claim_box):
                return True
            self.walk_to_box(
                find_function=self.find_boss_claim, end_condition=self.find_interac, x_threshold=0.1
            )
            self.send_interac(handle_claim=False)
            return self.wait_until(
                lambda: self.find_confirm(box=claim_box),
                time_out=5,
                raise_if_not_found=False,
            )

        if not self.find_confirm(box=claim_box):
            self.rotate_and_find(find=self.find_boss_claim, interrupt=self.find_interac, count=8)
        if not self.retry_on_action(action):
            return False
        if not self.wait_click_confirm(range=self.REWARD_CLAIM_BOX):
            return False
        exit_box = self.box_of_screen(*self.REWARD_EXIT_BOX, hcenter=True)
        retry_box = self.box_of_screen(*self.REWARD_RETRY_BOX, hcenter=True)
        return bool(
            self.wait_until(
                lambda: self.find_confirm(box=exit_box) and self.find_confirm(box=retry_box),
                time_out=20,
                settle_time=0.5,
            )
        )

    def retry_target(self, target: str) -> bool:
        """Restart a wiped team in the same arena, without leaving the encounter."""
        if not self.wait_click_confirm(range=self.WIPE_RETRY_BOX, time_out=30):
            return False
        return bool(self.wait_in_team(time_out=300, settle_time=1))

    def exit_target(self) -> bool:
        """Exit the encounter and recover the team at the nearest map teleport."""
        wipe_box = self.box_of_screen(*self.WIPE_EXIT_BOX, hcenter=True)
        reward_box = self.box_of_screen(*self.REWARD_EXIT_BOX, hcenter=True)
        button = self.wait_until(
            lambda: self.find_confirm(box=wipe_box) or self.find_confirm(box=reward_box),
            time_out=30,
            settle_time=0.5,
        )
        if not button or not self.wait_click_confirm(range=button):
            return False
        if not self.wait_in_team(time_out=300, settle_time=1):
            return False
        self.click_nearest_map_teleport()
        return bool(self.wait_in_team(time_out=300, settle_time=1))

    def return_to_target_list(self) -> bool:
        """Exit to the world before reopening the F1 pilgrimage list."""
        if not self.exit_target():
            return False
        self.open_target_list()
        return True

    def read_remaining_attempts(self) -> int:
        """Wait for a readable counter in the current page."""
        box = self.box_of_screen(
            *self.REMAINING_ATTEMPTS_BOX, name="pilgrimage_remaining_attempts", hcenter=True
        )

        def find_counter():
            results = self.ocr(box=box, match=None) or []
            remaining = self.parse_remaining_attempts(" ".join(result.name for result in results))
            # A tuple keeps a valid zero truthy for wait_until.
            return (remaining,) if remaining is not None else None

        result = self.wait_until(find_counter, time_out=3, settle_time=0)
        if result is None:
            raise CannotFindException("无法识别巡礼剩余次数")
        return result[0]

    def find_pilgrimage_target(self, target: str) -> Box:
        """Match before scrolling and check the last view too."""
        prepared = WEEKLYBOSS_ITEM.get_template(target, self.height)
        if prepared is None:
            raise ValueError(f"无法读取巡礼素材: {target}")
        template, mask = prepared
        mask_function = (lambda _: mask) if mask is not None else None
        box = self.get_target_list_box()
        if template.shape[1] > box.width or template.shape[0] > box.height:
            raise ValueError(f"巡礼素材大于列表搜索区域, 请检查尺寸适配: {target}")
        self.reset_target_list(box)
        end_reached = False
        for step in range(self.MAX_LIST_SCROLLS + 1):
            match = self.find_one(
                target,
                template=template,
                box=box,
                threshold=self.MATCH_THRESHOLD,
                mask_function=mask_function,
            )
            if match is not None:
                return match
            if end_reached or step == self.MAX_LIST_SCROLLS:
                break
            end_reached = self.scroll_target_list(box, -self.SCROLL_STEP)
        raise CannotFindException(f"巡礼列表中未找到目标: {target}")

    def find_boss_claim(self):
        return self.openvino_detect(
            model="general",
            label="boss_claim",
            sync=True,
            box=self.pos.screen.main_viewport.to_box(),
            threshold=0.5,
            mask_regions=[(0.020, 0.017, 0.145, 0.240)],
        )

    def get_target_list_box(self) -> Box:
        """Keep icon matching within the authored material search region."""
        return self.pos.panels.f1.domain.search_area.to_box()

    def scroll_target_list(self, box: Box, count: int) -> bool:
        """Return whether the list stopped moving, using the existing UI helper."""
        x, y = self.pos.panels.f1.domain.scroll_area.to_box().center()
        return self.scroll_and_is_end(
            x,
            y,
            count,
            snap_box=box,
            check_box=box,
            after_sleep=self.SCROLL_SETTLE_TIME,
        )

    def reset_target_list(self, box: Box) -> None:
        """Start each search at the top, even after returning from another boss."""
        for _ in range(self.MAX_LIST_SCROLLS):
            if self.scroll_target_list(box, self.SCROLL_STEP):
                return
        raise CannotFindException("无法将巡礼列表滚动到顶部")

    def resolve_targets(self) -> list[str]:
        """Validate the complete queue without discarding duplicates or entries."""
        names = WEEKLYBOSS_ITEM.list_names()
        if not names:
            raise ValueError("没有可用的巡礼素材, 请检查资源是否完整")
        targets = self.config.get(self.CONF_TARGET, [])
        if not isinstance(targets, list) or not targets:
            raise ValueError("请至少选择1项巡礼目标")
        if any(not isinstance(target, str) or target not in names for target in targets):
            raise ValueError("巡礼目标包含无效选项, 请重新选择")
        return list(targets)

    def rotation_offset(self, today: date) -> int | None:
        """Advance three queue positions per complete week; a blank date disables rotation."""
        value = self.config.get(self.CONF_START_DATE, "").strip()
        if not value:
            return None
        try:
            start = date.fromisoformat(value)
        except ValueError:
            raise ValueError("轮换起始日期格式错误, 请使用 YYYY-MM-DD") from None
        if start > today:
            raise ValueError("轮换起始日期不能晚于当前日期")
        return ((today - start).days // 7) * self.WEEKLY_REWARD_LIMIT

    @staticmethod
    def parse_remaining_attempts(text: str) -> int | None:
        """Accept an unambiguous n/3 counter, including spacing and full-width text."""
        normalized = unicodedata.normalize("NFKC", text)
        counts = {
            int(match.group(1))
            for match in AnomalyPilgrimage.REMAINING_ATTEMPTS_RE.finditer(normalized)
        }
        return counts.pop() if len(counts) == 1 else None
