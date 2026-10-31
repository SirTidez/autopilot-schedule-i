"""Pure filesystem/fake-process tests for the live-session runner. No game, OS input or network."""

import importlib.util
import json
import queue
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[3] / "tools" / "schedule_i_test_session.py"
spec = importlib.util.spec_from_file_location("schedule_i_session_under_test", SCRIPT)
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


class FakeGame:
    pid = 12345

    def __init__(self, *, uncertain=False, exit_after_polls=None):
        self.returncode, self.uncertain, self.polls = None, uncertain, 0
        self.exit_after_polls = exit_after_polls

    def poll(self):
        self.polls += 1
        if self.exit_after_polls is not None and self.polls > self.exit_after_polls:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        if self.uncertain:
            raise OSError("injected owned-process exit uncertainty")
        self.returncode = 0

    def kill(self):
        self.terminate()

    def wait(self, **_):
        return self.returncode


class SessionTests(unittest.TestCase):
    def layout(self, folder):
        base = Path(folder)
        root, saves, source, evidence = base / "game", base / "saves", base / "build.dll", base / "evidence"
        (root / "Mods").mkdir(parents=True)
        (root / "UserData").mkdir()
        saves.mkdir()
        (root / "Schedule I.exe").write_bytes(b"not an executable")
        (root / "Mods" / "PackRat-Mono.dll").write_bytes(b"original packrat")
        (root / "Mods" / "Other.dll").write_bytes(b"original other mod")
        (root / "Mods" / "Autopilot.ScheduleI.dll").write_bytes(b"previous mod")
        (root / "UserData" / "settings.cfg").write_bytes(b"original config")
        (saves / "original.json").write_bytes(b"original save")
        source.write_bytes(b"reviewed new mod")
        return root, source, evidence, saves

    def run_fake(self, folder, *, server_error=False, uncertain=False, new_unknown=False, fixture=False):
        root, source, evidence, saves = self.layout(folder)
        game = FakeGame(uncertain=uncertain, exit_after_polls=1 if fixture else None)
        class Server:
            def __init__(self, *_):
                self.ready = queue.Queue()
                self.ready.put(SimpleNamespace(hello={"engine": "schedule_i", "game_pid": game.pid}, game_time=0))
                (root / "UserData" / "settings.cfg").write_bytes(b"test changed config")
                (saves / "original.json").write_bytes(b"test changed save")
                command = {"id": "quit", "action": "quit"}
                if new_unknown:
                    (saves / "unknown-new.json").write_bytes(b"never delete unknown data")
                    (root / "UserData" / "unknown-new.cfg").write_bytes(b"never delete unknown config")
                if fixture:
                    (saves / "SaveGame_9").mkdir()
                    (saves / "SaveGame_9" / "Game.json").write_text(
                        json.dumps({"OrganisationName": "AutopilotEvidenceMono"}))
                    command = {"id": "fixture", "action": "mark_fixture", "relative_dir": "SaveGame_9",
                               "fixture_name": "AutopilotEvidenceMono"}
                session.write_json(evidence / "command.json", command)
            def stop(self):
                if server_error:
                    raise OSError("injected listener cleanup failure")
        argv = ["--root", str(root), "--dll", str(source), "--evidence", str(evidence), "--saves", str(saves),
                "--packrat", "PackRat-Mono.dll", "--execute", "--seconds", "30"]
        with patch.object(session, "ensure_no_game") as guard, patch.object(session, "LocalTestServer", Server), \
                patch.object(session.subprocess, "Popen", return_value=game) as popen:
            code = session.main(argv)
        popen.assert_called_once()  # fake Popen only; no executable was launched
        state = json.loads((evidence / "state.json").read_text())
        return code, state, (root, source, evidence, saves), guard.call_count

    def test_paths_fail_before_recursive_self_backup_or_source_target_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            root, source, evidence, saves = self.layout(folder)
            for bad in (saves / "evidence", root / "evidence", saves.parent):
                with self.assertRaises(ValueError):
                    session.validate_paths(root, source, bad, saves)
            with self.assertRaises(ValueError):
                session.validate_paths(root, root / "Mods" / "Autopilot.ScheduleI.dll", evidence, saves)
            session.validate_paths(root, source, evidence, saves)
            self.assertFalse(evidence.exists())

    def test_process_enumeration_fails_closed_without_real_process_query(self):
        for code in (2, 3):
            with patch.object(session.subprocess, "run", return_value=SimpleNamespace(returncode=code)):
                with self.assertRaises(RuntimeError):
                    session.ensure_no_game()
        with patch.object(session.subprocess, "run", side_effect=subprocess.TimeoutExpired("mock", 20)):
            with self.assertRaises(subprocess.TimeoutExpired):
                session.ensure_no_game()

    def test_reparse_attribute_is_rejected_before_directory_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            alias = root / "junction-like-directory"
            alias.mkdir()
            original = Path.lstat
            def fake_lstat(path):
                info = original(path)
                if path == alias:
                    return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
                return info
            with patch.object(Path, "lstat", fake_lstat), self.assertRaisesRegex(ValueError, "Reparse"):
                session.snapshot(root)

    def test_closed_peer_cleanup_still_closes_socket_and_owned_listener(self):
        server = session.LocalTestServer.__new__(session.LocalTestServer)
        from unittest.mock import Mock
        link = SimpleNamespace(closed="already disconnected", send=Mock(), close=Mock())
        socket = SimpleNamespace(close=Mock())
        import threading
        server.lock, server.stopping = threading.Lock(), threading.Event()
        server.peers, server.sockets = [(socket, link)], [socket]
        server.server = SimpleNamespace(shutdown=Mock())
        server.stop()
        link.send.assert_not_called()
        socket.close.assert_called_once()
        server.server.shutdown.assert_called_once()

    def test_backup_hash_failure_prevents_trusting_the_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root, _, evidence, saves = self.layout(folder)
            original = session.snapshot(saves)
            with patch.object(session.shutil, "copy2", side_effect=lambda src, dst: Path(dst).write_bytes(b"corrupt")):
                with self.assertRaisesRegex(RuntimeError, "hash verification"):
                    session.backup_tree(saves, evidence, original)
            self.assertEqual((saves / "original.json").read_bytes(), b"original save")

    def test_complete_rollback_restores_all_originals_and_repeats_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            code, state, paths, guards = self.run_fake(folder)
            root, _, evidence, saves = paths
            self.assertEqual(code, 0)
            self.assertTrue(state["full_original_state_restored"])
            self.assertGreaterEqual(guards, 4)
            self.assertEqual((root / "Mods" / "Other.dll").read_bytes(), b"original other mod")
            self.assertEqual((root / "Mods" / "Autopilot.ScheduleI.dll").read_bytes(), b"previous mod")
            self.assertEqual((root / "UserData" / "settings.cfg").read_bytes(), b"original config")
            self.assertEqual((saves / "original.json").read_bytes(), b"original save")
            self.assertEqual((evidence / "changed-saves" / "original.json").read_bytes(), b"test changed save")

    def test_listener_cleanup_error_does_not_skip_filesystem_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            code, state, paths, _ = self.run_fake(folder, server_error=True)
            self.assertEqual(code, 1)
            self.assertEqual(state["phase"], "incomplete")
            self.assertTrue(state["original_saves_restored_verified"])
            self.assertTrue(any("listener cleanup" in error for error in state["cleanup_errors"]))
            self.assertEqual((paths[0] / "Mods" / "Other.dll").read_bytes(), b"original other mod")

    def test_uncertain_game_exit_skips_restore_and_preserves_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            code, state, paths, _ = self.run_fake(folder, uncertain=True)
            root, _, evidence, saves = paths
            self.assertEqual(code, 1)
            self.assertFalse(state["owned_game_exit_verified"])
            self.assertFalse(state["full_original_state_restored"])
            self.assertIn("restore_pending", state)
            self.assertEqual((saves / "original.json").read_bytes(), b"test changed save")
            self.assertEqual((evidence / "backup" / "saves" / "original.json").read_bytes(), b"original save")
            self.assertEqual((root / "Mods" / "Autopilot.ScheduleI.dll").read_bytes(), b"reviewed new mod")

    def test_unknown_new_data_remains_intact_and_marks_restoration_incomplete(self):
        with tempfile.TemporaryDirectory() as folder:
            code, state, paths, _ = self.run_fake(folder, new_unknown=True)
            self.assertEqual(code, 1)
            self.assertTrue(state["original_saves_restored_verified"])
            self.assertFalse(state["full_original_state_restored"])
            self.assertTrue((paths[3] / "unknown-new.json").exists())
            self.assertTrue((paths[0] / "UserData" / "unknown-new.cfg").exists())
            self.assertIn("unknown-new.json", state["new_files_pending"]["saves"]["files"])

    def test_explicit_new_fixture_is_preserved_in_verified_reversible_quarantine(self):
        with tempfile.TemporaryDirectory() as folder:
            code, state, paths, _ = self.run_fake(folder, fixture=True)
            self.assertEqual(code, 0)
            self.assertTrue(state["full_original_state_restored"])
            self.assertFalse((paths[3] / "SaveGame_9").exists())
            saved = paths[2] / "task-created-saves" / "SaveGame_9" / "Game.json"
            self.assertIn("AutopilotEvidenceMono", saved.read_text())
            self.assertTrue(state["fixtures"][0]["quarantined_verified"])

    def test_original_save_directory_and_unmarked_new_directory_cannot_be_quarantined(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, _, saves = self.layout(folder)
            (saves / "old").mkdir()
            (saves / "old" / "Game.json").write_text('{"name":"AutopilotEvidenceOld"}')
            original = session.snapshot(saves)
            with self.assertRaises(ValueError):
                session.fixture_proof(saves, "old", "AutopilotEvidenceOld", "AutopilotEvidence", original)
            (saves / "unknown").mkdir()
            with self.assertRaises(ValueError):
                session.fixture_proof(saves, "unknown", "AutopilotEvidenceUnknown", "AutopilotEvidence", original)

    def test_video_path_never_overwrites_or_accepts_ads_devices_or_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = Path(folder)
            (evidence / "old.mp4").write_bytes(b"evidence")
            for name in ("old.mp4", "../outside.mp4", "stream.mp4:other.mp4", "CON.mp4"):
                with self.assertRaises(ValueError):
                    session.video_path(evidence, name)
            self.assertEqual(session.video_path(evidence, "new-evidence.mp4"), evidence / "new-evidence.mp4")


if __name__ == "__main__":
    unittest.main()
