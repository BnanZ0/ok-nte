import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.midi_player.cache import MappedPitchCache, MappedPitchCacheKey
from src.midi_player.controller import MidiPlaybackController
from src.midi_player.layout import PianoLayout
from src.midi_player.library import MidiLibraryService
from src.midi_player.models import (
    Instrument,
    LayoutMode,
    MidiNoteEvent,
    ParsedSong,
    PlaybackOptions,
    PlayMode,
    SongInfo,
)
from src.midi_player.pitch import PitchRemapCancelled, choose_best_transpose, remap_note_pitches
from src.midi_player.preparation import (
    PreparedMidiPlayback,
    mapped_pitch_cache_key,
    prepare_midi_analysis_async,
    prepare_midi_playback,
    prepare_midi_playback_async,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestInstrumentLayout(unittest.TestCase):
    def test_default_piano_and_guitar_match_their_note_ranges(self):
        piano = PianoLayout.default(LayoutMode.KEYS_36)
        guitar = PianoLayout.default(LayoutMode.KEYS_36, instrument=Instrument.GUITAR)

        self.assertEqual(piano.playable_pitches, frozenset(range(40, 76)))
        self.assertEqual(guitar.playable_pitches, frozenset(range(52, 88)))
        for row, pitch in ((0, 76), (1, 64), (2, 52)):
            with self.subTest(row=row):
                key = next(key for key in guitar.keys if key.row == row and key.column == 0)
                self.assertEqual(key.pitch, pitch)
                self.assertEqual(
                    guitar.coordinate_for_pitch(pitch), piano.coordinate_for_pitch(pitch - 12)
                )

    def test_remap_cache_keeps_instrument_ranges_separate(self):
        parsed = ParsedSong(
            info=_song_info("song-a", "Song A"),
            duration=0.1,
            ticks_per_beat=480,
            notes=(MidiNoteEvent(pitch=40, start=0.0, duration=0.1),),
        )
        cache = MappedPitchCache()
        for instrument, expected_pitch in (
            (Instrument.PIANO, 40),
            (Instrument.GUITAR, 52),
            (Instrument.PIANO, 40),
        ):
            with self.subTest(instrument=instrument):
                layout = PianoLayout.default(LayoutMode.KEYS_36, instrument=instrument)
                prepared = prepare_midi_playback(parsed, layout, 0, mapped_pitch_cache=cache)
                self.assertEqual(prepared.mapped_pitches, (expected_pitch,))


class TestMidiPitchMapping(unittest.TestCase):
    def test_remap_keeps_playable_notes_unchanged(self):
        notes = (
            MidiNoteEvent(pitch=60, start=0.0, duration=0.1),
            MidiNoteEvent(pitch=64, start=0.2, duration=0.1),
        )

        mapped = remap_note_pitches(notes, frozenset({60, 64, 67}))

        self.assertEqual(mapped, (60, 64))

    def test_remap_moves_unplayable_pitch_by_octave(self):
        notes = (MidiNoteEvent(pitch=72, start=0.0, duration=0.1),)

        mapped = remap_note_pitches(notes, frozenset({48, 60}))

        self.assertEqual(mapped, (60,))

    def test_remap_approximates_missing_pitch_class(self):
        notes = (MidiNoteEvent(pitch=61, start=0.0, duration=0.1),)

        mapped = remap_note_pitches(notes, frozenset({60, 62, 64}))

        self.assertIn(mapped[0], {60, 62})

    def test_remap_preserves_melodic_contour_after_octave_shift(self):
        notes = (
            MidiNoteEvent(pitch=84, start=0.0, duration=0.1),
            MidiNoteEvent(pitch=83, start=0.5, duration=0.1),
        )

        mapped = remap_note_pitches(notes, frozenset({60, 71, 72}))

        self.assertEqual(mapped, (72, 71))

    def test_remap_keeps_chord_bass_below_melody_when_compressed(self):
        notes = (
            MidiNoteEvent(pitch=36, start=0.0, duration=0.1),
            MidiNoteEvent(pitch=84, start=0.0, duration=0.1),
        )

        mapped = remap_note_pitches(notes, frozenset({48, 60, 72}))

        self.assertLess(mapped[0], mapped[1])

    def test_choose_best_transpose_prefers_smallest_best_shift(self):
        notes = (
            MidiNoteEvent(pitch=72, start=0.0, duration=0.1),
            MidiNoteEvent(pitch=76, start=0.2, duration=0.1),
        )

        transpose = choose_best_transpose(notes, frozenset({60, 64}), -24, 24)

        self.assertEqual(transpose, -12)

    def test_remap_can_cancel_stale_analysis(self):
        notes = tuple(
            MidiNoteEvent(pitch=84 - index % 12, start=index * 0.1, duration=0.1)
            for index in range(30)
        )

        with self.assertRaises(PitchRemapCancelled):
            remap_note_pitches(
                notes,
                frozenset({60, 64, 67, 72}),
                should_cancel=lambda: True,
            )


class TestMidiLibraryService(unittest.TestCase):
    def temp_dir(self):
        return TemporaryDirectory(dir=REPO_ROOT)

    def test_import_files_copies_only_midi_files(self):
        with self.temp_dir() as tmp:
            root = Path(tmp)
            source = root / "source.mid"
            ignored = root / "ignored.txt"
            source.write_bytes(b"MThd")
            ignored.write_text("not midi", encoding="utf-8")
            library = MidiLibraryService(library_dir=root / "library")

            imported = library.import_files([source, ignored])

            self.assertEqual(len(imported), 1)
            self.assertEqual(imported[0].title, "source")
            self.assertTrue(imported[0].path.exists())
            self.assertEqual(library.list_songs(), imported)

    def test_set_favorite_persists_selected_song(self):
        with self.temp_dir() as tmp:
            root = Path(tmp)
            source = root / "song.mid"
            source.write_bytes(b"MThd")
            library = MidiLibraryService(library_dir=root / "library")
            song = library.import_files([source])[0]

            updated = library.set_favorite(song.id, True)

            self.assertTrue(updated.favorite)
            reloaded = MidiLibraryService(library_dir=root / "library")
            reloaded.index()
            self.assertTrue(reloaded.list_songs()[0].favorite)


class TestMidiPreparation(unittest.TestCase):
    def test_prepare_midi_playback_populates_mapped_pitch_cache(self):
        parsed = ParsedSong(
            info=SongInfo(
                id="song-a",
                title="Song A",
                path=Path("song-a.mid"),
                size=10,
                mtime=1.0,
            ),
            duration=1.0,
            ticks_per_beat=480,
            notes=(MidiNoteEvent(pitch=72, start=0.0, duration=0.1),),
        )
        layout = PianoLayout.default(LayoutMode.KEYS_36)
        cache = MappedPitchCache()

        prepared = prepare_midi_playback(parsed, layout, -12, None, True, cache)

        key = MappedPitchCacheKey(
            song_id="song-a",
            mtime=1.0,
            size=10,
            track_indices=None,
            playable_pitches=tuple(sorted(layout.playable_pitches)),
            transpose=-12,
            smart_remap=True,
        )
        self.assertEqual(cache.get(key), prepared.mapped_pitches)
        stale_key = MappedPitchCacheKey(
            song_id="song-a",
            mtime=2.0,
            size=10,
            track_indices=None,
            playable_pitches=tuple(sorted(layout.playable_pitches)),
            transpose=-12,
            smart_remap=True,
        )
        self.assertIsNone(cache.get(stale_key))


class TestMidiProcessPreparation(unittest.IsolatedAsyncioTestCase):
    async def test_playback_options_apply_instrument_with_default_and_custom_bounds(self):
        from mido import Message, MidiFile, MidiTrack

        with TemporaryDirectory(dir=REPO_ROOT) as tmp:
            root = Path(tmp)
            source = root / "instrument-song.mid"
            midi = MidiFile(ticks_per_beat=480)
            track = MidiTrack()
            track.append(Message("note_on", note=64, velocity=64, time=0))
            track.append(Message("note_off", note=64, velocity=0, time=480))
            midi.tracks.append(track)
            midi.save(source)
            library = MidiLibraryService(library_dir=root / "library")
            song = library.import_files([source])[0]

            for bounds in (None, (0.1, 0.6, 0.9, 0.9)):
                for instrument, expected_row in ((Instrument.PIANO, 0), (Instrument.GUITAR, 1)):
                    with self.subTest(bounds=bounds, instrument=instrument):
                        options = PlaybackOptions(instrument=instrument, bounds=bounds)
                        prepared = await prepare_midi_playback_async(library, song.id, options)
                        self.assertEqual(prepared.mapped_pitches, (64,))
                        key = next(key for key in prepared.layout.keys if key.pitch == 64)
                        self.assertEqual(key.row, expected_row)
                        if bounds is not None:
                            self.assertEqual(prepared.layout.bounds, bounds)

    async def test_prepare_midi_analysis_async_populates_parent_caches(self):
        from mido import Message, MidiFile, MidiTrack

        with TemporaryDirectory(dir=REPO_ROOT) as tmp:
            root = Path(tmp)
            source = root / "process-song.mid"
            midi = MidiFile(ticks_per_beat=480)
            track = MidiTrack()
            track.append(Message("note_on", note=72, velocity=64, time=0))
            track.append(Message("note_off", note=72, velocity=0, time=480))
            midi.tracks.append(track)
            midi.save(source)

            library = MidiLibraryService(library_dir=root / "library")
            song = library.import_files([source])[0]
            layout = PianoLayout.default(LayoutMode.KEYS_36)

            analysis = await prepare_midi_analysis_async(
                library,
                song.id,
                layout,
                -12,
                None,
                True,
            )

            self.assertEqual(analysis.prepared.song_id, song.id)
            self.assertEqual(analysis.prepared.mapped_pitches, (60,))
            self.assertIsNotNone(library.cache.get(song))
            key = mapped_pitch_cache_key(
                song,
                None,
                layout.playable_pitches,
                -12,
                True,
            )
            self.assertEqual(library.mapped_pitch_cache.get(key), (60,))


class TestMidiPlaybackPreparation(unittest.IsolatedAsyncioTestCase):
    async def test_sequence_prepares_next_song_before_current_song_finishes(self):
        events = []
        library = _FakePlaybackLibrary(
            (
                _song_info("song-a", "Song A"),
                _song_info("song-b", "Song B"),
            )
        )
        controller = _RecordingPlaybackController(library, events)

        await controller.play("song-a", PlaybackOptions(play_mode=PlayMode.SEQUENTIAL))

        self.assertLess(
            events.index(("prepare", "song-b")),
            events.index(("play_end", "song-a")),
        )

    async def test_sequence_uses_ui_playlist_order_for_next_song(self):
        events = []
        library = _FakePlaybackLibrary(
            (
                _song_info("song-a", "Song A"),
                _song_info("song-c", "Song C"),
                _song_info("song-b", "Song B"),
            )
        )
        controller = _StoppingRandomPlaybackController(library, events)

        await controller.play(
            "song-a",
            PlaybackOptions(
                play_mode=PlayMode.SEQUENTIAL,
                playlist_song_ids=("song-a", "song-b", "song-c"),
            ),
        )

        self.assertLess(
            events.index(("prepare", "song-b")),
            events.index(("play_end", "song-a")),
        )
        self.assertNotIn(("prepare", "song-c"), events)

    async def test_random_sequence_prepares_selected_next_song_before_current_song_finishes(self):
        events = []
        library = _FakePlaybackLibrary(
            (
                _song_info("song-a", "Song A"),
                _song_info("song-b", "Song B"),
                _song_info("song-c", "Song C"),
            )
        )
        controller = _DeterministicRandomPlaybackController(library, events, "song-c")

        await controller.play("song-a", PlaybackOptions(play_mode=PlayMode.RANDOM))

        self.assertLess(
            events.index(("prepare", "song-c")),
            events.index(("play_end", "song-a")),
        )

    async def test_mode_change_replans_prefetched_next_song(self):
        events = []
        library = _FakePlaybackLibrary(
            (
                _song_info("song-a", "Song A"),
                _song_info("song-b", "Song B"),
                _song_info("song-c", "Song C"),
            )
        )
        controller = _ModeChangingPlaybackController(library, events, "song-c")

        await controller.play("song-a", PlaybackOptions(play_mode=PlayMode.SEQUENTIAL))

        self.assertIn(("prepare", "song-b"), events)
        self.assertLess(
            events.index(("prepare", "song-c")),
            events.index(("play_end", "song-a")),
        )
        self.assertEqual(events[-1], ("idle", "idle"))

    async def test_playlist_change_replans_prefetched_next_song(self):
        events = []
        library = _FakePlaybackLibrary(
            (
                _song_info("song-a", "Song A"),
                _song_info("song-b", "Song B"),
                _song_info("song-c", "Song C"),
            )
        )
        controller = _PlaylistChangingPlaybackController(library, events)

        await controller.play(
            "song-a",
            PlaybackOptions(
                play_mode=PlayMode.SEQUENTIAL,
                playlist_song_ids=("song-a", "song-b", "song-c"),
            ),
        )

        self.assertIn(("prepare", "song-b"), events)
        self.assertLess(
            events.index(("prepare", "song-c")),
            events.index(("play_end", "song-a")),
        )

    async def test_stop_finishes_play_task_without_leaking_internal_cancel(self):
        events = []
        library = _FakePlaybackLibrary((_song_info("song-a", "Song A"),))
        controller = _BlockingPlaybackController(library, events)

        play_task = asyncio.create_task(controller.play("song-a", PlaybackOptions()))
        await controller.started.wait()
        await controller.stop()
        await play_task

        self.assertTrue(play_task.done())
        self.assertEqual(
            events, [("prepare", "song-a"), ("play_start", "song-a"), ("idle", "idle")]
        )


class _FakePlaybackLibrary:
    def __init__(self, songs):
        self._songs = tuple(songs)

    def list_songs(self):
        return list(self._songs)


class _RecordingPlaybackController(MidiPlaybackController):
    def __init__(self, library, events):
        super().__init__(library)
        self.events = events

    async def _prepare_playback(self, song_id, options):
        self.events.append(("prepare", song_id))
        await self._yield_once()
        return PreparedMidiPlayback(
            song_id=song_id,
            parsed_song=ParsedSong(
                info=_song_info(song_id, song_id),
                duration=0.0,
                ticks_per_beat=480,
                notes=(),
            ),
            notes=(),
            mapped_pitches=(),
            source_duration=0.0,
            layout=PianoLayout.default(LayoutMode.KEYS_36),
        )

    async def _play_prepared(self, prepared, options):
        self.events.append(("play_start", prepared.song_id))
        await self._yield_once()
        self.events.append(("play_end", prepared.song_id))

    async def _yield_once(self):
        import asyncio

        await asyncio.sleep(0)


class _StoppingRandomPlaybackController(_RecordingPlaybackController):
    async def _play_prepared(self, prepared, options):
        self.events.append(("play_start", prepared.song_id))
        await self._yield_once()
        await self._yield_once()
        self.events.append(("play_end", prepared.song_id))
        self._stopped.set()


class _DeterministicRandomPlaybackController(_StoppingRandomPlaybackController):
    def __init__(self, library, events, next_song_id):
        super().__init__(library, events)
        self.next_song_id = next_song_id

    def _random_song_id(self, current_song_id):
        return self.next_song_id


class _ModeChangingPlaybackController(_DeterministicRandomPlaybackController):
    async def _play_prepared(self, prepared, options):
        self.events.append(("play_start", prepared.song_id))
        if prepared.song_id == "song-a":
            await self._yield_once()
            self.set_mode(PlayMode.RANDOM)
            await self._yield_once()
            self.events.append(("play_end", prepared.song_id))
            self._stopped.set()
            return
        await super()._play_prepared(prepared, options)

    def _status(self, options, status):
        if status == "idle":
            self.events.append(("idle", status))


class _PlaylistChangingPlaybackController(_StoppingRandomPlaybackController):
    async def _play_prepared(self, prepared, options):
        self.events.append(("play_start", prepared.song_id))
        if prepared.song_id == "song-a":
            await self._yield_once()
            self.set_playlist_song_ids(("song-a", "song-c"))
            await self._yield_once()
            self.events.append(("play_end", prepared.song_id))
            self._stopped.set()
            return
        await super()._play_prepared(prepared, options)


class _BlockingPlaybackController(_RecordingPlaybackController):
    def __init__(self, library, events):
        super().__init__(library, events)
        self.started = asyncio.Event()

    async def _play_prepared(self, prepared, options):
        self.events.append(("play_start", prepared.song_id))
        self.started.set()
        await asyncio.sleep(60)

    def _status(self, options, status):
        if status == "idle":
            self.events.append(("idle", status))


def _song_info(song_id, title):
    return SongInfo(
        id=song_id,
        title=title,
        path=Path(f"{song_id}.mid"),
        size=1,
        mtime=1.0,
    )


if __name__ == "__main__":
    unittest.main()
