# Schedule I personal runtime port

This is the local, scripted foundation of the personal Autopilot fork. It supports a retail Windows
Schedule I process through a small external adapter, plus an optional read-only MelonLoader observer.
It does **not** implement autonomous model-driven gameplay, the full upstream bridge, or game-source export.
Neither component launches the game, signs into a service, downloads dependencies at runtime, or sends data out.

The current reference target is Unity 2022.3, x64 IL2CPP, MelonLoader 0.8. Mono has a separate build target;
compilation alone does not establish compatibility with a current Mono release. No mod is automatically installed.

## Preview and offline validation

From the repository root, Python 3.12+ with no extra packages:

```powershell
python -m autopilot.schedule_i plugin/schedule-i/examples/observe.json
python -m unittest discover -s plugin/schedule-i/tests -p 'test_*.py' -v
```

Preview validates the entire JSON file and reports the frame count. It does not create Windows process handles,
emit input, read the game, or contact a service. The example contains only context/ping requests.
On managed Windows environments where the sandbox redirects the default temporary directory outside permitted
locations, set `TMP` and `TEMP` to an explicitly writable directory before running tests.

## Runtime input, when deliberately enabled

Review a replay before execution. The live command requires an explicit PID and the exact executable path:

```powershell
python -m autopilot.schedule_i my-reviewed-replay.json --execute --pid 12345 --exe 'D:\Games\Schedule I\Schedule I.exe'
```

Replace the placeholders yourself. The adapter verifies the process image, retains its process handle to detect
exit/PID reuse, waits five seconds for you to focus the game, then sends Windows input only while that PID is in
the foreground. It does not steal focus. `--countdown` accepts 0–30 seconds. The game and adapter must run at the
same Windows privilege level. Physical keyboard/mouse state can still interact with synthetic input; avoid holding
the same controls yourself during a replay.

Hold **F8** or press **Ctrl+C** to stop. Focus loss, process exit, cancellation, exceptions, and the 60-second
session limit trigger release of all inputs owned by the adapter. Each hold requires an explicit duration of at
most five seconds. A daemon watchdog polls live input leases, F8, focus, and cancellation every 20ms, including
when a local harness blocks. The watchdog terminates with the session and starts no persistent service.
Operating system denial, abrupt Python termination, or a machine crash can prevent release; no process can
guarantee cleanup after it is forcibly terminated. Release failures are surfaced, with all other releases attempted.

Replays are limited to 500 requests and 1 MiB; cumulative scripted waits/presses must fit 60 seconds.
All frames are validated before any live backend is created. Invalid commands stop a live session and release keys.

## Upstream compatibility boundary

The external adapter reuses upstream protocol-1 request/response **envelopes** and digital FKey names:

```json
[
  {"type":"request","id":1,"op":"key","args":{"key":"W","mode":"hold","seconds":0.5}},
  {"type":"request","id":2,"op":"wait","args":{"seconds":0.5}},
  {"type":"request","id":3,"op":"key","args":{"mode":"release_all"}}
]
```

| Operation | Supported meaning |
| --- | --- |
| `key` | Digital `press`, duration-bounded `hold`, `release`, `release_all` |
| `context` | Capabilities, PID, game identity, local replay mode |
| `ping` | Successful local adapter heartbeat |
| `session` | `status` or stop/release the adapter; never quit/restart the game |
| `pose` | Fresh observer snapshot; **camera-only**, not the upstream player-pose schema |
| `wait` | Local extension for replay timing; not an upstream wire operation |

Keys: A–Z, Zero–Nine, SpaceBar, Enter, Tab, Escape, LeftShift, LeftControl, and left/right/middle mouse buttons.
There is no camera mouse-motion, pointer, gamepad, analog axis, input-action, UI-tree, inventory, pathfinding, quest,
save/reset, multiplayer or arbitrary method/reflection support. Unsupported operations fail rather than return
fabricated success. The adapter is not registered in `autopilot.engines`: it cannot satisfy upstream
`bridge.REQUIRED_OPS` (`rc`, `ui`, `watch`, etc.) or safely export a retail game's source. `autopilot serve/play`
continue to be the upstream editor-agent paths and are not required for this port.

For local Python harnesses, `ScheduleIGame` preserves the useful subset of the upstream kit's names:

```python
from autopilot.schedule_i import ScheduleIGame, WindowsInput

# Explicit PID/path supplied by the operator; game must already be running and focused.
backend = WindowsInput(pid, executable_path)
try:
    with ScheduleIGame(backend, snapshot=optional_snapshot_path) as game:
        game.hold("W", seconds=0.5)
        game.wait(0.5)
        game.release("W")
        state = game.context()
finally:
    backend.dispose()
```

Only run harness code you review. No generated/downloaded harness loader is included. This API deliberately
does not inherit the upstream privileged `Game` interface. A future autonomous integration must implement
validated game observations and policies first, and explicitly choose any model/service it uses.

## Optional observer build and install

`Autopilot.ScheduleI.csproj` is a **MelonLoader mod**, not a Unity editor package and not a support library.
`ObserverMod.cs` owns startup, sampling and shutdown. It uses the stable Unity camera/scene seam, requiring
neither S1API nor game-assembly reflection. This seam is sufficient for scene/camera evidence only; it does not
identify a local player, infer multiplayer authority, or access gameplay systems. No Harmony/input patches are used.

Supply local references from the matching game/runtime. Assemblies are referenced with `Private=false` and must
never be copied into the repository or distributed. No game-specific path is hard-coded into the build:

```powershell
# IL2CPP: UnityRefs is MelonLoader/Il2CppAssemblies; LoaderDir is MelonLoader/net6.
dotnet build plugin/schedule-i/Autopilot.ScheduleI.csproj -m:1 -p:UnityRefs='LOCAL_UNITY_REFS' -p:LoaderDir='LOCAL_LOADER_DIR'

# Mono alternative: Managed Unity assemblies + matching Mono loader directory.
dotnet build plugin/schedule-i/Autopilot.ScheduleI.csproj -m:1 -p:RuntimeFlavor=Mono -p:UnityRefs='LOCAL_MANAGED_REFS' -p:LoaderDir='LOCAL_MONO_LOADER_DIR'
```

Build output is only `bin/`; the project has no post-build install/copy hooks. For a **later, deliberate manual**
install, copy only the matching `Autopilot.ScheduleI.dll` into that install's `Mods/` directory. Dependencies are
provided by the matching loader/game: nothing goes into `Plugins/` or `UserLibs/`, and the Unity editor package
is not copied into the retail game. Keep separate outputs for Mono and IL2CPP.

The observer remains disabled unless the game is launched with `-AutopilotScheduleIObserve`. When explicitly
enabled, it writes `UserData/AutopilotScheduleI/state-<PID>.json` four times per second and removes its files on
normal unload. It opens no port. Pass that file to the external adapter with `--snapshot` to use `pose`.
Snapshots include PID, timestamp, scene, Unity version, focus and optional main-camera eye/yaw/pitch. They use
the upstream kit coordinates: X forward, Y right, Z up, centimetres; yaw right, pitch up. A camera is not a player:
menus and cutscenes may have cameras too. Null means there is no tagged main camera.
The adapter rejects snapshots from another PID or more than two seconds old. Files stay local and are not
recorded by default. A filesystem failure disables observation with one warning.

Minimum later manual validation: load only the matching mod in a disposable/local test session, confirm the
opt-in and disabled states, inspect scene/camera snapshots, verify focus loss/F8/Ctrl+C release with a short benign
replay, and confirm normal unload cleanup. Do not infer live gameplay support from offline tests or compilation.

## Unity editor backport

The separate `plugin/unity/com.ramenvr.autopilot` package declares Unity 2022.3 and wraps the Unity 6
`Rigidbody.linearVelocity` / 2022.3 `velocity` difference. Its editor agent/service initialization and onboarding
are gated by `AUTOPILOT_ENABLE_EDITOR_AGENT=1`; the default window explains the local runtime path.
Enabling that environment variable restores upstream editor behavior, which can use model/account/network services.
The package is for projects you own, not injection into the Schedule I retail executable. Editor import and play
tests are separate from the dependency-free runtime tests above.

For a bounded compiler check without launching Unity, supply your own installed editor root and existing
matching package assemblies to `tools/check_schedule_i_unity.ps1`:

```powershell
./tools/check_schedule_i_unity.ps1 -EditorRoot 'LOCAL_UNITY_2022_VERSION_DIR' -InputSystemAssembly 'LOCAL_INPUT_SYSTEM_DLL' -UIAssembly 'LOCAL_UI_DLL' -TextMeshProAssembly 'LOCAL_TMP_DLL'
```

It compiles runtime/editor source together against the local API surface, with both input systems enabled,
using one compiler process and a local empty NuGet feed. It writes only ignored `artifacts/` under this adapter.
It does not validate Unity package resolution, assembly-definition boundaries, the IL postprocessor or engine
execution. Those require a later clean authoring-project import/test.

This fork retains the upstream Apache-2.0 [license](../../LICENSE), history and attribution. See the root README
for the scope and current validation evidence.
