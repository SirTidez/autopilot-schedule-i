"""Run reviewed Schedule I harnesses through the actual upstream Runtime, without automatic model routing.

Default CLI is preflight only. Live use requires an explicitly enabled MelonLoader bridge, --execute and
--reviewed-code. No source project, cloud credentials, account discovery, telemetry or persistent service.
"""

import argparse
import copy
import importlib.util
import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from .schedule_i import MAX_MOUSE_DELTA, MAX_MOUSE_TOTAL, MOUSE_AXES, AdapterError, seconds

REQUIRED_OPS = frozenset(("context", "session", "ping", "key", "pose", "find", "transforms", "view", "movement"))
OPTIONAL_OPS = frozenset(("ui",))  # typed hierarchy reads only; never ui_act
OPS = REQUIRED_OPS | OPTIONAL_OPS


@dataclass
class LocalChoice:
    """Local callback question, not a substitute for the TypeSafe SDK's wire/API objects."""

    instructions: str
    criteria: dict

    def model_dump(self, **_):
        return {"kind": "explicit_local_callback", "instructions": self.instructions, "criteria": self.criteria}


class CallbackClient:
    """Adapt an explicitly supplied Python decision function to Runtime's decision-client contract.

    choose(state, criteria) returns an offered name or a name->probability mapping. No imports,
    sockets, model fallback or provider selection occur here; the caller owns its callback.
    """

    def __init__(self, choose, *, cancel=None, on_error=None):
        if not callable(choose):
            raise ValueError("choose must be an explicitly supplied callable")
        self.choose = choose
        self.cancel, self.on_error = cancel, on_error

    def system_one(self, *, state, questions):
        try:
            return self._system_one(state=state, questions=questions)
        except BaseException as e:
            if self.cancel is not None:
                self.cancel.set()
            if self.on_error is not None:
                self.on_error("Decision callback: " + str(e)[:200])
            raise

    def _system_one(self, *, state, questions):
        if self.cancel is not None and self.cancel.is_set():
            raise AdapterError("Decision callback cancelled")
        if set(questions) != {"pick"} or not isinstance(questions["pick"], LocalChoice):
            raise AdapterError("Extra typed questions require a separately configured SDK decision client")
        criteria = questions["pick"].criteria
        answer = self.choose(copy.deepcopy(state), copy.deepcopy(criteria))
        if isinstance(answer, str):
            answer = {answer: 1.0}
        if not isinstance(answer, dict) or not answer or set(answer) - set(criteria):
            raise AdapterError("Decision must name only currently offered options")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               or not 0 <= v <= 1 for v in answer.values()):
            raise AdapterError("Decision probabilities must be finite numbers between zero and one")
        total = sum(answer.values())
        if total <= 0:
            raise AdapterError("Decision has no positive probability")
        probabilities = {name: answer.get(name, 0) / total for name in criteria}
        pick = SimpleNamespace(probabilities=probabilities, choice=max(probabilities, key=probabilities.get))
        return SimpleNamespace(choices={"pick": pick}, nouls={}, scores={}, raw={"local_callback": probabilities})


def _stop_peer(link):
    """Cleanup requires neither a working request/response loop nor permission to issue new actions."""
    for frame in ({"type": "request", "id": "schedule-i-final-release", "op": "key",
                   "args": {"mode": "release_all"}}, {"type": "stop"}):
        try:
            link.send(frame)
        except Exception:
            pass  # Native leases/disconnect watchdog remain the independent fallback.


class RestrictedLink:
    """Typed player-profile boundary around an upstream Link, including fire-and-forget failures."""

    def __init__(self, link, *, control=False, cancel=None, max_seconds=60):
        hello = link.hello or {}
        if (hello.get("protocol") != 1 or hello.get("engine") != "schedule_i"
                or hello.get("adapter") != "melonloader" or hello.get("source_export") is not False
                or getattr(link, "loopback", None) is not True):
            raise AdapterError("Expected the explicit loopback Schedule I MelonLoader protocol-1 bridge")
        capabilities = hello.get("capabilities")
        if (not isinstance(capabilities, list) or not all(isinstance(value, str) for value in capabilities)
                or not REQUIRED_OPS <= set(capabilities)):
            raise AdapterError("MelonLoader bridge is missing required typed runtime capabilities")
        if control and (hello.get("input") or {}).get("keys") is not True:
            raise AdapterError("Game control requires the separate -AutopilotScheduleIControl launch flag")
        self.raw, self.control = link, control
        self.capabilities = frozenset(capabilities)
        self.cancel = cancel if cancel is not None else threading.Event()
        self.revoked = threading.Event()
        self.requests = 0
        self.mouse_total = 0
        self.started = time.monotonic()
        self.max_seconds = seconds(max_seconds, 60, positive=True)
        self.lock = threading.RLock()

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def check(self):
        if self.revoked.is_set() or self.cancel.is_set():
            raise AdapterError("Schedule I harness cancelled or stopped")
        if self.raw.closed:
            raise AdapterError("MelonLoader bridge disconnected")
        if getattr(self.raw, "async_errors", None):
            raise AdapterError("MelonLoader input/request failed: " + str(self.raw.async_errors[-1])[:200])
        if time.monotonic() - self.started >= self.max_seconds:
            raise AdapterError("Schedule I harness wall-time budget expired")

    def validate(self, op, args):
        # Cleanup is always permitted, including after asynchronous errors or expired budgets.
        if op == "key" and args == {"mode": "release_all"}:
            return
        self.check()
        if (not isinstance(op, str) or op not in OPS or op not in self.capabilities
                or not isinstance(args, dict)):
            raise AdapterError("Operation is outside the typed Schedule I runtime profile")
        if op == "session" and args.get("action", "status") not in ("status", "stop"):
            raise AdapterError("No scene launch, restart, reset or game exit in the retail profile")
        if op == "key" and args.get("mode") != "release_all" and not self.control:
            raise AdapterError("Control was not explicitly enabled for this harness")
    def frames(self, op, args):
        """Prevalidate and charge the entire logical action before any mouse chunks are emitted."""
        self.validate(op, args)
        frames = [(op, dict(args))]
        count = 0
        if op == "key" and args.get("key") in MOUSE_AXES:
            value = args.get("value", 1)
            if (args.get("mode", "press") != "press" or args.get("rate", False) or "seconds" in args
                    or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or abs(value) > MAX_MOUSE_TOTAL):
                raise AdapterError("Mouse action requires finite one-shot counts within the session budget")
            signed = int(math.copysign(math.floor(abs(value) + 0.5), value))
            count = abs(signed)
            if self.mouse_total + count > MAX_MOUSE_TOTAL:
                raise AdapterError("Schedule I harness mouse count budget expired")
            frames = []
            remaining = signed
            while remaining:
                part = max(-MAX_MOUSE_DELTA, min(MAX_MOUSE_DELTA, remaining))
                frames.append((op, dict(args, value=part)))
                remaining -= part
        cleanup = op == "key" and args == {"mode": "release_all"}
        if not cleanup:
            if self.requests + len(frames) > 2000:
                raise AdapterError("Schedule I harness request budget expired")
            self.requests += len(frames)
            self.mouse_total += count
        return frames

    def request(self, op, args=None, timeout=None):
        args = {} if args is None else args
        seconds(2.0 if timeout is None else timeout, 60, positive=True)
        with self.lock:
            frames = self.frames(op, args)
        result = {}
        for part_op, part_args in frames:
            if part_op == "key":
                # Serialize actions with revocation; reads may wait up to two seconds independently.
                with self.lock:
                    self.validate(part_op, part_args)
                    result = self.raw.request(part_op, part_args, timeout=min(2.0, timeout or 2.0))
            else:
                self.validate(part_op, part_args)
                result = self.raw.request(part_op, part_args, timeout=min(2.0, timeout or 2.0))
        return result

    def post(self, op, args=None):
        args = {} if args is None else args
        with self.lock:
            for part_op, part_args in self.frames(op, args):
                self.validate(part_op, part_args)
                self.raw.post(part_op, part_args)

    def stop(self):
        self.revoked.set()  # fail new operations immediately, even while another request is waiting
        with self.lock:
            _stop_peer(self.raw)


def run_harness(link, harness, trace_path, *, choose=None, client=None, single_option=False,
                control=False, max_seconds=30, cancel=None):
    """The real upstream Runtime/Harness/Option/Step loop on a constrained compiled-player Game.

    choose is an explicit local callback; client is an explicit caller-owned SDK-compatible client.
    Neither is auto-created. single_option=True explicitly allows upstream's model-free single-option
    shortcut. The caller must review harness/callback code; Python code is not a security sandbox.
    """
    from .kit.game import Game
    from .runtime import Runtime

    seconds(max_seconds, 60, positive=True)
    if sum((choose is not None, client is not None, single_option)) != 1:
        raise ValueError("Choose exactly one explicit decision route: choose, client or single_option")
    if getattr(harness, "start_map", None):
        raise AdapterError("Retail Schedule I harnesses cannot request scene loading or reset")
    cancel = cancel if cancel is not None else threading.Event()
    bounded = RestrictedLink(link, control=control, cancel=cancel, max_seconds=max_seconds)
    if cancel.is_set():
        bounded.stop()
        raise AdapterError("Schedule I harness cancelled before setup")
    game = Game(bounded, codex=None)
    stop_fault = []
    fault_lock = threading.Lock()
    watchdog_done = threading.Event()

    def note_fault(message):
        with fault_lock:
            if message not in stop_fault:
                stop_fault.append(message)

    if choose is not None:
        client = CallbackClient(choose, cancel=cancel, on_error=note_fault)

    def stopped():
        if cancel.is_set():
            return True
        try:
            bounded.check()
        except AdapterError as e:
            note_fault(str(e))
            return True
        return False

    def watch_cleanup():
        # This also runs during setup/observe/executor code that hasn't yielded to Runtime yet.
        while not watchdog_done.wait(0.02):
            try:
                bounded.check()
            except AdapterError as e:
                note_fault(str(e))
                cancel.set()
                bounded.stop()
                return

    # Requiring one option is a contract check, not a fallback that invents model choices.
    original_options = harness.options
    if single_option:
        def options(obs):
            value = original_options(obs)
            if len(value) > 1:
                cancel.set()
                raise AdapterError("Multiple legal options need an explicitly configured decision client")
            return value
        harness.options = options
    runtime = None
    worker = threading.Thread(target=watch_cleanup, name="schedule-i-harness-cleanup", daemon=True)
    worker.start()
    try:
        if control:
            context = bounded.request("context")
            if context.get("control_enabled") is not True:
                raise AdapterError("MelonLoader control is disabled or latched off; no setup actions were run")
        runtime = Runtime(game, harness, str(trace_path), client=client, max_seconds=max_seconds,
                          stop=stopped, telemetry=None, choice_factory=LocalChoice if choose is not None else None)
        result = runtime.run()
        result["adapter"] = "schedule_i_melonloader"
        result["decision_route"] = ("local_callback" if choose is not None
                                    else "single_option" if single_option else "explicit_client")
        result["telemetry"] = False
        result["source_export"] = False
        result["safety_faults"] = stop_fault
        return result
    finally:
        watchdog_done.set()
        bounded.stop()  # revoke before teardown workers can issue further actions
        harness.options = original_options
        try:
            game.release_all()
        except Exception as e:
            note_fault("Final release: " + str(e)[:200])
        finally:
            game._pool.shutdown(wait=False, cancel_futures=True)
            if runtime is not None:
                runtime.trace.close()
                runtime.pool.shutdown(wait=False, cancel_futures=True)
            worker.join(timeout=2.1)


def preflight():
    return {"mode": "preflight", "engine": "schedule_i", "adapter": "melonloader",
            "runtime": "upstream Runtime/Harness/Option/Step", "protocol": 1, "game_touched": False,
            "required_ops": sorted(REQUIRED_OPS), "optional_ops": sorted(OPTIONAL_OPS),
            "required_runtimes": ["IL2CPP beta", "Mono alternate-beta"],
            "transport_dependencies": {name: importlib.util.find_spec(name) is not None
                                       for name in ("requests", "websockets")},
            "decision_routes": ["explicit local callback", "explicit SDK client", "single-option harness"],
            "automatic_provider": None, "source_export": False, "telemetry": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reviewed-code", action="store_true",
                        help="explicitly permit loading the supplied local harness")
    parser.add_argument("--harness", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--control", action="store_true", help="allow inputs; game must separately opt in")
    parser.add_argument("--decision", choices=("single-option", "harness-callback"), default="single-option")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(preflight(), indent=2))
        return 0
    if not args.reviewed_code or args.harness is None or args.trace is None:
        parser.error("--execute requires --reviewed-code, --harness and --trace")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024..65535")
    seconds(args.seconds, 60, positive=True)
    from . import bridge
    from .runtime import load_harness

    harness = load_harness(str(args.harness))
    choose = getattr(harness, "choose", None) if args.decision == "harness-callback" else None
    if args.decision == "harness-callback" and not callable(choose):
        parser.error("reviewed harness must define choose(state, criteria)")
    link = None
    try:
        # Explicit session-only loopback listener. Never announce/install it as a persistent agent service.
        link = bridge.serve_local(host="127.0.0.1", port=args.port, wait=60, role="schedule-i", announce_as=None)
        result = run_harness(link, harness, args.trace, choose=choose,
                             single_option=args.decision == "single-option", control=args.control,
                             max_seconds=args.seconds)
        print(json.dumps(result, indent=2))
        return 0 if (result.get("status") or {}).get("state") == "done" else 2
    except (AdapterError, KeyboardInterrupt, TimeoutError) as e:
        print("Stopped: " + str(e))
        return 2
    finally:
        if link is not None:
            _stop_peer(link)
            link.close("Schedule I harness finished")
            link.server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
