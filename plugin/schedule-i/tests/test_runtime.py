"""Offline tests: no Windows handles, game, SDK, model, network or upstream pytest fixtures."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autopilot.schedule_i import AdapterError, RuntimeAdapter, ScheduleIGame, main, validate_frames


class Fake:
    pid = 123

    def __init__(self):
        self.now, self.events = 0.0, []
        self.focus, self.stop = True, False
        self.on_sleep = lambda: None
        self.fail_down = False
        self.fail_up = set()

    def focused(self):
        return self.focus

    def emergency_stop(self):
        return self.stop

    def emit(self, key, down):
        self.events.append((key, down))
        if (down and self.fail_down) or (not down and key in self.fail_up):
            raise AdapterError("injected SendInput failure")

    def clock(self):
        return self.now

    def sleep(self, dt):
        self.now += dt
        self.on_sleep()

    def adapter(self, **kwargs):
        return RuntimeAdapter(self, clock=self.clock, sleep=self.sleep, **kwargs)


def key(name="W", mode="press", duration=0.1):
    return {"type": "request", "id": 7, "op": "key",
            "args": {"key": name, "mode": mode, "seconds": duration}}


class RuntimeTests(unittest.TestCase):
    def test_protocol_envelope_and_timed_press(self):
        b = Fake()
        with b.adapter() as a:
            response = a.frame(key())
            self.assertEqual(response["id"], 7)
            self.assertTrue(response["ok"])
        self.assertEqual(b.events, [("W", True), ("W", False)])
        self.assertAlmostEqual(b.now, 0.1)

    def test_simultaneous_hold_and_expiry(self):
        b = Fake()
        with b.adapter() as a:
            a.request("key", {"key": "W", "mode": "hold", "seconds": 0.2})
            a.request("key", {"key": "LeftShift", "mode": "hold", "seconds": 0.1})
            a.request("wait", {"seconds": 0.3})
            self.assertFalse(a.held)
        self.assertEqual(b.events[-2:], [("LeftShift", False), ("W", False)])

    def test_failure_paths_release_inputs(self):
        for failure in ("focus", "emergency", "cancel", "budget", "interrupt"):
            with self.subTest(failure=failure):
                b, cancel = Fake(), threading.Event()
                def fail():
                    if failure == "focus":
                        b.focus = False
                    elif failure == "emergency":
                        b.stop = True
                    elif failure == "cancel":
                        cancel.set()
                    elif failure == "budget":
                        b.now = 60
                    else:
                        raise KeyboardInterrupt
                b.on_sleep = fail
                with self.assertRaises((AdapterError, KeyboardInterrupt)):
                    with b.adapter(cancel=cancel) as a:
                        a.request("key", {"key": "W", "mode": "press", "seconds": 1})
                self.assertEqual(b.events, [("W", True), ("W", False)])

    def test_partial_send_failure_still_releases(self):
        b = Fake()
        b.fail_down = True
        with self.assertRaises(AdapterError):
            b.adapter().request("key", {"key": "E"})
        self.assertEqual(b.events, [("E", True), ("E", False)])

    def test_release_failure_does_not_skip_other_keys(self):
        b = Fake()
        a = b.adapter()
        for name in ("W", "A"):
            a.request("key", {"key": name, "mode": "hold", "seconds": 1})
        b.fail_up.add("W")
        with self.assertRaisesRegex(AdapterError, "release failed"):
            a.close()
        self.assertIn(("A", False), b.events)
        self.assertIn("W", a.held)

    def test_invalid_request_cancels_prior_hold(self):
        b = Fake()
        a = b.adapter()
        a.request("key", {"key": "W", "mode": "hold", "seconds": 1})
        with self.assertRaises(ValueError):
            a.request("rc", {"verb": "call"})
        self.assertEqual(b.events[-1], ("W", False))

    def test_malformed_envelope_cancels_prior_hold_without_new_down(self):
        for frame in ({"op": "key", "args": {"key": "A"}}, None,
                      {"type": "request", "op": "key", "args": {"key": []}}):
            b = Fake()
            a = b.adapter()
            a.request("key", {"key": "W", "mode": "hold", "seconds": 1})
            with self.assertRaises(ValueError):
                a.frame(frame)
            self.assertEqual(b.events, [("W", True), ("W", False)])

    def test_invalid_or_unbounded_replays_rejected_before_input(self):
        invalid = [[], [key(duration=float("nan"))], [key(duration=float("inf"))],
                   [key(duration=-1)], [key(duration=True)], [key(mode="hold", duration=0)],
                   [key(duration=6)], [key("F8")], [key(mode="toggle")], [key()] * 501,
                   [{"type": "request", "op": "rc"}], [{"op": "key"}],
                   [{"type": "request", "op": "session", "args": {"action": "restart"}}],
                   [{"type": "request", "op": "wait", "args": {"seconds": 60}}] * 2]
        for frames in invalid:
            with self.subTest(frames=str(frames)[:100]), self.assertRaises(ValueError):
                validate_frames(frames)

    def test_observer_is_fresh_and_bound_to_pid(self):
        b = Fake()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            with b.adapter(snapshot=path) as a:
                path.write_text(json.dumps({"protocol": 1, "pid": 123, "unix_ms": time.time() * 1000,
                                            "camera": {"eye": {"X": 100, "Y": 0, "Z": 200}}}))
                self.assertEqual(a.request("pose")["camera"]["eye"]["X"], 100)
            for pid, stamp in ((999, time.time() * 1000), (123, 0), (123, "bad")):
                path.write_text(json.dumps({"protocol": 1, "pid": pid, "unix_ms": stamp}))
                with self.assertRaises(AdapterError):
                    b.adapter(snapshot=path).request("pose")
            path.write_text("[]")
            with self.assertRaisesRegex(AdapterError, "must be an object"):
                b.adapter(snapshot=path).request("pose")

    def test_preview_does_not_construct_windows_backend(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "replay.json"
            path.write_text(json.dumps([key()]))
            with patch("autopilot.schedule_i.WindowsInput", side_effect=AssertionError("live backend touched")):
                self.assertEqual(main([str(path)]), 0)

    def test_session_stop_releases_without_exiting_game(self):
        b = Fake()
        a = b.adapter()
        a.request("key", {"key": "LeftMouseButton", "mode": "hold", "seconds": 1})
        a.request("session", {"action": "stop"})
        self.assertTrue(a.closed)
        self.assertEqual(b.events[-1], ("LeftMouseButton", False))

    def test_kit_style_harness(self):
        b = Fake()
        with ScheduleIGame(b, clock=b.clock, sleep=b.sleep) as game:
            self.assertEqual(game.context()["game"], "Schedule I")
            game.hold("W", seconds=0.5)
            game.press("E")
            game.release("W")
            game.release_all()
        self.assertEqual(b.events, [("W", True), ("E", True), ("E", False), ("W", False)])

    def test_live_lease_watchdog_releases_when_harness_blocks(self):
        b = Fake()
        b.watchdog = True
        with RuntimeAdapter(b) as a:
            a.request("key", {"key": "W", "mode": "hold", "seconds": 0.05})
            deadline = time.monotonic() + 1
            while ("W", False) not in b.events and time.monotonic() < deadline:
                threading.Event().wait(0.01)
            self.assertIn(("W", False), b.events)

    def test_live_watchdog_releases_on_focus_loss_without_harness_tick(self):
        b = Fake()
        b.watchdog = True
        with RuntimeAdapter(b) as a:
            a.request("key", {"key": "W", "mode": "hold", "seconds": 5})
            b.focus = False
            deadline = time.monotonic() + 1
            while ("W", False) not in b.events and time.monotonic() < deadline:
                threading.Event().wait(0.01)
            self.assertIn(("W", False), b.events)
            self.assertIsInstance(a.fault, AdapterError)


if __name__ == "__main__":
    unittest.main()
