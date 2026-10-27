from ok import WaitFailedException

from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask


class GoldenCrunchTask(NTEOneTimeTask, BaseNTETask):
    EXIT_CONFIRM = (0.51, 0.62, 0.70, 0.70)
    LOBBY_BACKGROUND = (0.38, 0.18, 0.62, 0.33)
    ORANGE = {"r": (210, 255), "g": (135, 225), "b": (40, 150)}
    BUTTON_FILL = {"r": (190, 255), "g": (190, 255), "b": (190, 255)}
    CONF_LEAVE_DELAY = "进入后等待秒数"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "咔滋!黄金双脆"
        self.description = "从活动详情页或匹配大厅开始, 进入对局后等待指定秒数再退出并重复运行"
        self.add_rounds_config(default=0)
        self.default_config.update({self.CONF_LEAVE_DELAY: 0})
        self.config_description.update(
            {self.CONF_LEAVE_DELAY: "确认进入对局后等待的秒数, 设置为0则立即退出"}
        )

    def run(self):
        super().run()
        self.start_rounds()
        self.wait_for_menu(time_out=5)
        while self.begin_round():
            self.enter_lobby()
            self.enter_match()
            self.sleep(max(0, float(self.config.get(self.CONF_LEAVE_DELAY, 0))))
            self.leave_match()
            self.add_success()
        self.finish_rounds()

    def find_menu_close(self):
        return self.find_one(
            Labels.close_button,
            box=self.box_of_screen(0.92, 0.01, 0.98, 0.09),
            threshold=0.8,
        )

    def find_menu_button(self):
        # These temporary event buttons are shorter than the shared template.
        # Inspect their neutral fill away from the rounded edges and text.
        box = self.box_of_screen(0.83, 0.91, 0.95, 0.94)
        if self.calculate_color_percentage(self.BUTTON_FILL, box) > 0.6:
            return box

    def is_menu(self):
        return bool(self.find_menu_close() and self.find_menu_button())

    def is_lobby(self):
        # The blank orange area separates the lobby from the event detail page.
        return (
            self.is_menu()
            and self.calculate_color_percentage(
                self.ORANGE, self.box_of_screen(*self.LOBBY_BACKGROUND)
            )
            > 0.8
        )

    def wait_for_menu(self, time_out=30):
        return self.wait_until(
            self.is_menu,
            time_out=time_out,
            settle_time=0.3,
            raise_if_not_found=True,
        )

    def enter_lobby(self):
        self.next_frame()
        if self.is_lobby():
            return
        if not self.is_menu():
            raise WaitFailedException("Open the Golden Crunch event page before starting")
        self.operate_click(self.find_menu_button())
        self.wait_until(self.is_lobby, time_out=15, settle_time=0.3, raise_if_not_found=True)

    def enter_match(self):
        self.log_info("Starting Golden Crunch matchmaking")
        # Click once: repeating this action could cancel an ongoing match search.
        self.operate_click(self.find_menu_button())
        self.wait_until(
            self.find_exit,
            time_out=180,
            settle_time=0.2,
            raise_if_not_found=True,
        )

    def leave_match(self):
        self.next_frame()
        if self.is_menu():
            return
        self.log_info("Leaving Golden Crunch match")
        # A long delay can let the match finish and reach its result screen.
        if not self.find_menu_button():
            self.send_key("esc")
            self.wait_click_confirm(range=self.EXIT_CONFIRM, time_out=10)

        # The result screen retains the exit icon. Wait for its leave button,
        # or accept an automatic return to the event menu.
        self.wait_until(
            lambda: self.is_menu() or (self.find_exit() and self.find_menu_button()),
            time_out=30,
            settle_time=0.3,
            raise_if_not_found=True,
        )
        if not self.is_menu():
            self.operate_click(self.find_menu_button())
        self.wait_for_menu()
