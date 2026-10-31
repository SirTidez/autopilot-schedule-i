"""Exercise the actual upstream Runtime/Game/Link with a simulated MelonLoader peer; no game or SDK."""

import ast
import contextlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

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
        self.assertFalse({"rc", "codex", "ui", "ui_act", "screen", "watch"} & set(bridge.SCHEDULE_I_OPS))
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

    def test_optional_ui_read_requires_advertisement_and_never_enables_actions(self):
        peer = SimulatedMelon()
        bounded = RestrictedLink(peer.link)
        with self.assertRaises(AdapterError):
            bounded.request("ui")
        peer.link.hello["capabilities"].append("ui")
        bounded = RestrictedLink(peer.link)
        bounded.request("ui", {"name": "Pack Rat"})
        self.assertEqual(peer.events[-1]["op"], "ui")
        with self.assertRaises(AdapterError):
            bounded.request("ui_act", {"action": "click"})

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

    def test_editor_routes_reject_before_account_memory_keys_feed_or_classification(self):
        # Execute the real entry functions without importing their provider-heavy module dependencies.
        path = Path(__file__).resolve().parents[3] / "autopilot" / "cli.py"
        source = ast.parse(path.read_text(encoding="utf-8"))
        wanted = {"_reject_schedule_i_editor", "run_prompt", "cmd_serve"}
        selected = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
        tripwire = Mock(side_effect=AssertionError("provider/account/source boundary crossed"))
        namespace = {"load_user_keys": tripwire, "told_kind": tripwire, "send_memory_state": tripwire,
                     "account": SimpleNamespace(send_state=tripwire, follow_preferences=tripwire)}
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
        link = SimpleNamespace(hello={"engine": " SCHEDULE_I "})
        feed = SimpleNamespace(start=tripwire)
        with self.assertRaisesRegex(RuntimeError, "editor/provider/account/source routes are disabled"):
            namespace["run_prompt"](link, "choose anything", feed=feed)
        with self.assertRaisesRegex(RuntimeError, "editor/provider/account/source routes are disabled"):
            namespace["cmd_serve"](link, SimpleNamespace())
        tripwire.assert_not_called()

    def test_normal_kit_wildcard_import_needs_no_sdk_and_constructs_real_types_when_requested(self):
        with patch.dict(sys.modules, {"typesafe_sdk": None}):
            namespace = {}
            exec("from autopilot.kit import *", namespace)
            self.assertIs(namespace["Harness"], Harness)
            self.assertTrue(callable(namespace["Choice"]))
            with self.assertRaises(ModuleNotFoundError):
                namespace["Choice"](criteria={"a": "A"})
        sdk = ModuleType("typesafe_sdk")
        class ActualChoice:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
        sdk.Choice = ActualChoice
        with patch.dict(sys.modules, {"typesafe_sdk": sdk}):
            value = namespace["Choice"](criteria={"a": "A"})
            self.assertIs(type(value), ActualChoice)
            self.assertIsInstance(value, namespace["Choice"])
            self.assertTrue(issubclass(ActualChoice, namespace["Choice"]))

    def test_precancel_never_runs_setup_or_issues_input(self):
        setup = Mock()
        class NeverSetup(Harness):
            def setup(self, game):
                setup()
                game.press("E")
        cancel = threading.Event()
        cancel.set()
        peer = SimulatedMelon()
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(AdapterError, "before setup"):
            run_harness(peer.link, NeverSetup(), Path(folder) / "trace.jsonl", single_option=True,
                        control=True, cancel=cancel)
        setup.assert_not_called()
        self.assertFalse(any(frame.get("args", {}).get("key") == "E" for frame in peer.events))
        self.assertTrue(any(frame["type"] == "stop" for frame in peer.events))

    def test_wrapper_rejects_cancelled_or_revoked_work_but_always_permits_cleanup(self):
        peer, cancel = SimulatedMelon(), threading.Event()
        bounded = RestrictedLink(peer.link, control=True, cancel=cancel)
        bounded.post("key", {"key": "W", "mode": "hold"})
        cancel.set()
        with self.assertRaises(AdapterError):
            bounded.post("key", {"key": "E", "mode": "press"})
        bounded.stop()
        cancel.clear()  # a caller cannot resurrect a revoked wrapper by clearing its event
        with self.assertRaises(AdapterError):
            bounded.request("pose")
        bounded.post("key", {"mode": "release_all"})
        self.assertFalse(peer.held)
        self.assertFalse(any(frame.get("args", {}).get("key") == "E" for frame in peer.events))

    def test_independent_cleanup_during_blocked_setup_on_cancel_or_deadline(self):
        for stop_kind in ("cancel", "deadline"):
            peer, cancel = SimulatedMelon(), threading.Event()
            started, unblock = threading.Event(), threading.Event()
            observations = []
            class SlowSetup(Harness):
                def setup(self, game):
                    game.hold("W")
                    started.set()
                    unblock.wait(1)
            def controller():
                started.wait(0.5)
                if stop_kind == "cancel":
                    cancel.set()
                deadline = time.monotonic() + 0.7
                while not any(e["type"] == "stop" for e in peer.events) and time.monotonic() < deadline:
                    time.sleep(0.005)
                observations.append((not peer.held, not unblock.is_set()))
                unblock.set()
            thread = threading.Thread(target=controller)
            thread.start()
            with tempfile.TemporaryDirectory() as folder:
                result = run_harness(peer.link, SlowSetup(), Path(folder) / "trace.jsonl", single_option=True,
                                     control=True, cancel=cancel, max_seconds=0.08 if stop_kind == "deadline" else 1)
            thread.join(1)
            self.assertEqual(observations, [(True, True)], stop_kind)
            self.assertEqual(result["reason"], "stopped")

    def test_invalid_local_decision_cancels_runtime_instead_of_retrying_forever(self):
        peer = SimulatedMelon()
        with tempfile.TemporaryDirectory() as folder:
            with patch("autopilot.runtime.decision_choice", side_effect=AssertionError("implicit SDK decision")):
                result = run_harness(peer.link, CameraGoal(), Path(folder) / "trace.jsonl",
                                     choose=lambda *_: "not_offered", control=True, max_seconds=1)
        self.assertEqual(result["reason"], "stopped")
        self.assertTrue(any("Decision callback" in fault for fault in result["safety_faults"]))
        self.assertEqual(peer.yaw, 0)
        self.assertFalse(peer.held)

    def test_large_mouse_action_is_prevalidated_and_split_into_bounded_counts(self):
        peer = SimulatedMelon()
        bounded = RestrictedLink(peer.link, control=True)
        bounded.post("key", {"key": "MouseX", "mode": "press", "value": 450.5})
        chunks = [e["args"]["value"] for e in peer.events if e.get("op") == "key"]
        self.assertEqual(chunks, [200, 200, 51])
        self.assertAlmostEqual(peer.yaw, 45.1)
        before = len(peer.events)
        for value in (4000, float("inf"), True, "5"):
            with self.assertRaises(AdapterError):
                bounded.post("key", {"key": "MouseX", "mode": "press", "value": value})
        self.assertEqual(len(peer.events), before)
        bounded.requests = 1999
        with self.assertRaises(AdapterError):
            bounded.post("key", {"key": "MouseX", "mode": "press", "value": -450})
        self.assertEqual(len(peer.events), before)

    def test_cancellation_between_mouse_chunks_stops_remaining_motion(self):
        cancel = threading.Event()
        class CancelAfterFirstChunk(SimulatedMelon):
            def send(self, frame):
                super().send(frame)
                if frame.get("args", {}).get("key") == "MouseX":
                    cancel.set()
        peer = CancelAfterFirstChunk()
        bounded = RestrictedLink(peer.link, control=True, cancel=cancel)
        with self.assertRaises(AdapterError):
            bounded.post("key", {"key": "MouseX", "mode": "press", "value": 600})
        self.assertEqual(peer.yaw, 20)


if __name__ == "__main__":
    unittest.main()
