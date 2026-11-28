"""离线仿真：用假任务跑完整 UI 流程，不需要游戏。

flow 在导入时就会 import cv2，宝箱缩放用例还会真正做图像缩放，
所以跑这套测试需要 opencv（opencv-python-headless 即可），但不需要游戏本体。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

from src.tasks.cultivation_goal.core import MaterialRow, Ratio, parse_ratios
from src.tasks.cultivation_goal.flow import (
    CG_BURST_CONFIRM_RANGE,
    CG_CONSOLE_STAMINA_OPEN,
    CG_FINAL_CONFIRM_RANGE,
    CultivationGoalMixin,
)
from src.tasks.cultivation_goal.flow_weekly import WeeklyMixin, weekly_entry_is_locked
from src.tasks.cultivation_goal.report import RunReport, format_records, read_records

DEFAULT_TEXTS = {
    # 周本材料（异能第2行）默认按已满足给, 免得每个用例都被第一顺位的周本抢走;
    # 要测周本的用例显式传 texts 覆盖。
    "异能-第2行": "3/2",
    "等级-第2行": "0/76",
    "异能-第1行": "0/9",
    "弧盘-第2行": "0/10 0/15",
    # 空幕页只给套装名, 关卡靠套装名反查; 默认「失落光芒」= 钟表把戏 = 序号 1。
    "套装-推荐": "推荐套装 「失落光芒」x4",
    "套装-当前": "当前套装 「失落光芒」x4",
}


class FakeBox:
    """OCR 结果最小替身: 只有 name 和 center(), 够流程分支用。"""

    def __init__(self, name, x=1500, y=320, width=210, height=62):
        self.name = name
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    def center(self):
        return self.x + self.width / 2, self.y + self.height / 2

    def copy(self, x_offset=0):
        return FakeBox(self.name, self.x + x_offset, self.y, self.width, self.height)


class ClaimWaitTests(unittest.TestCase):
    """11-20 实机: 按完 F 领奖界面晚一帧弹出, 单帧检测连续三次扑空 -> 误判「领奖未确认」。"""

    class _Stub(CultivationGoalMixin):
        def __init__(self, hits_at, opened_before=False, retry_after=1):
            self.hits_at = hits_at
            self.opened_before = opened_before
            self.retry_after = retry_after
            self.finds = 0
            self.finds_after_interact = 0
            self.interacts = 0
            self.clicks = []
            self.notes = []

        def log_info(self, *a, **k):
            pass

        def walk_until_combat(self, *a, **k):
            pass

        def combat_once(self, *a, **k):
            pass

        def rotate_and_find_treasure(self):
            pass

        def walk_to_treasure(self):
            pass

        def find_treasure(self):
            return FakeBox("treasure")

        def find_interac(self):
            return [FakeBox("interac")]

        def width_of_screen(self, ratio):
            return int(1920 * ratio)

        def middle_click(self, **k):
            pass

        def walk_until_interac(self, direction="w", time_out=10):
            return False

        def move_mouse_relative(self, dx, dy):
            pass

        def send_key(self, key, **k):
            if key == "f":
                self.interacts += 1
                self.finds_after_interact = 0

        def send_key_down(self, *a, **k):
            pass

        def send_key_up(self, *a, **k):
            pass

        def sleep(self, *a, **k):
            pass

        def send_interac(self, **k):
            self.interacts += 1
            self.finds_after_interact = 0

        def box_of_screen(self, *a, **k):
            return SimpleNamespace(name=k.get("name"))

        def find_feature(self, label, box=None):
            self.finds += 1
            if not self.interacts:
                return [FakeBox("claim")] if self.opened_before else []
            if self.interacts < self.retry_after:
                return []
            self.finds_after_interact += 1
            return [FakeBox("claim")] if self.finds_after_interact >= self.hits_at else []

        def wait_until(self, condition, *a, **k):
            for _ in range(8):
                result = condition()
                if result:
                    return result
            return None

        def retry_on_action(self, action, **k):
            return action()

        def operate_click(self, *a, **k):
            self.clicks.append(a)

        def _cg_note(self, kind, message):
            self.notes.append(message)

    def test_claim_button_found_after_waiting_frames(self):
        """按钮第 3 帧才出现也要等到, 不能直接判领奖未确认。"""
        task = self._Stub(hits_at=3)
        self.assertTrue(task.do_combat_and_claim(False))
        self.assertEqual(task.interacts, 1)
        self.assertEqual(len(task.clicks), 1)

    def test_claim_existing_button_skips_interact(self):
        """按钮已经在(上次交互已打开界面)就不该再按 F, 免得把界面切换关闭。"""
        task = self._Stub(hits_at=1, opened_before=True)
        self.assertTrue(task.do_combat_and_claim(False))
        self.assertEqual(task.interacts, 0)

    def test_claim_missing_button_fails_cleanly(self):
        task = self._Stub(hits_at=10 ** 6)
        self.assertFalse(task.do_combat_and_claim(False))
        self.assertEqual(task.clicks, [])
        self.assertTrue(any("没找到领奖按钮" in m for m in task.notes))

    def test_claim_walks_closer_when_f_has_no_effect(self):
        """第一轮按 F 没反应(还站在提示范围外 2m), 走近半步后再按就该领到。"""
        task = self._Stub(hits_at=1, retry_after=2)
        self.assertTrue(task.do_combat_and_claim(False))
        self.assertEqual(task.interacts, 2)
        self.assertEqual(len(task.clicks), 1)


class RewardSettledTests(unittest.TestCase):
    """结算判定不能把常驻的 ESC 图标当到账信号。

    旧版 `find_exit() or ...` 恒真(左上角 ESC 在副本内一直存在), 没领到也会记成已领,
    账本虚记一次领取并扣掉体力。这里锁死: 只有结算页确认按钮算数。
    """

    class _Stub:
        _cg_reward_settled = CultivationGoalMixin._cg_reward_settled

        def __init__(self, confirm_after=0):
            self.frames = 0
            self.confirm_after = confirm_after
            self.notes = []

        def box_of_screen(self, *a, **k):
            return FakeBox("range")

        def find_confirm(self, box=None):
            self.frames += 1
            return FakeBox("confirm") if self.frames > self.confirm_after else None

        def find_exit(self):
            return FakeBox("exit")

        def wait_until(self, condition, *a, **k):
            for _ in range(8):
                result = condition()
                if result:
                    return result
            return None

        def _cg_note(self, kind, message):
            self.notes.append(message)

    def test_esc_icon_alone_is_not_a_settlement(self):
        task = self._Stub(confirm_after=10 ** 6)
        self.assertFalse(task._cg_reward_settled())
        self.assertTrue(any("确认框未出现" in m for m in task.notes))

    def test_confirm_button_counts_as_settlement(self):
        task = self._Stub(confirm_after=2)
        self.assertTrue(task._cg_reward_settled())


class FakeTask(CultivationGoalMixin):
    TASK_CONSOLE = "空幕"
    CONF_TASK_TYPE = "任务类型"

    def __init__(
        self,
        *,
        stamina=300,
        weekly_text="3/3",
        texts=None,
        claim=True,
        landing="domain",
        daily_target=180,
        guide_ok=True,
        weekly_ok=True,
        weekly_first=False,
        report=None,
    ):
        self.config = {
            self.CONF_TASK_TYPE: "异象界域",
            self.CONF_CG_WEEKLY_FIRST: weekly_first,
        }
        self.info = {}
        self.logs = []
        self.clicks = []
        self.captures = []
        self.claims = []
        self.claim_flags = []
        self.reward_scans = []
        self.weekly_text = weekly_text
        self.stamina_script = stamina
        self.texts = texts if texts is not None else DEFAULT_TEXTS
        self.claim_script = claim
        self.landing = landing
        self.daily_target = daily_target
        self.guide_ok = guide_ok
        self.weekly_ok = weekly_ok
        self.weekly_claims = 1
        self.weekly_finished = True
        self.cg_report = report or RunReport(
            path=os.path.join(tempfile.gettempdir(), "cg_test_report.jsonl"),
            enabled=False,
        )
        self.round_index = -1
        self.probe_count = 0
        self.console_runs = 0
        self.weekly_runs = 0
        self.wild_runs = []
        self.walk_targets = []
        self.team_results = [True]
        self.walk_steps = 0
        self.boss_visible = False
        self.keys_down = []
        self.width = 1920
        self.height = 1080
        self.pos = SimpleNamespace(
            panels=SimpleNamespace(
                f1=SimpleNamespace(domain=SimpleNamespace(pilgrimage=(0.5, 0.5)))
            )
        )

    def _script(self, script, index, default=None):
        if callable(script):
            return script(index)
        if isinstance(script, (list, tuple)):
            return script[min(index, len(script) - 1)]
        return script if default is None else default

    def resolve_stamina_target(self):
        return self.daily_target

    def log_info(self, message, **kwargs):
        self.logs.append(("info", message))

    def log_warning(self, message, **kwargs):
        self.logs.append(("warning", message))

    def log_error(self, message, **kwargs):
        self.logs.append(("error", message))

    def info_set(self, key, value):
        self.info[key] = value

    def screenshot(self, name, **kwargs):
        self.captures.append(name)

    def _cg_scan_reward_list(self, tag):
        self.reward_scans.append(tag)
        return []

    def ensure_main(self, check_exit=True):
        pass

    def open_f1_domain(self):
        pass

    def openF1panel(self):
        pass

    def sleep(self, seconds):
        pass

    def is_boss(self):
        return self.boss_visible

    def send_key_down(self, key):
        self.keys_down.append(key)

    def send_key_up(self, key):
        self.walk_steps += 1

    def exit_anomaly(self):
        pass

    def wait_click_confirm(self, pre_action=None, range=None, on_found=None, **kwargs):
        """真实实现会在点掉确认按钮前调 on_found, 奖励界面抓拍就挂在那里。"""
        if callable(on_found):
            on_found()
        return True

    def click_traval_button(self, **kwargs):
        return True

    def find_traval_button(self):
        return self.landing == "map"

    def find_stamina_icon(self):
        return False

    def find_exit(self):
        return True

    def find_confirm(self, box=None, **kwargs):
        """结算确认框替身: 领奖后视为已出现。"""
        return True

    def wait_in_team(self, **kwargs):
        index = getattr(self, "_team_index", 0)
        self._team_index = index + 1
        return self.team_results[min(index, len(self.team_results) - 1)]

    def bring_to_front(self):
        self.front_calls = getattr(self, "front_calls", 0) + 1

    def operate_click(self, *args, **kwargs):
        self.clicks.append(args)

    def box_of_screen(self, *args, name=None, **kwargs):
        return SimpleNamespace(name=name or "")

    def wait_until(self, condition, *args, **kwargs):
        pre = kwargs.get("pre_action")
        if callable(pre):
            pre()
        return condition()

    def ocr(self, box=None, match=None, **kwargs):
        name = getattr(box, "name", "")
        if name == "cg_weekly":
            text = self.weekly_text
        elif name == "cg_text":
            self.probe_count += 1
            text = "前往培养" if self.probe_count % 2 == 1 else "养成指南 角色总览 所需材料"
        elif name in ("cg_screen", "cg_find"):
            text = self.landing_text()
        else:
            current = self._script(self.texts, self.round_index)
            parts = name.split("_")
            if name.startswith("cg_console_set_"):
                # 空幕页只有「当前套装 / 推荐套装」两行文字, 0 = 当前, 1 = 推荐。
                label = "套装-当前" if name.endswith("_0") else "套装-推荐"
            elif len(parts) == 3 and parts[2].isdigit():
                label = f"{parts[1]}-第{int(parts[2]) + 1}行"
            else:
                label = name
            text = current.get(label, "") if isinstance(current, dict) else ""
        return [FakeBox(text)] if text else []

    def get_stamina(self):
        self.round_index += 1
        return self._script(self.stamina_script, self.round_index)

    def do_combat_and_claim(self, flag):
        ok = self._script(self.claim_script, self.round_index)
        self.claims.append(ok)
        self.claim_flags.append(flag)
        return ok

    def _cg_wait_text(self, pattern, rect):
        return self.guide_ok

    def landing_text(self):
        return {
            # 真实大地图落点: 右侧是追猎面板(BOSS 名 + 消耗 60) + 底部传送按钮
            "map": "塞润尼缇 收取奖励消耗 x3×60 传送",
            "domain": "进入 消耗 40",
            "hunter": "塞润尼缇 前往",
            "stuck": "培养所需材料 获取材料",
        }.get(self.landing, "")

    def enter_hunter(self, target=None):
        """上游 enter_hunter 会按住方向键走到怪跟前, 仿真里只记录目标名。"""
        self.walk_targets.append(target)
        return True

    def _cg_fight_wild(self, row):
        self.wild_runs.append(row.label)
        return True

    def _cg_run_weekly(self):
        """周本走的是真实 AnomalyPilgrimage, 仿真只验证外层记账: (领到几次, 上游是否跑完)。"""
        self.weekly_runs += 1
        if not self.weekly_ok:
            return 0, False
        return self.weekly_claims, self.weekly_finished

    def info_get(self, key, default=None):
        return self.info.get(key, default)

    def enter_anomaly(self, idx, retry=False):
        """空幕入场替身: 记住序号与当时的任务类型, 并按 must use stamina 排局数。"""
        self.console_entries = getattr(self, "console_entries", [])
        self.console_entries.append((idx, retry))
        self.console_type_seen = self.config.get(self.CONF_TASK_TYPE)
        self.console_id_seen = self.config.get("空幕序号")
        if getattr(self, "console_entry_fails", 0) > 0:
            self.console_entry_fails -= 1
            return False
        units = int(self.info.get("must use stamina", 0) or 0) // 40
        double_count, single_count = divmod(units, 2)
        self.info["双倍次数"] = double_count
        self.info["单倍次数"] = single_count
        self.console_runs += 1
        return True


def actions_from(report_path):
    return [
        r
        for r in read_records(report_path)
        if r["event"] == "action" and r.get("action") != "log"
    ]


class FlowSimulationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.report_path = os.path.join(self.tmp.name, "cg.jsonl")
        self.report = RunReport(path=self.report_path)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kwargs):
        kwargs.setdefault("report", self.report)
        return FakeTask(**kwargs)

    def test_weekly_first_then_material_then_stop(self):
        # 周本内部把本周 3 次一次领完, 外层只记 1 次调用, 之后不再尝试周本。
        # 这里只验动作顺序, 目标放开, 免得 180 预算先把周本花光就收场。
        task = self.make(stamina=[300, 60, 45, 39], weekly_first=True, daily_target=None)
        task.weekly_claims = 3
        result = task.run_cultivation_goal()
        records = read_records(self.report_path)
        actions = actions_from(self.report_path)

        self.assertTrue(result)
        self.assertEqual([a["action"] for a in actions], ["weekly", "material", "material"])
        # 周本材料已满 → 周本分支只在有剩余次数时跑一次; 之后按 等级 > 异能 > 弧盘。
        self.assertEqual([a["label"] for a in actions[-2:]], ["等级-第2行", "异能-第1行"])
        self.assertTrue(all(a["ok"] for a in actions))
        self.assertEqual(task.weekly_runs, 1)
        stop = records[-1]
        self.assertEqual(stop["event"], "stop")
        self.assertEqual(stop["weekly_done"], 3)
        self.assertEqual(stop["material_done"], 2)
        self.assertIn("不够", stop["reason"])
        self.assertEqual(task.info["周本剩余次数"], 0)
        self.assertIn("培养目标统计", task.info)

    def test_ledger_mode_advances_by_loot_without_re_reading(self):
        texts = {
            "异能-第2行": "3/2",
            "等级-第2行": "76/76",
            "异能-第1行": "0/9",
            "弧盘-第2行": "0/10 0/15",
        }
        task = self.make(stamina=[300] * 6, texts=[texts], daily_target=None)
        reads = {"n": 0}
        raw_read = task._cg_read_rows

        def counted_read():
            reads["n"] += 1
            return raw_read()

        task._cg_read_rows = counted_read
        calls = {"n": 0}

        def reader():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"异能-第1行": (9,)}
            return {"弧盘-第2行": (9, 9)}

        task.cg_loot_reader = reader
        task.run_cultivation_goal()
        records = read_records(self.report_path)
        actions = [
            r for r in records if r["event"] == "action" and r.get("action") == "material"
        ]
        rounds = [r for r in records if r["event"] == "round"]

        # 只在开局读一次面板; 后续缺口和体力都由账本推进。
        self.assertEqual(reads["n"], 1)
        self.assertEqual(
            [a["label"] for a in actions],
            ["异能-第1行", "弧盘-第2行", "弧盘-第2行"],
        )
        # 第 4 轮缺口 19 件最低档, 单份产出 36 件 → 一份就够, 走单倍(140→100)。
        # 旧口径把「件数」和「份」混着比, 这里会多打一份双倍, 白花 40 体力。
        self.assertEqual([r["stamina"] for r in rounds[:4]], [300, 220, 140, 100])
        self.assertTrue(any("账本模式" in msg for _, msg in task.logs))

    def test_ledger_mode_falls_back_to_page_when_loot_unreadable(self):
        texts = {
            "异能-第2行": "3/2",
            "等级-第2行": "76/76",
            "异能-第1行": "0/9",
            "弧盘-第2行": "0/10 0/15",
        }
        task = self.make(stamina=[300, 20], texts=[texts])
        reads = {"n": 0}
        raw_read = task._cg_read_rows

        def counted_read():
            reads["n"] += 1
            return raw_read()

        task._cg_read_rows = counted_read
        task.run_cultivation_goal()
        self.assertGreater(reads["n"], 1)
        self.assertTrue(any("本局掉落未能读取" in msg for _, msg in task.logs))

    def test_read_loot_maps_reward_cards_by_learned_slots(self):
        # 开局养成页认出等级第2行是黑之书，奖励卡认出黑之书×7 → 落到唯一槽位。
        task = self.make()
        task._cg_row_slots = {"等级-第2行": ("boss_黑之书",)}
        task._cg_reward_loot = [
            {"key": "boss_黑之书", "count": 7, "dungeon": "黑之书", "inliers": 7},
        ]
        self.assertEqual(
            task._cg_read_loot("等级-第2行"),
            {"等级-第2行": (7,)},
        )
        self.assertTrue(any("掉落入账" in msg for _, msg in task.logs))

    def test_read_loot_three_tiers_by_slot_order(self):
        task = self.make()
        slots = ("ability_扑克茶会_紫", "ability_扑克茶会_蓝", "ability_扑克茶会_绿")
        task._cg_row_slots = {"异能-第1行": slots}
        task._cg_reward_loot = [
            {"key": "ability_扑克茶会_绿", "count": 4, "dungeon": "扑克茶会", "inliers": 9},
            {"key": "ability_扑克茶会_紫", "count": 1, "dungeon": "扑克茶会", "inliers": 8},
        ]
        self.assertEqual(task._cg_read_loot("异能-第1行"), {"异能-第1行": (1, 0, 4)})

    def test_read_loot_unknown_card_or_missing_slots_returns_none(self):
        task = self.make()
        task._cg_row_slots = {"等级-第2行": ("boss_黑之书",)}
        task._cg_reward_loot = [{"key": "金币", "count": 12000, "dungeon": "金币", "inliers": 0}]
        self.assertIsNone(task._cg_read_loot("等级-第2行"))
        # 槽位没学到时也不能瞎记。
        task._cg_reward_loot = [{"key": "boss_黑之书", "count": 7, "dungeon": "黑之书", "inliers": 7}]
        self.assertIsNone(task._cg_read_loot("没学过槽位的行"))

    def test_injected_reader_still_takes_priority(self):
        task = self.make()
        task.cg_loot_reader = lambda: {"异能-第1行": (9,)}
        task._cg_reward_loot = [{"key": "金币", "count": 12000, "dungeon": "金币", "inliers": 0}]
        self.assertEqual(task._cg_read_loot("等级-第2行"), {"异能-第1行": (9,)})

    def test_burst_then_auto_switch_when_material_filled(self):
        # 第1局没产出记录 → 单局; 第2轮读到 +4 → 按每局 2 份连打 2 局;
        # 第3轮面板已满 → 主循环自动换到下一行(弧盘)。
        texts = [
            {
                "异能-第2行": "3/2",
                "等级-第2行": "76/76",
                "异能-第1行": "0/9",
                "弧盘-第2行": "0/10 0/15",
            },
            {
                "异能-第2行": "3/2",
                "等级-第2行": "76/76",
                "异能-第1行": "4/9",
                "弧盘-第2行": "0/10 0/15",
            },
            {
                "异能-第2行": "3/2",
                "等级-第2行": "76/76",
                "异能-第1行": "9/9",
                "弧盘-第2行": "0/10 0/15",
            },
        ]
        # 不设日限额: 本用例只看连打与换行, 预算语义另有专门用例。
        task = self.make(stamina=[300] * 6, texts=texts, daily_target=None)
        task.run_cultivation_goal()
        records = read_records(self.report_path)
        actions = [
            r for r in records if r["event"] == "action" and r.get("action") == "material"
        ]
        claims = [r for r in records if r["event"] == "claim"]

        self.assertEqual(
            [a["label"] for a in actions[:3]],
            ["异能-第1行", "异能-第1行", "弧盘-第2行"],
        )
        self.assertEqual(actions[1]["burst"], 2)
        self.assertGreaterEqual(actions[1]["per_claim"], 2)
        # 后两轮弧盘仍未满, 会继续单局刷; 这里只核对连打那两局的领取序列。
        self.assertGreaterEqual(len(claims), 3)
        # 提前收场修复后连打只打 2 局就断开; 第 3 次领取来自下一行(弧盘)的首局。
        self.assertEqual([c["double"] for c in claims[:3]], [True, True, True])
        self.assertEqual(claims[2]["label"], "弧盘-第2行")
        self.assertTrue(any("估算材料已够" in msg for _, msg in task.logs))

    def test_combine_marks_row_full_without_extra_runs(self):
        """低档能合上来就算够: 48/8 32/40 16/13 不该再为「32/40」白刷一局。"""
        texts = [
            {
                "异能-第2行": "3/2",
                "等级-第2行": "76/76",
                "异能-第1行": "48/8 32/40 16/13",
                "弧盘-第2行": "0/10",
            },
            {
                "异能-第2行": "3/2",
                "等级-第2行": "76/76",
                "异能-第1行": "48/8 32/40 16/13",
                "弧盘-第2行": "6/10",
            },
        ]
        task = self.make(stamina=[300] * 4, texts=texts, daily_target=None)
        task.run_cultivation_goal()
        labels = [a["label"] for a in actions_from(self.report_path) if a.get("action") == "material"]

        self.assertTrue(labels)
        self.assertEqual(labels[0], "弧盘-第2行")
        self.assertNotIn("异能-第1行", labels)
        self.assertTrue(any("折最低级" in msg for _, msg in task.logs))

    def test_burst_stops_immediately_on_failed_claim(self):
        row = MaterialRow("异能", 0, 40, "domain", parse_ratios("0/9"))
        task = self.make()
        calls = []

        def claim(flag):
            calls.append(flag)
            return len(calls) < 2

        task.do_combat_and_claim = claim
        self.assertTrue(task._cg_fight_once(row, "dungeon", 300, 3, 2))
        self.assertEqual(len(calls), 2)
        claims = [r for r in read_records(self.report_path) if r["event"] == "claim"]
        self.assertEqual(len(claims), 1)
        self.assertIn(
            "未确认领奖成功", " ".join(message for _, message in task.logs)
        )

    def test_read_failed_stops_without_fighting(self):
        task = self.make(guide_ok=False)
        self.assertIsNone(task.run_cultivation_goal())
        self.assertEqual(task.claims, [])
        self.assertIn("cg_goal_page_failed", task.captures)
        self.assertTrue(any("fail-closed" in message for _, message in task.logs))
        stop = read_records(self.report_path)[-1]
        self.assertEqual(stop["rounds"], 0)
        self.assertIn("读取失败", stop["reason"])

    def test_all_satisfied_switches_to_console_and_restores_config(self):
        """四行全满 → 空幕兜底; 干完要把任务类型和序号还原, 不能污染配置文件。"""
        task = self.make(
            weekly_text="0/3",
            texts={
                "等级-第2行": "1902/212",
                "弧盘-第2行": "3/0 33/17",
                "异能-第1行": "10/9",
                "异能-第2行": "3/2",
                # 推荐套装「恶魔之血：诅咒」属于雕塑展馆 = 序号 2。
                "套装-推荐": "推荐套装 「恶魔之血：诅咒」x4",
            },
        )
        self.assertTrue(task.run_cultivation_goal())
        self.assertEqual(task.console_type_seen, "空幕")
        self.assertEqual(task.console_id_seen, 2)
        self.assertEqual(task.config[task.CONF_TASK_TYPE], "异象界域")
        self.assertNotIn("空幕序号", task.config)
        self.assertNotIn("must use stamina", task.info)
        self.assertEqual(actions_from(self.report_path)[-1]["action"], "console")

    def test_claim_failure_does_not_count_weekly(self):
        task = self.make(stamina=[300, 39], weekly_ok=False, weekly_first=True)
        task.run_cultivation_goal()
        records = read_records(self.report_path)
        weekly_actions = [r for r in records if r["event"] == "action" and r["action"] == "weekly"]

        self.assertEqual(len(weekly_actions), 1)
        self.assertFalse(weekly_actions[0]["ok"])
        self.assertIn("cg_weekly_failed", task.captures)
        self.assertTrue(any("已领到 0 次" in m for _, m in task.logs))
        self.assertEqual(records[-1]["weekly_done"], 0)

    def test_partial_weekly_still_counts_claims(self):
        """上游第 2 个目标卡住时, 已领到的那 1 次必须算数, 不能整轮判失败。"""
        task = self.make(stamina=[300, 39], weekly_first=True)
        task.weekly_claims = 1
        task.weekly_finished = False
        task.run_cultivation_goal()
        records = read_records(self.report_path)
        actions = actions_from(self.report_path)
        weekly = [a for a in actions if a["action"] == "weekly"]

        self.assertEqual(len(weekly), 1)
        self.assertTrue(weekly[0]["ok"])
        self.assertTrue(weekly[-1]["ok"])
        self.assertEqual(records[-1]["weekly_done"], 1)
        self.assertEqual(task.info["周本剩余次数"], 2)
        # 没跑完就不再碰周本, 免得反复卡在同一个传送按钮上
        self.assertEqual(task.weekly_runs, 1)

    def test_weekly_row_skipped_when_panel_says_zero(self):
        """面板读到周本 0 次且缺料: 直接跳过该行去刷其它行, 不白跑一趟巡礼。

        以前是把它排在第一顺位再跳过, 每轮都要耗一次「领奖未确认」的风险;
        现在决策层就把它排除, 其它行都满了才轮到空幕兜底。
        """
        task = self.make(
            weekly_text="0/3",
            weekly_first=True,
            stamina=[300, 39],
            texts=[{**DEFAULT_TEXTS, "异能-第2行": "0/2"}, dict(DEFAULT_TEXTS)],
        )
        task.run_cultivation_goal()

        self.assertEqual(task.weekly_runs, 0)
        self.assertTrue(any("周本次数已用完" in m for _, m in task.logs), task.logs)
        self.assertFalse(any("领奖未确认" in m for _, m in task.logs), task.logs)
        first = actions_from(self.report_path)[0]
        self.assertEqual(first["action"], "material")
        self.assertEqual(first["label"], "等级-第2行")
        self.assertFalse(any("本局未完成" in m for _, m in task.logs), task.logs)

    def test_weekly_unconfirmed_claims_void_ledger(self):
        """周本有领取没看到结算确认: 已确认次数照记扣, 但账本作废回养成指南复核。

        对照组注入同样的可入账掉落: 没有未确认领取时掉落正常入账、不作废,
        排除「掉落为空触发通用回退」造成的假阳性。
        """

        def inject(task):
            task.cg_loot_reader = lambda: {"等级-第2行": (1,)}

        control = self.make(stamina=[300, 300, 39], weekly_first=True, daily_target=None)
        control.weekly_claims = 2
        control.weekly_finished = False
        inject(control)
        control.run_cultivation_goal()
        self.assertFalse(
            any("领取未确认" in message for _, message in control.logs), control.logs
        )
        self.assertTrue(
            any("掉落统计" in message for _, message in control.logs), control.logs
        )

        task = self.make(stamina=[300, 300, 39], weekly_first=True, daily_target=None)
        task.weekly_claims = 2
        task.weekly_finished = False
        inject(task)
        # 模拟 trace_claim 记录: 2 次确认领取之外还有 1 次没看到结算。
        task._cg_weekly_unconfirmed = 1
        task.run_cultivation_goal()

        self.assertTrue(
            any("领取未确认" in message and "周本" in message
                for level, message in task.logs if level == "warning"),
            task.logs,
        )
        # 已确认的 2 次照常扣减剩余次数。
        self.assertEqual(task.info["周本剩余次数"], 1)

    def test_weekly_material_row_goes_through_pilgrimage(self):
        """异能第2行的粉色花来自异象巡礼, 不能拿世界BOSS顶替。"""
        task = self.make(
            weekly_first=False,
            stamina=[300, 39],
            texts={
                "等级-第2行": "40/36",
                "弧盘-第2行": "20/12 20/18",
                "异能-第1行": "9/5",
                "异能-第2行": "0/2",
            },
        )
        task.run_cultivation_goal()
        actions = actions_from(self.report_path)
        labels = [a["label"] for a in actions if a["action"] == "material"]

        self.assertEqual(labels, ["异能-第2行"])
        self.assertEqual(task.weekly_runs, 1)
        self.assertEqual(task.wild_runs, [])

    def test_weekly_ocr_unreadable_skips_weekly(self):
        task = self.make(weekly_text="?/?", stamina=[300, 39], weekly_first=True)
        task.run_cultivation_goal()
        actions = actions_from(self.report_path)

        self.assertEqual([a["action"] for a in actions], ["material"])
        self.assertTrue(any("周本剩余次数读不出" in m for _, m in task.logs))
        self.assertIn("cg_weekly_read_failed", [c for c in task.captures])

    def test_report_is_human_readable(self):
        task = self.make(stamina=[300, 60, 45, 39], weekly_first=True, daily_target=None)
        task.weekly_claims = 3
        task.run_cultivation_goal()
        records = read_records(self.report_path)
        text = format_records(records)

        self.assertEqual(records[0]["event"], "start")
        self.assertGreaterEqual(sum(1 for r in records if r["event"] == "round"), 4)
        self.assertIn("启动 session=", text)
        self.assertIn("结束 原因=", text)
        self.assertIn("周本=3 材料=2", text)

    def test_zero_daily_target_keeps_running_on_current_stamina(self):
        # 今日 180 已达标时原任务会跳过；培养目标模式必须继续按当前体力跑。
        task = self.make(daily_target=0, stamina=[100, 39], weekly_text="0/3")
        self.assertTrue(task.run_cultivation_goal())
        records = read_records(self.report_path)

        self.assertEqual(records[0]["daily_target"], 0)
        self.assertTrue(any("按当前体力继续刷" in m for _, m in task.logs))
        self.assertEqual([a["action"] for a in actions_from(self.report_path)], ["material"])

    def test_remaining_budget_does_not_block_affordable_run(self):
        # 主人 11-21 实测: 今日目标额度只剩 20, 面板还有 70 体力, 40 的副本明明打得起。
        # 目标只是最低消耗量, 不能当截断条件。
        task = self.make(stamina=[70, 30], daily_target=20, weekly_text="0/3")
        self.assertTrue(task.run_cultivation_goal())
        rounds = [r for r in read_records(self.report_path) if r["event"] == "round"]

        self.assertEqual(rounds[0]["effective_stamina"], 70)
        self.assertEqual([a["action"] for a in actions_from(self.report_path)], ["material"])

    def test_invalid_stamina_still_reads_requirement_rows(self):
        task = self.make(stamina=-1)
        self.assertIsNone(task.run_cultivation_goal())
        rounds = [r for r in read_records(self.report_path) if r["event"] == "round"]

        self.assertEqual(len(rounds), 1)
        self.assertEqual(len(rounds[0]["rows"]), 4)
        # 首行是周本材料（异能第2行），顺序即 TARGET_ROWS 的优先级。
        self.assertEqual(rounds[0]["rows"][0]["ratios"], ["3/2"])
        self.assertIn("0/76", [r["ratios"][0] for r in rounds[0]["rows"] if r["ratios"]])
        self.assertIn("体力读数无效", rounds[0]["reason"])

    def test_detail_page_clicks_guide_button_before_reading_rows(self):
        """前往培养后落在角色详情页: 必须再点一次右上角养成指南按钮。"""

        class DetailPageTask(FakeTask):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.wait_text_calls = 0

            def _cg_wait_text(self, pattern, rect):
                self.wait_text_calls += 1
                # 第1次=前往培养命中; 第2次=角色详情页(材料页特征还没出现); 第3次=点了按钮后命中。
                if self.wait_text_calls == 2:
                    return False
                return True

        task = DetailPageTask(report=self.report)
        task.run_cultivation_goal()
        from src.tasks.cultivation_goal.positions import POS

        self.assertIn(POS.detail_guide_button, task.clicks)
        self.assertGreaterEqual(task.wait_text_calls, 3)

    def test_empty_row_captures_screenshot(self):
        """任一材料行 OCR 读空必须留现场图, 便于分辨坐标错还是页面没打开。"""
        task = self.make(
            texts={"等级-第2行": "", "弧盘-第2行": "", "异能-第1行": "", "异能-第2行": ""}
        )
        task.run_cultivation_goal()

        self.assertIn("cg_row_empty_等级_2", task.captures)
        self.assertIn("cg_row_empty_弧盘_2", task.captures)
        self.assertIn("cg_row_empty_异能_1", task.captures)
        self.assertIn("cg_row_empty_异能_2", task.captures)

    def test_all_rows_failed_stop_captures_scene(self):
        """所有材料行本次均失败而 STOP 时必须留现场图(复现异能第2行失败那轮)。"""
        task = self.make(stamina=[300, 300, 300, 300, 39], weekly_first=False)
        task._cg_run_material = lambda row, stamina=None: False
        task.run_cultivation_goal()

        self.assertIn("cg_stop", task.captures)
        stop = read_records(self.report_path)[-1]
        self.assertIn("均已失败", stop["reason"])

    def test_unknown_row_icon_captures_scene(self):
        """材料图标认不出时留行级现场图, 便于分辨是哪行图标变了还是整页没加载。"""
        import numpy as np
        import src.tasks.cultivation_goal.guide_icons as guide_icons

        task = self.make()
        task.frame = np.zeros((1080, 1920, 3), np.uint8)
        original = guide_icons.identify_row
        guide_icons.identify_row = lambda strip, height=1080: []
        try:
            dungeon = CultivationGoalMixin._cg_identify_row(task, "异能", 1)
        finally:
            guide_icons.identify_row = original

        self.assertIsNone(dungeon)
        self.assertIn("cg_row_icon_unknown_异能_2", task.captures)

    def _make_icon_frame(self):
        """合成材料行画面: 黑底 + 3 个白块图标, 投影可切出 3 槽。"""
        import numpy as np

        frame = np.zeros((300, 1000, 3), np.uint8)
        for x1, x2 in ((300, 340), (450, 490), (600, 640)):
            frame[140:190, x1:x2] = 255
        return frame

    def test_slot_reading_reads_each_icon_separately(self):
        """分框逐槽读比例: 斜杠误读只污染单槽, 不再跨槽拼出 8140/13 假比例。"""
        slot_texts = {"cg_slot_异能_0_0": "12/8", "cg_slot_异能_0_1": "8/40", "cg_slot_异能_0_2": "4/13"}
        task = self.make()
        task.frame = self._make_icon_frame()
        original_ocr = task.ocr

        def fake_ocr(box=None, match=None, **kw):
            name = getattr(box, "name", "")
            if name in slot_texts and kw.get("frame_processor") is None:
                return [FakeBox(slot_texts[name])]
            return original_ocr(box=box, match=match, **kw)

        task.ocr = fake_ocr
        text, ratios = task._cg_read_row_ratios("异能", 0)

        self.assertEqual([(r.owned, r.required) for r in ratios], [(12, 8), (8, 40), (4, 13)])
        self.assertTrue(all(not r.guessed for r in ratios))
        self.assertIn("8/40", text)

    def test_swallowed_slash_slot_repairs_via_binarize_retry(self):
        """槽内 `8/40` 被读成 `8140`: 单串修复先标 guessed, 二值化重试还原斜杠。"""
        task = self.make()
        task.frame = self._make_icon_frame()
        original_ocr = task.ocr
        calls = []

        def fake_ocr(box=None, match=None, **kw):
            name = getattr(box, "name", "")
            if name == "cg_slot_异能_0_0":
                if kw.get("frame_processor") is None:
                    calls.append("raw")
                    return [FakeBox("8140")]
                calls.append("binarize")
                return [FakeBox("8/40")]
            if name == "cg_slot_异能_0_1":
                return [FakeBox("0/10")]
            if name == "cg_slot_异能_0_2":
                return [FakeBox("4/13")]
            return original_ocr(box=box, match=match, **kw)

        task.ocr = fake_ocr
        _, ratios = task._cg_read_row_ratios("异能", 0)

        self.assertEqual(calls, ["raw", "binarize"])
        self.assertEqual([(r.owned, r.required) for r in ratios], [(8, 40), (0, 10), (4, 13)])

    def test_unreadable_slot_marks_row_not_full(self):
        """任一槽解析不出(如斜杠和数字都丢只剩 `/13`): 整行判读不出, 绝不把缺当满。"""
        task = self.make()
        task.frame = self._make_icon_frame()
        original_ocr = task.ocr

        def fake_ocr(box=None, match=None, **kw):
            name = getattr(box, "name", "")
            if name == "cg_slot_异能_0_0":
                return [FakeBox("12/8")]
            if name == "cg_slot_异能_0_1":
                return [FakeBox("/13")]
            if name == "cg_slot_异能_0_2":
                return [FakeBox("4/13")]
            return original_ocr(box=box, match=match, **kw)

        task.ocr = fake_ocr
        text, ratios = task._cg_read_row_ratios("异能", 0)

        self.assertIsNone(ratios, "槽残缺时整行必须判 None, 不能拿部分值当满")
        self.assertIn("/13", text)

    def test_slot_reading_falls_back_without_valid_frame(self):
        """frame 无 shape(旧调用方/测试桩)时切槽失效, 退回整行读数。"""
        from types import SimpleNamespace

        task = self.make()
        task.frame = SimpleNamespace(size=1)
        self.assertEqual(task._cg_read_row_ratios("异能", 0), ("", None))

    def test_hunter_landing_runs_wild_combat(self):
        """异象追猎落点走野外打法, 不能等副本的「退出」按钮。"""
        task = self.make(stamina=[100, 39], weekly_text="0/3", landing="hunter")
        task.run_cultivation_goal()

        self.assertEqual(task.wild_runs, ["等级-第2行"])
        self.assertEqual(task.claims, [])
        self.assertNotIn("cg_landing_unknown", task.captures)
        landings = [r for r in read_records(self.report_path) if r["event"] == "landing"]
        self.assertEqual(landings[0]["kind"], "hunter")

    def test_landing_records_hunter_boss_name(self):
        """落点页出现 BOSS 名要记下来, 走到跟前时才能选对上游的走路方向。"""
        task = self.make(stamina=[100, 39], weekly_text="0/3", landing="hunter")
        task.run_cultivation_goal()

        self.assertEqual(task._cg_hunter_name, "塞润尼缇")

    def test_hunter_name_survives_map_landing(self):
        """大地图落点也带 BOSS 名, 传送过去同样要按名字选方向。"""
        task = self.make(stamina=[100, 39], weekly_text="0/3", landing="map")
        task.run_cultivation_goal()

        self.assertEqual(task._cg_hunter_name, "塞润尼缇")

    def test_approach_boss_walks_until_boss_name_shows(self):
        """锁上怪≠走到位: 用 BOSS 名牌出现当到位判据, 没出现就继续往前顶。"""
        task = self.make()
        task.WALK_METHOD = {"塞润尼缇": [["w"]]}

        def fake_boss():
            return task.walk_steps >= 3

        task.is_boss = fake_boss
        walked = CultivationGoalMixin._cg_approach_boss(task, "塞润尼缇", max_steps=6, step_time=0)

        self.assertTrue(walked)
        self.assertEqual(task.walk_steps, 3)
        self.assertEqual(task.keys_down, ["w"] * 3)

    def test_approach_boss_skips_when_boss_already_visible(self):
        task = self.make()
        task.boss_visible = True
        self.assertTrue(CultivationGoalMixin._cg_approach_boss(task, "塞润尼缇", step_time=0))
        self.assertEqual(task.walk_steps, 0)

    def test_find_treasure_wide_scales_up(self):
        """宝箱走近后比素材大: 上游只缩小检不出, 宽容版必须试放大。"""
        import numpy as np

        task = self.make()
        task.pos = SimpleNamespace(
            screen=SimpleNamespace(main_viewport=SimpleNamespace(to_box=lambda: None))
        )
        seen = []

        class FakeFeature:
            mat = np.zeros((34, 20, 3), dtype="uint8")

        task.get_feature_by_name = lambda name: FakeFeature()

        def fake_find_one(feature_name=None, template=None, box=None, threshold=0, frame_processor=None):
            seen.append(template.shape[:2])
            if template.shape[0] > 34:
                return SimpleNamespace(name=feature_name, confidence=0.9)
            return None

        task.find_one = fake_find_one
        hit = CultivationGoalMixin._cg_find_treasure_wide(task)

        self.assertIsNotNone(hit)
        self.assertTrue(any(h > 34 for h, _ in seen))

    def test_search_treasure_rotates_then_claims(self):
        """原版只朝一个方向找: 转视角找到宝箱后要照搬走过去+交互领奖。"""
        task = self.make()
        turns = []
        walked = []
        interactions = []
        clicks = []

        def fake_find():
            return SimpleNamespace(name="boss_treasure", confidence=0.9) if len(turns) >= 2 else None

        task.move_mouse_relative = lambda dx, dy: turns.append(dx)
        task.walk_to_box = lambda find, **kwargs: (walked.append(kwargs.get("time_out")), True)[1]
        task.find_interac = lambda: []
        task.is_claim_btn_ready = lambda: True
        task.is_in_team = lambda: True
        task.send_interac = lambda **kwargs: (interactions.append("interac"), True)[1]
        task.operate_click = lambda *args, **kwargs: (clicks.append(args), True)[1]
        task.width_of_screen = lambda percent: 1920
        task._cg_find_treasure_wide = fake_find

        def fake_wait_until(cond, **kwargs):
            pre = kwargs.get("pre_action")
            if callable(pre):
                pre()
            return bool(cond())

        task.wait_until = fake_wait_until

        ok = CultivationGoalMixin._cg_search_treasure(task, turns=5)

        self.assertTrue(ok)
        self.assertEqual(len(turns), 2)
        self.assertEqual(walked, [15])
        # 走到宝箱后必须先交互(send_interac)再点领奖坐标。
        self.assertEqual(interactions, ["interac"])
        self.assertEqual(len(clicks), 1)
        self.assertAlmostEqual(clicks[0][0], 0.609, places=3)
        self.assertAlmostEqual(clicks[0][1], 0.659, places=3)

    def test_failed_row_skipped_and_other_rows_continue(self):
        """单行打不通只跳过该行, 让其它行继续把体力刷掉。"""
        task = self.make(stamina=[300, 300, 300, 300, 39], weekly_first=False)
        calls = []

        def fake_material(row, stamina=None):
            calls.append(row.label)
            return row.label != "等级-第2行"

        task._cg_run_material = fake_material
        task.run_cultivation_goal()

        self.assertEqual(calls[0], "等级-第2行")
        self.assertIn("弧盘-第2行", calls)
        self.assertTrue(any("本次跳过该行" in m for _, m in task.logs))

    def test_wild_fight_walks_before_combat(self):
        """野外必须先走 enter_hunter 再开战, 否则站在锚点原地打空气。"""
        from unittest import mock

        from src.tasks.AnomalyHunter import AnomalyHunter
        from src.tasks.cultivation_goal.core import MaterialRow

        task = self.make()
        task._cg_hunter_name = "塞润尼缇"
        order = []

        def fake_walk(target=None):
            order.append(("walk", target))
            return True

        def fake_claim(instance):
            order.append(("claim", None))
            return True

        task.enter_hunter = fake_walk
        with mock.patch.object(AnomalyHunter, "do_combat_and_claim", fake_claim):
            ok = CultivationGoalMixin._cg_fight_wild(task, MaterialRow("等级", 1, 60))

        self.assertTrue(ok)
        self.assertEqual(order, [("walk", "塞润尼缇"), ("claim", None)])

    def test_wild_fight_walks_with_default_direction_without_name(self):
        """认不出 BOSS 名时也要走, 交给上游按默认 W 前进。"""
        from unittest import mock

        from src.tasks.AnomalyHunter import AnomalyHunter
        from src.tasks.cultivation_goal.core import MaterialRow

        task = self.make()
        task._cg_hunter_name = None
        seen = {}

        def fake_walk(target=None):
            seen["target"] = target
            return True

        task.enter_hunter = fake_walk
        with mock.patch.object(AnomalyHunter, "do_combat_and_claim", return_value=True):
            CultivationGoalMixin._cg_fight_wild(task, MaterialRow("等级", 1, 60))

        self.assertIsNone(seen["target"])

    def test_read_line_swallows_ocr_exception(self):
        """OCR 抛异常只当读空, 不能冒泡炸掉整个日常任务(实机踩过 use_grayscale 崩)。"""
        task = self.make()

        def boom(**kwargs):
            raise ValueError("not enough values to unpack (expected 3, got 2)")

        task.ocr = boom
        self.assertEqual(CultivationGoalMixin._cg_read_line(task, SimpleNamespace(name="cg_x")), "")
        self.assertTrue(any("OCR 异常" in m for _, m in task.logs))

    def test_read_retry_waits_then_binarize_then_gray(self):
        """三级重试: 等一下普通读 → 二值化(修斜杠丢失) → 灰度; 绝不传 use_grayscale。"""
        task = self.make()
        seen = []

        def fake_ocr(box=None, match=None, threshold=0, frame_processor=None, **kwargs):
            self.assertNotIn("use_grayscale", kwargs)
            seen.append((threshold, frame_processor is not None))
            return [] if len(seen) < 3 else [SimpleNamespace(name="7/36")]

        task.ocr = fake_ocr
        text = CultivationGoalMixin._cg_read_retry(task, SimpleNamespace(name="cg_等级_1"))

        self.assertEqual(text, "7/36")
        self.assertEqual(seen, [(0, False), (0, True), (0.3, True)])

    def test_empty_row_retries_then_succeeds(self):
        """实机现象: 切 tab 后首读为空, 等一下就能读到 7/36, 不能直接判失败。"""
        task = self.make(stamina=[100, 39], weekly_text="0/3", landing="domain")
        calls = {"n": 0}
        real_ocr = task.ocr

        def flaky_ocr(box=None, match=None, **kwargs):
            if getattr(box, "name", "") == "cg_等级_1":
                calls["n"] += 1
                if calls["n"] == 1:
                    return []
                return [SimpleNamespace(name="7/36")]
            return real_ocr(box=box, match=match, **kwargs)

        task.ocr = flaky_ocr
        task.run_cultivation_goal()

        self.assertGreaterEqual(calls["n"], 2)
        self.assertNotIn("cg_row_empty_等级_2", task.captures)

    def test_stuck_on_material_page_skips_row_and_captures(self):
        """点了获取材料但页面没动: 该行跳过并留证据, 不能当成已进本, 也不拖死整场。"""
        task = self.make(stamina=[100, 39], weekly_text="0/3", landing="stuck")
        self.assertTrue(task.run_cultivation_goal())

        self.assertIn("cg_landing_unknown", task.captures)
        self.assertEqual(task.wild_runs, [])
        self.assertEqual(task.claims, [])
        self.assertTrue(any("未识别出落点" in m for _, m in task.logs))
        self.assertTrue(any("本次跳过该行" in m for _, m in task.logs))

    CONSOLE_TEXTS = {
        "等级-第2行": "1902/212",
        "弧盘-第2行": "3/0 33/17",
        "异能-第1行": "10/9",
        "异能-第2行": "3/2",
        "套装-推荐": "推荐套装 「失落光芒」x4",
    }

    def test_console_index_follows_recommended_set(self):
        """空幕页只有套装名: 「缇娅的夜间酒馆」→ 守卫萝卜 = 序号 4, 不能盲刷 1 号。"""
        task = self.make(
            weekly_text="0/3",
            texts={**self.CONSOLE_TEXTS, "套装-推荐": "推荐套装 「缇娅的夜间酒馆」x4"},
        )
        task.run_cultivation_goal()

        self.assertEqual(task.console_entries[0][0], 3)
        self.assertTrue(any("守卫萝卜 (序号 4)" in m for _, m in task.logs), task.logs)

    def test_console_refuses_when_set_unlisted(self):
        """套装没收录就别刷 —— 刷错副本等于白花 40 体力。"""
        task = self.make(
            weekly_text="0/3",
            texts={
                key: value
                for key, value in {**self.CONSOLE_TEXTS, "套装-推荐": "「虚构套装甲」x4"}.items()
                if key != "套装-当前"
            },
        )
        task.run_cultivation_goal()

        self.assertEqual(task.console_runs, 0)
        self.assertEqual(getattr(task, "console_entries", []), [])
        self.assertTrue(any("未收录" in m for _, m in task.logs), task.logs)

    def test_console_loops_each_run_and_spends_real_stamina(self):
        """120 体力 = 1 双倍 + 1 单倍 = 2 局; 扣账要按真实消耗 120, 不是 局数×40。"""
        task = self.make(weekly_text="0/3", stamina=120, texts=self.CONSOLE_TEXTS)
        task.run_cultivation_goal()

        self.assertEqual(len(task.claims), 2)
        self.assertEqual(task.claim_flags, [True, False])
        self.assertEqual(task._cg_console_spent, 120)
        self.assertTrue(any("账本扣体力 120" in m for _, m in task.logs), task.logs)
        self.assertEqual(read_records(self.report_path)[-1]["console_done"], 2)

    def test_console_budget_floors_to_whole_rewards(self):
        """预算向下取整成整份: 60 只放行 1 局单倍, 不足 40 直接不刷。

        原版按 ceil(目标/40) 排次数, 直接传 60 会被排成 2 份(80 点)超支。
        """
        task = self.make(weekly_text="0/3", texts=self.CONSOLE_TEXTS)

        self.assertEqual(task._cg_run_console(60), 1)
        self.assertEqual(task.claim_flags, [False])
        self.assertEqual(task._cg_run_console(20), 0)
        self.assertEqual(len(task.claims), 1)

    def test_console_retries_once_then_gives_up(self):
        """单局领奖失败退出重进一次; 连续两局失败就收场, 不无限重试。"""
        task = self.make(weekly_text="0/3", stamina=120, texts=self.CONSOLE_TEXTS)
        task.claim_script = False
        task.run_cultivation_goal()

        self.assertEqual(len(task.claims), 2)
        self.assertIn("cg_console_failed", task.captures)
        self.assertTrue(any("领奖失败" in m for _, m in task.logs), task.logs)

    def test_console_restores_config_on_exception(self):
        class BrokenConsole(FakeTask):
            def enter_anomaly(self, idx, retry=False):
                raise RuntimeError("enter failed")

        task = BrokenConsole(weekly_text="0/3", report=self.report, texts=self.CONSOLE_TEXTS)
        with self.assertRaises(RuntimeError):
            task._cg_run_console(120)

        self.assertEqual(task.config[task.CONF_TASK_TYPE], "异象界域")
        self.assertNotIn("空幕序号", task.config)
        self.assertNotIn("must use stamina", task.info)

    def test_console_does_not_write_config_to_disk(self):
        class TrackedConfig(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.saves = 0

            def __setitem__(self, key, value):
                self.saves += 1
                super().__setitem__(key, value)

            def save_file(self):
                self.saves += 1

        task = self.make(weekly_text="0/3", texts=self.CONSOLE_TEXTS)
        tracked = TrackedConfig(task.config)
        task.config = tracked
        task.run_cultivation_goal()

        self.assertEqual(tracked.saves, 0, "临时的空幕配置不能落盘")
        self.assertEqual(tracked[task.CONF_TASK_TYPE], "异象界域")

    def test_console_without_budget_opens_stamina_limit(self):
        """不限额时给原版一个大值, 让它按面板体力刷空; 借来的键要删掉。"""
        task = self.make(weekly_text="0/3", texts=self.CONSOLE_TEXTS)

        self.assertGreater(task._cg_run_console(), 0)
        self.assertNotIn("must use stamina", task.info)

    def test_console_refuses_exhausted_budget(self):
        """今日预算已经用完时不再碰空幕。"""
        task = self.make(weekly_text="0/3", texts=self.CONSOLE_TEXTS)

        self.assertEqual(task._cg_run_console(0), 0)
        self.assertEqual(task.console_runs, 0)
        self.assertEqual(task.config[task.CONF_TASK_TYPE], "异象界域")

    def test_console_allows_late_node_without_templates(self):
        """第 5/6 节点照刷: 空幕不记收获, 奖励卡认不出无所谓, 未解锁只会入场失败(不扣体力)。"""
        task = self.make(
            weekly_text="0/3",
            texts={**self.CONSOLE_TEXTS, "套装-推荐": "推荐套装 「音速蓝刺猬」x4"},
        )
        task.run_cultivation_goal()

        entries = getattr(task, "console_entries", [])
        self.assertEqual(entries[:1], [(4, False)])  # 0 基 → 序号 5
        self.assertGreater(task.console_runs, 0)
        self.assertTrue(any("后段节点" in m for _, m in task.logs), task.logs)

    def test_console_logs_loot_without_false_alarm(self):
        """奖励卡名是「钟表把戏 金」这种带档位的写法, 序号对得上就不该报映射错误。"""

        class LootConsole(FakeTask):
            def do_combat_and_claim(self, flag):
                ok = super().do_combat_and_claim(flag)
                self._cg_reward_loot = [
                    {"key": "curtain_r1_s2", "count": 2, "dungeon": "钟表把戏 金", "inliers": 0}
                ]
                return ok

        task = LootConsole(weekly_text="0/3", report=self.report, texts=self.CONSOLE_TEXTS)
        task._cg_run_console(40)

        records = [r for r in read_records(self.report_path) if r["event"] == "console_loot"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["console_id"], 1)
        self.assertFalse(any("与预期" in m for _, m in task.logs), task.logs)

    def test_console_logs_mismatched_loot_as_reference_only(self):
        """金圆盘模板相近, 产出对不上只记参考信息, 不当告警也不当选错本的证据。"""

        class WrongLoot(FakeTask):
            def do_combat_and_claim(self, flag):
                ok = super().do_combat_and_claim(flag)
                self._cg_reward_loot = [
                    {"key": "curtain_r2_s2", "count": 2, "dungeon": "雕塑展馆 金", "inliers": 0}
                ]
                return ok

        task = WrongLoot(weekly_text="0/3", report=self.report, texts=self.CONSOLE_TEXTS)
        task._cg_run_console(40)

        self.assertTrue(any("仅供参考" in m for _, m in task.logs), task.logs)

    def test_console_stops_when_continue_button_is_gone(self):
        """点不到「继续挑战」说明连打到头, 不能再硬打下一局空转。"""

        class NoContinue(FakeTask):
            def wait_click_confirm(self, pre_action=None, range=None, on_found=None, **kwargs):
                if range == CG_BURST_CONFIRM_RANGE:
                    return False
                return True

        task = NoContinue(weekly_text="0/3", report=self.report, texts=self.CONSOLE_TEXTS)
        task._cg_run_console(240)

        self.assertEqual(len(task.claims), 1)
        self.assertTrue(any("无继续挑战按钮" in m for _, m in task.logs), task.logs)

    def test_console_resets_spent_on_early_return(self):
        """提前返回不能留着上次的消耗 —— 决策侧会拿这个值扣账本, 留着就重复扣。"""
        task = self.make(weekly_text="0/3", report=self.report, texts=self.CONSOLE_TEXTS)

        self.assertEqual(task._cg_run_console(40), 1)
        self.assertEqual(task._cg_console_spent, 40)

        self.assertEqual(task._cg_run_console(20), 0)
        self.assertEqual(task._cg_console_spent, 0)

    def test_console_does_not_exit_when_entry_fails(self):
        """入场就没成功时不能调 exit_anomaly —— 它会在主界面上反复按 ESC 找「已离开副本」。"""

        class EntryFail(FakeTask):
            exits = 0

            def enter_anomaly(self, idx, retry=False):
                super().enter_anomaly(idx, retry)
                return False

            def exit_anomaly(self):
                self.exits += 1

        task = EntryFail(weekly_text="0/3", report=self.report, texts=self.CONSOLE_TEXTS)

        self.assertEqual(task._cg_run_console(80), 0)
        self.assertEqual(task.exits, 0)

    def test_console_warns_when_no_runs_computed(self):
        """上游没算出局数时不能静默返回 0, 否则日志上看不出这轮发生过什么。"""

        class ZeroRuns(FakeTask):
            def enter_anomaly(self, idx, retry=False):
                super().enter_anomaly(idx, retry)
                self.info["双倍次数"] = 0
                self.info["单倍次数"] = 0
                return True

        task = ZeroRuns(weekly_text="0/3", report=self.report, texts=self.CONSOLE_TEXTS)

        self.assertEqual(task._cg_run_console(80), 0)
        self.assertTrue(any("没算出局数" in m for _, m in task.logs), task.logs)

    def test_console_run_not_counted_when_settlement_never_appears(self):
        """领奖点完却停在领取界面(11-22 15:35 实测形态): 这局不算到账, 撤掉重进也只重试一次。"""

        class Unsettled(FakeTask):
            def _cg_reward_settled(self, time_out=6):
                return False

        task = Unsettled(weekly_text="0/3", report=self.report, texts=self.CONSOLE_TEXTS)
        runs = task._cg_run_console(120)

        self.assertEqual(runs, 0)
        self.assertEqual(task.claims, [True, True])
        self.assertEqual(task._cg_console_spent, 0)
        self.assertIn("cg_claim_unsettled", task.captures)
        self.assertTrue(any("未见结算界面" in m for _, m in task.logs), task.logs)

    def test_weekly_skipped_unless_option_enabled(self):
        """周本材料已满时默认不为次数白跑, 体力全留给缺料的行。"""
        task = self.make(stamina=[100, 39])
        task.run_cultivation_goal()

        self.assertEqual([a["action"] for a in actions_from(self.report_path)], ["material"])
        self.assertEqual(task.weekly_runs, 0)
        self.assertTrue(any("周本只在异能第2行缺料时才打" in m for _, m in task.logs))

    def test_weekly_material_row_goes_first(self):
        """异能第2行(周本材料)缺料时必须排第一顺位, 靠缺口驱动而非每周次数。"""
        texts = [
            {**DEFAULT_TEXTS, "异能-第2行": "1/2"},
            {**DEFAULT_TEXTS, "异能-第2行": "2/2"},
            dict(DEFAULT_TEXTS),
        ]
        task = self.make(stamina=[300, 300, 0], texts=texts)
        task.run_cultivation_goal()
        actions = actions_from(self.report_path)

        self.assertEqual(actions[0]["action"], "material")
        self.assertEqual(actions[0]["label"], "异能-第2行")
        self.assertEqual(task.weekly_runs, 1)

    def test_borrow_adds_and_releases_owner_members(self):
        """未绑定调用上游 do_run 时, 缺的类常量与方法必须借进来, 用完摘掉。"""
        from src.tasks.AnomalyHunter import AnomalyHunter
        from src.tasks.AnomalyPilgrimage import AnomalyPilgrimage

        task = self.make()
        self.assertFalse(hasattr(task, "CONF_DAILY_START_DAY"))

        borrowed = task._cg_borrow(AnomalyPilgrimage)
        self.assertIn("CONF_DAILY_START_DAY", borrowed)
        self.assertEqual(task.CONF_DAILY_START_DAY, AnomalyPilgrimage.CONF_DAILY_START_DAY)
        task._cg_release(borrowed)
        self.assertFalse(hasattr(task, "CONF_DAILY_START_DAY"))

        borrowed = task._cg_borrow(AnomalyHunter)
        self.assertIn("walk_to_boss_treasure", borrowed)
        self.assertTrue(callable(task.walk_to_boss_treasure))
        task._cg_release(borrowed)
        self.assertFalse(hasattr(task, "walk_to_boss_treasure"))

    def test_shadow_replaces_method_on_instance_only(self):
        """探针只能盖实例, 不能改类; 摘掉后必须回到原版方法。"""
        task = self.make()
        original = task._cg_capture

        def fake(box=None, threshold=0.7):
            return "probe"

        task._cg_shadow("_cg_capture", fake)
        self.assertEqual(task._cg_capture(), "probe")
        task.__dict__.pop("_cg_capture", None)
        self.assertEqual(task._cg_capture, original)

    def test_shadow_rejects_non_callable(self):
        """上一轮就是错把 dict.__setitem__ 用在实例上, 直接把整轮周本炸掉; 现在参数错要早报。"""
        task = self.make()
        with self.assertRaises(TypeError):
            task._cg_shadow("find_confirm", "not callable")

    def test_wild_fight_hands_borrowed_methods_to_hunter(self):
        from unittest import mock

        from src.tasks.AnomalyHunter import AnomalyHunter
        from src.tasks.cultivation_goal.core import MaterialRow

        task = self.make()
        seen = {}

        def fake_claim(instance):
            seen["walk"] = hasattr(instance, "walk_to_boss_treasure")
            seen["const"] = hasattr(instance, "BOSS_TREASURE_THRESHOLD")
            return True

        with mock.patch.object(AnomalyHunter, "do_combat_and_claim", fake_claim):
            ok = CultivationGoalMixin._cg_fight_wild(task, MaterialRow("等级", 1, 60))

        self.assertTrue(ok)
        self.assertEqual(seen, {"walk": True, "const": True})
        self.assertFalse(hasattr(task, "walk_to_boss_treasure"))


class MaterialGainFlowTests(unittest.TestCase):
    """面板数字的前后差值是唯一能反推单局掉落的证据, 这里锁住它的算法与用法。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.report_path = os.path.join(self.tmp.name, "cg.jsonl")
        self.report = RunReport(path=self.report_path)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kwargs):
        kwargs.setdefault("report", self.report)
        return FakeTask(**kwargs)

    def test_second_round_reports_gain_and_remaining_gap(self):
        texts = [
            dict(DEFAULT_TEXTS),
            {**DEFAULT_TEXTS, "等级-第2行": "6/76"},
        ]
        self.make(stamina=[300, 0], texts=texts).run_cultivation_goal()
        rounds = [r for r in read_records(self.report_path) if r["event"] == "round"]
        self.assertEqual(len(rounds), 2)
        # 第一轮没有基准, 只能算缺口。
        self.assertIsNone(rounds[0]["gains"]["等级-第2行"])
        self.assertEqual(rounds[0]["gaps"]["等级-第2行"], 76)
        self.assertEqual(rounds[1]["gains"]["等级-第2行"], 6)
        self.assertEqual(rounds[1]["gaps"]["等级-第2行"], 70)

    def test_gain_covers_every_material_in_the_row(self):
        texts = [
            dict(DEFAULT_TEXTS),
            {**DEFAULT_TEXTS, "弧盘-第2行": "4/10 6/15"},
        ]
        self.make(stamina=[300, 0], texts=texts).run_cultivation_goal()
        rounds = [r for r in read_records(self.report_path) if r["event"] == "round"]
        # 增量也折最低档: 4 绿 + 6 蓝 = 4 + 18 = 22 件。
        self.assertEqual(rounds[1]["gains"]["弧盘-第2行"], 22)

    def test_two_empty_rounds_drop_the_row(self):
        """连打两局一点没涨, 说明这副本不产这个材料, 不能继续烧体力。"""
        task = self.make(stamina=[300] * 8, daily_target=None)
        task.run_cultivation_goal()
        self.assertTrue(
            any(level == "warning" and "零产出" in message for level, message in task.logs),
            task.logs,
        )
        labels = [a.get("label") for a in actions_from(self.report_path) if a.get("action") == "material"]
        self.assertIn("等级-第2行", labels)
        self.assertIn("弧盘-第2行", labels)

    def test_report_prints_gain_and_gap(self):
        texts = [dict(DEFAULT_TEXTS), {**DEFAULT_TEXTS, "等级-第2行": "6/76"}]
        self.make(stamina=[300, 0], texts=texts).run_cultivation_goal()
        text = format_records(read_records(self.report_path))
        self.assertIn("+6", text)
        self.assertIn("缺70", text)

    def test_swallowed_slash_is_repaired_on_real_device_text(self):
        """真机 10:40 的原样读数就是 `47160`, 修好之后这一轮不会白停。"""
        texts = [
            {**DEFAULT_TEXTS, "等级-第2行": "47160"},
            {**DEFAULT_TEXTS, "等级-第2行": "47160"},
            {**DEFAULT_TEXTS, "等级-第2行": "47/60"},
        ]
        self.make(stamina=[300, 0], texts=texts).run_cultivation_goal()
        rounds = [r for r in read_records(self.report_path) if r["event"] == "round"]
        level = next(r for r in rounds[0]["rows"] if r["label"] == "等级-第2行")
        self.assertEqual(level["ratios"], ["47/60"])
        self.assertEqual(rounds[0]["gaps"]["等级-第2行"], 13)

    def test_guessed_single_string_is_not_used_as_gap(self):
        """单串猜出来的比例(3150→3/50)切法不唯一, 不能当缺口去刷。"""

        task = self.make(stamina=[300, 300, 0], texts={**DEFAULT_TEXTS, "等级-第2行": "3150"})
        task.run_cultivation_goal()
        self.assertTrue(any("单串猜出" in message for _, message in task.logs), task.logs)

    def test_guessed_ratio_retries_before_stale_fallback(self):
        """首读猜出低置信比例时先重试 OCR: 重试读出可信比例就不用旧值。"""
        task = self.make(stamina=[300, 0], texts={**DEFAULT_TEXTS, "等级-第2行": "3150"})
        task._cg_read_retry = lambda box: "3/50"
        task.run_cultivation_goal()

        self.assertTrue(any("重试成功" in message for _, message in task.logs), task.logs)
        self.assertFalse(any("沿用上一轮" in message for _, message in task.logs), task.logs)
        rounds = [r for r in read_records(self.report_path) if r["event"] == "round"]
        level = next(r for r in rounds[0]["rows"] if r["label"] == "等级-第2行")
        self.assertEqual(level["ratios"], ["3/50"])

    def test_guessed_retry_still_guessed_falls_back_to_stale(self):
        """重试还是猜不出来就按读不出处理: 沿用旧值并标记 stale, 不拿猜测去规划。"""
        texts = [
            dict(DEFAULT_TEXTS),
            {**DEFAULT_TEXTS, "等级-第2行": "3150"},
        ]
        task = self.make(stamina=[300, 300, 0], texts=texts)
        task._cg_read_retry = lambda box: "7136"
        task.run_cultivation_goal()

        self.assertTrue(any("沿用上一轮" in message for _, message in task.logs), task.logs)
        self.assertFalse(any("重试成功" in message for _, message in task.logs), task.logs)

    def test_unreadable_row_reuses_last_value_instead_of_stopping(self):
        """读不出就往回看一轮, 别为一次识别抖动把剩下的体力全放弃。"""
        texts = [
            dict(DEFAULT_TEXTS),                                  # 第一轮建立基准
            {**DEFAULT_TEXTS, "等级-第2行": "材料不足"},           # 第二轮整行读空
            dict(DEFAULT_TEXTS),                                  # 第三轮恢复
        ]
        task = self.make(stamina=[300, 300, 300, 0], texts=texts)
        task.run_cultivation_goal()
        self.assertTrue(
            any(level == "warning" and "沿用上一轮" in message for level, message in task.logs),
            task.logs,
        )
        self.assertNotIn("材料行读不出", task.info.get("培养目标统计", ""))
        actions = [a.get("action") for a in actions_from(self.report_path)]
        self.assertIn("material", actions[1:])

    def test_stale_row_is_excluded_from_gain_accounting(self):
        """沿用的旧值不能当成"这一局没涨", 否则两轮就会误杀换行。"""
        texts = [
            dict(DEFAULT_TEXTS),
            {**DEFAULT_TEXTS, "等级-第2行": "无法识别"},
            dict(DEFAULT_TEXTS),
            dict(DEFAULT_TEXTS),
        ]
        self.make(stamina=[300, 300, 300, 0], texts=texts).run_cultivation_goal()
        rounds = [r for r in read_records(self.report_path) if r["event"] == "round"]
        self.assertNotIn("等级-第2行", rounds[1]["gains"])


class RewardCaptureTests(unittest.TestCase):
    """奖励界面只在确认按钮出现到被点掉之间存在, 抓拍必须挂在 on_found 上。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.report = RunReport(path=os.path.join(self.tmp.name, "cg.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kwargs):
        kwargs.setdefault("report", self.report)
        return FakeTask(**kwargs)

    def test_confirm_click_scans_reward_list(self):
        task = self.make(stamina=[300, 0])
        task.run_cultivation_goal()
        self.assertGreaterEqual(len(task.reward_scans), 1)

    def test_reward_scan_stops_at_limit(self):
        task = self.make(stamina=[300, 10])
        task._cg_arm_reward_capture(limit=2)
        for _ in range(5):
            task.wait_click_confirm(range=CG_FINAL_CONFIRM_RANGE)
        self.assertEqual(len(task.reward_scans), 2)

    def test_scan_skips_ranges_outside_whitelist(self):
        """白名单外的确认(如 ESC 离场)不触发扫描, 也不登记漏扫。"""
        task = self.make(stamina=[300, 10])
        task._cg_arm_reward_capture()
        task.wait_click_confirm(range=(0.619, 0.607, 0.709, 0.709))
        task.wait_click_confirm()
        self.assertEqual(task.reward_scans, [])

    def test_hook_is_removed_after_stop(self):
        task = self.make(stamina=[300, 0])
        task.run_cultivation_goal()
        self.assertNotIn("wait_click_confirm", task.__dict__)

    def test_wild_reward_list_scanned_after_claim_click(self):
        """野外奖励清单在点掉领奖确认后才出现，只识别一次。"""
        task = self.make(stamina=[300, 0])
        task.claim_btn_ready = False
        task.is_claim_btn_ready = lambda: task.claim_btn_ready
        task._cg_arm_wild_reward_capture()

        task.claim_btn_ready = True
        self.assertTrue(task.is_claim_btn_ready())
        self.assertEqual(task.reward_scans, [])

        # 点掉领奖确认 -> 结算清单出现 -> 识别一次
        task.operate_click(0.609, 0.659, after_sleep=2)
        self.assertEqual(len(task.reward_scans), 1)

        # 再点一次不再重复识别, 免得掉落重复入账。
        task.operate_click(0.609, 0.659)
        self.assertEqual(len(task.reward_scans), 1)

    def test_wild_scan_ignores_other_clicks(self):
        task = self.make(stamina=[300, 0])
        task.claim_btn_ready = False
        task.is_claim_btn_ready = lambda: task.claim_btn_ready
        task._cg_arm_wild_reward_capture()
        task.claim_btn_ready = True
        task.is_claim_btn_ready()
        task.operate_click(0.5, 0.5)
        self.assertEqual(task.reward_scans, [])

    def test_reward_count_rejects_multiple_number_groups(self):
        """数量 OCR 出现多组数字("4张5个")是拼接误读, 宁可重读也不入账。"""
        task = self.make()
        task.ocr = lambda box=None, match=None, **kw: [SimpleNamespace(name="4张5个")]
        self.assertIsNone(CultivationGoalMixin._cg_read_reward_count(task, (0.5, 0.5, 0.9, 0.9)))
        task.ocr = lambda box=None, match=None, **kw: [SimpleNamespace(name="×4")]
        self.assertEqual(CultivationGoalMixin._cg_read_reward_count(task, (0.5, 0.5, 0.9, 0.9)), 4)
        task.ocr = lambda box=None, match=None, **kw: [SimpleNamespace(name="24")]
        self.assertEqual(CultivationGoalMixin._cg_read_reward_count(task, (0.5, 0.5, 0.9, 0.9)), 24)

    def test_reward_count_falls_back_to_alt_box(self):
        """数量主框读不出时换备选框再读, 两框都失败才判读不出。"""
        task = self.make()
        calls = []

        def ocr(box=None, match=None, **kw):
            calls.append(box)
            return [] if len(calls) == 1 else [SimpleNamespace(name="×7")]

        task.ocr = ocr
        count = CultivationGoalMixin._cg_read_reward_count(
            task, (0.5, 0.5, 0.9, 0.9), (0.4, 0.6, 0.6, 0.65)
        )
        self.assertEqual(count, 7)
        self.assertEqual(len(calls), 2)

    def test_unmatched_cards_mark_round_incomplete(self):
        """投影切出 3 张卡只认出 1 张: 缺的那张不能按零入账, 整轮按残缺处理。"""
        import src.tasks.cultivation_goal.reward_icons as reward_icons

        task = self.make()
        task.frame = SimpleNamespace(size=1)
        task.ocr = lambda box=None, match=None, **kw: [SimpleNamespace(name="×4")]
        fake_match = SimpleNamespace(key="ability_扑克茶会_紫", dungeon="扑克茶会",
                                     tier="紫", inliers=9, count_box=(0.5, 0.5, 0.9, 0.9))
        original = reward_icons.identify_rewards_detailed
        reward_icons.identify_rewards_detailed = lambda frame, height=1080, info=None: ([fake_match], 3)
        try:
            loot = CultivationGoalMixin._cg_scan_reward_list(task, "副本")
        finally:
            reward_icons.identify_rewards_detailed = original

        self.assertEqual(len(loot), 1)
        self.assertTrue(task._cg_loot_partial)
        self.assertEqual(task._cg_scan_flags, [False], "有卡没认出, 整轮登记为不完整")

    def test_all_cards_matched_keeps_round_complete(self):
        """全部卡都认出时完整性不受影响。"""
        import src.tasks.cultivation_goal.reward_icons as reward_icons

        task = self.make()
        task.frame = SimpleNamespace(size=1)
        task.ocr = lambda box=None, match=None, **kw: [SimpleNamespace(name="×4")]
        fake_match = SimpleNamespace(key="ability_扑克茶会_紫", dungeon="扑克茶会",
                                     tier="紫", inliers=9, count_box=(0.5, 0.5, 0.9, 0.9))
        original = reward_icons.identify_rewards_detailed
        reward_icons.identify_rewards_detailed = lambda frame, height=1080, info=None: ([fake_match], 1)
        try:
            CultivationGoalMixin._cg_scan_reward_list(task, "副本")
        finally:
            reward_icons.identify_rewards_detailed = original

        self.assertFalse(task._cg_loot_partial)
        self.assertEqual(task._cg_scan_flags, [True])


class TravelRetryTests(unittest.TestCase):
    """10:15 实机: 传送按钮点到了但游戏没反应, 之后干等 300 秒, 表面看就是卡死。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.report = RunReport(path=os.path.join(self.tmp.name, "cg.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kwargs):
        kwargs.setdefault("report", self.report)
        return FakeTask(**kwargs)

    def test_retry_with_fixed_coordinate_when_teleport_is_ignored(self):
        task = self.make(stamina=[300, 0], landing="map")
        task.team_results = [False, True]
        task.run_cultivation_goal()
        self.assertTrue(any("补点传送" in msg for _, msg in task.logs), task.logs)
        self.assertIn((0.870, 0.911), task.clicks)
        self.assertGreaterEqual(getattr(task, "front_calls", 0), 1)

    def test_gives_up_after_both_waits_fail(self):
        task = self.make(stamina=[300, 0], landing="map")
        task.team_results = [False]
        task.run_cultivation_goal()
        self.assertTrue(any("放弃该行" in msg for _, msg in task.logs), task.logs)

    def test_no_retry_when_teleport_works_first_time(self):
        task = self.make(stamina=[300, 0], landing="map")
        task.run_cultivation_goal()
        self.assertFalse(any("补点传送" in msg for _, msg in task.logs), task.logs)
        self.assertNotIn((0.870, 0.911), task.clicks)


class DoubleClaimTests(unittest.TestCase):
    """双倍领取：一次 80 体力领两份，只在体力够且缺口撑得住时启用。"""

    ROW = MaterialRow("弧盘", 1, 40, ratios=(Ratio(0, 5),))

    def decide(self, stamina, row=None):
        return CultivationGoalMixin._cg_pick_double(None, row or self.ROW, stamina)

    def test_doubles_when_stamina_and_gap_allow(self):
        self.assertTrue(self.decide(100))
        self.assertTrue(self.decide(80))

    def test_single_when_stamina_short(self):
        # 79 < 80: 攒不出双倍就把这 40 花掉, 别留着。
        self.assertFalse(self.decide(79))
        self.assertFalse(self.decide(None))

    def test_single_when_gap_is_one(self):
        # 只缺 1 份时双倍会溢出, 那 40 体力留给别的行更划算。
        self.assertFalse(self.decide(300, MaterialRow("弧盘", 1, 40, ratios=(Ratio(4, 5),))))

    def test_doubles_when_gap_unknown(self):
        self.assertTrue(self.decide(300, MaterialRow("弧盘", 1, 40, ratios=None)))

    def test_flag_reaches_claim(self):
        task = FakeTask(stamina=[300])
        task._cg_fight_once(self.ROW, "dungeon", 300)
        self.assertEqual(task.claim_flags, [True])

    def test_short_stamina_falls_back_to_single(self):
        task = FakeTask(stamina=[300])
        task._cg_fight_once(self.ROW, "dungeon", 40)
        self.assertEqual(task.claim_flags, [False])


class _FakeWeeklyProbe:
    """只提供周本探测过滤需要的接口，探测结果由 locked/raises 指定。"""

    def __init__(self, locked=(), raises=()):
        self.locked = set(locked)
        self.raises = set(raises)
        self.notes = []
        self.probed = []

    def _cg_weekly_probe_locked(self, target):
        self.probed.append(target)
        if target in self.raises:
            raise RuntimeError(f"列表里找不到 {target}")
        return target in self.locked

    def _cg_note(self, level, message):
        self.notes.append((level, message))


class WeeklyLockTests(unittest.TestCase):
    """右下角「追踪」= 副本未解锁，要跳过而不是让上游等传送按钮等到超时。"""

    def test_tracking_means_locked(self):
        self.assertTrue(weekly_entry_is_locked("追踪"))
        self.assertTrue(weekly_entry_is_locked("讨债人 追踪"))

    def test_teleport_means_unlocked(self):
        self.assertFalse(weekly_entry_is_locked("传送"))
        self.assertFalse(weekly_entry_is_locked("前往"))
        self.assertFalse(weekly_entry_is_locked("当前无法传送"))

    def test_unreadable_text_stays_unlocked(self):
        # 读不出字就当已解锁：错杀能打的副本比多试一次更亏。
        self.assertFalse(weekly_entry_is_locked(""))
        self.assertFalse(weekly_entry_is_locked(None))
        self.assertFalse(weekly_entry_is_locked("收取奖励消耗"))

    def test_tracking_in_progress_is_locked_too(self):
        # 点过「追踪」后按钮会变成「追踪中/取消追踪」，同样进不去副本。
        self.assertTrue(weekly_entry_is_locked("追踪中"))
        self.assertTrue(weekly_entry_is_locked("取消追踪"))

    def test_locked_target_is_dropped_with_log(self):
        fake = _FakeWeeklyProbe(locked={"讨债人"})
        kept = WeeklyMixin._cg_filter_locked_weekly(fake, ["讨债人", "守夜人"])
        self.assertEqual(kept, ["守夜人"])
        self.assertEqual([note[0] for note in fake.notes], ["warning"])
        self.assertIn("讨债人", fake.notes[0][1])
        self.assertIn("未解锁", fake.notes[0][1])
        self.assertIn("跳过", fake.notes[0][1])

    def test_probe_failure_keeps_target(self):
        fake = _FakeWeeklyProbe(raises={"坏目标"}, locked={"讨债人"})
        kept = WeeklyMixin._cg_filter_locked_weekly(fake, ["坏目标", "讨债人", "守夜人"])
        self.assertEqual(kept, ["坏目标", "守夜人"])
        self.assertIn("已解锁处理", fake.notes[0][1])

    def test_all_locked_returns_empty(self):
        fake = _FakeWeeklyProbe(locked={"讨债人", "守夜人"})
        self.assertEqual(WeeklyMixin._cg_filter_locked_weekly(fake, ["讨债人", "守夜人"]), [])


class StopVerifyTests(unittest.TestCase):
    """收工判定前回面板实测体力: 账本是估算, 外层自动化只认最后写进去的数字。"""

    TEXTS = {
        "异能-第2行": "3/2",
        "等级-第2行": "76/76",
        "异能-第1行": "0/9",
        "弧盘-第2行": "0/55",
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.report = RunReport(path=os.path.join(self.tmp.name, "cg.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, readings, loot=None, **kwargs):
        """readings[0] 是开局读数, 之后每次调用依次取下一个, 用完后沿用最后一个。"""
        task = FakeTask(report=self.report, daily_target=None, texts=[self.TEXTS], **kwargs)
        # 掉落读得出来, 账本才会一直沿用, 才会走到「体力不够」这个收工分支。
        payload = loot if loot is not None else {"异能-第1行": (1,)}
        task.cg_loot_reader = lambda: dict(payload)
        calls = {"n": 0}

        def reader():
            calls["n"] += 1
            return readings[min(calls["n"] - 1, len(readings) - 1)]

        task._cg_read_stamina = reader
        return task, calls

    @staticmethod
    def verify_notes(task):
        return [msg for _, msg in task.logs if "收工前复核体力" in msg]

    def test_insufficient_stop_reports_measured_value(self):
        # 账本 10 就要收工, 面板实测也是 10: 判定和上报都用 10, 不能搬开局旧值。
        task, calls = self.make([50, 10])
        task.run_cultivation_goal()

        self.assertEqual(calls["n"], 2)
        self.assertEqual(task.info["当前体力"], 10)
        self.assertTrue(any("体力 10 不够" in msg for _, msg in task.logs))
        self.assertEqual(self.verify_notes(task), [])

    def test_measured_more_than_ledger_resumes(self):
        # 账本 10 收工, 面板实测还有 50: 说明账本低估, 应继续开打而不是白停。
        task, calls = self.make([50, 50])
        task.run_cultivation_goal()

        self.assertGreaterEqual(calls["n"], 2)
        notes = self.verify_notes(task)
        self.assertTrue(notes)
        self.assertIn("账本", notes[0])
        self.assertIn("实测", notes[0])

    def test_read_failure_keeps_ledger_value(self):
        # 复核读不到(-1)时不能崩, 也不能把账本抹成 -1。
        task, calls = self.make([50, -1])
        task.run_cultivation_goal()

        self.assertEqual(calls["n"], 2)
        self.assertEqual(task.info["当前体力"], 10)
        self.assertTrue(any("体力 10 不够" in msg for _, msg in task.logs))

    def test_verify_runs_only_once(self):
        # 实测完仍不够就收工, 不许反复开面板。
        task, _ = self.make([10, 20])
        task.run_cultivation_goal()
        self.assertEqual(len(self.verify_notes(task)), 1)
        self.assertEqual(task.info["当前体力"], 20)

    def test_non_stamina_stop_skips_verify(self):
        # 材料行全失败属于非体力原因, 不该多跑一次面板。
        task, _ = self.make([300, 300], claim=False)
        task.run_cultivation_goal()
        self.assertEqual(self.verify_notes(task), [])
        self.assertTrue(any("均已失败" in msg for _, msg in task.logs))


class CoinReadTests(unittest.TestCase):
    """甲硬币读数: 三页各读一行, 需求相加、拥有只算一次、任一页读不出就退出。"""

    def make(self, pages):
        from src.tasks.cultivation_goal.flow_scan import ScanMixin
        from src.tasks.cultivation_goal.core import parse_ratios

        class Stub(ScanMixin):
            def __init__(self, pages):
                self.pages = list(pages)
                self.logs = []
                self.scrolled = 0
                self.tabs = []

            def _cg_click(self, x, y, name, after_sleep=0):
                self.tabs.append(name)

            def _cg_scroll_material_to_bottom(self):
                self.scrolled += 1

            def box_of_screen(self, *args, **kwargs):
                return None

            def _cg_read_line(self, box, **kwargs):
                return self.pages.pop(0) if self.pages else ""

            def _cg_read_retry(self, box):
                return ""

            @staticmethod
            def _cg_usable_ratios(text):
                ratios = parse_ratios(text)
                if ratios and any(getattr(ratio, "guessed", False) for ratio in ratios):
                    return None
                return ratios or None

            def _cg_note(self, level, message):
                self.logs.append((level, message))

        return Stub(pages)

    def test_需求三页相加拥有只取一次(self):
        stub = self.make(["246364/807500", "246364/1280250", "246364/21000"])
        row = stub._cg_read_coin()
        self.assertEqual(row.label, "甲硬币")
        self.assertEqual(row.ratios[0].owned, 246364, "拥有是同一个库存, 不能乘三")
        self.assertEqual(row.ratios[0].required, 807500 + 1280250 + 21000)
        self.assertFalse(row.satisfied)
        self.assertEqual(stub.scrolled, 3, "每页都要先滑到底, 金币是最后一行")

    def test_任一页读不出就不参与(self):
        stub = self.make(["246364/807500", "", "246364/21000"])
        self.assertIsNone(stub._cg_read_coin())
        self.assertTrue(any("本轮不检查甲硬币" in msg for _, msg in stub.logs))

    def test_单页需求小于拥有不算读错(self):
        # 异能只要 2.1 万而库存 24 万, 这是真实情况, 不能判成读错。
        stub = self.make(["246364/807500", "246364/1280250", "246364/21000"])
        row = stub._cg_read_coin()
        self.assertIsNotNone(row)
        self.assertEqual(row.ratios[0].owned, 246364)

    def test_三页拥有互不相同就不参与(self):
        stub = self.make(["100/500", "200/600", "300/700"])
        self.assertIsNone(stub._cg_read_coin())
        self.assertTrue(any("互不相同" in msg for _, msg in stub.logs))

    def test_已够时判为满(self):
        # 每页需求 3000, 三页合计 9000 = 拥有 9000。
        stub = self.make(["9000/3000", "9000/3000", "9000/3000"])
        self.assertTrue(stub._cg_read_coin().satisfied)

    def test_七位数的金币需求能读出来(self):
        # 弧盘满级需求 1280250 是 7 位, 正则卡 6 位时整行会读成空。
        stub = self.make(["246364/807500", "246364/1280250", "246364/21000"])
        self.assertEqual(stub._cg_read_coin().ratios[0].required, 2108750)


class CoinLootTests(unittest.TestCase):
    """甲硬币从结算页币种卡取数量, 读不到就不入账。"""

    def make(self, loot):
        from src.tasks.cultivation_goal.flow_combat import CombatMixin

        class Stub(CombatMixin):
            def __init__(self, loot):
                self._cg_reward_loot = loot
                self.logs = []

            def _cg_note(self, level, message):
                self.logs.append((level, message))

        return Stub(loot)

    def test_币种卡数量入账(self):
        stub = self.make([
            {"key": "currency_金币", "count": 120000},
            {"key": "ability_扑克茶会_紫", "count": 2},
        ])
        self.assertEqual(stub._cg_read_coin_loot(), {"甲硬币": (120000,)})

    def test_没有币种卡返回None(self):
        stub = self.make([{"key": "ability_扑克茶会_紫", "count": 2}])
        self.assertIsNone(stub._cg_read_coin_loot())

    def test_走_read_loot_分流(self):
        stub = self.make([{"key": "currency_金币", "count": 60000}])
        stub.cg_loot_reader = None
        self.assertEqual(stub._cg_read_loot("甲硬币"), {"甲硬币": (60000,)})


if __name__ == "__main__":
    unittest.main()