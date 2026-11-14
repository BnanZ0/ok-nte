import asyncio
import random
import threading
from collections import Counter

from ok import og
from ok.ui.qt.kit import (
    Button,
    Card,
    ComboBox,
    Label,
    NumberBox,
    ProgressRing,
    Switch,
    TabStrip,
    icon,
    show_toast,
    theme,
)
from ok.ui.qt.kit.icons import tinted_icon
from ok.ui.qt.widget.CustomTab import CustomTab
from ok.util.config import Config
from PySide6.QtCore import QEvent, QLocale, QObject, QSize, Qt, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QBoxLayout,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionSlider,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.midi_player import (
    Instrument,
    LayoutMode,
    MidiLibraryService,
    MidiPlaybackController,
    PianoLayout,
    PlaybackOptions,
    PlayMode,
    SongStats,
)
from src.midi_player.preparation import store_prepared_analysis, submit_midi_analysis
from src.ui.features.midi.time_picker import ScheduleTimePicker
from src.ui.features.midi.widgets import (
    KeyConfigWidget,
    MarqueeBodyLabel,
    MarqueeTitleLabel,
    PitchChartWidget,
    PlaybackSlider,
    SongLibraryTree,
    TrackCheckBox,
    TrackListScroll,
)
from src.ui.foundation.widgets.lists import COLLECTION_STATUS_ROLE
from src.ui.foundation.widgets.page_tabs import PageTabStrip
from src.ui.icons import feature_icon, resource_icon

MIDI_PLAYER_CONFIG_DEFAULTS = {
    "instrument": Instrument.PIANO.value,
    "pitch": 0,
    "auto_pitch": False,
    "smart_remap": True,
    "play_mode": 1,
    "speed_percent": 100,
    "schedule_enabled": False,
    "schedule_time": "00:00",
    "bounds_36": [0.103, 0.654, 0.903, 0.919],
    "bounds_guitar_36": [0.103, 0.654, 0.903, 0.919],
    "collapsed_sections": {},  # Retain saved values for older versions.
    "track_selection": {},
    "song_analysis_settings": {},
}


async def _cancel_task_and_wait(task):
    task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)
    exception = result[0]
    if isinstance(exception, asyncio.CancelledError):
        return
    if isinstance(exception, BaseException):
        raise exception


class MidiPlayerSignals(QObject):
    analysis_done = Signal(str, object, object, float, object, object)
    analysis_failed = Signal(str, str)
    playback_status = Signal(str)
    playback_progress = Signal(float, float)
    playback_song_changed = Signal(str)
    library_indexed = Signal(object)


class MidiPlayerTab(CustomTab):
    PLAYBACK_BUTTON_SIZE = QSize(40, 32)

    def __init__(self):
        super().__init__()
        self.icon = feature_icon("music-note")
        self.tr_name_tab = self.tr("自动乐器")
        self.tr_no_song_selected = self.tr("未选择歌曲")
        self.is_playing = False
        self._disposed = False
        self._calibration_stop = threading.Event()
        self.is_favorite = False
        self.favorite_active_icon = tinted_icon(
            resource_icon("heart-fill"), {"light": "#E81123", "dark": "#FF99A4"}
        )
        self.current_key_mode = "36_keys"
        self.config = Config("MidiPlayerTab", MIDI_PLAYER_CONFIG_DEFAULTS)
        self.library = MidiLibraryService()
        self.songs_by_id = {}
        self.selected_song_id = None
        self.playing_song_id = None
        self.fav_filter = "all"
        self.track_checkboxes = []
        self._loaded = False
        self._track_ui_song_id = None
        self._loading_settings = False
        self._analysis_generation = 0
        self._analysis_future = None
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setSingleShot(True)
        self._analysis_timer.setInterval(160)
        self._analysis_timer.timeout.connect(self._start_analysis_for_current_song)
        self._play_loop = None
        self._play_controller = None
        self._playback_thread = None
        self._duration_seconds = 0.0
        self._current_seconds = 0.0
        self._is_slider_dragging = False
        self._calibration_thread = None
        self.midi_signals = MidiPlayerSignals(self)
        self.midi_signals.analysis_done.connect(
            self.on_song_analysis_done, Qt.ConnectionType.QueuedConnection
        )
        self.midi_signals.analysis_failed.connect(
            self.on_song_analysis_failed, Qt.ConnectionType.QueuedConnection
        )
        self.midi_signals.playback_status.connect(
            self.on_playback_status_changed, Qt.ConnectionType.QueuedConnection
        )
        self.midi_signals.playback_progress.connect(
            self.on_playback_progress, Qt.ConnectionType.QueuedConnection
        )
        self.midi_signals.playback_song_changed.connect(
            self.on_playback_song_changed, Qt.ConnectionType.QueuedConnection
        )
        self.midi_signals.library_indexed.connect(
            self._on_library_indexed, Qt.ConnectionType.QueuedConnection
        )

        # Main horizontal layout to split left (list) and right (details)
        self.main_h_layout = QHBoxLayout()
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.addLayout(self.main_h_layout, 1)
        self.main_h_layout.setContentsMargins(0, 0, 0, 0)
        self.main_h_layout.setSpacing(16)

        self.setup_left_panel()
        self.setup_right_panel()
        self._refresh_control_icons()
        theme.changed.connect(self._refresh_control_icons)

        self.main_h_layout.addWidget(self.left_widget, 1)
        self.main_h_layout.addWidget(self.right_widget, 3)

        self.fav_segment.set_current("all")
        self.segmented_widget.set_current("36_keys")
        self._load_saved_settings()

    @property
    def name(self):  # type: ignore
        return self.tr_name_tab

    def setup_left_panel(self):
        self.left_widget = QWidget()
        self.left_v_layout = QVBoxLayout(self.left_widget)
        self.left_v_layout.setContentsMargins(16, 16, 0, 16)
        self.left_v_layout.setSpacing(10)

        # Header with segment
        self.fav_segment = TabStrip()
        self.fav_segment.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for key, title in (("all", self.tr("全部歌曲")), ("fav", self.tr("已收藏"))):
            tab = self.fav_segment.add_tab(key, title)
            tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tab.layout().setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fav_segment.current_changed.connect(self.on_fav_segment_changed)
        self.left_v_layout.addWidget(self.fav_segment)

        self.library_card = Card(self.left_widget)
        library_layout = QVBoxLayout(self.library_card)
        library_layout.setContentsMargins(14, 14, 14, 14)
        library_layout.setSpacing(10)

        hbox_search = QHBoxLayout()
        hbox_search.setContentsMargins(0, 0, 0, 0)
        hbox_search.setSpacing(5)
        self.song_search_edit = QLineEdit(self.library_card)
        self.song_search_edit.setPlaceholderText(self.tr("搜索歌曲"))
        self.song_search_edit.setClearButtonEnabled(True)
        self.song_search_edit.textChanged.connect(self._apply_song_tree_filter)
        hbox_search.addWidget(self.song_search_edit)

        self.btn_import_midi = Button(icon="plus", variant="subtle")
        self.btn_import_midi.setToolTip(self.tr("导入 MIDI"))
        self.btn_import_midi.clicked.connect(self.on_import_midi)
        hbox_search.addWidget(self.btn_import_midi)

        self.btn_open_midi = Button(icon=icon("folder"), variant="subtle")
        self.btn_open_midi.setToolTip(self.tr("打开 mid_lib"))
        self.btn_open_midi.clicked.connect(self.on_open_midi)
        hbox_search.addWidget(self.btn_open_midi)

        library_layout.addLayout(hbox_search)

        self.library_stack = QStackedWidget(self.library_card)
        self.progress_container = QWidget(self.library_stack)
        self.progress_ring = ProgressRing(size=50)
        progress_layout = QVBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.addWidget(self.progress_ring, alignment=Qt.AlignmentFlag.AlignCenter)

        self.song_tree_widget = SongLibraryTree(self.library_stack)
        self.song_tree_widget.setHeaderHidden(True)
        self.song_tree_widget.setRootIsDecorated(True)
        self.song_tree_widget.setIndentation(12)
        self.song_tree_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.song_tree_widget.setStyleSheet("QTreeWidget { background: transparent; border: 0; }")
        self.song_tree_widget.currentItemChanged.connect(self.on_song_selected)
        self.library_stack.addWidget(self.song_tree_widget)
        self.library_stack.addWidget(self.progress_container)
        library_layout.addWidget(self.library_stack, 1)
        self.left_v_layout.addWidget(self.library_card, 1)

    def setup_right_panel(self):
        self.right_widget = QWidget()
        self.right_v_layout = QVBoxLayout(self.right_widget)
        self.right_v_layout.setContentsMargins(0, 16, 16, 16)
        self.right_v_layout.setSpacing(8)
        self.setup_player_controls()
        self.setup_selection_info()
        player_area = QVBoxLayout()
        player_area.setSpacing(20)
        player_area.addWidget(self.player_card)
        player_area.addWidget(self.selection_card)
        self.right_v_layout.addLayout(player_area)

        # Reserve the same text width before and after a tab is selected.
        self.settings_tabs = PageTabStrip(stable_label_width=True)
        self.settings_tab_scroll = QScrollArea(self.right_widget)
        self.settings_tab_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_tab_scroll.setWidgetResizable(True)
        self.settings_tab_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_tab_scroll.setWidget(self.settings_tabs)
        self.right_v_layout.addWidget(self.settings_tab_scroll)
        self.settings_stack = QStackedWidget(self.right_widget)
        self.settings_pages = {}
        self.right_v_layout.addWidget(self.settings_stack, 1)

        self.setup_playback_settings()
        self.setup_track_selection()
        self.setup_pitch_adjustment()
        self.setup_key_config()
        self.setup_calibration_tools()

        tab_height = self.settings_tabs.sizeHint().height()
        scrollbar_height = self.settings_tab_scroll.horizontalScrollBar().sizeHint().height()
        self.settings_tab_scroll.setFixedHeight(tab_height + scrollbar_height)
        self.settings_tabs.current_changed.connect(self._show_settings_page)

    def setup_player_controls(self):
        self.player_card = Card(self.right_widget)
        vbox_player = QVBoxLayout(self.player_card)
        vbox_player.setContentsMargins(20, 20, 20, 20)
        vbox_player.setSpacing(15)

        # Track Info & Favorite
        hbox_top = QHBoxLayout()
        self.lbl_track_name = MarqueeTitleLabel(self.tr_no_song_selected)

        self.btn_favorite = Button(icon="heart", variant="subtle", size="small")
        self.btn_favorite.setToolTip(self.tr("收藏歌曲"))
        self.btn_favorite.clicked.connect(self.on_favorite_toggled)
        self.btn_favorite.hide()

        hbox_top.addWidget(self.lbl_track_name, 1)
        hbox_top.addWidget(self.btn_favorite)
        vbox_player.addLayout(hbox_top)

        # Progress Slider
        self.slider_progress = PlaybackSlider()
        self.slider_progress.setRange(0, 10000)
        self.slider_progress.setValue(0)
        self.slider_progress.valueChanged.connect(self.on_progress_changed)
        self.slider_progress.sliderPressed.connect(self.on_progress_slider_pressed)
        self.slider_progress.sliderReleased.connect(self.on_progress_slider_released)
        self.slider_progress.installEventFilter(self)
        vbox_player.addWidget(self.slider_progress)

        self.lbl_time_current = Label("00:00", "body")
        self.lbl_time_total = Label("00:00", "body")
        time_layout = QHBoxLayout()
        time_layout.addWidget(self.lbl_time_current)
        time_layout.addStretch(1)
        time_layout.addWidget(self.lbl_time_total)
        vbox_player.addLayout(time_layout)

        # Playback Buttons
        hbox_controls = QHBoxLayout()
        hbox_controls.setSpacing(25)

        self.btn_prev = Button(icon=resource_icon("previous"))
        self._play_icon = resource_icon("play", color="on_accent")
        self._pause_icon = resource_icon("pause", color="on_accent")
        self.btn_play_pause = Button(icon=self._play_icon, variant="primary")
        self.btn_next = Button(icon=resource_icon("next"))

        self.btn_prev.clicked.connect(self.on_prev_clicked)
        self.btn_play_pause.clicked.connect(self.on_play_pause_clicked)
        self.btn_next.clicked.connect(self.on_next_clicked)

        hbox_controls.addStretch(1)
        for button in (self.btn_prev, self.btn_play_pause, self.btn_next):
            button.setFixedSize(self.PLAYBACK_BUTTON_SIZE)
            hbox_controls.addWidget(button)
        hbox_controls.addStretch(1)
        vbox_player.addLayout(hbox_controls)

    def setup_selection_info(self):
        self.selection_card = Card(self.right_widget)
        layout = QHBoxLayout(self.selection_card)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(14)
        self._selection_note_icon = resource_icon("music-note")
        self.selection_icon = QLabel(self.selection_card)
        layout.addWidget(self.selection_icon)
        self.selection_title = MarqueeBodyLabel(self.tr_no_song_selected, self.selection_card)
        layout.addWidget(self.selection_title, 1)
        self.btn_play_selected = Button(
            self.tr("播放"), icon="play", size="small", parent=self.selection_card
        )
        self.btn_play_selected.clicked.connect(self.on_play_selected_clicked)
        self.btn_play_selected.setDisabled(True)
        layout.addWidget(self.btn_play_selected)

    def _refresh_control_icons(self):
        self.selection_icon.setPixmap(
            self._selection_note_icon.pixmap(QSize(18, 18), self.selection_icon.devicePixelRatioF())
        )
        self.song_tree_widget.viewport().update()
        self.track_scroll.viewport().update()

    def setup_playback_settings(self):
        self.settings_card = QWidget()
        vbox = QVBoxLayout(self.settings_card)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        # Playback Mode
        hbox_mode = QHBoxLayout()
        lbl_mode = Label(self.tr("播放模式"), "body")
        self.cb_mode = ComboBox()
        self.cb_mode.addItems([self.tr("单曲循环"), self.tr("顺序播放"), self.tr("随机播放")])
        self.cb_mode.setCurrentIndex(1)
        self.cb_mode.currentIndexChanged.connect(self.on_mode_changed)
        hbox_mode.addWidget(lbl_mode)
        hbox_mode.addStretch()
        hbox_mode.addWidget(self.cb_mode)
        vbox.addLayout(hbox_mode)

        # Playback Speed
        hbox_speed = QHBoxLayout()
        lbl_speed = Label(self.tr("播放速度"), "body")
        self.lbl_speed_value = Label("1.00x", "body")
        self.lbl_speed_value.setFixedWidth(50)
        self.lbl_speed_value.setAlignment(Qt.AlignCenter)
        self.slider_speed = PlaybackSlider()
        self.slider_speed.setRange(25, 200)
        self.slider_speed.setValue(100)
        self.slider_speed.setFixedWidth(200)
        self.slider_speed.valueChanged.connect(self.on_speed_value_changed)
        self.slider_speed.sliderReleased.connect(self.on_speed_slider_released)
        self.slider_speed.installEventFilter(self)
        hbox_speed.addWidget(lbl_speed)
        hbox_speed.addStretch()
        hbox_speed.addWidget(self.lbl_speed_value)
        hbox_speed.addWidget(self.slider_speed)
        vbox.addLayout(hbox_speed)

        # Scheduled Stop
        hbox_schedule = QHBoxLayout()
        lbl_schedule = Label(self.tr("定时关闭"), "body")
        self.time_picker = ScheduleTimePicker(lbl_schedule.text(), parent=self.settings_card)
        self.time_picker.setTime(QTime(0, 30))
        self.time_picker.setEnabled(False)
        self.time_picker.timeChanged.connect(self.on_schedule_time_changed)
        self.switch_schedule = Switch()
        self.switch_schedule.toggled.connect(self.on_schedule_toggled)
        hbox_schedule.addWidget(lbl_schedule)
        hbox_schedule.addStretch()
        hbox_schedule.addWidget(self.time_picker)
        hbox_schedule.addSpacing(10)
        hbox_schedule.addWidget(self.switch_schedule)
        vbox.addLayout(hbox_schedule)

        self._add_settings_page("playback_settings", self.tr("播放设置"), self.settings_card)

    def setup_track_selection(self):
        self.track_page = QWidget(self.settings_stack)
        self.track_page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.track_page.installEventFilter(self)
        page_layout = QVBoxLayout(self.track_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        track_card = Card(self.track_page)
        page_layout.addWidget(track_card)
        page_layout.addStretch(1)
        self.track_layout = QBoxLayout(QBoxLayout.Direction.RightToLeft, track_card)
        self.track_layout.setContentsMargins(14, 14, 14, 14)
        self.track_scroll = TrackListScroll(track_card)
        self.track_scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.track_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.track_scroll.setWidgetResizable(True)
        self.track_scroll.setAccessibleName(self.tr("音轨"))
        self.track_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.track_preview_panel = QWidget(track_card)
        self.track_preview_layout = QBoxLayout(
            QBoxLayout.Direction.TopToBottom, self.track_preview_panel
        )
        self.track_preview_layout.setContentsMargins(6, 4, 6, 4)
        self.track_preview_layout.setSpacing(10)
        metrics = self.track_metrics = QWidget(self.track_preview_panel)
        metrics_layout = QVBoxLayout(metrics)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setSpacing(6)
        self.track_preview_layout.addWidget(metrics, 1)

        selected_row = QHBoxLayout()
        selected_row.addWidget(Label(self.tr("已选音轨"), "secondary", metrics))
        selected_row.addStretch(1)
        self.track_selected_value = Label("0 / 0", "strong", metrics)
        selected_row.addWidget(self.track_selected_value)
        metrics_layout.addLayout(selected_row)

        notes_row = QHBoxLayout()
        notes_row.addWidget(Label(self.tr("选中音符"), "secondary", metrics))
        notes_row.addStretch(1)
        self.track_notes_value = Label("0", "strong", metrics)
        notes_row.addWidget(self.track_notes_value)
        metrics_layout.addLayout(notes_row)

        self.btn_play_tracks = Button(
            self.tr("试听选中音轨"), icon="play", variant="primary", parent=self.track_preview_panel
        )
        self.btn_play_tracks.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.btn_play_tracks.clicked.connect(self.on_play_selected_clicked)
        self.btn_play_tracks.setEnabled(False)
        self.track_preview_layout.addWidget(self.btn_play_tracks, 0, Qt.AlignmentFlag.AlignVCenter)

        self.track_checks_widget = QWidget(self.track_scroll)
        self.track_checks_widget.setObjectName("scrollContents")
        self.track_checks_widget.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.track_checks_layout = QVBoxLayout(self.track_checks_widget)
        self.track_checks_layout.setContentsMargins(0, 0, 0, 0)
        self.track_checks_layout.setSpacing(0)
        self.track_checks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.track_checks_layout.addWidget(Label(self.tr("尚未载入音轨"), "body"))
        self.track_scroll.setWidget(self.track_checks_widget)

        self.track_layout.addWidget(self.track_preview_panel, 1, Qt.AlignmentFlag.AlignTop)
        self.track_layout.addWidget(self.track_scroll, 2)
        self._update_track_summary()
        self.settings_pages["track_selection"] = self.track_page
        self.settings_stack.addWidget(self.track_page)
        self.settings_tabs.add_tab("track_selection", self.tr("音轨选择"))

    def _update_track_layout(self):
        height = self.track_page.height()
        # Stacking is useful only when it leaves enough height for the track viewport.
        compact = self.track_page.width() < 520 and height >= 200
        direction = (
            QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.RightToLeft
        )
        self.track_layout.setDirection(direction)
        self.track_layout.setStretch(0, 0 if compact else 1)
        self.track_layout.setStretch(1, 0 if compact else 2)
        self.track_preview_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if compact else QBoxLayout.Direction.TopToBottom
        )
        self.track_layout.setSpacing(10 if compact else 20)
        self.track_preview_panel.setMaximumWidth(self.track_page.width() if compact else 244)
        short = height < 140
        self.track_metrics.setVisible(not short)
        margin = 6 if short else 14
        self.track_layout.setContentsMargins(margin, margin, margin, margin)

    def _update_track_summary(self):
        playable = [checkbox for checkbox in self.track_checkboxes if checkbox.track.note_count > 0]
        selected = [checkbox for checkbox in playable if checkbox.isChecked()]
        notes = sum(checkbox.track.note_count for checkbox in selected)
        self.track_selected_value.setText(f"{len(selected)} / {len(playable)}")
        self.track_notes_value.setText(QLocale().toString(notes))
        self.track_preview_panel.setToolTip(
            f"{self.tr('已选音轨')} {self.track_selected_value.text()}\n"
            f"{self.tr('选中音符')} {self.track_notes_value.text()}"
        )
        self.btn_play_tracks.setEnabled(bool(selected))

    def setup_pitch_adjustment(self):
        self.pitch_card = QWidget()
        vbox_pitch = QVBoxLayout(self.pitch_card)
        vbox_pitch.setContentsMargins(0, 0, 0, 0)
        vbox_pitch.setSpacing(10)

        self.lbl_pitch = Label(self.tr("音高调整 (半音):"), "body")
        self.spn_pitch = NumberBox(minimum=-24, maximum=24)
        self.spn_pitch.setFixedWidth(240)
        self.spn_pitch.spin_box.wheelEvent = lambda event: event.ignore()
        self.spn_pitch.spin_box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.spn_pitch.valueChanged.connect(self.on_pitch_changed)
        self.lbl_auto_pitch = Label(self.tr("自动音高"), "body")
        self.switch_auto_pitch = Switch()
        self.switch_auto_pitch.setChecked(False)
        self.switch_auto_pitch.toggled.connect(self.on_auto_pitch_changed)
        self.lbl_smart_remap = Label(self.tr("智能音域压缩"), "body")
        self.switch_smart_remap = Switch()
        self.switch_smart_remap.setChecked(True)
        self.switch_smart_remap.toggled.connect(self.on_smart_remap_changed)

        hbox_pitch = QHBoxLayout()
        hbox_pitch.addWidget(self.lbl_pitch)
        hbox_pitch.addStretch()
        hbox_pitch.addWidget(self.spn_pitch)
        vbox_pitch.addLayout(hbox_pitch)

        hbox_auto = QHBoxLayout()
        hbox_auto.addWidget(self.lbl_auto_pitch)
        hbox_auto.addStretch()
        hbox_auto.addWidget(self.switch_auto_pitch)
        vbox_pitch.addLayout(hbox_auto)

        hbox_remap = QHBoxLayout()
        hbox_remap.addWidget(self.lbl_smart_remap)
        hbox_remap.addStretch()
        hbox_remap.addWidget(self.switch_smart_remap)
        vbox_pitch.addLayout(hbox_remap)

        # Pitch Chart
        self.pitch_chart = PitchChartWidget()
        vbox_pitch.addWidget(self.pitch_chart)

        self._add_settings_page("pitch_analysis", self.tr("音高与音域分析"), self.pitch_card)

    def setup_key_config(self):
        self.key_card = QWidget()
        vbox_key = QVBoxLayout(self.key_card)
        vbox_key.setContentsMargins(0, 0, 0, 0)
        vbox_key.setSpacing(12)

        hbox_instrument = QHBoxLayout()
        hbox_instrument.addWidget(Label(self.tr("乐器"), "body", self.key_card))
        hbox_instrument.addStretch()
        self.cb_instrument = ComboBox(parent=self.key_card)
        self.cb_instrument.addItem(self.tr("钢琴"), userData=Instrument.PIANO.value)
        self.cb_instrument.addItem(self.tr("吉他"), userData=Instrument.GUITAR.value)
        self.cb_instrument.setMinimumWidth(150)
        self.cb_instrument.setToolTip(self.tr("选择与游戏当前演奏界面一致的乐器"))
        self.cb_instrument.currentIndexChanged.connect(self.on_instrument_changed)
        hbox_instrument.addWidget(self.cb_instrument)
        vbox_key.addLayout(hbox_instrument)

        hbox_mode = QHBoxLayout()
        lbl_mode = Label(self.tr("键盘布局"), "body")

        # Mode Selection
        self.segmented_widget = TabStrip()
        self.segmented_widget.add_tab("36_keys", self.tr("36键半音布局 (12x3)"))
        self.segmented_widget.add_tab("21_keys", self.tr("21键自然音布局 (已禁用)"))
        self.segmented_widget.item("21_keys").setEnabled(False)
        self.segmented_widget.item("21_keys").setToolTip(self.tr("当前乐器使用 36 键半音布局"))

        hbox_mode.addWidget(lbl_mode)
        hbox_mode.addStretch()
        hbox_mode.addWidget(self.segmented_widget)
        vbox_key.addLayout(hbox_mode)

        # Stacked Widget for Coordinates
        self.stacked_widget = QStackedWidget()

        # 36 keys: 0.103, 0.654, 0.903, 0.919
        self.config_36 = KeyConfigWidget(0.103, 0.654, 0.903, 0.919)
        self.stacked_widget.addWidget(self.config_36)

        # 21 keys: 0.247, 0.655, 0.802, 0.918
        self.config_21 = KeyConfigWidget(0.247, 0.655, 0.802, 0.918)
        self.config_21.setEnabled(False)
        self.stacked_widget.addWidget(self.config_21)

        vbox_key.addWidget(self.stacked_widget)
        self._add_settings_page("key_config", self.tr("乐器与键位设置"), self.key_card)

        self.segmented_widget.current_changed.connect(self.on_key_mode_changed)
        self._connect_bounds_savers(self.config_36)

    def setup_calibration_tools(self):
        self.calibration_card = QWidget()
        vbox_calibration = QVBoxLayout(self.calibration_card)
        vbox_calibration.setContentsMargins(0, 0, 0, 0)
        vbox_calibration.setSpacing(12)

        hbox = QHBoxLayout()
        self.btn_print_mapping = Button(self.tr("打印键位映射"))
        self.btn_test_middle_row = Button(self.tr("测试中音行"))
        self.btn_test_all_keys = Button(self.tr("测试全部键"))

        self.btn_print_mapping.clicked.connect(self.print_current_key_mapping)
        self.btn_test_middle_row.clicked.connect(lambda: self.start_key_calibration("middle"))
        self.btn_test_all_keys.clicked.connect(lambda: self.start_key_calibration("all"))

        hbox.addWidget(self.btn_print_mapping)
        hbox.addWidget(self.btn_test_middle_row)
        hbox.addWidget(self.btn_test_all_keys)
        hbox.addStretch()
        vbox_calibration.addLayout(hbox)

        self.calibration_hint = Label(
            self.tr("每个测试音间隔约 1 秒，请用调音器确认实际发音。"), "body"
        )
        self.calibration_hint.setWordWrap(True)
        vbox_calibration.addWidget(self.calibration_hint)

        self._add_settings_page("calibration_tools", self.tr("调试校准"), self.calibration_card)

    # --- Data Demos & Logic ---

    def _add_settings_page(self, key, title, content):
        scroll = QScrollArea(self.settings_stack)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        page = QWidget()
        page.setObjectName("scrollContents")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        card = Card(page)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.addWidget(content)
        layout.addWidget(card)
        layout.addStretch(1)
        scroll.setWidget(page)
        self.settings_pages[key] = scroll
        self.settings_stack.addWidget(scroll)
        self.settings_tabs.add_tab(key, title)

    def _show_settings_page(self, key):
        self.settings_stack.setCurrentWidget(self.settings_pages[key])
        self.settings_tab_scroll.ensureWidgetVisible(self.settings_tabs.item(key), 8, 0)

    def _connect_bounds_savers(self, widget):
        for edit in (widget.spn_x1, widget.spn_y1, widget.spn_x2, widget.spn_y2):
            edit.textChanged.connect(self.save_key_bounds)

    def _load_saved_settings(self):
        self._loading_settings = True
        try:
            index = self.cb_instrument.findData(
                self.config.get("instrument", Instrument.PIANO.value)
            )
            self.cb_instrument.setCurrentIndex(max(0, index))
            self.spn_pitch.setValue(int(self.config.get("pitch", 0)))
            self.switch_auto_pitch.setChecked(bool(self.config.get("auto_pitch", False)))
            self.switch_smart_remap.setChecked(bool(self.config.get("smart_remap", True)))
            self.spn_pitch.setEnabled(not self.switch_auto_pitch.isChecked())
            self.cb_mode.setCurrentIndex(int(self.config.get("play_mode", 1)))
            self._set_speed_percent(int(self.config.get("speed_percent", 100)))
            self.switch_schedule.setChecked(bool(self.config.get("schedule_enabled", False)))
            saved_time = QTime.fromString(str(self.config.get("schedule_time", "00:00")), "HH:mm")
            if saved_time.isValid():
                self.time_picker.setTime(saved_time)
            self._load_instrument_bounds()
        finally:
            self._loading_settings = False

    def save_key_bounds(self, *args):
        if self._loading_settings:
            return
        self.config[self._instrument_bounds_key()] = list(self.config_36.get_coords())

    def _current_instrument(self):
        return Instrument(self.cb_instrument.currentData())

    def _instrument_bounds_key(self):
        if self._current_instrument() == Instrument.GUITAR:
            return "bounds_guitar_36"
        return "bounds_36"

    def _load_instrument_bounds(self):
        key = self._instrument_bounds_key()
        bounds = self.config.get(key, MIDI_PLAYER_CONFIG_DEFAULTS[key])
        if not isinstance(bounds, list) or len(bounds) != 4:
            bounds = MIDI_PLAYER_CONFIG_DEFAULTS[key]
        for edit, value in zip(
            (
                self.config_36.spn_x1,
                self.config_36.spn_y1,
                self.config_36.spn_x2,
                self.config_36.spn_y2,
            ),
            bounds,
        ):
            edit.setText(str(value))
        self.config_36.setEnabled(self._current_instrument() == Instrument.PIANO)

    def on_instrument_changed(self, index):
        if self._loading_settings:
            return
        self._loading_settings = True
        try:
            self._load_instrument_bounds()
        finally:
            self._loading_settings = False
        self.config["instrument"] = self._current_instrument().value
        self._current_seconds = 0.0
        self._set_progress_slider_value(0)
        self.lbl_time_current.setText("00:00")
        self._request_analysis_for_current_song()

    def _set_speed_percent(self, value):
        value = max(25, min(200, int(value)))
        self.slider_speed.blockSignals(True)
        self.slider_speed.setValue(value)
        self.slider_speed.setSliderPosition(value)
        self.slider_speed.update()
        self.slider_speed.blockSignals(False)
        self.lbl_speed_value.setText(f"{value / 100:.2f}x")

    def on_speed_value_changed(self, value):
        self._set_speed_percent(value)

    def on_speed_slider_released(self):
        value = self.slider_speed.value()
        self._apply_speed_change(value)

    def on_speed_slider_clicked(self, value):
        self._apply_speed_change(value)

    def _apply_speed_change(self, value):
        if not self._loading_settings:
            self.config["speed_percent"] = int(value)

    def load_pitch_demo_data(self):
        """Inject dummy data for interactive visual testing of the chart"""
        dummy = dict.fromkeys(range(35, 91), 0)
        for note in [45, 48, 52, 55, 60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 85]:
            # Some dummy heights
            dummy[note] = 100 - abs(65 - note) * 2
            if dummy[note] < 0:
                dummy[note] = 10

        # specifically make a failing note match the tooltip for fun
        dummy[45] = 16

        base_min = 48 if self.current_key_mode == "36_keys" else 60
        base_max = 83 if self.current_key_mode == "36_keys" else 81
        shift = self.spn_pitch.value()

        playable = set(range(base_min - shift, base_max - shift + 1))
        self.pitch_chart.set_data(dummy, base_min - shift, base_max - shift, playable)

    def refresh_song_list(self, keep_selection=True):
        """Refresh the visible list from mid_lib without parsing MIDI files.
        Runs indexing in a background thread to prevent UI freezing.
        """
        selected_id = self.selected_song_id if keep_selection else None

        def _task():
            try:
                self.library.index()
            except Exception as e:
                self.logger.error("Failed to index MIDI library", e)
            self.midi_signals.library_indexed.emit(selected_id)

        self.library_stack.setCurrentWidget(self.progress_container)

        threading.Thread(target=_task, daemon=True).start()

    def _on_library_indexed(self, selected_id):
        if self._disposed:
            return
        self.library_stack.setCurrentWidget(self.song_tree_widget)
        self.songs_by_id = {song.id: song for song in self.library.list_songs()}
        self._prune_missing_song_settings()
        self.populate_song_list(selected_id)

    def populate_song_list(self, selected_id=None):
        show_favorites = self.fav_filter == "fav"
        self.song_tree_widget.setUpdatesEnabled(False)
        self.song_tree_widget.clear()
        selected_item = None
        folder_items = {}

        for song in self.library.list_songs():
            if show_favorites and not song.favorite:
                continue
            try:
                relative_path = song.path.relative_to(self.library.library_dir)
                folder_parts = relative_path.parts[:-1]
            except ValueError:
                folder_parts = ()

            parent_item = None
            folder_key = ()
            for folder_name in folder_parts:
                folder_key = (*folder_key, folder_name)
                folder_item = folder_items.get(folder_key)
                if folder_item is None:
                    folder_item = QTreeWidgetItem([folder_name])
                    folder_item.setToolTip(0, folder_name)
                    folder_item.setData(0, Qt.ItemDataRole.UserRole, None)
                    if parent_item is None:
                        self.song_tree_widget.addTopLevelItem(folder_item)
                    else:
                        parent_item.addChild(folder_item)
                    folder_item.setExpanded(True)
                    folder_items[folder_key] = folder_item
                parent_item = folder_item

            item = QTreeWidgetItem([song.title])
            item.setData(0, Qt.ItemDataRole.UserRole, song.id)
            if song.favorite and not show_favorites:
                status = self.tr("已收藏")
                item.setData(0, COLLECTION_STATUS_ROLE, status)
                item.setData(0, Qt.ItemDataRole.AccessibleDescriptionRole, status)
            item.setToolTip(0, item.text(0))
            if parent_item is None:
                self.song_tree_widget.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            if song.id == selected_id:
                selected_item = item

        self.song_tree_widget.setUpdatesEnabled(True)
        self._apply_song_tree_filter(self.song_search_edit.text())
        if selected_item is not None:
            self.song_tree_widget.setCurrentItem(selected_item)
        elif self._first_visible_song_item() is not None and selected_id is None:
            self.song_tree_widget.setCurrentItem(self._first_visible_song_item())
        else:
            self.song_tree_widget.setCurrentItem(None)
            if selected_id is not None:
                self.on_song_selected(None, None)
        self._sync_playlist_to_controller()

    def _iter_tree_items(self, parent=None):
        if parent is None:
            for index in range(self.song_tree_widget.topLevelItemCount()):
                item = self.song_tree_widget.topLevelItem(index)
                yield item
                yield from self._iter_tree_items(item)
            return
        for index in range(parent.childCount()):
            item = parent.child(index)
            yield item
            yield from self._iter_tree_items(item)

    def _is_song_tree_item(self, item):
        return item is not None and bool(item.data(0, Qt.ItemDataRole.UserRole))

    def _apply_song_tree_filter(self, keyword):
        normalized = keyword.strip().lower()

        def apply_item(item):
            is_song = self._is_song_tree_item(item)
            own_match = normalized in item.text(0).lower()
            child_match = False
            for index in range(item.childCount()):
                child_match = apply_item(item.child(index)) or child_match
            visible = (own_match if is_song else child_match or not normalized) or child_match
            item.setHidden(not visible)
            if child_match:
                item.setExpanded(True)
            return visible

        for index in range(self.song_tree_widget.topLevelItemCount()):
            apply_item(self.song_tree_widget.topLevelItem(index))
        self._sync_playlist_to_controller()

    def _first_visible_song_item(self):
        for item in self._iter_tree_items():
            if self._is_song_tree_item(item) and not item.isHidden():
                return item
        return None

    def _current_layout_mode(self):
        return LayoutMode.KEYS_21 if self.current_key_mode == "21_keys" else LayoutMode.KEYS_36

    def _current_layout(self):
        bounds = self.get_current_key_coordinates()
        return PianoLayout(
            self._current_layout_mode(),
            bounds,
            instrument=self._current_instrument(),
        )

    def _key_label(self, key):
        row_names = {
            0: self.tr("高音"),
            1: self.tr("中音"),
            2: self.tr("低音"),
        }
        labels_36 = ["1", "#1", "2", "b3", "3", "4", "#4", "5", "#5", "6", "b7", "7"]
        labels_21 = ["1", "2", "3", "4", "5", "6", "7"]
        labels = labels_36 if self._current_layout_mode() == LayoutMode.KEYS_36 else labels_21
        if self._current_instrument() == Instrument.GUITAR:
            names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
            note = f"{names[key.pitch % 12]}{key.pitch // 12 - 1}"
        else:
            note = labels[key.column] if 0 <= key.column < len(labels) else str(key.column + 1)
        return f"{row_names.get(key.row, key.row)} {note}"

    def _current_key_sequence(self, mode):
        keys = self._current_layout().keys
        if mode == "middle":
            keys = tuple(key for key in keys if key.row == 1)
        return keys

    def print_current_key_mapping(self):
        layout = self._current_layout()
        try:
            width = og.executor.method.width
            height = og.executor.method.height
        except Exception:
            width = 0
            height = 0
        print("=== MIDI Instrument Key Mapping ===")
        print(
            f"instrument={self._current_instrument().value}, mode={self.current_key_mode}, "
            f"bounds={self.get_current_key_coordinates()}, "
            f"client={width}x{height}"
        )
        for key in layout.keys:
            coord = layout.client_coordinate_for_pitch(key.pitch, width, height)
            print(
                f"{self._key_label(key)} | pitch={key.pitch} | "
                f"ratio=({key.ratio_x:.4f},{key.ratio_y:.4f}) | client={coord}"
            )

    def start_key_calibration(self, mode):
        if self._disposed:
            return
        if self._calibration_thread is not None and self._calibration_thread.is_alive():
            return
        self.print_current_key_mapping()
        keys = self._current_key_sequence(mode)
        layout = self._current_layout()
        self._calibration_stop.clear()
        self._calibration_thread = threading.Thread(
            target=self._run_key_calibration,
            args=(keys, layout),
            daemon=True,
        )
        self._calibration_thread.start()

    def _run_key_calibration(self, keys, layout):
        try:
            executor = og.executor
            if executor.paused:
                og.app.start_controller.do_start()
            width = executor.method.width
            height = executor.method.height
            interaction = executor.interaction
        except Exception as e:
            print(f"MIDI calibration failed: {e}")
            return

        for key in keys:
            if self._calibration_stop.is_set():
                break
            coord = layout.client_coordinate_for_pitch(key.pitch, width, height)
            if coord is None:
                print(f"skip {self._key_label(key)} pitch={key.pitch}: no coordinate")
                continue
            x, y = coord
            print(f"calibration click {self._key_label(key)} pitch={key.pitch} x={x} y={y}")
            try:
                interaction.mouse_down(x, y)
                try:
                    self._calibration_stop.wait(0.08)
                finally:
                    interaction.mouse_up()
            except Exception as e:
                print(f"calibration click failed: {e}")
                return
            if self._calibration_stop.wait(0.08):
                break

    def _current_play_mode(self):
        index = self.cb_mode.currentIndex()
        if index == 0:
            return PlayMode.SINGLE_LOOP
        if index == 1:
            return PlayMode.SEQUENTIAL
        if index == 2:
            return PlayMode.RANDOM
        return PlayMode.SEQUENTIAL

    def _current_playback_options(self):
        track_indices = self._selected_track_indices()
        return PlaybackOptions(
            instrument=self._current_instrument(),
            layout_mode=self._current_layout_mode(),
            transpose=self.spn_pitch.value(),
            smart_remap=self.switch_smart_remap.isChecked(),
            bounds=self.get_current_key_coordinates(),
            play_mode=self._current_play_mode(),
            track_indices=track_indices,
            playlist_song_ids=tuple(self._playlist_song_ids()),
            speed=self.slider_speed.value() / 100,
            start_offset=self._current_seconds,
            on_status=self.midi_signals.playback_status.emit,
            on_progress=self.midi_signals.playback_progress.emit,
            on_song_changed=self.midi_signals.playback_song_changed.emit,
        )

    def _playlist_song_ids(self):
        ids = []
        for item in self._iter_tree_items():
            if self._is_song_tree_item(item) and not item.isHidden():
                ids.append(item.data(0, Qt.ItemDataRole.UserRole))
        if ids:
            return ids
        return [song.id for song in self.library.list_songs()]

    def _select_song_id(self, song_id):
        for item in self._iter_tree_items():
            if item is not None and item.data(0, Qt.ItemDataRole.UserRole) == song_id:
                self.song_tree_widget.setCurrentItem(item)
                return

    def _song_config_key(self, song_id=None):
        song_id = song_id or self.selected_song_id
        song = self.songs_by_id.get(song_id)
        if song is not None:
            return self._song_config_key_from_info(song)
        return song_id or ""

    def _song_config_key_from_info(self, song):
        try:
            return song.path.relative_to(self.library.library_dir).as_posix()
        except ValueError:
            return song.path.name

    def _prune_missing_song_settings(self):
        valid_keys = {self._song_config_key_from_info(song) for song in self.songs_by_id.values()}
        for config_key in ("track_selection", "song_analysis_settings"):
            current = self.config.get(config_key, {})
            if not isinstance(current, dict):
                continue
            pruned = {key: value for key, value in current.items() if key in valid_keys}
            if config_key == "song_analysis_settings":
                pruned = {
                    key: {"pitch": int(value.get("pitch", self.config.get("pitch", 0)))}
                    for key, value in pruned.items()
                    if isinstance(value, dict)
                }
            if pruned != current:
                self.config[config_key] = pruned

    def _saved_track_indices(self, song_id):
        selections = self.config.get("track_selection", {})
        raw = (
            selections.get(self._song_config_key(song_id), None)
            if isinstance(selections, dict)
            else None
        )
        if isinstance(raw, list):
            return tuple(int(index) for index in raw)
        return None

    def _song_analysis_settings(self, song_id):
        settings = self.config.get("song_analysis_settings", {})
        raw = settings.get(self._song_config_key(song_id), {}) if isinstance(settings, dict) else {}
        return raw if isinstance(raw, dict) else {}

    def _load_song_analysis_settings(self, song_id):
        settings = self._song_analysis_settings(song_id)
        old_loading = self._loading_settings
        self._loading_settings = True
        try:
            self.spn_pitch.setValue(int(settings.get("pitch", self.config.get("pitch", 0))))
        finally:
            self._loading_settings = old_loading

    def _save_song_analysis_settings(self):
        pitch_val = int(self.spn_pitch.value())
        if not self.selected_song_id:
            self.config["pitch"] = pitch_val
            return

        settings = dict(self.config.get("song_analysis_settings", {}))
        song_key = self._song_config_key()

        if pitch_val == 0:
            if song_key in settings:
                settings.pop(song_key)
                self.config["song_analysis_settings"] = settings
        else:
            settings[song_key] = {"pitch": pitch_val}
            self.config["song_analysis_settings"] = settings

    def _set_pitch_without_reanalysis(self, value, save=True):
        old_loading = self._loading_settings
        self._loading_settings = True
        self.spn_pitch.blockSignals(True)
        try:
            self.spn_pitch.setValue(int(value))
        finally:
            self.spn_pitch.blockSignals(False)
            self._loading_settings = old_loading
        if save:
            self._save_song_analysis_settings()

    def _selected_track_indices(self, song_id=None):
        if song_id is not None and song_id != self._track_ui_song_id:
            return self._saved_track_indices(song_id)
        if self.track_checkboxes:
            return tuple(
                checkbox.track.index for checkbox in self.track_checkboxes if checkbox.isChecked()
            )
        return self._saved_track_indices(song_id or self.selected_song_id)

    def populate_track_selection(self, song_id, tracks):
        if song_id == self._track_ui_song_id and self.track_checkboxes:
            return

        self._clear_track_rows()
        self._track_ui_song_id = song_id
        if tracks:
            saved = self._saved_track_indices(song_id)
            selected = (
                set(saved)
                if saved is not None
                else {track.index for track in tracks if track.note_count > 0}
            )
            index_digits = len(str(max(track.index for track in tracks) + 1))
            for row_index, track in enumerate(tracks):
                checkbox = TrackCheckBox(track, index_digits, row_index, self.track_checks_widget)
                checkbox.setChecked(track.index in selected)
                checkbox.setEnabled(track.note_count > 0)
                checkbox.stateChanged.connect(self.on_track_selection_changed)
                self.track_checks_layout.addWidget(checkbox)
                self.track_checkboxes.append(checkbox)
        else:
            self.track_checks_layout.addWidget(Label(self.tr("没有可选择的音轨"), "body"))
        self._update_track_summary()
        self._reset_track_scroll()

    def clear_track_selection(self):
        self._track_ui_song_id = None
        self._clear_track_rows()
        self.track_checks_layout.addWidget(Label(self.tr("尚未载入音轨"), "body"))
        self._reset_track_scroll()
        self._update_track_summary()

    def _clear_track_rows(self):
        self.track_checkboxes.clear()
        while self.track_checks_layout.count():
            widget = self.track_checks_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def _reset_track_scroll(self):
        self.track_checks_layout.activate()
        self.track_scroll.updateGeometry()
        self.track_scroll.verticalScrollBar().setValue(0)

    def on_track_selection_changed(self, _state):
        selected = list(self._selected_track_indices() or ())
        selections = dict(self.config.get("track_selection", {}))
        selections[self._song_config_key()] = selected
        self.config["track_selection"] = selections
        self._update_track_summary()
        self._request_analysis_for_current_song()

    def _request_analysis_for_current_song(self):
        self._analysis_generation += 1
        future = self._analysis_future
        if future is not None and not future.done():
            future.cancel()
        if not self.selected_song_id:
            self._analysis_timer.stop()
            self.pitch_chart.set_data({}, 0, 0)
            return
        self._analysis_timer.start()

    def _start_analysis_for_current_song(self):
        if not self.selected_song_id:
            self.pitch_chart.set_data({}, 0, 0)
            return

        song_id = self.selected_song_id
        transpose = self.spn_pitch.value()
        auto_pitch = self.switch_auto_pitch.isChecked()
        smart_remap = self.switch_smart_remap.isChecked()
        track_indices = self._selected_track_indices(song_id)
        min_transpose = self.spn_pitch.spin_box.minimum()
        max_transpose = self.spn_pitch.spin_box.maximum()
        generation = self._analysis_generation

        layout = self._current_layout()
        try:
            future = submit_midi_analysis(
                self.library,
                song_id,
                layout,
                transpose,
                track_indices,
                smart_remap,
                auto_pitch=auto_pitch,
                min_transpose=min_transpose,
                max_transpose=max_transpose,
            )
        except Exception as e:
            if generation == self._analysis_generation:
                self.midi_signals.analysis_failed.emit(song_id, str(e))
            return

        self._analysis_future = future

        def on_done(completed):
            if completed.cancelled():
                return
            try:
                analysis = completed.result()
                store_prepared_analysis(
                    self.library,
                    analysis,
                    track_indices,
                    smart_remap,
                    transpose,
                )
                if generation != self._analysis_generation:
                    return
                prepared = analysis.prepared
                pitches = prepared.mapped_pitches
                notes = prepared.notes
                counts = Counter(pitches)
                playable_pitches = {pitch for pitch in pitches if pitch in layout.playable_pitches}
                unplayable_pitches = {
                    pitch for pitch in pitches if pitch not in layout.playable_pitches
                }
                stats = SongStats(
                    total_notes=len(notes),
                    playable_notes=sum(
                        count for pitch, count in counts.items() if pitch in layout.playable_pitches
                    ),
                    unplayable_notes=sum(
                        count
                        for pitch, count in counts.items()
                        if pitch not in layout.playable_pitches
                    ),
                    playable_pitches=tuple(sorted(playable_pitches)),
                    unplayable_pitches=tuple(sorted(unplayable_pitches)),
                )
                self.midi_signals.analysis_done.emit(
                    song_id,
                    stats,
                    dict(counts),
                    prepared.source_duration,
                    prepared.parsed_song.tracks,
                    analysis.applied_transpose,
                )
            except Exception as e:
                if generation == self._analysis_generation:
                    self.midi_signals.analysis_failed.emit(song_id, str(e))

        future.add_done_callback(on_done)

    def on_song_analysis_done(
        self, song_id, stats, note_counts, duration, tracks, applied_transpose
    ):
        if self._disposed or song_id != self.selected_song_id:
            return
        if applied_transpose is not None and applied_transpose != self.spn_pitch.value():
            self._set_pitch_without_reanalysis(applied_transpose, save=False)
        self.populate_track_selection(song_id, tracks)
        playable = list(stats.playable_pitches)
        if playable:
            min_playable = min(playable)
            max_playable = max(playable)
        else:
            layout = self._current_layout()
            pitches = layout.playable_pitches
            min_playable = min(pitches)
            max_playable = max(pitches)
        layout = self._current_layout()
        self.pitch_chart.set_data(note_counts, min_playable, max_playable, layout.playable_pitches)
        if not self.is_playing or song_id == self.playing_song_id:
            effective_duration = duration / max(0.1, self.slider_speed.value() / 100)
            self._duration_seconds = effective_duration
            self.lbl_time_total.setText(self._format_seconds(effective_duration))

    def on_song_analysis_failed(self, song_id, message):
        if self._disposed:
            return
        if song_id != self.selected_song_id:
            return
        print(f"MIDI analysis failed for {song_id}: {message}")
        self.pitch_chart.set_data({}, 0, 0)
        self.clear_track_selection()

        show_toast(self.window(), message, self.tr("MIDI 分析失败"), tone="danger", duration=3500)

    def _format_seconds(self, seconds):
        total = max(0, int(seconds))
        return f"{total // 60:02d}:{total % 60:02d}"

    # --- Interfaces / Slots for Core Agent ---

    def on_import_midi(self):
        """Allow users to select multiple midi files and copy to mid_lib"""
        files, _ = QFileDialog.getOpenFileNames(
            self, self.tr("选择 MIDI 档案"), "", "MIDI Files (*.mid *.midi)"
        )
        if files:
            try:
                imported = self.library.import_files(files)
                # 避免完整重新扫描资料夹造成卡顿，直接更新内存中的歌曲列表
                for song in imported:
                    self.songs_by_id[song.id] = song
                self.populate_song_list(None)
                print(f"Imported {len(imported)} files to mid_lib.")
            except Exception as e:
                print(f"Error importing MIDI files: {e}")

    def on_open_midi(self):
        try:
            mid_lib = self.library.library_dir
            mid_lib.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(mid_lib.resolve())))
        except Exception as e:
            print(f"Error opening mid_lib: {e}")

    def on_fav_segment_changed(self, key):
        """Toggle showing 'all' songs vs 'fav' (favorite) songs."""
        self.fav_filter = key
        self.populate_song_list(self.selected_song_id)

    def on_favorite_toggled(self):
        """Toggle the favorite state for the currently player's song."""
        target_id = self.playing_song_id or self.selected_song_id
        if not target_id:
            return
        self.is_favorite = not self.is_favorite
        try:
            updated = self.library.set_favorite(target_id, self.is_favorite)
            self.songs_by_id[target_id] = updated
        except Exception as e:
            print(f"Failed to update favorite: {e}")
            self.is_favorite = not self.is_favorite
            return

        self._update_favorite_button()
        self.populate_song_list(self.selected_song_id)

    def on_song_selected(self, current, previous):
        """Slot to handle song selection from the list"""
        if current:
            song_id = current.data(0, Qt.ItemDataRole.UserRole)
            if not song_id:
                return
            self.selected_song_id = song_id
            song = self.songs_by_id.get(song_id)
            display_name = song.title if song else current.text(0)
            self.selection_title.setText(display_name)
            if self.playing_song_id is None:
                self._show_player_song(display_name, favorite=bool(song and song.favorite))
                self.lbl_time_current.setText("00:00")
                self.lbl_time_total.setText("--:--")
                self._current_seconds = 0.0
                self._duration_seconds = 0.0
                self._set_progress_slider_value(0)
            self._load_song_analysis_settings(song_id)
            self.btn_favorite.show()
            self.btn_play_selected.setDisabled(False)
            self.pitch_chart.set_data({}, 0, 0)
            self.clear_track_selection()
            self._request_analysis_for_current_song()
        else:
            self.selected_song_id = None
            self.selection_title.setText(self.tr_no_song_selected)
            if self.playing_song_id is None:
                self._show_player_song(self.tr_no_song_selected)
            self.btn_favorite.hide()
            self.btn_play_selected.setDisabled(True)
            # Clear chart data if no song is selected
            self.pitch_chart.set_data({}, 0, 0)
            self.clear_track_selection()

    def on_play_pause_clicked(self):
        if self.is_playing:
            self.stop_playback()
        else:
            reset_position = self.playing_song_id is None
            song_id = self.playing_song_id or self.selected_song_id
            if song_id:
                self._restart_playback_when_ready(song_id, reset_position=reset_position)

    def on_play_selected_clicked(self):
        self.play_selected_song()

    def play_selected_song(self):
        if not self.selected_song_id:
            return

        song_id = self.selected_song_id
        self._current_seconds = 0.0
        if self._playback_thread is not None and self._playback_thread.is_alive():
            self.stop_playback()
            self._restart_playback_when_ready(song_id, reset_position=True)
            return
        self.start_playback(song_id, reset_position=True)

    def start_playback(self, song_id=None, reset_position=False):
        if self._disposed:
            return
        song_id = song_id or self.playing_song_id
        if not song_id:
            return
        if self._playback_thread is not None and self._playback_thread.is_alive():
            return
        self.playing_song_id = song_id
        if reset_position:
            self._current_seconds = 0.0
            self._set_progress_slider_value(0)
            self.lbl_time_current.setText("00:00")
        if self._duration_seconds > 0 and self._current_seconds >= self._duration_seconds - 0.25:
            self._current_seconds = 0.0

        options = self._current_playback_options()
        scheduled_stop_delay = (
            self._scheduled_stop_delay() if self.switch_schedule.isChecked() else None
        )
        self.is_playing = True
        song = self.songs_by_id.get(song_id)
        self._show_player_song(
            song.title if song else self.tr("播放中"),
            favorite=bool(song.favorite) if song else None,
        )
        self._update_play_button_icon()

        def worker():
            if self._disposed:
                return
            loop = asyncio.new_event_loop()
            controller = MidiPlaybackController(self.library, og_provider=lambda: og)
            self._play_loop = loop
            self._play_controller = controller
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    self._run_playback_with_scheduled_stop(
                        controller,
                        song_id,
                        options,
                        scheduled_stop_delay,
                    )
                )
            except Exception as e:
                self.midi_signals.playback_status.emit(f"error:{e}")
            finally:
                self.midi_signals.playback_status.emit("idle")
                self._play_controller = None
                self._play_loop = None
                loop.close()

        self._playback_thread = threading.Thread(target=worker, daemon=True)
        self._playback_thread.start()

    async def _run_playback_with_scheduled_stop(
        self,
        controller,
        song_id,
        options,
        scheduled_stop_delay,
    ):
        if self._disposed:
            return
        stop_task = None
        if scheduled_stop_delay is not None:

            async def stop_after_delay():
                if scheduled_stop_delay > 0:
                    await asyncio.sleep(scheduled_stop_delay)
                await controller.stop()

            stop_task = asyncio.create_task(stop_after_delay())
        try:
            await controller.play(song_id, options)
        finally:
            if stop_task is not None and not stop_task.done():
                await _cancel_task_and_wait(stop_task)

    def stop_playback(self):
        controller = self._play_controller
        loop = self._play_loop
        if controller is not None and loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(controller.stop(), loop)
        self.is_playing = False
        self._update_play_button_icon()

    def _restart_playback_when_ready(self, song_id, reset_position=False):
        if self._disposed:
            return
        if self._playback_thread is not None and self._playback_thread.is_alive():
            QTimer.singleShot(
                80, lambda: self._restart_playback_when_ready(song_id, reset_position)
            )
            return
        self.start_playback(song_id, reset_position=reset_position)

    def on_playback_song_changed(self, song_id):
        if self._disposed:
            return
        is_new_song = song_id != self.playing_song_id
        self.playing_song_id = song_id
        song = self.songs_by_id.get(song_id)
        if song is not None:
            self._show_player_song(song.title, favorite=bool(song.favorite))
        if is_new_song:
            self._duration_seconds = 0.0
            self._current_seconds = 0.0
            self._set_progress_slider_value(0)

    def on_playback_status_changed(self, status):
        if self._disposed:
            return
        if status == "playing":
            self.is_playing = True
        elif status == "idle":
            self.is_playing = False
        elif status.startswith("error:"):
            print(f"MIDI playback failed: {status[6:]}")

            show_toast(
                self.window(), status[6:], self.tr("MIDI 播放失败"), tone="danger", duration=3500
            )
        self._update_play_button_icon()

    def on_playback_progress(self, current, total):
        if self._disposed:
            return
        self._current_seconds = current
        if total > 0:
            self._duration_seconds = total
        if self._is_slider_dragging:
            return
        self.update_progress(int(current * 1000), int(total * 1000))

    def _update_play_button_icon(self):
        self.cb_instrument.setEnabled(not self.is_playing)
        if self.is_playing:
            self.btn_play_pause.set_icon(self._pause_icon)
        else:
            self.btn_play_pause.set_icon(self._play_icon)
        # Kit icon-only buttons reset to a square when their icon changes.
        self.btn_play_pause.setFixedSize(self.PLAYBACK_BUTTON_SIZE)

    def _show_player_song(self, title: str, *, favorite: bool | None = None) -> None:
        self.lbl_track_name.setText(title)
        if favorite is not None:
            self.is_favorite = favorite
            self._update_favorite_button()

    def _update_favorite_button(self):
        if self.is_favorite:
            self.btn_favorite.setIcon(self.favorite_active_icon)
        else:
            self.btn_favorite.setIcon(icon("heart"))

    def on_prev_clicked(self):
        ids = self._playlist_song_ids()
        if not ids:
            return
        self.stop_playback()
        self._current_seconds = 0.0
        base_id = self.playing_song_id or self.selected_song_id
        index = ids.index(base_id) if base_id in ids else 0
        song_id = ids[(index - 1) % len(ids)]
        self._select_song_id(song_id)
        self._restart_playback_when_ready(song_id, reset_position=True)

    def on_next_clicked(self):
        ids = self._playlist_song_ids()
        if not ids:
            return
        self.stop_playback()
        self._current_seconds = 0.0
        base_id = self.playing_song_id or self.selected_song_id
        song_id = self._next_button_song_id(ids, base_id)
        self._select_song_id(song_id)
        self._restart_playback_when_ready(song_id, reset_position=True)

    def _next_button_song_id(self, ids, base_id):
        if self._current_play_mode() == PlayMode.RANDOM:
            candidates = [song_id for song_id in ids if song_id != base_id]
            if not candidates:
                candidates = list(ids)
            return random.choice(candidates)

        index = ids.index(base_id) if base_id in ids else -1
        return ids[(index + 1) % len(ids)]

    def on_progress_changed(self, value):
        if not self._is_slider_dragging or self._duration_seconds <= 0:
            return
        self._current_seconds = self._duration_seconds * value / 10000
        self.lbl_time_current.setText(self._format_seconds(self._current_seconds))

    def on_progress_slider_pressed(self):
        self._is_slider_dragging = True

    def on_progress_slider_released(self):
        if self._duration_seconds <= 0:
            self._is_slider_dragging = False
            return
        self._current_seconds = self._duration_seconds * self.slider_progress.value() / 10000
        self._is_slider_dragging = False
        was_playing = self.is_playing
        if was_playing:
            self.stop_playback()
            self._restart_playback_when_ready(self.playing_song_id)
        else:
            self.update_progress(
                int(self._current_seconds * 1000),
                int(self._duration_seconds * 1000),
            )

    def on_progress_slider_clicked(self, value):
        if self._duration_seconds <= 0:
            return
        self._set_progress_slider_value(value)
        self._current_seconds = self._duration_seconds * value / 10000
        was_playing = self.is_playing
        if was_playing:
            self.stop_playback()
            self._restart_playback_when_ready(self.playing_song_id)
        else:
            self.update_progress(
                int(self._current_seconds * 1000),
                int(self._duration_seconds * 1000),
            )

    def update_progress(self, current_time_ms, total_time_ms):
        if total_time_ms > 0:
            percentage = int((current_time_ms / total_time_ms) * 10000)
            self._set_progress_slider_value(percentage)
        self.lbl_time_current.setText(self._format_seconds(current_time_ms / 1000))
        self.lbl_time_total.setText(self._format_seconds(total_time_ms / 1000))

    def _set_progress_slider_value(self, value):
        self.slider_progress.blockSignals(True)
        self.slider_progress.setValue(value)
        self.slider_progress.setSliderPosition(value)
        self.slider_progress.update()
        self.slider_progress.blockSignals(False)

    def on_pitch_changed(self, value):
        if not self._loading_settings:
            self._save_song_analysis_settings()
        layout = self._current_layout()
        pitches = layout.playable_pitches
        base_min = min(pitches)
        base_max = max(pitches)

        self.pitch_chart.min_playable = base_min
        self.pitch_chart.max_playable = base_max
        self.pitch_chart.playable_pitches = set(pitches)
        self.pitch_chart.update()
        self._request_analysis_for_current_song()

    def on_smart_remap_changed(self, is_checked):
        if not self._loading_settings:
            self.config["smart_remap"] = bool(is_checked)
        self._request_analysis_for_current_song()

    def on_auto_pitch_changed(self, is_checked):
        self.spn_pitch.setEnabled(not is_checked)
        if not self._loading_settings:
            self.config["auto_pitch"] = bool(is_checked)
            if not is_checked and self.selected_song_id:
                settings = self._song_analysis_settings(self.selected_song_id)
                self._set_pitch_without_reanalysis(
                    int(settings.get("pitch", self.config.get("pitch", 0))), save=False
                )
        self._request_analysis_for_current_song()

    def on_mode_changed(self, index):
        if not self._loading_settings:
            self.config["play_mode"] = int(index)
        self._sync_playback_mode_to_controller()

    def _sync_playback_mode_to_controller(self):
        controller = self._play_controller
        loop = self._play_loop
        if controller is None or loop is None or not loop.is_running():
            return
        mode = self._current_play_mode()
        loop.call_soon_threadsafe(controller.set_mode, mode)

    def _sync_playlist_to_controller(self):
        controller = self._play_controller
        loop = self._play_loop
        if controller is None or loop is None or not loop.is_running():
            return
        song_ids = tuple(self._playlist_song_ids())
        loop.call_soon_threadsafe(controller.set_playlist_song_ids, song_ids)

    def on_schedule_toggled(self, is_checked):
        self.time_picker.setEnabled(is_checked)
        if not self._loading_settings:
            self.config["schedule_enabled"] = bool(is_checked)

    def on_schedule_time_changed(self, time):
        if not self._loading_settings:
            self.config["schedule_time"] = time.toString("HH:mm")

    def _scheduled_stop_delay(self):
        selected = self.time_picker.time()
        seconds = selected.hour() * 3600 + selected.minute() * 60 + selected.second()
        if seconds <= 0:
            return None
        return seconds

    def on_key_mode_changed(self, key):
        if key == "21_keys":
            QTimer.singleShot(0, lambda: self.segmented_widget.set_current("36_keys"))
            return
        self.current_key_mode = key
        if key == "36_keys":
            self.stacked_widget.setCurrentWidget(self.config_36)

        self.on_pitch_changed(self.spn_pitch.value())

    def get_current_key_coordinates(self):
        current_widget = self.stacked_widget.currentWidget()
        if isinstance(current_widget, KeyConfigWidget):
            return current_widget.get_coords()
        return (0, 0, 0, 0)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded and not self._disposed:
            self._loaded = True
            self.refresh_song_list()

    def eventFilter(self, watched, event):
        if watched is getattr(self, "track_page", None) and event.type() == QEvent.Type.Resize:
            self._update_track_layout()
        sliders = (getattr(self, "slider_progress", None), getattr(self, "slider_speed", None))
        if (
            watched in sliders
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            option = QStyleOptionSlider()
            watched.initStyleOption(option)
            handle = watched.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, watched
            )
            if not handle.contains(event.position().toPoint()):
                value = QStyle.sliderValueFromPosition(
                    watched.minimum(),
                    watched.maximum(),
                    int(event.position().x() - handle.width() / 2),
                    max(1, watched.width() - handle.width()),
                    option.upsideDown,
                )
                watched.setValue(value)
                if watched is self.slider_progress:
                    self.on_progress_slider_clicked(value)
                else:
                    self.on_speed_slider_clicked(value)
                return True
        return super().eventFilter(watched, event)

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._calibration_stop.set()
        self._analysis_generation += 1
        self._analysis_timer.stop()
        if self._analysis_future is not None:
            self._analysis_future.cancel()
        self.stop_playback()
        for name in (
            "analysis_done",
            "analysis_failed",
            "playback_status",
            "playback_progress",
            "playback_song_changed",
            "library_indexed",
        ):
            getattr(self.midi_signals, name).disconnect()
