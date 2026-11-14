from ok.ui.qt.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget

from src.char.custom.CustomCharManager import CustomCharManager
from src.ui.CharManagerTab import CharManagerTab
from src.ui.foundation.widgets.page_tabs import PageTabStrip
from src.ui.TeamManagerTab import TeamManagerTab


class CharHubTab(CustomTab):
    icon = "users"

    def __init__(self, manager: CustomCharManager = None):
        super().__init__()
        self.tr_name_tab = self.tr("角色中心")
        self.manager = manager or CustomCharManager()
        self._disposed = False
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.vBoxLayout.setContentsMargins(0, 16, 0, 0)
        self.vBoxLayout.setSpacing(0)
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(15, 0, 15, 0)
        self.pivot = PageTabStrip(parent=self.view, label_size=15)
        tab_row.addWidget(self.pivot, 1)
        self.vBoxLayout.addLayout(tab_row)
        self.stacked_widget = QStackedWidget(self.view)
        self.vBoxLayout.addWidget(self.stacked_widget, 1)
        self.team_manager_tab = TeamManagerTab(self.manager, owner=self)
        self.char_manager_tab = CharManagerTab(owner=self, manager=self.manager)
        for widget, key in (
            (self.char_manager_tab, "CharManagerTab"),
            (self.team_manager_tab, "TeamManagerTab"),
        ):
            widget.setObjectName(key)
            self.stacked_widget.addWidget(widget)
            self.pivot.add_tab(key, widget.name)
        self.pivot.current_changed.connect(self._select_tab)
        self.stacked_widget.currentChanged.connect(self._sync_tab)
        self._select_tab("CharManagerTab")

    def _select_tab(self, key):
        for index in range(self.stacked_widget.count()):
            widget = self.stacked_widget.widget(index)
            if widget.objectName() == key:
                self.stacked_widget.setCurrentWidget(widget)
                return

    def _sync_tab(self, index):
        widget = self.stacked_widget.widget(index)
        if widget is not None:
            self.pivot.set_current(widget.objectName())

    def navigate_to_workshop(self):
        if not self._disposed:
            self.pivot.set_current("TeamManagerTab")
            self.team_manager_tab.navigate_to_workshop()

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.char_manager_tab.dispose()
        self.team_manager_tab.dispose()

    @property
    def name(self):
        return self.tr_name_tab
