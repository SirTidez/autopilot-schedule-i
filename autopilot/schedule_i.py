"""Personal Schedule I runtime adapter. Stdlib only; no agent, model, telemetry or server imports.

Consumes the upstream protocol-1 request envelope and FKey names for a deliberately small
subset. This is bounded local replay, not the upstream model-driven editor playtesting loop.
"""

import argparse
import ctypes
import json
import math
import os
import threading
import time
from pathlib import Path

MAX_SECONDS = 60.0
MAX_HOLD = 5.0
MAX_FRAMES = 500
MAX_MOUSE_DELTA = 200
MAX_MOUSE_TOTAL = 4000
MOUSE_AXES = ("MouseX", "MouseY")
KEYS = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
KEYS.update({name: 0x30 + i for i, name in enumerate(
    ("Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"))})
KEYS.update(SpaceBar=0x20, Enter=0x0D, Tab=0x09, Escape=0x1B, LeftShift=0xA0, LeftControl=0xA2)
MOUSE = {"LeftMouseButton": (0x0002, 0x0004), "RightMouseButton": (0x0008, 0x0010),
         "MiddleMouseButton": (0x0020, 0x0040)}


class AdapterError(RuntimeError):
    pass


def seconds(value, maximum, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("seconds must be a finite number")
    if value < 0 or value > maximum or (positive and value == 0):
        raise ValueError(f"seconds must be {'greater than zero and ' if positive else ''}at most {maximum}")
    return float(value)


def mouse_counts(value):
    """One relative axis delta, nearest integer count (half counts round away from zero)."""
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or abs(value) > MAX_MOUSE_DELTA):
        raise ValueError(f"mouse value must be finite numeric counts within +/-{MAX_MOUSE_DELTA}")
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def validate_frames(frames):
    """Validate the ENTIRE replay before acquiring a process or emitting any input."""
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError(f"replay must contain 1..{MAX_FRAMES} request frames")
    total = 0.0
    mouse_total = 0
    for frame in frames:
        if not isinstance(frame, dict) or frame.get("type") != "request":
            raise ValueError("expected upstream protocol-1 request envelope")
        args = frame.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("args must be an object")
        op = frame.get("op")
        if op == "wait":  # local extension; upstream timing is normally between requests
            total += seconds(args.get("seconds", 0), MAX_SECONDS)
        elif op == "key":
            mode = args.get("mode", "press")
            if mode not in ("press", "hold", "release", "release_all"):
                raise ValueError("unsupported key mode")
            if isinstance(args.get("key"), str) and args["key"] in MOUSE_AXES and mode != "release_all":
                if mode != "press" or args.get("rate", False) or "seconds" in args:
                    raise ValueError("mouse axes support one-shot press counts only; no hold/rate/seconds")
                mouse_total += abs(mouse_counts(args.get("value", 1)))
                continue
            if mode != "release_all" and (not isinstance(args.get("key"), str) or args["key"] not in KEYS | MOUSE):
                raise ValueError("unsupported FKey")
            if mode in ("press", "hold"):
                duration = seconds(args.get("seconds", 0.1 if mode == "press" else 0), MAX_HOLD, positive=True)
                if mode == "press":
                    total += duration
                if args.get("value", 1) != 1 or args.get("rate", False):
                    raise ValueError("only digital keys/buttons are supported")
        elif op not in ("ping", "context", "pose", "session"):
            raise ValueError(f"unsupported op: {op}; no rc, source export, scene loading or arbitrary calls")
        elif op == "session" and args.get("action", "status") not in ("status", "stop"):
            raise ValueError("runtime sessions only support status/stop; never launch or restart the game")
    if total > MAX_SECONDS:
        raise ValueError(f"replay exceeds {MAX_SECONDS} seconds")
    if mouse_total > MAX_MOUSE_TOTAL:
        raise ValueError(f"replay exceeds {MAX_MOUSE_TOTAL} relative mouse counts")
    return frames


class RuntimeAdapter:
    """Backend contract: focused(), emergency_stop(), emit(FKey, down). No focus stealing.

    Holds have <=5 second leases. Live backends get a 20ms independent watchdog.
    Call close() in a finally block (or use the context manager) for every live session.
    """

    def __init__(self, backend, *, cancel=None, snapshot=None, clock=time.monotonic, sleep=time.sleep):
        self.backend = backend
        self.cancel = cancel if cancel is not None else threading.Event()
        self.snapshot = Path(snapshot) if snapshot else None
        self.clock, self.sleep = clock, sleep
        self.held = {}
        self.mouse_total = 0
        self.started = clock()
        self.closed = False
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.fault = None
        # Live backends need an independent lease watchdog even when a harness blocks.
        # Pure simulated backends opt out, so offline tests can use a deterministic clock.
        if getattr(backend, "watchdog", False):
            self._worker = threading.Thread(target=self._watchdog, name="schedule-i-input-lease", daemon=True)
            self._worker.start()

    def _watchdog(self):
        while not self._stop.wait(0.02):
            try:
                self.tick()
            except BaseException as e:
                self.fault = e
                self.cancel.set()
                try:
                    self.close()
                except Exception as release_error:
                    self.fault = release_error
                return

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        """Attempt EVERY release, including after an earlier SendInput failure."""
        self._stop.set()
        with self._lock:
            errors = []
            for key in list(self.held):
                try:
                    self.backend.emit(key, False)
                    del self.held[key]
                except Exception as e:
                    errors.append(str(e))
            self.closed = True
            if errors:
                raise AdapterError("Input release failed: " + "; ".join(errors))

    def tick(self):
        with self._lock:
            if self.fault:
                raise AdapterError(str(self.fault))
            if self.closed or self.cancel.is_set() or self.backend.emergency_stop():
                raise AdapterError("Stopped (cancellation/F8/closed)")
            if self.clock() - self.started >= MAX_SECONDS:
                raise AdapterError("Runtime budget expired")
            if not self.backend.focused():
                raise AdapterError("Schedule I lost foreground focus")
            for key, expiry in list(self.held.items()):
                if self.clock() >= expiry:
                    self.backend.emit(key, False)
                    del self.held[key]

    def _key(self, args):
        with self._lock:
            self.tick()
            key, mode = args.get("key"), args.get("mode", "press")
            if key in MOUSE_AXES and mode == "press":
                count = mouse_counts(args.get("value", 1))
                if self.mouse_total + abs(count) > MAX_MOUSE_TOTAL:
                    raise AdapterError("Session relative mouse count budget exhausted")
                # Charge before dispatch: partial backend failures must not renew the budget.
                self.mouse_total += abs(count)
                if count:
                    self.backend.move_mouse(count if key == "MouseX" else 0,
                                            -count if key == "MouseY" else 0)
            elif mode == "release_all":
                for held in list(self.held):
                    self.backend.emit(held, False)
                    del self.held[held]
            elif mode == "release":
                if key in self.held:
                    self.backend.emit(key, False)
                    del self.held[key]
            else:
                duration = args.get("seconds", 0.1)
                # Record ownership BEFORE emitting: a partial failure must still be released.
                self.held[key] = self.clock() + duration
                self.backend.emit(key, True)
        if mode == "press" and key not in MOUSE_AXES:
            self.wait(duration)

    def wait(self, duration):
        duration = seconds(duration, MAX_SECONDS)
        end = self.clock() + duration
        while True:
            self.tick()
            remaining = end - self.clock()
            if remaining <= 0:
                return
            self.sleep(min(0.02, remaining))

    def observation(self):
        if self.snapshot is None:
            raise AdapterError("pose requires an explicitly supplied local observer snapshot")
        # Never treat a stale observer from another process or previous game session as live.
        if self.snapshot.stat().st_size > 65536:
            raise AdapterError("Observer snapshot too large")
        value = json.loads(self.snapshot.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise AdapterError("Observer snapshot must be an object")
        if value.get("pid") != self.backend.pid or value.get("protocol") != 1:
            raise AdapterError("Observer process/protocol mismatch")
        stamp = value.get("unix_ms")
        if (isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp)
                or not 0 <= time.time() * 1000 - stamp <= 2000):
            raise AdapterError("Observer snapshot is stale")
        return value

    def request(self, op, args=None):
        try:
            args = {} if args is None else args
            validate_frames([{"type": "request", "op": op, "args": args}])
            self.tick()
            if op == "wait":
                self.wait(args.get("seconds", 0))
            elif op == "key":
                self._key(args)
            elif op == "pose":
                return self.observation()
            elif op == "session" and args.get("action") == "stop":
                self.close()
            elif op == "context":
                return {"engine": "unity", "game": "Schedule I", "host": "external",
                        "protocol": 1, "mode": "local-scripted-replay", "source_export": False,
                        "ops": ["key", "pose", "context", "session", "ping", "wait"],
                        "mouse": {"mode": "relative_counts", "axes": list(MOUSE_AXES),
                                  "max_delta": MAX_MOUSE_DELTA, "session_budget": MAX_MOUSE_TOTAL},
                        "pose_source": "camera only; not player pose", "pid": self.backend.pid}
            return {}
        except BaseException:
            self.close()
            raise

    def frame(self, frame):
        """Upstream response envelope; unsupported capabilities fail explicitly."""
        try:
            validate_frames([frame])
        except BaseException:
            self.close()
            raise
        return {"type": "response", "id": frame.get("id"), "ok": True,
                "result": self.request(frame["op"], frame.get("args")), "t": self.clock() - self.started}


class WindowsInput:
    """Windows SendInput, bound to a PID AND the exact executable path supplied by the user."""

    watchdog = True

    def __init__(self, pid, exe):
        if os.name != "nt":
            raise AdapterError("Live input is Windows-only")
        from ctypes import wintypes as w

        self.pid = pid
        self.exe = Path(exe).resolve(strict=True)
        if self.exe.name.lower() != "schedule i.exe":
            raise AdapterError("Target executable must be Schedule I.exe")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.user.GetForegroundWindow.restype = w.HWND
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user.GetAsyncKeyState.restype = ctypes.c_short
        # Retain the process handle: prevents silently reusing a recycled PID after exit.
        self.process = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not self.process:
            raise AdapterError("Cannot query target process")
        self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.kernel.WaitForSingleObject.restype = w.DWORD
        size, buf = w.DWORD(32768), ctypes.create_unicode_buffer(32768)
        if (not self.kernel.QueryFullProcessImageNameW(self.process, 0, buf, ctypes.byref(size))
                or os.path.normcase(buf.value) != os.path.normcase(str(self.exe))):
            self.dispose()
            raise AdapterError("Target PID executable does not match --exe")

        class Mouse(ctypes.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("data", w.DWORD), ("flags", w.DWORD),
                        ("time", w.DWORD), ("extra", ctypes.c_size_t)]

        class Keyboard(ctypes.Structure):
            _fields_ = [("vk", w.WORD), ("scan", w.WORD), ("flags", w.DWORD),
                        ("time", w.DWORD), ("extra", ctypes.c_size_t)]

        class Hardware(ctypes.Structure):
            _fields_ = [("message", w.DWORD), ("low", w.WORD), ("high", w.WORD)]

        class Payload(ctypes.Union):
            _fields_ = [("mouse", Mouse), ("keyboard", Keyboard), ("hardware", Hardware)]

        class Input(ctypes.Structure):
            _fields_ = [("kind", w.DWORD), ("payload", Payload)]

        self.Input = Input
        self.user.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.user.SendInput.restype = w.UINT

    def dispose(self):
        if getattr(self, "process", None):
            self.kernel.CloseHandle(self.process)
            self.process = None

    def focused(self):
        from ctypes import wintypes as w
        if not self.process or self.kernel.WaitForSingleObject(self.process, 0) != 258:
            return False
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(self.user.GetForegroundWindow(), ctypes.byref(pid))
        return pid.value == self.pid

    def emergency_stop(self):
        return bool(self.user.GetAsyncKeyState(0x77) & 0x8000)  # F8

    def emit(self, key, down):
        if down and (not self.focused() or self.emergency_stop()):
            raise AdapterError("Target is not focused, or F8 held")
        item = self.Input()
        if key in MOUSE:
            item.kind = 0
            item.payload.mouse.flags = MOUSE[key][0 if down else 1]
        else:
            item.kind = 1
            item.payload.keyboard.vk = KEYS[key]
            item.payload.keyboard.flags = 0 if down else 2
        if self.user.SendInput(1, ctypes.byref(item), ctypes.sizeof(item)) != 1:
            raise AdapterError("SendInput failed (Windows may block input across privilege levels)")

    def move_mouse(self, dx, dy):
        """A bounded Windows-relative mouse event; dy is down, unlike upstream MouseY."""
        if (type(dx) is not int or type(dy) is not int
                or max(abs(dx), abs(dy)) > MAX_MOUSE_DELTA):
            raise ValueError("mouse backend requires bounded integer counts")
        if not self.focused() or self.emergency_stop():
            raise AdapterError("Target is not focused, or F8 held")
        item = self.Input()
        item.kind = 0
        item.payload.mouse.dx, item.payload.mouse.dy = dx, dy
        item.payload.mouse.flags = 0x0001  # MOUSEEVENTF_MOVE, relative (never ABSOLUTE)
        if self.user.SendInput(1, ctypes.byref(item), ctypes.sizeof(item)) != 1:
            raise AdapterError("Relative mouse SendInput failed")


class ScheduleIGame(RuntimeAdapter):
    """Small kit-style harness surface backed by the same bounded adapter.

    The upstream kit Game includes privileged APIs unavailable in a retail game. These
    conveniences preserve familiar names only where semantics can be supported honestly.
    A harness receives this object directly; no downloaded/generated code is executed.
    """

    def press(self, key, seconds=None, *, value=None):
        args = {"key": key, "mode": "press"}
        if seconds is not None or key not in MOUSE_AXES:
            args["seconds"] = 0.1 if seconds is None else seconds
        if value is not None:
            args["value"] = value
        return self.request("key", args)

    def mouse(self, right=0.0, up=0.0):
        """Upstream-style one-shot relative counts; sensitivity applies, not calibrated degrees."""
        try:
            # Validate both axes and their combined remaining budget before emitting either.
            dx, up_count = mouse_counts(right), mouse_counts(up)
            with self._lock:
                self.tick()
                if self.mouse_total + abs(dx) + abs(up_count) > MAX_MOUSE_TOTAL:
                    raise AdapterError("Session relative mouse count budget exhausted")
                for axis, count in (("MouseX", dx), ("MouseY", up_count)):
                    if count:
                        self.request("key", {"key": axis, "mode": "press", "value": count})
        except BaseException:
            self.close()
            raise

    def hold(self, key, seconds):
        return self.request("key", {"key": key, "mode": "hold", "seconds": seconds})

    def release(self, key):
        return self.request("key", {"key": key, "mode": "release"})

    def release_all(self):
        return self.request("key", {"mode": "release_all"})

    def pose(self):
        """Observer snapshot labelled camera-only; never fabricates a local player position."""
        return self.request("pose")

    def context(self):
        return self.request("context")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path, help="JSON array of protocol-1 requests; preview by default")
    parser.add_argument("--execute", action="store_true", help="explicitly enable live input")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--snapshot", type=Path, help="optional observer file for pose")
    parser.add_argument("--countdown", type=float, default=5, help="seconds to manually focus the game (0..30)")
    args = parser.parse_args(argv)
    try:
        seconds(args.countdown, 30)
        if args.replay.stat().st_size > 1024 * 1024:
            raise ValueError("Replay exceeds 1 MiB")
        frames = validate_frames(json.loads(args.replay.read_text(encoding="utf-8")))
        if not args.execute:
            print(json.dumps({"mode": "preview", "frames": len(frames), "game_touched": False}, indent=2))
            return 0
        if not args.pid or args.pid < 1 or args.exe is None:
            raise ValueError("--execute requires --pid and the exact --exe path")
        backend = WindowsInput(args.pid, args.exe)
        try:
            print("Focus Schedule I manually. Hold F8 or press Ctrl+C to stop. Maximum session: 60s.")
            time.sleep(args.countdown)
            with ScheduleIGame(backend, snapshot=args.snapshot) as adapter:
                for frame in frames:
                    print(json.dumps(adapter.frame(frame)))
        finally:
            backend.dispose()
        return 0
    except (AdapterError, ValueError, OSError, KeyboardInterrupt) as e:
        print(f"Stopped: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
