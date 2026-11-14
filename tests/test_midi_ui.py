import importlib
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import og
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from src.midi_player.models import MidiTrackInfo, SongInfo, SongStats
from tests.support.qt_events import click_at, send_key

midi_ui = importlib.import_module("src.ui.MidiPlayerTab")


class TestMidiUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.config = deepcopy(midi_ui.MIDI_PLAYER_CONFIG_DEFAULTS)
        self.legacy_sections = {"playback_settings": True, "track_selection": False}
        self.config["collapsed_sections"] = self.legacy_sections.copy()
        self.library = Mock()
        self.library.list_songs.return_value = []
        self.patches = [
            patch.object(midi_ui, "Config", return_value=self.config),
            patch.object(midi_ui, "MidiLibraryService", return_value=self.library),
            patch.object(midi_ui.MidiPlayerTab, "refresh_song_list"),
            patch.object(og, "app", SimpleNamespace(tr=lambda value: value)),
        ]
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.tab = midi_ui.MidiPlayerTab()
        self.tab.resize(1200, 800)

    def tearDown(self):
        self.tab.dispose()
        self.tab.deleteLater()
        self.app.processEvents()

    def test_saved_settings_and_legacy_section_config_keep_their_formats(self):
        self.tab.time_picker.setTime(midi_ui.QTime(1, 25))
        self.tab.switch_schedule.setChecked(True)
        self.tab.settings_tabs.set_current("track_selection")

        self.assertEqual(self.config["schedule_time"], "01:25")
        self.assertTrue(self.config["schedule_enabled"])
        self.assertEqual(self.config["collapsed_sections"], self.legacy_sections)
        self.assertEqual(self.tab._scheduled_stop_delay(), 85 * 60)
        self.assertFalse(self.tab.segmented_widget.item("21_keys").isEnabled())

    def test_switching_settings_pages_keeps_player_and_library_in_place(self):
        self.tab.resize(960, 640)
        self.tab.show()
        self.tab.populate_track_selection(
            "song", [MidiTrackInfo(index, f"Track {index}", 4) for index in range(40)]
        )
        self.app.processEvents()
        self.assertEqual(self.tab.fav_segment.width(), self.tab.library_card.width())
        anchored_widgets = (
            self.tab.player_card,
            self.tab.selection_card,
            self.tab.song_tree_widget,
            self.tab.settings_stack,
        )
        geometry = [widget.geometry() for widget in anchored_widgets]
        self.tab.settings_tabs.set_current("track_selection")
        self.app.processEvents()
        track_page = self.tab.settings_pages["track_selection"]
        self.assertIs(self.tab.settings_stack.currentWidget(), track_page)
        scrollbar = self.tab.track_scroll.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        preview_geometry = self.tab.track_preview_panel.geometry()
        scrollbar.setValue(scrollbar.maximum())
        self.app.processEvents()
        self.assertEqual(self.tab.track_preview_panel.geometry(), preview_geometry)

        for key in ("calibration_tools", "track_selection"):
            self.tab.settings_tabs.set_current(key)
            self.app.processEvents()
            self.assertEqual([widget.geometry() for widget in anchored_widgets], geometry)
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

    def test_track_summary_counts_only_selected_tracks_with_notes(self):
        self.tab.selected_song_id = "song"
        self.config["track_selection"] = {"song": [1]}
        self.tab.populate_track_selection(
            "song",
            [
                MidiTrackInfo(0, "Empty", 0),
                MidiTrackInfo(1, "Flute", 17),
                MidiTrackInfo(2, "Piano", 24),
            ],
        )
        self.assertEqual(self.tab.track_selected_value.text(), "1 / 2")
        self.assertEqual(self.tab.track_notes_value.text(), "17")
        self.assertTrue(self.tab.btn_play_tracks.isEnabled())

        self.tab.settings_tabs.set_current("track_selection")
        self.tab.show()
        self.app.processEvents()
        checkbox = self.tab.track_checkboxes[1]
        click_at(checkbox, QPointF(checkbox.width() - 8, checkbox.height() / 2))
        self.assertEqual(self.tab.track_selected_value.text(), "0 / 2")
        self.assertEqual(self.tab.track_notes_value.text(), "0")
        self.assertFalse(self.tab.btn_play_tracks.isEnabled())
        self.assertEqual(self.config["track_selection"]["song"], [])

        for event_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            send_key(checkbox, Qt.Key.Key_Space, event_type)
        self.assertEqual(self.tab.track_selected_value.text(), "1 / 2")
        self.assertEqual(self.config["track_selection"]["song"], [1])

        self.tab.clear_track_selection()
        self.assertEqual(self.tab.track_selected_value.text(), "0 / 0")
        self.assertEqual(self.tab.track_notes_value.text(), "0")

    def test_native_slider_click_preserves_immediate_speed_change(self):
        self.tab.show()
        self.app.processEvents()
        slider = self.tab.slider_speed
        position = QPointF(slider.width() - 2, slider.height() / 2)
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            position,
            QPointF(slider.mapToGlobal(position.toPoint())),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(slider, event)

        self.assertGreater(self.config["speed_percent"], 100)
        self.assertEqual(self.config["speed_percent"], slider.value())

    def test_populated_library_keeps_native_navigation_and_filtering(self):
        self.library.library_dir = Path("mid_lib")
        songs = [
            SongInfo("first", "First song", Path("mid_lib/Folder/first.mid"), 1, 0),
            SongInfo("second", "Second song", Path("mid_lib/Folder/second.mid"), 1, 0),
        ]
        self.library.list_songs.return_value = songs
        self.tab.songs_by_id = {song.id: song for song in songs}
        tree = self.tab.song_tree_widget

        with patch.object(self.tab, "_request_analysis_for_current_song"):
            self.tab.populate_song_list()
            self.tab.show()
            self.app.processEvents()
            tree.setFocus(Qt.FocusReason.TabFocusReason)
            folder = tree.topLevelItem(0)
            self.assertTrue(folder.isExpanded())
            self.assertEqual(self.tab.selected_song_id, "first")
            send_key(tree, Qt.Key.Key_Down)
            self.assertEqual(self.tab.selected_song_id, "second")
            self.assertEqual(tree.currentItem().data(0, Qt.ItemDataRole.UserRole), "second")
            send_key(tree, Qt.Key.Key_Left)
            self.assertIs(tree.currentItem(), folder)
            send_key(tree, Qt.Key.Key_Left)
            self.assertFalse(folder.isExpanded())
            send_key(tree, Qt.Key.Key_Right)
            self.assertTrue(folder.isExpanded())
            self.tab.song_search_edit.setText("Second")
            self.assertTrue(folder.child(0).isHidden())
            self.assertFalse(folder.child(1).isHidden())
            self.assertEqual(self.tab._playlist_song_ids(), ["second"])
            send_key(tree, Qt.Key.Key_Down)
            self.assertEqual(tree.currentItem().data(0, Qt.ItemDataRole.UserRole), "second")
            self.tab.song_search_edit.clear()
            self.assertEqual(self.tab._playlist_song_ids(), ["first", "second"])

    def test_dispose_cancels_analysis_and_prevents_playback_restart(self):
        future = Mock()
        self.tab._analysis_future = future
        self.tab.dispose()
        self.tab.dispose()
        self.tab.start_playback("song")
        self.tab._restart_playback_when_ready("song", False)

        future.cancel.assert_called_once()
        self.assertTrue(self.tab._calibration_stop.is_set())
        self.assertFalse(self.tab._analysis_timer.isActive())
        self.assertIsNone(self.tab._playback_thread)

    def test_calibration_teardown_releases_a_pressed_mouse_button(self):
        layout = self.tab._current_layout()
        interaction = Mock()
        interaction.mouse_down.side_effect = lambda *_: self.tab.dispose()
        executor = SimpleNamespace(
            paused=False,
            method=SimpleNamespace(width=1920, height=1080),
            interaction=interaction,
        )
        with patch.object(og, "executor", executor):
            self.tab._run_key_calibration(layout.keys[:2], layout)

        interaction.mouse_down.assert_called_once()
        interaction.mouse_up.assert_called_once()

    def test_disposed_page_ignores_late_indexing_and_analysis_results(self):
        self.tab.selected_song_id = "song"
        self.tab.dispose()
        self.library.list_songs.reset_mock()
        previous_time = self.tab.lbl_time_total.text()
        self.tab._on_library_indexed(None)
        self.tab.on_song_analysis_done(
            "song", SongStats(1, 1, 0, (60,), ()), {60: 1}, 30,
            [MidiTrackInfo(0, "Piano", 1)], None,
        )
        self.tab.on_playback_status_changed("playing")
        self.library.list_songs.assert_not_called()
        self.assertEqual(self.tab.lbl_time_total.text(), previous_time)
        self.assertFalse(self.tab.track_checkboxes)
        self.assertFalse(self.tab.is_playing)
