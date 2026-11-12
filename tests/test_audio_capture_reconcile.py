"""Regression tests for the audio-capture reconcile loop.

The bug these guard against: SoundCombatContext.enter() used to bind the WASAPI
process-loopback target before Choose Window had settled on the cloud variant.
An event hook then re-read a stale global and decided no rebind was needed, so
the listener waited on HTGame.exe for the whole session. The listener now
resolves the user's variant choice itself on every loop iteration; there is no
event to lose, and a switch is picked up even while the old process is still
emitting audio.
"""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.sound_trigger.capture.spec import AudioCaptureSpec


def _spec(*names):
    return AudioCaptureSpec(process_names=tuple(names))


class SelectedVariantIntentTests(unittest.TestCase):
    """The audio target follows the explicit choice, not the live device."""

    @staticmethod
    def _manager(preferred):
        return SimpleNamespace(config={"preferred": preferred}, device_dict={})

    def test_last_explicit_choice_wins_over_a_stale_device_config(self):
        from src.patches import window_variants_patch as wvp

        with (
            patch("ok.og", SimpleNamespace(device_manager=self._manager("pc_local"))),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            self.assertEqual(wvp.get_selected_variant_id(), "pc_cloud")
            self.assertEqual(
                wvp.resolve_audio_capture_spec().factory_process_name,
                "NTECloudGame.exe",
            )

    def test_persisted_preference_is_used_before_the_first_device_refresh(self):
        from src.patches import window_variants_patch as wvp

        with (
            patch("ok.og", SimpleNamespace(device_manager=self._manager("pc_cloud"))),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", None),
        ):
            self.assertEqual(wvp.get_selected_variant_id(), "pc_cloud")
            self.assertEqual(
                wvp.resolve_audio_capture_spec().factory_process_name,
                "NTECloudGame.exe",
            )

    def test_clear_devices_does_not_change_the_audio_intent(self):
        from src.patches import window_variants_patch as wvp

        with (
            patch("ok.og", SimpleNamespace(device_manager=self._manager(""))),
            patch.object(wvp, "cloud_experimental_enabled", return_value=True),
            patch.object(wvp, "_LAST_PREFERRED_VARIANT", "pc_cloud"),
        ):
            self.assertEqual(wvp.get_selected_variant_id(), "pc_cloud")

    def test_local_variant_resolves_to_the_local_game_process(self):
        from src.patches import window_variants_patch as wvp

        with patch(
            "src.patches.window_variants_patch.get_selected_variant_id",
            return_value="pc_local",
        ):
            spec = wvp.resolve_audio_capture_spec()
        self.assertEqual(spec.factory_process_name, "HTGame.exe")
        self.assertTrue(spec.include_process_tree)

    def test_a_third_variant_needs_only_a_config_row(self):
        """Future browser variant: several processes, zero listener changes."""
        from src.patches import window_variants_patch as wvp

        browser = {
            "id": "pc_browser",
            "exe": ["chrome.exe"],
            "audio_capture": {
                "kind": "process_loopback",
                "process_names": ["chrome.exe", "msedge.exe"],
                "include_process_tree": True,
            },
        }
        with (
            patch.dict(wvp._VARIANT_BY_ID, {"pc_browser": browser}),
            patch(
                "src.patches.window_variants_patch.get_selected_variant_id",
                return_value="pc_browser",
            ),
        ):
            spec = wvp.resolve_audio_capture_spec()
        self.assertEqual(spec.factory_process_name, ("chrome.exe", "msedge.exe"))


class AudioCaptureSpecTests(unittest.TestCase):
    def test_single_name_keeps_the_existing_factory_signature(self):
        self.assertEqual(_spec("HTGame.exe").factory_process_name, "HTGame.exe")

    def test_multiple_names_are_passed_as_a_collection(self):
        names = ("chrome.exe", "msedge.exe")
        self.assertEqual(_spec(*names).factory_process_name, names)

    def test_empty_spec_never_fools_the_reconcile_check(self):
        self.assertEqual(_spec().factory_process_name, "")


class SoundListenerReconcileTests(unittest.TestCase):
    @staticmethod
    def _listener(provider=None, process_name="HTGame.exe"):
        from src.sound_trigger.SoundListener import SoundListener

        listener = object.__new__(SoundListener)
        listener.process_name = process_name
        listener._requested_process_name = process_name
        listener._capture_spec_provider = provider
        listener._capture = Mock()
        listener._log_gate = Mock()
        listener._running = False
        listener._stop_event = None
        listener._listener_thread = None
        return listener

    def test_lost_switch_event_self_heals_on_the_next_loop_iteration(self):
        target = ["HTGame.exe"]
        listener = self._listener(lambda: _spec(target[0]))

        listener._apply_requested_process_name()
        self.assertEqual(listener.process_name, "HTGame.exe")

        target[0] = "NTECloudGame.exe"  # variant switched, event never arrived
        capture = listener._capture
        listener._apply_requested_process_name()

        self.assertEqual(listener.process_name, "NTECloudGame.exe")
        capture.stop.assert_called_once()
        self.assertIsNone(listener._capture)

    def test_stale_setup_is_corrected_before_the_thread_starts(self):
        """The 22:22 race: enter() happened before the cloud variant settled."""
        target = ["HTGame.exe"]
        listener = self._listener(lambda: _spec(target[0]))

        target[0] = "NTECloudGame.exe"
        listener.apply_spec_now()

        self.assertEqual(listener.process_name, "NTECloudGame.exe")

    def test_provider_failure_keeps_the_current_target(self):
        provider = Mock(side_effect=RuntimeError("config not ready"))
        listener = self._listener(provider)
        capture = listener._capture

        listener._apply_requested_process_name()

        self.assertEqual(listener.process_name, "HTGame.exe")
        self.assertIs(listener._capture, capture)
        capture.stop.assert_not_called()
        listener._log_gate.warning.assert_called_once()

    def test_apply_spec_now_is_inert_while_the_listener_thread_runs(self):
        listener = self._listener(lambda: _spec("NTECloudGame.exe"))
        listener._running = True
        listener._stop_event = threading.Event()
        listener._listener_thread = Mock(is_alive=Mock(return_value=True))

        with patch.object(listener, "_apply_requested_process_name") as apply:
            listener.apply_spec_now()

        apply.assert_not_called()

    def test_listener_without_provider_still_uses_the_legacy_hint(self):
        listener = self._listener(None)
        capture = listener._capture

        listener.request_process_name("NTECloudGame.exe")
        listener._apply_requested_process_name()

        self.assertEqual(listener.process_name, "NTECloudGame.exe")
        capture.stop.assert_called_once()


class VariantChoiceRebindTests(unittest.TestCase):
    """The user's Choose Window choice drives the target, with no event hook.

    These use the real ``resolve_audio_capture_spec`` resolver. The listener
    receives frames in steady state, so a rebind here proves the switch is
    detected by spec comparison rather than by data starvation.
    """

    @staticmethod
    def _listener(process_name="HTGame.exe"):
        from src.patches import window_variants_patch as wvp
        from src.sound_trigger.SoundListener import SoundListener

        listener = object.__new__(SoundListener)
        listener.process_name = process_name
        listener._requested_process_name = process_name
        listener._capture_spec_provider = wvp.resolve_audio_capture_spec
        listener._bound_spec = None
        listener._capture = Mock()
        listener._log_gate = Mock()
        listener._running = False
        listener._stop_event = None
        listener._listener_thread = None
        return listener

    @staticmethod
    def _select(variant_id):
        return patch(
            "src.patches.window_variants_patch.get_selected_variant_id",
            return_value=variant_id,
        )

    def test_default_local_binding_stays_quiet_while_the_choice_is_unchanged(self):
        listener = self._listener()
        with self._select("pc_local"):
            listener._apply_requested_process_name()
            self.assertEqual(listener.process_name, "HTGame.exe")
            listener._capture.stop.assert_not_called()

            capture = listener._capture
            for _ in range(10):  # steady state: frames keep arriving
                listener._apply_requested_process_name()

            self.assertIs(listener._capture, capture)
            capture.stop.assert_not_called()
            listener._log_gate.info.assert_called_once()

    def test_switching_to_cloud_rebinds_even_while_local_audio_flows(self):
        listener = self._listener()
        with self._select("pc_local"):
            listener._apply_requested_process_name()
        capture = listener._capture

        with self._select("pc_cloud"):
            listener._apply_requested_process_name()

        self.assertEqual(listener.process_name, "NTECloudGame.exe")
        capture.stop.assert_called_once()
        self.assertIsNone(listener._capture)

    def test_switching_back_to_local_rebinds_again(self):
        listener = self._listener()
        with self._select("pc_cloud"):
            listener._apply_requested_process_name()
        self.assertEqual(listener.process_name, "NTECloudGame.exe")

        capture = Mock()  # loopback was rebuilt after the first rebind
        listener._capture = capture
        with self._select("pc_local"):
            listener._apply_requested_process_name()
        self.assertEqual(listener.process_name, "HTGame.exe")
        capture.stop.assert_called_once()

    def test_a_changed_spec_beyond_the_first_process_name_also_rebinds(self):
        """Future variants (multi-process browser, no-tree) stay covered."""
        current = [_spec("HTGame.exe")]
        listener = self._listener()
        listener._capture_spec_provider = lambda: current[0]
        listener._apply_requested_process_name()
        capture = listener._capture

        current[0] = AudioCaptureSpec(
            process_names=("chrome.exe", "msedge.exe"),
            include_process_tree=False,
        )
        listener._apply_requested_process_name()

        self.assertEqual(listener.process_name, ("chrome.exe", "msedge.exe"))
        capture.stop.assert_called_once()


class SoundCombatContextEnterTests(unittest.TestCase):
    def test_enter_reconciles_before_starting_the_listener(self):
        from src.sound_trigger.SoundCombatContext import SoundCombatContext

        with patch.object(SoundCombatContext, "_instance", None):
            context = SoundCombatContext()
        listener = Mock()
        listener.start.return_value = True
        context._is_active = True
        context._listener = listener

        self.assertTrue(context.enter())

        listener.apply_spec_now.assert_called_once()
        listener.start.assert_called_once()
        # No event-driven hook writes a hint into the listener any more.
        listener.request_process_name.assert_not_called()


if __name__ == "__main__":
    unittest.main()
