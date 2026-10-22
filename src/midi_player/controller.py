from __future__ import annotations

import asyncio
import inspect
import random
from dataclasses import replace

from src.utils import clock

from .keyboard import KeyboardPlayer, build_key_events
from .library import MidiLibraryService
from .models import Instrument, PlaybackOptions, PlayMode
from .preparation import PreparedMidiPlayback, prepare_midi_playback_async


async def _cancel_and_wait(task: asyncio.Task) -> None:
    task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)
    exception = result[0]
    if isinstance(exception, asyncio.CancelledError):
        return
    if isinstance(exception, BaseException):
        raise exception


class MidiPlaybackController:
    """Stable UI-facing playback controller."""

    def __init__(self, library: MidiLibraryService, og_provider=None) -> None:
        self.library = library
        self.og_provider = og_provider
        self._mode = PlayMode.SEQUENTIAL
        self._play_task: asyncio.Task[None] | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._pause = asyncio.Event()
        self._pause.set()
        self._stopped = asyncio.Event()
        self._stopped.set()
        self._sequence_options: PlaybackOptions | None = None
        self._current_song_id: str | None = None
        self._prefetch_song_id: str | None = None
        self._prefetch_task: asyncio.Task[PreparedMidiPlayback] | None = None

    @property
    def is_playing(self) -> bool:
        return self._play_task is not None and not self._play_task.done() and self._pause.is_set()

    def set_mode(self, mode: PlayMode | str) -> None:
        self._mode = PlayMode(mode)
        self._restart_prefetch_for_current_song()

    def set_playlist_song_ids(self, song_ids: tuple[str, ...] | list[str]) -> None:
        if self._sequence_options is None:
            return
        unique_song_ids = tuple(dict.fromkeys(song_ids))
        self._sequence_options.playlist_song_ids = unique_song_ids or None
        self._restart_prefetch_for_current_song()

    async def play(self, song_id: str, options: PlaybackOptions | None = None) -> None:
        await self.stop()
        play_options = options or PlaybackOptions()
        self._mode = play_options.play_mode
        self._stopped.clear()
        self._pause.set()
        self._play_task = asyncio.create_task(self._play_sequence(song_id, play_options))
        try:
            await self._play_task
        finally:
            self._status(play_options, "idle")

    def play_background(
        self,
        song_id: str,
        options: PlaybackOptions | None = None,
    ) -> asyncio.Task[None]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("play_background requires a running asyncio loop") from exc
        self._runner_task = loop.create_task(self.play(song_id, options))
        return self._runner_task

    def pause(self) -> None:
        self._pause.clear()

    def resume(self) -> None:
        self._pause.set()

    async def stop(self) -> None:
        self._stopped.set()
        self._pause.set()
        self._cancel_prefetch()
        if self._play_task is not None and not self._play_task.done():
            await _cancel_and_wait(self._play_task)
        self._play_task = None

    async def _play_sequence(self, song_id: str, options: PlaybackOptions) -> None:
        next_song_id: str | None = song_id
        first_song = True
        prepared_task: asyncio.Task[PreparedMidiPlayback] | None = None
        self._sequence_options = options
        self._current_song_id = None
        try:
            while next_song_id and not self._stopped.is_set():
                current_song_id = next_song_id
                play_options = options if first_song else replace(options, start_offset=0.0)
                if prepared_task is None:
                    prepared_task = asyncio.create_task(
                        self._prepare_playback(current_song_id, play_options)
                    )

                self._status(play_options, "loading")
                self._song_changed(play_options, current_song_id)
                try:
                    prepared = await prepared_task
                except Exception as e:
                    prepared_task = None
                    if self._stopped.is_set():
                        break
                    self._status(play_options, f"error:{e}")
                    self._current_song_id = current_song_id
                    next_song_id = self._upcoming_song_id(current_song_id)
                    first_song = False
                    await asyncio.sleep(1.0)
                    continue

                prepared_task = None
                if self._stopped.is_set():
                    break

                self._current_song_id = current_song_id
                self._restart_prefetch_for_current_song()
                await asyncio.sleep(0)

                try:
                    await self._play_prepared(prepared, play_options)
                except Exception as e:
                    if self._stopped.is_set():
                        break
                    self._status(play_options, f"error:{e}")

                first_song = False
                next_song_id = self._prefetch_song_id
                prepared_task = self._prefetch_task
                self._prefetch_song_id = None
                self._prefetch_task = None
        except asyncio.CancelledError:
            if not self._stopped.is_set():
                raise
        finally:
            if prepared_task is not None:
                if not prepared_task.done():
                    await _cancel_and_wait(prepared_task)
                else:
                    try:
                        prepared_task.result()
                    except Exception:
                        pass
            self._cancel_prefetch()
            self._sequence_options = None
            self._current_song_id = None

    async def _play_once(self, song_id: str, options: PlaybackOptions) -> None:
        self._status(options, "loading")
        self._song_changed(options, song_id)
        prepared = await self._prepare_playback(song_id, options)
        await self._play_prepared(prepared, options)

    async def _prepare_playback(
        self,
        song_id: str,
        options: PlaybackOptions,
    ) -> PreparedMidiPlayback:
        return await prepare_midi_playback_async(self.library, song_id, options)

    def _restart_prefetch_for_current_song(self) -> None:
        if (
            self._current_song_id is None
            or self._sequence_options is None
            or self._stopped.is_set()
        ):
            return

        self._cancel_prefetch()
        upcoming_song_id = self._upcoming_song_id(self._current_song_id)
        self._prefetch_song_id = upcoming_song_id
        if upcoming_song_id is None:
            return

        upcoming_options = replace(self._sequence_options, start_offset=0.0)
        try:
            self._prefetch_task = asyncio.create_task(
                self._prepare_playback(upcoming_song_id, upcoming_options)
            )
        except RuntimeError:
            self._prefetch_task = None
            self._prefetch_song_id = None

    def _cancel_prefetch(self) -> None:
        task = self._prefetch_task
        self._prefetch_task = None
        self._prefetch_song_id = None
        if task is None:
            return
        if not task.done():
            task.cancel()
            task.add_done_callback(self._discard_prefetch_result)
            return
        self._discard_prefetch_result(task)

    @staticmethod
    def _discard_prefetch_result(task: asyncio.Task[PreparedMidiPlayback]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            pass

    async def _play_prepared(
        self,
        prepared: PreparedMidiPlayback,
        options: PlaybackOptions,
    ) -> None:
        notes = prepared.notes
        source_duration = prepared.source_duration
        speed = max(0.1, float(options.speed))
        playback_duration = source_duration / speed if source_duration > 0 else 0.0
        layout = prepared.layout
        mapped_pitches = prepared.mapped_pitches
        og = self._require_og()
        self._prepare_capture(og)
        method = og.executor.method
        interaction = og.executor.interaction
        client_width = method.width
        client_height = method.height
        keyboard = KeyboardPlayer(interaction) if layout.instrument == Instrument.GUITAR else None

        start_offset = max(0.0, min(options.start_offset, playback_duration))
        source_offset = start_offset * speed
        events = ()
        if keyboard is not None:
            events = build_key_events(
                notes,
                mapped_pitches,
                layout,
                speed=speed,
                start_offset=start_offset,
                minimum_duration=max(0.001, float(options.note_gap)),
            )

        self._status(options, "playing")
        playback_origin = clock.now() - start_offset
        progress_task = asyncio.create_task(
            self._progress_loop(lambda: playback_origin, playback_duration, options)
        )

        try:
            if keyboard is not None:
                for event in events:
                    playback_origin = await self._wait_until_playback_time(
                        playback_origin, event.time, keyboard=keyboard
                    )
                    if self._stopped.is_set():
                        break
                    keyboard.apply(event)
            else:
                for note, pitch in zip(notes, mapped_pitches):
                    if note.start < source_offset:
                        continue
                    if self._stopped.is_set():
                        break
                    coordinate = layout.client_coordinate_for_pitch(
                        pitch,
                        client_width,
                        client_height,
                    )
                    if coordinate is None:
                        continue

                    playback_origin = await self._wait_until_playback_time(
                        playback_origin,
                        note.start / speed,
                    )
                    if self._stopped.is_set():
                        break

                    x, y = coordinate
                    await self._tap(interaction, x, y, options.note_gap)

            if not self._stopped.is_set():
                playback_origin = await self._wait_until_playback_time(
                    playback_origin,
                    playback_duration,
                    keyboard=keyboard,
                )
                self._progress(options, playback_duration, playback_duration)
        finally:
            try:
                if keyboard is not None:
                    keyboard.release_all()
            finally:
                await _cancel_and_wait(progress_task)

    def _random_song_id(self, current_song_id: str) -> str | None:
        song_ids = self._sequence_song_ids()
        if not song_ids:
            return None
        candidates = [song_id for song_id in song_ids if song_id != current_song_id]
        if not candidates:
            candidates = song_ids
        return random.choice(candidates)

    def _next_song_id(self, current_song_id: str) -> str | None:
        song_ids = self._sequence_song_ids()
        if not song_ids:
            return None
        if current_song_id not in song_ids:
            return song_ids[0]
        next_index = song_ids.index(current_song_id) + 1
        if next_index < len(song_ids):
            return song_ids[next_index]
        return None

    def _sequence_song_ids(self) -> list[str]:
        if self._sequence_options is not None and self._sequence_options.playlist_song_ids:
            return list(dict.fromkeys(self._sequence_options.playlist_song_ids))
        return [song.id for song in self.library.list_songs()]

    def _upcoming_song_id(self, current_song_id: str) -> str | None:
        if self._mode == PlayMode.SINGLE_LOOP:
            return current_song_id
        if self._mode == PlayMode.SEQUENTIAL:
            return self._next_song_id(current_song_id)
        if self._mode == PlayMode.RANDOM:
            return self._random_song_id(current_song_id)
        return None

    def _require_og(self):
        og = self.og_provider() if callable(self.og_provider) else self.og_provider
        if og is None:
            try:
                from ok import og as global_og
            except ImportError as exc:
                raise RuntimeError("No og provider is available for MIDI playback") from exc
            og = global_og
        if getattr(og, "executor", None) is None:
            raise RuntimeError("Software is not started: executor is unavailable")
        return og

    @staticmethod
    def _prepare_capture(og) -> None:
        executor = og.executor
        if executor.paused:
            if not og.app.start_controller.do_start():
                raise RuntimeError("OK start_controller failed")
            return
        og.device_manager.do_refresh(True)
        if error := og.app.start_controller.check_device_error():
            raise RuntimeError(error)

    async def _call(self, func, *args) -> None:
        result = func(*args)
        if inspect.isawaitable(result):
            await result

    async def _tap(self, interaction, x: int, y: int, note_gap: float) -> None:
        await self._call(interaction.mouse_down, x, y)
        await asyncio.sleep(max(0.001, float(note_gap)))
        await self._call(interaction.mouse_up)

    async def _wait_until_playback_time(
        self,
        playback_origin: clock.TimePoint,
        playback_time: float,
        *,
        keyboard: KeyboardPlayer | None = None,
    ) -> clock.TimePoint:
        while not self._stopped.is_set():
            if not self._pause.is_set():
                pause_started_at = clock.now()
                if keyboard is not None:
                    keyboard.release_all()
                await self._pause.wait()
                playback_origin += clock.elapsed(pause_started_at)
                if keyboard is not None and not self._stopped.is_set():
                    keyboard.restore()
                continue

            delay = playback_origin + playback_time - clock.now()
            if delay <= 0:
                return playback_origin
            await asyncio.sleep(min(delay, 0.02))
        return playback_origin

    def _status(self, options: PlaybackOptions, status: str) -> None:
        if options.on_status is not None:
            options.on_status(status)

    def _progress(self, options: PlaybackOptions, current: float, total: float) -> None:
        if options.on_progress is not None:
            options.on_progress(max(0.0, current), max(0.0, total))

    def _song_changed(self, options: PlaybackOptions, song_id: str) -> None:
        if options.on_song_changed is not None:
            options.on_song_changed(song_id)

    async def _progress_loop(
        self,
        origin_provider,
        duration: float,
        options: PlaybackOptions,
    ) -> None:
        while not self._stopped.is_set():
            if not self._pause.is_set():
                await self._pause.wait()
            current = clock.elapsed(origin_provider())
            self._progress(options, min(current, duration), duration)
            if current >= duration:
                break
            await asyncio.sleep(0.2)
