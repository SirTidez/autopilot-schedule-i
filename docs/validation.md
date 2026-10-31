# Validation - Schedule I runtime integration, 2026-10-31

The requested implementation worker was selected as `gpt-6.1-sol` with high reasoning. Offline simulation,
reference compilation and actual game acceptance are separate evidence categories.

| Check | Result | Scope |
| --- | --- | --- |
| Python unittest discovery under `plugin/schedule-i/tests` | **51 passed** | Replay policy, real upstream loop with simulated peer, explicit callback selection, cancellation/provider gates, mouse limits, Pack Rat observation semantics and reversible test-driver failure/restore cases |
| C# `SafetyTests.csproj` console suite | **12 groups passed** | Fake-input lease/stop/release and endpoint/frame policy; no Unity, Win32 sink or provider |
| Final IL2CPP net6.0 Release build | **Passed, zero warnings/errors** | Selected beta Unity 2022.3.62f2 / MelonLoader 0.7.3 local references |
| Final Mono netstandard2.1 Release build | **Passed, zero warnings/errors** | Selected alternate-beta Managed Unity assemblies / MelonLoader 0.7.3 net35 references |
| Non-executing replay preview and harness preflight | Passed | No game/control/provider creation |
| Earlier Unity 2022.3 source API compile | Passed, zero warnings/errors | Combined runtime/editor source against local API assemblies; not a package import or IL postprocessor test |
| IL2CPP actual bridge load/hello | **Passed** | Owned game 0.4.7f12, Unity 2022.3.62f2, loader 0.7.3.0, protocol 1, matching PID/runtime |
| First session cleanup | **Passed** | Timer exited owned game, removed test DLL, restored originals; no unknown new save/config/mod files or cleanup errors |
| Independent post-session verification | **Passed** | SHA-256 matched all 5 original mod DLLs, 3 UserData files and 9,964 save files; owned PID and test driver absent at 11:37:54 UTC |

Final runtime DLL SHA-256 values:

- IL2CPP: `38495331be9ce5cb2555d1a10f237accecd3516dbf4aa72bf32b2f875460a867`
- Mono: `10a6413a9ff2aae7bef6302208790b6b2c9a11f90288dd208deb48009161aadb`

Builds used installed/cached references and a local-only restore source, with NuGet auditing disabled for that
offline check. No dependency vulnerability audit or latest-version claim is implied. Build output and test
backups/logs stay ignored/outside the repository; no game assemblies or assets are distributed.

## Blocked and unrun

The first IL2CPP session reached a verified bridge connection. Computer-use access to its game window then
timed out pending app approval and the workstation disconnected. The session's bounded timer performed cleanup.
**No gameplay commands, new fixture save, Pack Rat interaction or video recording occurred.** The pending UI
approval is not a denial or authorization to retry. Parent instruction currently allows cleanup/checkpoint only.

Still unrun: Mono in-game loading/hello; native input, actual cancellation/F8/focus-loss release and menu/scene
transitions; Pack Rat open/close acceptance; truthful owned-window video; multiplayer; performance; save progression
or compatibility testing; Unity package resolution/import, assembly-definition boundaries and IL postprocessing;
full upstream dependency suite; hosted-model, coding-agent, Aura/account, billing and external telemetry flows.

The earlier 14-test observer-only foundation used a different Steam-main loader (0.8.0-ci.2575) and older Mono
reference tree. Those historical results are superseded for current reference compilation by the selected dual
0.7.3 targets above; they do not prove current Mono gameplay support.

## Repeat bounded offline checks

```powershell
python -m unittest discover -s plugin/schedule-i/tests -p 'test_*.py' -v
python -m autopilot.schedule_i plugin/schedule-i/examples/observe.json
python -m autopilot.schedule_i_harness
dotnet run --project plugin/schedule-i/tests/SafetyTests.csproj -m:1
```

The C# suite targets installed .NET 10. Set `TMP`/`TEMP` to an explicitly writable directory if the sandbox's
redirected temp directory is inaccessible. Supply explicit matching local reference paths for each runtime
build as described in the adapter guide; never commit or redistribute those references.

Production acceptance requires the remaining actual game checks on each runtime. A simulated success or successful
compilation is not evidence of native gameplay behavior. See the durable handoff for private evidence location,
resumption requirements and unresolved provider/process-isolation decisions.
