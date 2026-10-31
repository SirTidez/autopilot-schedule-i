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

from .schedule_i import AdapterError, seconds

OPS = frozenset(("context", "session", "ping", "key", "pose", "find", "transforms", "view", "movement"))


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

    def __init__(self, choose):
        if not callable(choose):
            raise ValueError("choose must be an explicitly supplied callable")
        self.choose = choose

    def system_one(self, *, state, questions):
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


class RestrictedLink:
    """Typed player-profile boundary around an upstream Link, including fire-and-forget failures."""

    def __init__(self, link, *, control=False):
        hello = link.hello or {}
        if (hello.get("protocol") != 1 or hello.get("engine") != "schedule_i"
                or hello.get("adapter") != "melonloader" or hello.get("source_export") is not False
                or getattr(link, "loopback", None) is not True):
            raise AdapterError("Expected the explicit loopback Schedule I MelonLoader protocol-1 bridge")
        capabilities = hello.get("capabilities")
        if not isinstance(capabilities, list) or not OPS <= set(capabilities):
            raise AdapterError("MelonLoader bridge is missing required typed runtime capabilities")
        if control and (hello.get("input") or {}).get("keys") is not True:
            raise AdapterError("Game control requires the separate -AutopilotScheduleIControl launch flag")
        self.raw, self.control = link, control
        self.requests = 0
        self.started = time.monotonic()
        self.lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def check(self):
        if self.raw.closed:
            raise AdapterError("MelonLoader bridge disconnected")
        if getattr(self.raw, "async_errors", None):
            raise AdapterError("MelonLoader input/request failed: " + str(self.raw.async_errors[-1])[:200])
        if time.monotonic() - self.started >= 60:
            raise AdapterError("Schedule I harness wall-time budget expired")

    def validate(self, op, args):
        # Cleanup is always permitted, including after asynchronous errors or expired budgets.
        if op == "key" and args == {"mode": "release_all"}:
            return
        self.check()
        if op not in OPS or not isinstance(args, dict):
            raise AdapterError("Operation is outside the typed Schedule I runtime profile")
        if op == "session" and args.get("action", "status") not in ("status", "stop"):
            raise AdapterError("No scene launch, restart, reset or game exit in the retail profile")
        if op == "key" and args.get("mode") != "release_all" and not self.control:
            raise AdapterError("Control was not explicitly enabled for this harness")
        with self.lock:
            self.requests += 1
            if self.requests > 2000:
                raise AdapterError("Schedule I harness request budget expired")

    def request(self, op, args=None, timeout=None):
        args = {} if args is None else args
        self.validate(op, args)
        return self.raw.request(op, args, timeout=min(2.0, timeout or 2.0))

    def post(self, op, args=None):
        args = {} if args is None else args
        self.validate(op, args)
        self.raw.post(op, args)

    def stop(self):
        # Cleanup bypasses expired budgets and read-only controls, so every exit can release.
        for frame in ({"type": "request", "id": "schedule-i-final-release", "op": "key",
                       "args": {"mode": "release_all"}}, {"type": "stop"}):
            try:
                self.raw.send(frame)
            except Exception:
                pass  # Native .6s leases and the disconnect watchdog are the independent fallback.


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
    bounded = RestrictedLink(link, control=control)
    cancel = cancel if cancel is not None else threading.Event()
    game = Game(bounded, codex=None)
    if choose is not None:
        client = CallbackClient(choose)
    stop_fault = []

    def stopped():
        if cancel.is_set():
            return True
        try:
            bounded.check()
        except AdapterError as e:
            stop_fault.append(str(e))
            return True
        return False

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
    try:
        runtime = Runtime(game, harness, str(trace_path), client=client, max_seconds=max_seconds,
                          stop=stopped, telemetry=None, choice_factory=LocalChoice if choose is not None else None)
        result = runtime.run()
        result["adapter"] = "schedule_i_melonloader"
        result["decision_route"] = "local_callback" if choose is not None else "single_option" if single_option else "explicit_client"
        result["telemetry"] = False
        result["source_export"] = False
        result["safety_faults"] = stop_fault
        return result
    finally:
        harness.options = original_options
        try:
            game.release_all()
        except Exception as e:
            stop_fault.append("Final release: " + str(e)[:200])
        finally:
            bounded.stop()
            game._pool.shutdown(wait=False, cancel_futures=True)
            if runtime is not None:
                runtime.trace.close()
                runtime.pool.shutdown(wait=False, cancel_futures=True)


def preflight():
    return {"mode": "preflight", "engine": "schedule_i", "adapter": "melonloader",
            "runtime": "upstream Runtime/Harness/Option/Step", "protocol": 1, "game_touched": False,
            "required_ops": sorted(OPS), "required_runtimes": ["IL2CPP beta", "Mono alternate-beta"],
            "transport_dependencies": {name: importlib.util.find_spec(name) is not None
                                       for name in ("requests", "websockets")},
            "decision_routes": ["explicit local callback", "explicit SDK client", "single-option harness"],
            "automatic_provider": None, "source_export": False, "telemetry": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reviewed-code", action="store_true", help="explicitly permit loading the supplied local harness")
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
            RestrictedLink.stop(SimpleNamespace(raw=link))
            link.close("Schedule I harness finished")
            link.server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
