"""Exercise the actual upstream Runtime/Game/Link with a simulated MelonLoader peer; no game or SDK."""

import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from autopilot import bridge, engines
from autopilot.kit.harness import Harness, Option, Status
from autopilot.schedule_i import AdapterError
from autopilot.schedule_i_harness import CallbackClient, LocalChoice, RestrictedLink, main, run_harness


class SimulatedMelon:
    """Real Link request/response envelopes, simulated camera updates and owned input."""

    def __init__(self, cancel=None, fail_input=False):
        self.yaw, self.held, self.events = 0.0, set(), []
        self.begun, self.cancel, self.fail_input = time.monotonic(), cancel, fail_input
        self.link = bridge.Link(self.send)
        self.link.loopback = True
        self.link.feed({"type": "hello", "protocol": 1, "engine": "schedule_i", "adapter": "melonloader",
                        "source_export": False, "capabilities": list(bridge.SCHEDULE_I_OPS),
                        "input": {"keys": True}, "features": ["timed_hold", "schedule_i_runtime"]})

    def send(self, frame):
        self.events.append(frame)
        if frame["type"] == "stop":
            self.held.clear()
            return
        if frame["type"] != "request":
            return
        op, args = frame["op"], frame.get("args") or {}
        result = {}
        if op == "pose":
            result = {"yaw": self.yaw, "pitch": 0, "at": {"X": 0, "Y": 0, "Z": 100},
                      "eye": {"X": 0, "Y": 0, "Z": 160},
                      "pose_source": "camera_ancestor_character_controller_candidate"}
        elif op == "context":
            result = {"character": "runtime:1:1", "control_enabled": True, "input": {"keys": True}}
        elif op == "key":
            key, mode = args.get("key"), args.get("mode")
            if mode == "release_all":
                self.held.clear()
            elif mode == "release":
                self.held.discard(key)
            elif self.fail_input:
                self.held.clear()
                self.link.feed({"type": "response", "id": frame["id"], "ok": False,
                                "status": 400, "error": "simulated focus guard"})
                return
            elif key == "MouseX":
                self.yaw += args["value"] / 10
            elif mode == "hold":
                self.held.add(key)
                if self.cancel is not None:
                    self.cancel.set()
        self.link.feed({"type": "response", "id": frame["id"], "ok": True,
                        "result": result, "t": time.monotonic() - self.begun})


class CameraGoal(Harness):
    goal, tick_hz, tempo = "Observe camera yaw reaching eight degrees", 40, 0

    def observe(self, game):
        return game.request("pose")

    def status(self, obs):
        return Status("done" if obs["yaw"] >= 8 else "running", evidence={"observed_yaw": obs["yaw"]})

    def options(self, obs):
        def turn(counts):
            def execute(game, _):
                game.mouse(right=counts)
                yield
                return True
            return execute
        return [Option("right", "Turn toward the target", turn(40)),
                Option("left", "Turn away from the target", turn(-40))]


class HarnessIntegrationTests(unittest.TestCase):
    def test_profile_is_registered_without_fabricating_editor_capabilities(self):
        self.assertEqual(engines.for_hello({"engine": "schedule_i"}).name, "schedule_i")
        self.assertEqual(set(bridge.required_ops({"engine": "schedule_i"})), set(bridge.SCHEDULE_I_OPS))
        self.assertFalse({"rc", "codex", "ui", "screen", "watch"} & set(bridge.SCHEDULE_I_OPS))
        self.assertIsNone(engines.get("schedule_i").project_at("Schedule I.exe"))

    def test_actual_runtime_observes_chooses_executes_and_reaches_observed_goal(self):
        peer, choices = SimulatedMelon(), []
        def choose(state, criteria):
            choices.append((state["situation"]["yaw"], set(criteria)))
            return "right" if state["situation"]["yaw"] < 8 else "left"
        with tempfile.TemporaryDirectory() as folder:
            trace = Path(folder) / "trace.jsonl"
            result = run_harness(peer.link, CameraGoal(), trace, choose=choose, control=True, max_seconds=2)
            records = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual(result["status"]["state"], "done")
        self.assertGreaterEqual(result["decisions"], 1)
        self.assertTrue(choices)
        self.assertGreaterEqual(result["status"]["evidence"]["observed_yaw"], 8)
        self.assertEqual({"right", "left"}, choices[0][1])
        self.assertTrue({"start", "obs", "end"} <= {e["kind"] for e in records})
        self.assertFalse(result["source_export"] or result["telemetry"] or peer.held)
        self.assertTrue(any(e["type"] == "stop" for e in peer.events))

    def test_cancel_and_async_input_error_release_via_actual_runtime(self):
        class Hold(Harness):
            tick_hz = 40
            def observe(self, game):
                return game.request("pose")
            def options(self, obs):
                def execute(game, _):
                    game.hold("W")
                    while True:
                        yield
                return [Option("hold", "Hold one bounded key", execute)]
        for fail in (False, True):
            cancel = threading.Event()
            peer = SimulatedMelon(cancel=None if fail else cancel, fail_input=fail)
            with tempfile.TemporaryDirectory() as folder:
                result = run_harness(peer.link, Hold(), Path(folder) / "trace.jsonl", single_option=True,
                                     control=True, max_seconds=1, cancel=cancel)
            self.assertEqual(result["reason"], "stopped")
            self.assertFalse(peer.held)
            self.assertTrue(any(e["type"] == "stop" for e in peer.events))

    def test_explicit_decision_route_is_required(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            run_harness(SimulatedMelon().link, CameraGoal(), "unused.jsonl")

    def test_local_decision_rejects_unoffered_nonfinite_and_zero_probabilities(self):
        question = {"pick": LocalChoice("choose", {"a": "A", "b": "B"})}
        for answer in ("unknown", {"a": float("nan")}, {"a": True}, {"a": -1}, {"a": 0}, {"a": 2}):
            with self.subTest(answer=answer), self.assertRaises(AdapterError):
                CallbackClient(lambda *_: answer).system_one(state={}, questions=question)

    def test_restricted_link_checks_peer_and_rejects_privileged_operations(self):
        peer = SimulatedMelon()
        bounded = RestrictedLink(peer.link)
        for op, args in (("rc", {}), ("session", {"action": "restart"}),
                         ("key", {"key": "W", "mode": "press"})):
            with self.assertRaises(AdapterError):
                bounded.request(op, args)
        self.assertFalse(peer.events)
        peer.link.loopback = False
        with self.assertRaises(AdapterError):
            RestrictedLink(peer.link)

    def test_camera_only_is_never_a_fabricated_player_pose(self):
        from autopilot.kit.game import Game, GameError
        peer = SimulatedMelon()
        game = Game(peer.link)
        game.request = lambda *_: {"eye": {"X": 1, "Y": 2, "Z": 3}, "yaw": 10}
        with self.assertRaises(GameError):
            game.pose()
        game._pool.shutdown(wait=False)

    def test_default_cli_is_preflight_without_game_or_model_connection(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main([]), 0)
        report = json.loads(output.getvalue())
        self.assertFalse(report["game_touched"] or report["source_export"] or report["telemetry"])
        self.assertIsNone(report["automatic_provider"])


if __name__ == "__main__":
    unittest.main()
