# Schedule I personal runtime port

This personal Autopilot port provides a MelonLoader runtime bridge for retail Windows Schedule I,
plus a separate bounded external replay adapter and optional local observation files. The runtime bridge
speaks a documented subset of upstream protocol 1 and supplies typed Unity reads and guarded OS input.
It does **not** implement the full editor bridge or game-source export. Neither component launches the game,
signs into a service, starts a model/service, or downloads dependencies at runtime. The bridge connects only
to an explicitly enabled local WebSocket service; the replay adapter needs no service.

Both required reference targets are Schedule I 0.4.7f12, Unity 2022.3.62f2, MelonLoader 0.7.3:
beta IL2CPP and alternate-beta Mono. Both final builds passed. One IL2CPP bridge handshake was verified;
live Mono loading and all gameplay/input acceptance remain unverified. No mod is automatically installed.

## MelonLoader runtime bridge

Build the matching runtime DLL using the instructions below. Installation and game launch remain deliberate
manual steps; there are no install hooks. The following are separate launch-time opt-ins:

| Flag | Meaning |
| --- | --- |
| `-AutopilotScheduleIConnect` | Connect out to an already running local bridge service |
| `-AutopilotScheduleIControl` | Also permit bounded input; ineffective without Connect |
| `-AutopilotScheduleIUrl=ws://127.0.0.1:8765/v1/bridge` | Optional service endpoint; only literal loopback IPs and this path are accepted |
| `-AutopilotScheduleIObserve` | Independently enable local camera snapshot files |
| `-AutopilotScheduleIDiagnostics` | Show local connection/control/owned-input/stop diagnostics in the game window |

All are off by default. There is no auto-reconnect or service startup. Connections use `engine=schedule_i`,
`host=game`, `adapter=melonloader`, `source_export=false`, `share=false`, and feature names `timed_hold` and
`schedule_i_runtime`. The Python Schedule I engine provides the corresponding restricted upstream harness seam.

The C# bridge supports `context`, `ping`, `session` status/stop, `key`, `pose`, `movement`, `find`, `transforms`,
`view`, and optional read-only `ui`. Socket work uses background queues; Unity reads and request dispatch run in `OnUpdate`. Incoming
and outgoing queues are limited to 256 items, messages to 64 KiB, JSON nesting to 32, and dispatch to 32
requests per frame. Scene hierarchy scans examine at most 2048 objects from up to eight loaded scenes;
find/transform results cap at 128, view results at 64. Results explicitly disclose possible truncation.
Filters accept only typed GameObject/Camera/CharacterController/Renderer classes, not game class reflection.

`pose.eye/yaw/pitch` describe the tagged main camera. A CharacterController **ancestor of that camera** may
add candidate `at`, `velocity`, and `on_ground`, labelled `camera_ancestor_character_controller_candidate` with
`player_identity_verified=false`. No global controller guess is made. Context reports an empty character
when there is no candidate. Movement exposes candidate dimensions/step/slope only; unknown walk/jump/gravity
parameters are null. View projects renderer centers; it explicitly does **not** test occlusion or the crosshair.
An object can therefore appear through a wall. Runtime IDs identify results; display names are separate.

Input is bound to this game process's foreground PID. F8, focus loss, errors, stop, disconnect and unload
release owned input. Scene changes clear leases. Default holds expire after 0.6s unless renewed by the upstream
runtime; explicit timed holds last at most five seconds. An independent 20ms timer releases leases even if
Unity frames stall. A fresh connection has a hard 60s control budget that renewals cannot reset. Stop/F8/focus
loss latch control off for that connection. The budget starts at the first valid action; capability probes,
releases and observation reads never arm control. Context distinguishes `control_enabled` from `control_active`.
A reconnect is never initiated automatically. Release failures are
recorded in the stop reason while all remaining keys are still attempted. As with any OS input mechanism,
forced process termination or an OS denial can prevent cleanup.

The optional `ui` operation scans active uGUI Canvas hierarchies, including persistent canvases, with caps of
32 canvases, 4096 visited nodes and 256 results. A name filter is limited to 128 characters. It reports names,
active state and ancestor CanvasGroup alpha, not text semantics, occlusion or proof that a panel is on screen.
Returned runtime IDs are cached (256 maximum); `ui {"objects":[ids]}` queries at most 64 tracked objects and
reports inactive/destroyed or unknown IDs explicitly. Scene changes clear this cache. Do not infer a closed
panel from its absence in a truncated scan. `session` status exposes `owned_inputs` for cancellation diagnostics.

There is no `rc`, arbitrary method call, UI click/action, source/codex export, screenshot, pause, scene loading,
restart, teleport, save manipulation or game SDK access. Unsupported requests return an explicit `unknown op`
error and release input. An empty-key capability probe emits no input and does not stop existing leases.
No Harmony patches or virtual Input System devices are installed. The bridge does not infer multiplayer
ownership or safety; a live test must establish which camera/controller represents the local player.

## Preview and offline validation

From the repository root, Python 3.12+ with no extra packages:

```powershell
python -m autopilot.schedule_i plugin/schedule-i/examples/observe.json
python -m unittest discover -s plugin/schedule-i/tests -p 'test_*.py' -v
dotnet run --project plugin/schedule-i/tests/SafetyTests.csproj -m:1
```

Preview validates the entire JSON file and reports the frame count. It does not create Windows process handles,
emit input, read the game, or contact a service. The example contains only context/ping requests.
On managed Windows environments where the sandbox redirects the default temporary directory outside permitted
locations, set `TMP` and `TEMP` to an explicitly writable directory before running tests.
The C# console suite requires .NET 10 and tests fake input/endpoint policy only; it never instantiates the
Win32 sink, loads Unity/MelonLoader or connects to a service.

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

The external adapter reuses upstream protocol-1 request/response **envelopes** and FKey names:

```json
[
  {"type":"request","id":1,"op":"key","args":{"key":"W","mode":"hold","seconds":0.5}},
  {"type":"request","id":2,"op":"wait","args":{"seconds":0.5}},
  {"type":"request","id":3,"op":"key","args":{"mode":"release_all"}}
]
```

| Operation | Supported meaning |
| --- | --- |
| `key` | Digital `press`, duration-bounded `hold`, `release`, `release_all`; one-shot MouseX/MouseY relative counts |
| `context` | Capabilities, PID, game identity, local replay mode |
| `ping` | Successful local adapter heartbeat |
| `session` | `status` or stop/release the adapter; never quit/restart the game |
| `pose` | Fresh observer snapshot; **camera-only**, not the upstream player-pose schema |
| `wait` | Local extension for replay timing; not an upstream wire operation |

Keys: A–Z, Zero–Nine, SpaceBar, Enter, Tab, Escape, LeftShift, LeftControl, and left/right/middle mouse buttons.
MouseX positive moves right; MouseY positive moves up. Mouse axes support `mode=press` only, with finite signed
`value` up to 200 counts per request and 4000 total absolute counts per session/replay. Values are quantized
to integer OS counts (half counts away from zero). Axis presses have no hold/release/seconds/rate semantics.
The OS and game sensitivity settings apply; these are **not** calibrated degrees and OS acceleration can differ
from Unity virtual-device deltas. `ScheduleIGame.mouse(right=..., up=...)` validates both axes before sending
either, and `press("MouseX", value=...)` also works. No angular `turn()` or calibration is fabricated.

There is no absolute pointer, gamepad, other analog axis, input-action, UI-action, inventory, pathfinding, quest,
save/reset, multiplayer or arbitrary method/reflection support. Unsupported operations fail rather than return
fabricated success. The **external replay adapter** is not the registered MelonLoader engine: it cannot satisfy upstream
editor `bridge.REQUIRED_OPS` (`rc`, `ui`, `watch`, etc.) or safely export a retail game's source.
The registered `schedule_i` runtime profile instead advertises its actual restricted operation subset.

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

Only run harness code you review. This external replay API does not inherit the upstream privileged `Game`
interface. For actual upstream harness execution, use the separate restricted runner below.

## Original upstream harness loop

`python -m autopilot.schedule_i_harness` is a non-executing preflight. Live use requires an already reviewed
local Harness, an explicit decision route, an explicitly launched game bridge, and opt-in flags on both sides:

```powershell
python -m autopilot.schedule_i_harness --execute --reviewed-code --harness reviewed_harness.py --trace local-trace.jsonl --decision harness-callback --control --seconds 30
```

The reviewed harness must supply `choose(state, criteria)` for this callback route. Alternatively use
`--decision single-option` for harnesses with at most one legal option. The Python `run_harness` API accepts
a separately supplied SDK-compatible client; it never creates one or selects a provider. The runner uses the
original Runtime/Harness/Option/Step loop, with restricted typed bridge requests and local traces. Default editor
CLI/account/source-export routes reject this retail profile before loading keys or classifying prompts.

Pre-cancellation blocks setup, every request checks cancellation/deadline/revocation, and an independent Python
watchdog sends native stop during blocked setup/observe/executor work. Invalid local decisions cancel the run.
Reviewed Python code is cooperative and may block a thread or process exit; native leases remain independent.
This is not a hard process sandbox. Wildcard kit imports are SDK-free; constructing SDK question proxies or
using a caller-owned SDK client explicitly requires that dependency. Exact SDK class identity/subclassing code
should import the real SDK classes itself.

## Reversible acceptance tooling

`tools/schedule_i_test_session.py --help` describes the preview-by-default local driver. Explicit execution
backs up and hash-verifies save/config/mod files, isolates selected mod DLLs, starts one owned game, verifies
the bridge PID, and restores originals after verified game exit. It refuses reparse/overlapping paths, an
existing game, unbounded backups and overwriting evidence. Unknown new files remain pending; changed originals
are retained separately before restoration. Use it only during a coordinated single-player test window.

Its typed local command file can request observations, stop, or exact-owned-HWND Windows Graphics Capture
without desktop/audio fallback. The reviewed Pack Rat harness requires a proved newly created disposable save,
the exact observed panel name and default B binding. It observes the same panel open and close, then can hold
only LeftShift for an explicit cancellation check. Bag unlocking and save creation are separate operator fixture
steps. Sent keys alone never count as observed success. Keep raw evidence/backups private.

The first IL2CPP session verified loading/handshake and complete rollback, but UI access timed out before any
commands or recording. Pack Rat behavior, live input release, video and Mono loading are still unrun.

## Runtime mod build and deliberate install

`Autopilot.ScheduleI.csproj` is a **MelonLoader mod**, not a Unity editor package or support library.
`ObserverMod.cs` owns lifecycle and optional file sampling; `RuntimeBridge.cs` owns the connection/dispatch,
`RuntimeRead.cs` typed reads, and `RuntimeInput.cs` input/lease policy. These require neither S1API nor game-assembly
reflection. Candidate observations do not prove player identity or multiplayer authority. No Harmony patches are used.

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
