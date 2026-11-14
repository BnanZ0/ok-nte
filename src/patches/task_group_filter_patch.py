"""Limit task-kind tabs and their counts to ungrouped tasks."""

from functools import wraps

_PATCH_INSTALLED = False


def install_task_group_filter_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.ui.qt.tasks import tasks_page

    original_matches_filter = tasks_page.matches_filter

    @wraps(original_matches_filter)
    def matches_filter(task, key, is_trigger):
        if (
            key in (tasks_page.KIND_ONETIME, tasks_page.KIND_TRIGGER)
            and getattr(task, "group_name", None) is not None
        ):
            return False
        return original_matches_filter(task, key, is_trigger)

    tasks_page.matches_filter = matches_filter

    original_build_filters = tasks_page.TasksPage._build_filters

    @wraps(original_build_filters)
    def build_filters_with_ungrouped_counts(self):
        original_build_filters(self)
        for key, is_trigger, tasks in (
            (tasks_page.KIND_ONETIME, False, self.onetime_tasks),
            (tasks_page.KIND_TRIGGER, True, self.trigger_tasks),
        ):
            if key in self.filter_tabs.keys():
                count = sum(matches_filter(task, key, is_trigger) for task in tasks)
                self.filter_tabs.set_count(key, count)

    tasks_page.TasksPage._build_filters = build_filters_with_ungrouped_counts
    _PATCH_INSTALLED = True
