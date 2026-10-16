import asyncio
import random
import threading
import time
from collections import Counter

from ok import og
from ok.ui.qt.widget.CustomTab import CustomTab
from ok.util.config import Config
from PySide6.QtCore import QObject, Qt, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QSizePolicy,
    QStackedWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    ExpandLayout,
    FluentIcon,
    IndeterminateProgressRing,
    PrimaryToolButton,
    PushButton,
    SearchLineEdit,
    SegmentedWidget,
    SimpleCardWidget,
    Slider,
    SmoothScrollArea,
    SpinBox,
    SwitchButton,
    TimePicker,
    TitleLabel,
    ToolButton,
    ToolTipFilter,
    TransparentToolButton,
    TreeWidget,
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
from src.ui.features.midi.widgets import (
    CollapsibleSection,
    KeyConfigWidget,
    MarqueeBodyLabel,
    MarqueeSubtitleLabel,
    PitchChartWidget,
)
from src.ui.foundation.icons import FluentSystemIcon

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
    "collapsed_sections": {},
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
    def __init__(self):
        super().__init__()
        self.icon = FluentIcon.MUSIC
        self.tr_name_tab = self.tr("自动乐器")
        self.tr_no_song_selected = self.tr("未选择歌曲")
        self.is_playing = False
        self.is_favorite = False
        self.favorite_active_icon = FluentSystemIcon.HEART_FILL.colored("#E81123", "#FF99A4")
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
        self._loading_tracks = False
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
        self.main_h_layout = QHBoxLayout(self)
        self.main_h_layout.setContentsMargins(0, 0, 0, 0)
        self.main_h_layout.setSpacing(0)

        self.setup_left_panel()
        self.setup_right_panel()

        self.main_h_layout.addWidget(self.left_widget, 1)
        self.main_h_layout.addWidget(self.right_widget, 3)

        self.fav_segment.setCurrentItem("all")
        self.segmented_widget.setCurrentItem("36_keys")
        self._load_saved_settings()

        # Loading overlay
        self.progress_ring = IndeterminateProgressRing()
        self.progress_ring.setFixedSize(50, 50)
        self.progress_container = QWidget()
        progress_layout = QVBoxLayout(self.progress_container)
        progress_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_layout.addWidget(self.progress_ring, alignment=Qt.AlignmentFlag.AlignCenter)
        self.progress_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.left_v_layout.insertWidget(2, self.progress_container)
        self.progress_container.setVisible(False)

    @property
    def name(self):  # type: ignore
        return self.tr_name_tab

    def setup_left_panel(self):
        self.left_widget = QWidget()
        self.left_v_layout = QVBoxLayout(self.left_widget)
        self.left_v_layout.setContentsMargins(10, 10, 10, 10)
        self.left_v_layout.setSpacing(10)

        # Header with segment
        self.fav_segment = SegmentedWidget()
        self.fav_segment.addItem("all", self.tr("全部歌曲"))
        self.fav_segment.addItem("fav", self.tr("已收藏"))
        self.fav_segment.currentItemChanged.connect(self.on_fav_segment_changed)
        self.left_v_layout.addWidget(self.fav_segment)

        hbox_search = QHBoxLayout()
        hbox_search.setContentsMargins(0, 0, 0, 0)
        hbox_search.setSpacing(5)
        self.song_search_edit = SearchLineEdit(self)
        self.song_search_edit.setPlaceholderText(self.tr("搜索歌曲"))
        self.song_search_edit.setClearButtonEnabled(True)
        self.song_search_edit.textChanged.connect(self._apply_song_tree_filter)
        hbox_search.addWidget(self.song_search_edit)

        self.btn_import_midi = TransparentToolButton(FluentIcon.FOLDER_ADD)
        self.btn_import_midi.setToolTip(self.tr("导入 MIDI"))
        self.btn_import_midi.installEventFilter(ToolTipFilter(self.btn_import_midi, showDelay=300))
        self.btn_import_midi.clicked.connect(self.on_import_midi)
        hbox_search.addWidget(self.btn_import_midi)

        self.btn_open_midi = TransparentToolButton(FluentIcon.FOLDER)
        self.btn_open_midi.setToolTip(self.tr("打开 mid_lib"))
        self.btn_open_midi.installEventFilter(ToolTipFilter(self.btn_open_midi, showDelay=300))
        self.btn_open_midi.clicked.connect(self.on_open_midi)
        hbox_search.addWidget(self.btn_open_midi)

        self.left_v_layout.addLayout(hbox_search)

        self.song_tree_widget = TreeWidget(self)
        self.song_tree_widget.setHeaderHidden(True)
        self.song_tree_widget.setRootIsDecorated(True)
        self.song_tree_widget.setIndentation(16)
        self.song_tree_widget.currentItemChanged.connect(self.on_song_selected)
        self.left_v_layout.addWidget(self.song_tree_widget)

    def setup_right_panel(self):
        self.right_widget = QWidget()
        self.right_v_layout = QVBoxLayout(self.right_widget)
        self.right_v_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = SmoothScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.enableTransparentBackground()
        self.scroll_area.setStyleSheet(
            "SmoothScrollArea { border: none; background: transparent; }"
        )
        self.scroll_area.setViewportMargins(0, 20, 0, 20)

        self.container = QWidget()
        self.container.setObjectName("MidiPlayerContainer")
        self.container.setStyleSheet("#MidiPlayerContainer { background: transparent; }")
        self.scroll_area.setWidget(self.container)

        self.right_v_layout.addWidget(self.scroll_area)

        self.vbox = ExpandLayout(self.container)
        self.vbox.setContentsMargins(10, 10, 30, 0)
        self.vbox.setSpacing(20)

        self.title_label = TitleLabel(self.tr("自动乐器"), self.container)
        self.title_label.adjustSize()
        self.vbox.addWidget(self.title_label)

        self.setup_player_controls()
        self.setup_selection_info()
        self.setup_playback_settings()
        self.setup_track_selection()
        self.setup_pitch_adjustment()
        self.setup_key_config()
        self.setup_calibration_tools()

    def setup_player_controls(self):
        self.player_card = SimpleCardWidget(self.container)
        vbox_player = QVBoxLayout(self.player_card)
        vbox_player.setContentsMargins(20, 20, 20, 20)
        vbox_player.setSpacing(15)

        # Track Info & Favorite
        hbox_top = QHBoxLayout()
        self.lbl_track_name = MarqueeSubtitleLabel(self.tr_no_song_selected)
        self.lbl_track_name.installEventFilter(ToolTipFilter(self.lbl_track_name, showDelay=300))

        self.btn_favorite = TransparentToolButton(FluentIcon.HEART)
        self.btn_favorite.setToolTip(self.tr("收藏歌曲"))
        self.btn_favorite.installEventFilter(ToolTipFilter(self.btn_favorite, showDelay=300))
        self.btn_favorite.clicked.connect(self.on_favorite_toggled)
        self.btn_favorite.hide()

        hbox_top.addWidget(self.lbl_track_name, 1)
        hbox_top.addWidget(self.btn_favorite)
        vbox_player.addLayout(hbox_top)

        # Progress Slider
        self.slider_progress = Slider(Qt.Orientation.Horizontal)
        self.slider_progress.setRange(0, 10000)
        self.slider_progress.setValue(0)
        self.slider_progress.valueChanged.connect(self.on_progress_changed)
        self.slider_progress.sliderPressed.connect(self.on_progress_slider_pressed)
        self.slider_progress.sliderReleased.connect(self.on_progress_slider_released)
        self.slider_progress.clicked.connect(self.on_progress_slider_clicked)
        vbox_player.addWidget(self.slider_progress)

        # Time Labels
        hbox_time = QHBoxLayout()
        self.lbl_time_current = BodyLabel("00:00")
        self.lbl_time_total = BodyLabel("00:00")
        hbox_time.addWidget(self.lbl_time_current)
        hbox_time.addStretch()
        hbox_time.addWidget(self.lbl_time_total)
        vbox_player.addLayout(hbox_time)

        # Playback Buttons
        hbox_controls = QHBoxLayout()

        self.btn_prev = ToolButton(FluentSystemIcon.PREVIOUS)
        self.btn_play_pause = PrimaryToolButton(FluentIcon.PLAY)
        self.btn_next = ToolButton(FluentSystemIcon.NEXT)

        self.btn_prev.clicked.connect(self.on_prev_clicked)
        self.btn_play_pause.clicked.connect(self.on_play_pause_clicked)
        self.btn_next.clicked.connect(self.on_next_clicked)

        hbox_controls.addStretch()
        hbox_controls.addWidget(self.btn_prev)
        hbox_controls.addSpacing(10)
        hbox_controls.addWidget(self.btn_play_pause)
        hbox_controls.addSpacing(10)
        hbox_controls.addWidget(self.btn_next)
        hbox_controls.addStretch()

        vbox_player.addLayout(hbox_controls)
        self.player_card.setFixedHeight(self.player_card.layout().sizeHint().height())
        self.vbox.addWidget(self.player_card)

    def setup_selection_info(self):
        from qfluentwidgets import PushButton, PushSettingCard

        self.selection_card = PushSettingCard(
            self.tr("播放"), FluentIcon.MUSIC, self.tr_no_song_selected, None, self.container
        )

        self.selection_card.button.deleteLater()
        self.btn_play_selected = PushButton(self.tr("播放"), self.selection_card, FluentIcon.PLAY)
        self.selection_card.hBoxLayout.insertWidget(
            self.selection_card.hBoxLayout.count() - 1, self.btn_play_selected, 0, Qt.AlignRight
        )

        self.btn_play_selected.clicked.connect(self.on_play_selected_clicked)
        self.btn_play_selected.setDisabled(True)

        # Swap the title label with a MarqueeBodyLabel so long songs still scroll
        marquee = MarqueeBodyLabel(self.tr_no_song_selected)
        marquee.installEventFilter(ToolTipFilter(marquee, showDelay=300))
        self.selection_card.vBoxLayout.replaceWidget(self.selection_card.titleLabel, marquee)
        self.selection_card.titleLabel.deleteLater()
        self.selection_card.titleLabel = marquee
        self.selection_card.contentLabel.hide()

        self.vbox.addWidget(self.selection_card)

    def setup_playback_settings(self):
        self.settings_card = QWidget(self.container)
        vbox = QVBoxLayout(self.settings_card)
        vbox.setContentsMargins(20, 20, 20, 20)
        vbox.setSpacing(16)

        # Playback Mode
        hbox_mode = QHBoxLayout()
        lbl_mode = BodyLabel(self.tr("播放模式"))
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
        lbl_speed = BodyLabel(self.tr("播放速度"))
        self.lbl_speed_value = BodyLabel("1.00x")
        self.lbl_speed_value.setFixedWidth(50)
        self.lbl_speed_value.setAlignment(Qt.AlignCenter)
        self.slider_speed = Slider(Qt.Orientation.Horizontal)
        self.slider_speed.setRange(25, 200)
        self.slider_speed.setValue(100)
        self.slider_speed.setFixedWidth(200)
        self.slider_speed.valueChanged.connect(self.on_speed_value_changed)
        self.slider_speed.sliderReleased.connect(self.on_speed_slider_released)
        self.slider_speed.clicked.connect(self.on_speed_slider_clicked)
        hbox_speed.addWidget(lbl_speed)
        hbox_speed.addStretch()
        hbox_speed.addWidget(self.lbl_speed_value)
        hbox_speed.addWidget(self.slider_speed)
        vbox.addLayout(hbox_speed)

        # Scheduled Stop
        hbox_schedule = QHBoxLayout()
        lbl_schedule = BodyLabel(self.tr("定时关闭"))
        self.time_picker = TimePicker()
        self.time_picker.setTime(QTime(0, 30))
        self.time_picker.setEnabled(False)
        self.time_picker.timeChanged.connect(self.on_schedule_time_changed)
        self.switch_schedule = SwitchButton()
        self.switch_schedule.checkedChanged.connect(self.on_schedule_toggled)
        hbox_schedule.addWidget(lbl_schedule)
        hbox_schedule.addStretch()
        hbox_schedule.addWidget(self.time_picker)
        hbox_schedule.addSpacing(10)
        hbox_schedule.addWidget(self.switch_schedule)
        vbox.addLayout(hbox_schedule)

        self.settings_section = self._add_collapsible_section(
            "playback_settings", self.tr("播放设置"), self.settings_card
        )

    def setup_track_selection(self):
        self.track_section = self._add_collapsible_section(
            "track_selection", self.tr("音轨选择"), None
        )

        self.btn_play_tracks = PushButton(
            self.tr("试听选中音轨"), self.track_section, FluentIcon.PLAY
        )
        self.btn_play_tracks.clicked.connect(self.on_play_selected_clicked)
        self.btn_play_tracks.setEnabled(False)
        self.track_section.addWidget(self.btn_play_tracks)

        self.track_checks_widget = QWidget()
        self.track_checks_layout = QVBoxLayout(self.track_checks_widget)
        self.track_checks_layout.setContentsMargins(20, 20, 20, 20)
        self.track_checks_layout.setSpacing(8)
        self.track_empty_label = BodyLabel(self.tr("尚未载入音轨"))
        self.track_checks_layout.addWidget(self.track_empty_label)

        self.track_section.addGroupWidget(self.track_checks_widget)

    def setup_pitch_adjustment(self):
        self.pitch_card = QWidget(self.container)
        vbox_pitch = QVBoxLayout(self.pitch_card)
        vbox_pitch.setContentsMargins(20, 20, 20, 20)
        vbox_pitch.setSpacing(20)

        self.lbl_pitch = BodyLabel(self.tr("音高调整 (半音):"))
        self.spn_pitch = SpinBox()
        self.spn_pitch.wheelEvent = lambda event: event.ignore()
        self.spn_pitch.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.spn_pitch.setRange(-24, 24)
        self.spn_pitch.setValue(0)
        self.spn_pitch.valueChanged.connect(self.on_pitch_changed)
        self.lbl_auto_pitch = BodyLabel(self.tr("自动音高"))
        self.switch_auto_pitch = SwitchButton()
        self.switch_auto_pitch.setChecked(False)
        self.switch_auto_pitch.checkedChanged.connect(self.on_auto_pitch_changed)
        self.lbl_smart_remap = BodyLabel(self.tr("智能音域压缩"))
        self.switch_smart_remap = SwitchButton()
        self.switch_smart_remap.setChecked(True)
        self.switch_smart_remap.checkedChanged.connect(self.on_smart_remap_changed)

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

        self._add_collapsible_section("pitch_analysis", self.tr("音高与音域分析"), self.pitch_card)

    def setup_key_config(self):
        self.key_card = QWidget(self.container)
        vbox_key = QVBoxLayout(self.key_card)
        vbox_key.setContentsMargins(20, 20, 20, 20)
        vbox_key.setSpacing(15)

        hbox_instrument = QHBoxLayout()
        hbox_instrument.addWidget(BodyLabel(self.tr("乐器"), self.key_card))
        hbox_instrument.addStretch()
        self.cb_instrument = ComboBox(self.key_card)
        self.cb_instrument.addItem(self.tr("钢琴"), userData=Instrument.PIANO.value)
        self.cb_instrument.addItem(self.tr("吉他"), userData=Instrument.GUITAR.value)
        self.cb_instrument.setMinimumWidth(150)
        self.cb_instrument.setToolTip(self.tr("选择与游戏当前演奏界面一致的乐器"))
        self.cb_instrument.installEventFilter(ToolTipFilter(self.cb_instrument, showDelay=300))
        self.cb_instrument.currentIndexChanged.connect(self.on_instrument_changed)
        hbox_instrument.addWidget(self.cb_instrument)
        vbox_key.addLayout(hbox_instrument)

        hbox_mode = QHBoxLayout()
        lbl_mode = BodyLabel(self.tr("键盘布局"))

        # Mode Selection
        self.segmented_widget = SegmentedWidget()
        self.segmented_widget.addItem("36_keys", self.tr("36键半音布局 (12x3)"))
        self.segmented_widget.addItem("21_keys", self.tr("21键自然音布局 (已禁用)"))
        self.segmented_widget.items["21_keys"].setEnabled(False)
        self.segmented_widget.items["21_keys"].setToolTip(self.tr("当前乐器使用 36 键半音布局"))
        self.segmented_widget.items["21_keys"].installEventFilter(
            ToolTipFilter(self.segmented_widget.items["21_keys"], showDelay=300)
        )

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
        self._add_collapsible_section("key_config", self.tr("乐器与键位设置"), self.key_card)

        self.segmented_widget.currentItemChanged.connect(self.on_key_mode_changed)
        self._connect_bounds_savers(self.config_36)

    def setup_calibration_tools(self):
        self.calibration_card = QWidget()
        vbox_calibration = QVBoxLayout(self.calibration_card)
        vbox_calibration.setContentsMargins(48, 18, 44, 18)
        vbox_calibration.setSpacing(12)

        hbox = QHBoxLayout()
        self.btn_print_mapping = PushButton(self.tr("打印键位映射"))
        self.btn_test_middle_row = PushButton(self.tr("测试中音行"))
        self.btn_test_all_keys = PushButton(self.tr("测试全部键"))

        self.btn_print_mapping.clicked.connect(self.print_current_key_mapping)
        self.btn_test_middle_row.clicked.connect(lambda: self.start_key_calibration("middle"))
        self.btn_test_all_keys.clicked.connect(lambda: self.start_key_calibration("all"))

        hbox.addWidget(self.btn_print_mapping)
        hbox.addWidget(self.btn_test_middle_row)
        hbox.addWidget(self.btn_test_all_keys)
        hbox.addStretch()
        vbox_calibration.addLayout(hbox)

        self.calibration_hint = BodyLabel(
            self.tr("每个测试音间隔约 1 秒，请用调音器确认实际发音。")
        )
        # Removed setWordWrap(True) because QWidget's sizeHint() does not account for heightForWidth
        # which breaks ExpandGroupSettingCard's _adjustViewSize() height calculation.
        vbox_calibration.addWidget(self.calibration_hint)

        self._add_collapsible_section(
            "calibration_tools", self.tr("调试校准"), self.calibration_card
        )

    # --- Data Demos & Logic ---

    def _add_collapsible_section(self, section_key, title, content):
        collapsed_sections = dict(self.config.get("collapsed_sections", {}))
        if section_key not in collapsed_sections:
            collapsed_sections[section_key] = False
            self.config["collapsed_sections"] = collapsed_sections
        collapsed = bool(collapsed_sections.get(section_key, False))
        section = CollapsibleSection(
            section_key, title, content, collapsed=collapsed, parent=self.container
        )
        section.toggled.connect(self.on_section_toggled)
        self.vbox.addWidget(section)
        return section

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

    def on_section_toggled(self, section_key, collapsed):
        collapsed_sections = dict(self.config.get("collapsed_sections", {}))
        collapsed_sections[section_key] = bool(collapsed)
        self.config["collapsed_sections"] = collapsed_sections

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
        adjust_handle = getattr(self.slider_speed, "_adjustHandlePos", None)
        if callable(adjust_handle):
            adjust_handle()
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

        self.progress_container.setVisible(True)
        self.progress_ring.start()

        threading.Thread(target=_task, daemon=True).start()

    def _on_library_indexed(self, selected_id):
        self.progress_ring.stop()
        self.progress_container.setVisible(False)
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
            if song.favorite:
                item.setIcon(0, self.favorite_active_icon.icon())
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
        if self._calibration_thread is not None and self._calibration_thread.is_alive():
            return
        self.print_current_key_mapping()
        keys = self._current_key_sequence(mode)
        self._calibration_thread = threading.Thread(
            target=self._run_key_calibration,
            args=(keys,),
            daemon=True,
        )
        self._calibration_thread.start()

    def _run_key_calibration(self, keys):
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

        layout = self._current_layout()
        for key in keys:
            coord = layout.client_coordinate_for_pitch(key.pitch, width, height)
            if coord is None:
                print(f"skip {self._key_label(key)} pitch={key.pitch}: no coordinate")
                continue
            x, y = coord
            print(f"calibration click {self._key_label(key)} pitch={key.pitch} x={x} y={y}")
            try:
                interaction.mouse_down(x, y)
                time.sleep(0.08)
                interaction.mouse_up()
            except Exception as e:
                print(f"calibration click failed: {e}")
                return
            time.sleep(0.08)

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
        indices = tuple(
            checkbox.property("track_index")
            for checkbox in self.track_checkboxes
            if checkbox.isChecked()
        )
        if self.track_checkboxes:
            return tuple(int(index) for index in indices)
        return self._saved_track_indices(song_id or self.selected_song_id)

    def populate_track_selection(self, song_id, tracks):
        if song_id == self._track_ui_song_id and self.track_checkboxes:
            return

        self._loading_tracks = True
        try:
            while self.track_checks_layout.count():
                item = self.track_checks_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self.track_checkboxes = []
            self._track_ui_song_id = song_id

            note_tracks = [track for track in tracks if getattr(track, "note_count", 0) > 0]
            saved = self._saved_track_indices(song_id)
            selected = set(saved if saved is not None else [track.index for track in note_tracks])

            if not tracks:
                self.track_empty_label = BodyLabel(self.tr("没有可选择的音轨"))
                self.track_checks_layout.addWidget(self.track_empty_label)
                self.btn_play_tracks.setEnabled(False)
                return

            for track in tracks:
                text = f"{track.index + 1}. {track.name} ({track.note_count})"
                checkbox = QCheckBox(text)
                checkbox.setProperty("track_index", int(track.index))
                checkbox.setChecked(track.index in selected)
                checkbox.setEnabled(track.note_count > 0)
                checkbox.stateChanged.connect(self.on_track_selection_changed)
                self.track_checks_layout.addWidget(checkbox)
                self.track_checkboxes.append(checkbox)
            self.btn_play_tracks.setEnabled(
                any(checkbox.isChecked() for checkbox in self.track_checkboxes)
            )
        finally:
            self._loading_tracks = False
            if hasattr(self, "track_section"):
                self.track_checks_layout.invalidate()
                self.track_section.request_adjust_view_size()

    def clear_track_selection(self):
        self._track_ui_song_id = None
        self.track_checkboxes = []
        while self.track_checks_layout.count():
            item = self.track_checks_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.track_empty_label = BodyLabel(self.tr("尚未载入音轨"))
        self.track_checks_layout.addWidget(self.track_empty_label)
        self.btn_play_tracks.setEnabled(False)
        if hasattr(self, "track_section"):
            self.track_checks_layout.invalidate()
            self.track_section.request_adjust_view_size()

    def on_track_selection_changed(self, state):
        if self._loading_tracks:
            return
        selected = list(self._selected_track_indices() or ())
        selections = dict(self.config.get("track_selection", {}))
        selections[self._song_config_key()] = selected
        self.config["track_selection"] = selections
        self.btn_play_tracks.setEnabled(bool(selected))
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
        min_transpose = self.spn_pitch.minimum()
        max_transpose = self.spn_pitch.maximum()
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
        if song_id != self.selected_song_id:
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
        if song_id != self.selected_song_id:
            return
        print(f"MIDI analysis failed for {song_id}: {message}")
        self.pitch_chart.set_data({}, 0, 0)
        self.clear_track_selection()
        from PySide6.QtCore import Qt
        from qfluentwidgets import InfoBar, InfoBarPosition

        InfoBar.error(
            title=self.tr("MIDI 分析失败"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3500,
            parent=self.window(),
        )

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
            self.selection_card.titleLabel.setText(display_name)
            if self.playing_song_id is None:
                self.lbl_track_name.setText(display_name)
                self.lbl_time_current.setText("00:00")
                self.lbl_time_total.setText("--:--")
                self._current_seconds = 0.0
                self._duration_seconds = 0.0
                self._set_progress_slider_value(0)
                self.is_favorite = bool(song.favorite) if song else False
                self._update_favorite_button()
            self._load_song_analysis_settings(song_id)
            self.btn_favorite.show()
            self.btn_play_selected.setDisabled(False)
            self.pitch_chart.set_data({}, 0, 0)
            self.clear_track_selection()
            self._request_analysis_for_current_song()
        else:
            self.selected_song_id = None
            self.selection_card.titleLabel.setText(self.tr_no_song_selected)
            if self.playing_song_id is None:
                self.lbl_track_name.setText(self.tr_no_song_selected)
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
        self.lbl_track_name.setText(song.title if song else self.tr("播放中"))
        if song:
            self.is_favorite = bool(song.favorite)
            self._update_favorite_button()
        self._update_play_button_icon()

        def worker():
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
        if self._playback_thread is not None and self._playback_thread.is_alive():
            QTimer.singleShot(
                80, lambda: self._restart_playback_when_ready(song_id, reset_position)
            )
            return
        self.start_playback(song_id, reset_position=reset_position)

    def on_playback_song_changed(self, song_id):
        is_new_song = song_id != self.playing_song_id
        self.playing_song_id = song_id
        song = self.songs_by_id.get(song_id)
        if song is not None:
            self.lbl_track_name.setText(song.title)
            self.is_favorite = bool(song.favorite)
            self._update_favorite_button()
        if is_new_song:
            self._duration_seconds = 0.0
            self._current_seconds = 0.0
            self._set_progress_slider_value(0)

    def on_playback_status_changed(self, status):
        if status == "playing":
            self.is_playing = True
        elif status == "idle":
            self.is_playing = False
        elif status.startswith("error:"):
            print(f"MIDI playback failed: {status[6:]}")
            from PySide6.QtCore import Qt
            from qfluentwidgets import InfoBar, InfoBarPosition

            InfoBar.error(
                title=self.tr("MIDI 播放失败"),
                content=status[6:],
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self.window(),
            )
        self._update_play_button_icon()

    def on_playback_progress(self, current, total):
        self._current_seconds = current
        if total > 0:
            self._duration_seconds = total
        if self._is_slider_dragging:
            return
        self.update_progress(int(current * 1000), int(total * 1000))

    def _update_play_button_icon(self):
        self.cb_instrument.setEnabled(not self.is_playing)
        if self.is_playing:
            self.btn_play_pause.setIcon(FluentIcon.PAUSE)
        else:
            self.btn_play_pause.setIcon(FluentIcon.PLAY)

    def _update_favorite_button(self):
        if self.is_favorite:
            self.btn_favorite.setIcon(self.favorite_active_icon)
        else:
            self.btn_favorite.setIcon(FluentIcon.HEART)

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
        adjust_handle = getattr(self.slider_progress, "_adjustHandlePos", None)
        if callable(adjust_handle):
            adjust_handle()
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
        selected = self.time_picker.time
        if callable(selected):
            selected = selected()
        seconds = selected.hour() * 3600 + selected.minute() * 60 + selected.second()
        if seconds <= 0:
            return None
        return seconds

    def on_key_mode_changed(self, key):
        if key == "21_keys":
            QTimer.singleShot(0, lambda: self.segmented_widget.setCurrentItem("36_keys"))
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
        if not self._loaded:
            self._loaded = True
            self.refresh_song_list()
