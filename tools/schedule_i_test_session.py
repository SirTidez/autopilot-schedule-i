"""Reversible, explicitly launched local Schedule I tests. Preview is the default.

Only the owned game PID may be captured (WGC, no desktop/audio fallback). Original saves,
UserData and DLLs are hash-verified backups. Unknown new files are left intact and reported.
No provider, upload, persistent service, save progression or gameplay action is automatic.
"""

import argparse
import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autopilot import bridge  # noqa: E402

LIMIT = 1024 ** 3
OPS = {"context", "session", "ping", "key", "pose", "find", "transforms", "view", "movement", "ui"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(temp, path)


def no_reparse(path):
    """Check lexical ancestors BEFORE resolve() can conceal a junction/symlink."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if item.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Reparse points are not permitted: " + str(item))
    return path.resolve()


def below(path, root):
    return path == root or path.is_relative_to(root)


def validate_paths(root, source, evidence, saves):
    root, source, evidence, saves = (no_reparse(p) for p in (root, source, evidence, saves))
    groups = (root, evidence, saves)
    for i, left in enumerate(groups):
        for right in groups[i + 1:]:
            if below(left, right) or below(right, left):
                raise ValueError("Game, evidence and saves must be disjoint canonical directory trees")
    if any(below(source, folder) for folder in groups):
        raise ValueError("Reviewed source DLL must be outside game, saves and evidence trees")
    if not root.is_dir() or not saves.is_dir() or not source.is_file():
        raise ValueError("Game/save directories and reviewed source DLL must already exist")
    if evidence.exists():
        raise ValueError("Evidence directory must be new")
    for child in (root / "Mods", root / "UserData", root / "Schedule I.exe"):
        no_reparse(child)
    return root, source, evidence, saves


def snapshot(root, *, dlls=False):
    files, directories, total = {}, [], 0
    if not root.exists():
        return {"files": files, "directories": directories, "root_existed": False}
    no_reparse(root)
    pending, entries = [root], 0
    while pending:
        folder = pending.pop()
        for path in sorted(folder.iterdir()):
            no_reparse(path)  # reject BEFORE traversal, including Windows junctions
            entries += 1
            if entries > 100000:
                raise RuntimeError("Backup entry count exceeds bounded 100000")
            relative = str(path.relative_to(root))
            if path.is_dir():
                if not dlls:
                    directories.append(relative)
                    pending.append(path)
            elif path.is_file() and (not dlls or path.suffix.lower() == ".dll"):
                size = path.stat().st_size
                total += size
                if total > LIMIT:
                    raise RuntimeError("Backup scope exceeds bounded 1 GiB: " + str(root))
                files[relative] = {"sha256": digest(path), "size": size}
    return {"files": files, "directories": directories, "root_existed": True}


def verify(root, manifest):
    return all((root / rel).is_file() and digest(root / rel) == value["sha256"]
               for rel, value in manifest["files"].items())


def copy_verified(source, destination, expected):
    no_reparse(source)
    no_reparse(destination)
    if digest(source) != expected:
        raise RuntimeError("Backup/source hash changed: " + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if digest(destination) != expected:
        raise RuntimeError("Copied file failed hash verification: " + str(destination))


def backup_tree(root, destination, manifest):
    destination.mkdir()
    for relative in manifest["directories"]:
        (destination / relative).mkdir(parents=True, exist_ok=True)
    for relative, value in manifest["files"].items():
        copy_verified(root / relative, destination / relative, value["sha256"])
    if not verify(destination, manifest) or not verify(root, manifest):
        raise RuntimeError("Backup verification failed; no game may be launched")


def ensure_no_game():
    # CIM returns an empty collection normally when no game exists; query errors are fail-closed.
    script = ("$ErrorActionPreference='Stop'; try { "
              "$p=@(Get-CimInstance Win32_Process -Filter \"Name = 'Schedule I.exe'\"); "
              "if($p.Count -ne 0){exit 2}; exit 0 } catch { exit 3 }")
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, timeout=20,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError("A Schedule I process exists or process enumeration failed; "
                           "refusing mutation/launch/restore")


def attempt(state, label, action):
    """Cleanup stages are independent; one failure cannot skip the rest."""
    try:
        return True, action()
    except BaseException as error:
        state.setdefault("cleanup_errors", []).append(label + ": " + type(error).__name__ + ": " + str(error))
        return False, None


def end_owned_process(process, *, game=False):
    if process is None:
        return True
    if process.poll() is None:
        if not game:
            try:
                process.communicate(input=b"q\n", timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20 if game else 3)
            except subprocess.TimeoutExpired:
                process.kill()  # retained Popen handle belongs to this session only
                process.wait(timeout=5)
    if process.poll() is None:
        raise RuntimeError("Owned process exit remains uncertain")
    return True


def window_owned(hwnd, game):
    if hwnd <= 0 or game.poll() is not None:
        raise ValueError("Owned game/window is not live")
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    owner = wintypes.DWORD()
    if not user.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(owner)) or owner.value != game.pid:
        raise ValueError("Capture HWND is not owned by the retained game process")


def video_path(evidence, name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.mp4", name):
        raise ValueError("Video requires a simple local MP4 filename")
    if name.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                                          *[f"LPT{i}" for i in range(1, 10)]}:
        raise ValueError("Reserved Windows filename")
    output = evidence / name
    if output.exists() or (evidence / (name + ".ffmpeg.log")).exists():
        raise ValueError("Evidence files must never be overwritten")
    return output


def fixture_proof(saves, relative, name, prefix, original):
    if not isinstance(relative, str) or not isinstance(name, str) or not name.startswith(prefix):
        raise ValueError("Fixture must have an explicit task-owned name/prefix")
    if (Path(relative).is_absolute() or ".." in Path(relative).parts
            or not re.fullmatch(r"[A-Za-z0-9 _.\-]{8,128}", name)):
        raise ValueError("Fixture requires a simple relative directory and distinctive task-owned name")
    folder = no_reparse(saves / relative)
    if folder == saves or not below(folder, saves) or not folder.is_dir():
        raise ValueError("Fixture directory must stay strictly inside the save tree")
    rel = str(folder.relative_to(saves))
    if rel in original["directories"] or any(below(no_reparse(saves / p), folder) for p in original["files"]):
        raise ValueError("An original save directory can never be marked as a test fixture")
    matches = folder.name == name
    def contains(value, depth=0):
        if depth > 16:
            return False
        if value == name:
            return True
        if isinstance(value, dict):
            return any(contains(v, depth + 1) for v in value.values())
        if isinstance(value, list):
            return any(contains(v, depth + 1) for v in value)
        return False
    for file in folder.glob("*.json"):
        no_reparse(file)
        if file.stat().st_size <= 1024 * 1024:
            try:
                matches = matches or contains(json.loads(file.read_text(encoding="utf-8-sig")))
            except (ValueError, OSError):
                pass
    if not matches:
        raise ValueError("New save directory/name metadata does not prove the task fixture identity")
    return rel


def restore_tree(root, backup, original, changed, state, label):
    for relative in original["directories"]:
        attempt(state, label + " directory " + relative,
                lambda rel=relative: no_reparse(root / rel).mkdir(parents=True, exist_ok=True))
    restored = True
    for relative, value in original["files"].items():
        def restore(rel=relative, value=value):
            destination = no_reparse(root / rel)
            if destination.is_file() and digest(destination) == value["sha256"]:
                return
            if destination.is_file():
                copy_verified(destination, changed / rel, digest(destination))  # preserve changed contents reversibly
            copy_verified(backup / rel, destination, value["sha256"])
        ok, _ = attempt(state, label + " file " + relative, restore)
        restored &= ok
    ok, result = attempt(state, label + " hash verification", lambda: verify(root, original))
    return restored and ok and result is True


class LocalTestServer:
    """Owned session-only listener; can be shut down even if the game never connects."""
    def __init__(self, port, owned_game):
        from websockets.sync.server import serve
        self.ready, self.peers, self.sockets, self.lock = queue.Queue(), [], [], threading.Lock()
        self.stopping = threading.Event()
        self.owned_game = owned_game
        self.server = serve(self.handle, "127.0.0.1", port, max_size=65536, compression=None, close_timeout=2)
        threading.Thread(target=self.server.serve_forever, name="schedule-i-test-listener", daemon=True).start()

    def handle(self, ws):
        link = None
        try:
            with self.lock:
                if self.stopping.is_set() or len(self.sockets) >= 4:
                    raise RuntimeError("Test listener is stopping or its connection bound was reached")
                self.sockets.append(ws)
            hello = json.loads(ws.recv(timeout=10))
            game = self.owned_game()
            if (not isinstance(hello, dict) or game is None or game.poll() is not None
                    or hello.get("engine") != "schedule_i" or hello.get("protocol") != 1
                    or hello.get("game_pid") != game.pid or hello.get("adapter") != "melonloader"
                    or hello.get("source_export") is not False or not bridge.is_loopback(ws.remote_address)):
                raise RuntimeError("Unexpected bridge peer; owned PID/profile must match")
            link = bridge.Link(lambda frame: ws.send(json.dumps(frame)))
            link.loopback = True
            link.feed(hello)
            with self.lock:
                if self.stopping.is_set():
                    raise RuntimeError("Test listener stopped during handshake")
                self.peers.append((ws, link))
            ws.send(json.dumps({"type": "welcome", "role": "schedule-i-test", "session": "owned-test", "features": []}))
            self.ready.put(link)
            for message in ws:
                frame = json.loads(message)
                if not isinstance(frame, dict):
                    raise ValueError("Bridge frame must be an object")
                link.feed(frame)
        except Exception as error:
            self.ready.put(error)
        finally:
            if link is not None:
                link.close("test peer disconnected")

    def stop(self):
        errors = []
        self.stopping.set()
        with self.lock:
            peers = list(self.peers)
            sockets = list(self.sockets)
        for ws, link in peers:
            for action in (lambda: None if link.closed else link.send({"type": "stop"}),
                           lambda: link.close("test session ending")):
                try:
                    action()
                except Exception as error:
                    if not link.closed:
                        errors.append(str(error))
        for ws in sockets:
            try:
                ws.close()
            except Exception as error:
                errors.append(str(error))
        self.server.shutdown()
        if errors:
            raise RuntimeError("; ".join(errors))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "dll", "evidence", "saves"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--packrat", choices=("PackRat-IL2CPP.dll", "PackRat-Mono.dll"), required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--control", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--fixture-prefix", default="AutopilotEvidence")
    args = parser.parse_args(argv)
    try:
        root, source, evidence, saves = validate_paths(args.root, args.dll, args.evidence, args.saves)
        exe, mods, userdata = root / "Schedule I.exe", root / "Mods", root / "UserData"
        if not exe.is_file() or not (mods / args.packrat).is_file():
            raise ValueError("Explicit installed game and matching PackRat binary are required")
        if not 1024 <= args.port <= 65535 or not 30 <= args.seconds <= 1800:
            raise ValueError("Invalid bounded port/duration")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", args.fixture_prefix):
            raise ValueError("Fixture prefix must be a distinctive simple task-owned name")
        if not args.execute:
            print(json.dumps({"mode": "preview", "game": str(exe), "control": args.control, "no_changes": True}))
            return 0
        if os.name != "nt":
            raise ValueError("Live session is Windows-only")
        ensure_no_game()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    evidence.mkdir(parents=True)
    backup = evidence / "backup"
    backup.mkdir()
    state = {"phase": "backup", "session_id": uuid.uuid4().hex, "root": str(root), "saves": str(saves),
             "control_opt_in": args.control, "commands": [], "video": [], "restoration": {}, "cleanup_errors": [],
             "fixtures": [], "mod_sha256": digest(source), "packrat_sha256": digest(mods / args.packrat)}
    game = recording = server = video_log = None
    harness_worker = acceptance = None
    harness_cancel = threading.Event()
    harness_done = queue.Queue()
    originals, backup_complete, mutation_started = {}, False, False
    target = mods / "Autopilot.ScheduleI.dll"
    def save_state():
        write_json(evidence / "state.json", state)
    def stop_recording():
        nonlocal recording, video_log
        errors = []
        if recording is not None:
            try:
                end_owned_process(recording)
                recording = None
            except BaseException as error:
                errors.append(str(error))
        if video_log is not None:
            try:
                video_log.close()
                video_log = None
            except BaseException as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError("; ".join(errors))
    try:
        save_state()
        for scope, folder in (("saves", saves), ("userdata", userdata), ("mods", mods)):
            originals[scope] = snapshot(folder, dlls=scope == "mods")
            write_json(evidence / ("original-" + scope + "-manifest.json"), originals[scope])
            backup_tree(folder, backup / scope, originals[scope])
        backup_complete = True
        state["backups_verified"] = True
        save_state()
        ensure_no_game()  # backup hashing can take time; fail closed again before the first mutation
        mutation_started = True
        quarantine = evidence / "quarantined-mods"
        quarantine.mkdir()
        for file in mods.glob("*.dll"):
            no_reparse(file)
            if file.name not in (args.packrat, target.name):
                shutil.move(str(file), str(quarantine / file.name))
                save_state()
        copy_verified(source, target, state["mod_sha256"])
        server = LocalTestServer(args.port, lambda: game)
        flags = ["-AutopilotScheduleIConnect", "-AutopilotScheduleIDiagnostics",
                 f"-AutopilotScheduleIUrl=ws://127.0.0.1:{args.port}/v1/bridge"]
        if args.control:
            flags.append("-AutopilotScheduleIControl")
        ensure_no_game()  # repeat immediately before Popen; never reuse or stop another game's PID
        game = subprocess.Popen([str(exe), *flags], cwd=root)
        state.update(pid=game.pid, launch_flags=flags, phase="starting")
        save_state()
        start, link = time.monotonic(), None
        while link is None:
            try:
                item = server.ready.get(timeout=0.25)
                if isinstance(item, BaseException):
                    raise item
                link = item
            except queue.Empty:
                if game.poll() is not None or time.monotonic() - start > min(180, args.seconds):
                    raise RuntimeError("Owned game exited or timed out before its verified bridge connected")
        state.update(hello=link.hello, phase="ready")
        save_state()
        print(json.dumps({"phase": "ready", "pid": game.pid, "evidence": str(evidence)}), flush=True)
        seen = set()
        while game.poll() is None and time.monotonic() - start < args.seconds:
            try:
                state["harness_result"] = harness_done.get_nowait()
                save_state()
            except queue.Empty:
                pass
            if len(state["commands"]) >= 500:
                raise RuntimeError("Test command/event budget exhausted")
            control_file = evidence / "command.json"
            if not control_file.exists():
                time.sleep(0.1)
                continue
            command = {}
            try:
                if control_file.stat().st_size > 65536:
                    raise ValueError("Test command exceeds 64 KiB")
                command = json.loads(control_file.read_text(encoding="utf-8-sig"))
                if not isinstance(command, dict):
                    raise ValueError("Test command must be an object")
                identity = str(command["id"])
                if identity in seen:
                    time.sleep(0.1)
                    continue
                if len(seen) >= 500:
                    raise RuntimeError("Test command budget exhausted")
                seen.add(identity)
                action, result = command.get("action", "request"), {}
                if action == "request":
                    op, payload = command["op"], command.get("args", {})
                    if op not in OPS or not isinstance(payload, dict):
                        raise ValueError("Only typed runtime operations/object args are allowed")
                    if op == "key" and not args.control and payload.get("mode") != "release_all":
                        raise ValueError("No control opt-in")
                    if op == "session" and payload.get("action", "status") not in ("status", "stop"):
                        raise ValueError("No scene/save/reset actions")
                    result = link.request(op, payload, timeout=4)
                elif action == "stop":
                    link.send({"type": "stop"})
                    result = {"stop_sent": True}
                elif action == "packrat_harness":
                    if not args.control or not state["fixtures"]:
                        raise ValueError("Acceptance input requires control opt-in and a proved task-created fixture")
                    if harness_worker is not None:
                        raise ValueError("One acceptance run per game connection; native stop is permanently latched")
                    from autopilot.schedule_i_acceptance import PackRatToggleHarness
                    from autopilot.schedule_i_harness import run_harness
                    duration = command.get("max_seconds", 35)
                    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 5 <= duration <= 45:
                        raise ValueError("Acceptance duration must be 5..45 seconds")
                    trace = evidence / "packrat-runtime-trace.jsonl"
                    if trace.exists():
                        raise ValueError("Acceptance trace must never be overwritten")
                    acceptance = PackRatToggleHarness(command["panel_name"], wait_for_cancel=True)
                    def run_acceptance():
                        try:
                            outcome = run_harness(link, acceptance, trace, choose=acceptance.choose, control=True,
                                                  max_seconds=duration, cancel=harness_cancel)
                            harness_done.put({"ok": True, "runtime_result": outcome,
                                              "open_observed": acceptance.open_observed,
                                              "close_observed": acceptance.close_observed})
                        except BaseException as error:
                            harness_done.put({"ok": False, "error": type(error).__name__ + ": " + str(error)})
                    harness_worker = threading.Thread(target=run_acceptance, name="packrat-acceptance", daemon=True)
                    harness_worker.start()
                    result = {"harness_started": True, "decision_route": "reviewed local observation callback"}
                elif action == "harness_status":
                    result = {"worker_alive": harness_worker is not None and harness_worker.is_alive(),
                              "observations": dict(acceptance.last) if acceptance is not None else None,
                              "result": state.get("harness_result")}
                elif action == "cancel_harness":
                    harness_cancel.set()
                    link.send({"type": "stop"})
                    result = {"cancel_requested": True, "native_stop_sent": True}
                elif action == "mark_fixture":
                    relative = fixture_proof(saves, command["relative_dir"], command["fixture_name"],
                                             args.fixture_prefix, originals["saves"])
                    entry = {"relative_dir": relative, "fixture_name": command["fixture_name"]}
                    if entry not in state["fixtures"]:
                        state["fixtures"].append(entry)
                    result = entry
                elif action == "record":
                    stop_recording()
                    if len(state["video"]) >= 8:
                        raise ValueError("Video count limit reached")
                    hwnd = int(command["hwnd"])
                    output = video_path(evidence, command.get("name", "game-evidence.mp4"))
                    window_owned(hwnd, game)
                    video_log = (evidence / (output.name + ".ffmpeg.log")).open("xb")
                    capture = (f"gfxcapture=hwnd={hwnd}:capture_cursor=0:capture_border=0:display_border=0:"
                               "max_framerate=15:width=1280:height=720:resize_mode=scale_aspect")
                    recording = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-n",
                        "-f", "lavfi", "-i", capture, "-vf", "hwdownload,format=bgra,format=yuv420p", "-an",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-threads", "2",
                        "-t", "120", str(output)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=video_log,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    try:
                        window_owned(hwnd, game)  # recheck the same PID/HWND immediately after recorder creation
                    except BaseException:
                        stop_recording()
                        raise
                    state["video"].append({"file": output.name, "hwnd": hwnd, "game_pid": game.pid,
                                           "source": "Windows Graphics Capture/owned window", "audio": False})
                    result = {"recording_pid": recording.pid, "file": output.name}
                elif action == "stop_recording":
                    stop_recording()
                    result = {"recording_stopped": True}
                elif action == "quit":
                    break
                else:
                    raise ValueError("Unknown test-session command")
                event = {"id": identity, "action": action, "ok": True, "result": result,
                         "wall": time.time(), "game_time": link.game_time}
            except Exception as error:
                event = {"id": str(command.get("id", "invalid")) if isinstance(command, dict) else "invalid",
                         "ok": False, "error": str(error), "wall": time.time()}
            state["commands"].append(event)
            write_json(evidence / "response.json", event)
            save_state()
    except BaseException as error:
        state["error"] = type(error).__name__ + ": " + str(error)
        print("Test session: " + state["error"], flush=True)
    finally:
        state["phase"] = "restoring"
        harness_cancel.set()
        if server is not None:
            attempt(state, "bridge cleanup", server.stop)
        if harness_worker is not None:
            attempt(state, "acceptance worker cleanup", lambda: harness_worker.join(timeout=3))
            state["acceptance_worker_still_alive"] = harness_worker.is_alive()
            if harness_worker.is_alive():
                state["cleanup_errors"].append("Acceptance worker did not exit before its bounded join")
            try:
                state["harness_result"] = harness_done.get_nowait()
            except queue.Empty:
                pass
        attempt(state, "recording cleanup", stop_recording)
        exited, result = attempt(state, "owned game exit", lambda: end_owned_process(game, game=True))
        safe = exited and result is True
        if safe:
            safe, _ = attempt(state, "no other game writer", ensure_no_game)
        state["owned_game_exit_verified"] = exited and result is True
        if backup_complete and mutation_started and safe:
            # No rollback under a live/uncertain game writer. Each file is independently recoverable.
            def restore_mods():
                for relative, value in originals["mods"]["files"].items():
                    def restore(rel=relative, value=value):
                        file = no_reparse(mods / rel)
                        if file.exists() and digest(file) == value["sha256"]:
                            return
                        if file.exists() and (file != target or digest(file) != state["mod_sha256"]):
                            raise RuntimeError("Unexpected modified DLL preserved: " + rel)
                        copy_verified(backup / "mods" / rel, file, value["sha256"])
                    attempt(state, "mod " + relative, restore)
                if target.name not in originals["mods"]["files"] and target.exists():
                    if digest(target) != state["mod_sha256"]:
                        raise RuntimeError("Unexpected new DLL preserved; restore pending")
                    destination = evidence / "session-installed-Autopilot.ScheduleI.dll"
                    no_reparse(target)
                    shutil.move(str(target), str(destination))
                return verify(mods, originals["mods"])
            ok, verified = attempt(state, "DLL restoration", restore_mods)
            state["restoration"]["mods_verified"] = ok and verified is True
            for label, folder in (("userdata", userdata), ("saves", saves)):
                state["restoration"][label + "_originals_verified"] = restore_tree(
                    folder, backup / label, originals[label], evidence / ("changed-" + label), state, label)
            for fixture in state["fixtures"]:
                def quarantine_fixture(fixture=fixture):
                    relative = fixture_proof(saves, fixture["relative_dir"], fixture["fixture_name"],
                                             args.fixture_prefix, originals["saves"])
                    folder = no_reparse(saves / relative)
                    if folder == saves or not below(folder, saves):
                        raise RuntimeError("Fixture move escaped the verified save root")
                    manifest = snapshot(folder)
                    destination = evidence / "task-created-saves" / relative
                    if destination.exists():
                        raise RuntimeError("Fixture evidence destination already exists")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    fixture["quarantine_manifest"] = manifest
                    fixture["quarantine_destination"] = str(destination)
                    save_state()  # recovery journal before the only verified task-owned directory move
                    shutil.move(str(folder), str(destination))
                    if not verify(destination, manifest):
                        raise RuntimeError("Fixture quarantine hash verification failed")
                    fixture["quarantined_verified"] = True
                attempt(state, "task fixture quarantine", quarantine_fixture)
            for label, folder in (("userdata", userdata), ("saves", saves), ("mods", mods)):
                def extras(label=label, folder=folder):
                    current = snapshot(folder, dlls=label == "mods")
                    unknown = {"files": {rel: v for rel, v in current["files"].items()
                                         if rel not in originals[label]["files"]},
                               "directories": sorted(set(current["directories"]) - set(originals[label]["directories"]))}
                    state.setdefault("new_files_pending", {})[label] = unknown
                    return (not unknown["files"] and not unknown["directories"]
                            and (originals[label]["root_existed"] or not folder.exists()))
                ok, clean = attempt(state, label + " new-file inventory", extras)
                state["restoration"][label + "_no_unknown_new_files"] = ok and clean is True
        elif mutation_started:
            state["restore_pending"] = ("Game writer exit is uncertain; backups preserved, "
                                        "filesystem rollback skipped")
        latest = root / "MelonLoader" / "Latest.log"
        if latest.exists():
            attempt(state, "private loader evidence",
                    lambda: shutil.copy2(no_reparse(latest), evidence / "MelonLoader-private.log"))
        state["original_saves_restored_verified"] = state["restoration"].get("saves_originals_verified", False)
        state["full_original_state_restored"] = (backup_complete and safe and bool(state["restoration"])
                                                 and all(v is True for v in state["restoration"].values()))
        complete = state["full_original_state_restored"] and not state.get("error") and not state["cleanup_errors"]
        state["phase"] = "finished" if complete else "incomplete"
        persisted, _ = attempt(state, "final state persistence", save_state)
        if not persisted:
            state["phase"] = "incomplete"
            attempt(state, "incomplete state persistence retry", save_state)
        print(json.dumps({"phase": state["phase"], "error": state.get("error"),
                          "full_original_state_restored": state["full_original_state_restored"],
                          "original_saves_restored_verified": state["original_saves_restored_verified"],
                          "cleanup_errors": state["cleanup_errors"], "evidence": str(evidence)}), flush=True)
    return 0 if state["phase"] == "finished" and not state["cleanup_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
