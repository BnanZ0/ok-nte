from ok import CannotFindException, TaskDisabledException, find_color_rectangles

from src import text_white_color
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.daily.ActivityPanelMixin import ActivityPanelMixin
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.utils import image_utils as iu


class DailyClaimTask(ActivityPanelMixin, NTEOneTimeTask, BaseNTETask):
    CONF_CLAIM_MAIL = "邮件"
    CONF_CLAIM_ACTIVITY = "活跃度奖励"
    CONF_CLAIM_BATTLE_PASS = "环期任务奖励"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常领取"
        self.description = "领取每日邮件和奖励"
        self.visible = False
        self.default_config.update(
            {
                self.CONF_CLAIM_MAIL: True,
                self.CONF_CLAIM_ACTIVITY: True,
                self.CONF_CLAIM_BATTLE_PASS: True,
            }
        )

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error("DailyClaimTask error", e)
            raise

    def do_run(self) -> bool:
        results = []
        tasks = {
            self.CONF_CLAIM_MAIL: self.claim_mail,
            self.CONF_CLAIM_ACTIVITY: self.claim_activity_rewards,
            self.CONF_CLAIM_BATTLE_PASS: self.claim_battle_pass_rewards,
        }
        for key, task in tasks.items():
            self.ensure_main()
            if self.config.get(key, True):
                results.append(task())

        return all(result is not False for result in results)

    def open_mail_panel(self):
        def action():
            self.openESCpanel()
            self.operate_click(0.8707, 0.8736)
            self.sleep(0.5)
            return self.wait_panel(Labels.mail_panel)

        self.log_info("正在打开邮件面板")
        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("无法找到邮件面板", notify=True)
            raise CannotFindException("can't find mail panel")
        return result

    def claim_mail(self):
        self.log_info("正在领取邮件奖励")
        self.open_mail_panel()
        self.operate_click(0.1289, 0.9299)
        self.sleep(1)
        return True

    def claim_activity_rewards(self):
        self.log_info("正在领取活跃度奖励")
        if not self.open_activity_panel():
            return False
        if self.find_one(Labels.f1_activity_mission):
            self.operate_click(0.2348, 0.7653)
            self.sleep(2)

        target = self.get_activity_reward_box()
        if not target:
            self.log_error("无法找到活跃度奖励领取框")
            return False
        self.wait_until(
            lambda: not self.get_activity_reward_box(),
            pre_action=lambda: self.operate_click(target, interval=1),
        )
        self.sleep(1)
        return True

    def get_activity_reward_box(self):
        box = self.get_box_by_name(Labels.box_f1_activity_reward)
        mask = iu.binarize_bgr_by_brightness(self.frame, threshold=245, to_bgr=False)
        mask = iu.morphology_mask(mask, kernel_size=7, to_bgr=True)
        reward_boxes = find_color_rectangles(
            mask, color_range=text_white_color, min_width=10, min_height=10, box=box, threshold=0.6
        )
        if reward_boxes:
            target = max(reward_boxes, key=lambda candidate: candidate.x)
            self.draw_boxes(boxes=target)
            return target
        return None

    def claim_battle_pass_rewards(self):
        def action():
            self.openF2panel()
            self.operate_click(0.0570, 0.3451)
            self.sleep(0.5)
            return self.wait_panel(Labels.f2_mission_panel)

        self.log_info("正在领取环期任务奖励")
        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("无法找到环期任务面板")
            return False
        self.operate_click(0.8777, 0.8187)
        self.sleep(1)
        self.operate_click(0.0570, 0.2333)
        self.sleep(1)
        self.operate_click(0.6934, 0.8229)
        self.sleep(1)
        return True
