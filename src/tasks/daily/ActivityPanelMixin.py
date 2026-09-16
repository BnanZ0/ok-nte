from src.Labels import Labels


class ActivityPanelMixin:
    def open_activity_panel(self):
        def action():
            self.openF1panel()
            self.operate_click(0.0551, 0.3833)
            self.sleep(0.5)
            return self.wait_panel(Labels.f1_activity_panel)

        self.log_info("开启活跃度面板")
        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("无法找到活跃度面板")
            return False
        return True
