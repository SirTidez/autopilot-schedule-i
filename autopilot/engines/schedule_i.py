"""Schedule I's MelonLoader player bridge: typed observations and leased input, no source project."""

from .base import OpEngine


class ScheduleI(OpEngine):
    name = "schedule_i"
    title = "Schedule I (MelonLoader)"
    error_log = False  # No game-log/error detector is advertised by the current bridge.

    def name_of(self, path):
        return str(path)  # Runtime IDs are not hierarchy paths; view.name uses the same ID.

    def project_at(self, path):
        return None  # A retail player is not a Unity source project.

    def codex_path(self, rel):
        return None  # Never discover/copy/decompile a game's files as a source codex.

    def copy_source(self, project, codex_root):
        raise RuntimeError("Schedule I runtime does not export or read game source")

    def launch_command(self, *args, **kwargs):
        raise RuntimeError("Start the explicitly configured Schedule I test installation manually")

    def pose(self, game):
        p = self._pose(game)
        if not p.get("at"):
            from ..kit.game import GameError

            raise GameError("No camera-ancestor CharacterController candidate; camera pose is not player pose")
        result = super().pose(game)
        result["source"] = p.get("pose_source", "unverified")
        result["local_player_verified"] = False
        return result

    def kit(self):
        # The generic kit describes arbitrary property access/restarts and source reading.
        # A retail Schedule I harness must receive the narrower, accurate contract instead.
        return self.kit_section()
