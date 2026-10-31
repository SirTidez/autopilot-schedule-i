# Initial port validation — 2026-10-31

The implementation worker was explicitly selected as `gpt-6.1-sol` with `high` reasoning. These results are offline
checks of the initial port. They are not evidence of a loaded mod or autonomous gameplay.

| Check | Result | Scope |
| --- | --- | --- |
| `python -m unittest discover -s plugin/schedule-i/tests -v` | 14 passed | Simulated input, deterministic clocks and temporary fake snapshots; no game or model calls |
| `python -m autopilot.schedule_i plugin/schedule-i/examples/observe.json` | Passed; 2 frames; `game_touched: false` | Validates preview and whole-replay validation |
| Python bytecode compilation | Passed | New adapter and offline tests |
| PowerShell parser check | Passed | `tools/check_schedule_i_unity.ps1` |
| Final observer Release build, `net6.0` | Passed; 0 errors, 0 warnings | References from the installed IL2CPP game and MelonLoader 0.8.0-ci.2575; no installation |
| Final observer Release build, `netstandard2.1` / Mono | Passed; 0 errors, 0 warnings | References from a separate older Mono tree; its game-version/runtime acceptance remains unverified |
| Final Unity source API check | Passed; 0 errors, 0 warnings | Runtime and editor source against installed Unity 2022.3.62f2 engine assemblies, local input/UI/TMP assemblies, both input systems enabled |
| `git diff --check` | Passed | Source whitespace validation |

The source API check compiles runtime/editor sources together. It does not reproduce Unity's assembly-definition
boundaries, import the package or execute its IL postprocessor. Its local input/UI/TMP references are not proof
that the package manager resolves the exact declared dependency versions. Use the compiler-check script's explicit
local-reference parameters to repeat this limited check.

The first observer build emitted `NU1900` because the NuGet vulnerability feed was unavailable. Final observer
checks used the already-installed/cached references and a local-only package source with auditing disabled for
that offline build. No package vulnerability audit is claimed.

## Not run

- Unity package import/resolution, IL postprocessor execution, Unity Test Runner, play-mode or GPU tests.
- Actual observer startup/unload inside Mono or IL2CPP, camera snapshots from the game, Windows input acceptance,
  or F8/focus-loss behavior in a real Schedule I session.
- Gameplay automation, multiplayer, save compatibility or performance testing.
- The full upstream Python suite, which requires upstream development dependencies. Existing upstream Python
  modules were not modified; the new adapter uses only the standard library.
- Hosted-model, Aura/account, billing, telemetry or external-agent flows.

No game process was launched, controlled or attached to; no mod was installed; no existing authoring project was
modified. No proprietary game assemblies, assets, private logs, credentials or personal machine paths are tracked.
Use the [adapter's manual acceptance steps](../plugin/schedule-i/README.md) for a later deliberate runtime test.
