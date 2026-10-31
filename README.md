# Autopilot for Schedule I

SirTidez's personal fork of [RamenVR Autopilot](https://github.com/ramenvr/autopilot), based on upstream `48966d174939487cc759fdf8560f2bed6e717676`. The Apache-2.0 license, notices and history are preserved. This independent project is unaffiliated with TVGS or RamenVR.

## Implemented runtime path

- A MelonLoader runtime bridge with separate **IL2CPP and Mono** builds, typed scene/camera/controller-candidate observations, read-only uGUI observations and explicitly enabled keyboard/mouse input.
- Integration with the original **Runtime/Harness/Option/Step** loop, using a caller-selected local decision callback, single-option harness or explicitly supplied SDK client. The retail profile rejects editor source export, account and automatic provider routes.
- Request-boundary cancellation and an independent native input watchdog: renewable leases, F8/focus/disconnect release, permanent stop revocation and a 60-second control budget starting at the first action.
- A reviewed Pack Rat acceptance harness and reversible test driver with hash-verified backups, owned-window-only video capture and explicit incomplete-restoration reporting.
- A separate dependency-free replay adapter and Unity 2022.3 API compatibility changes for owned authoring projects.

See the [runtime setup guide](plugin/schedule-i/README.md). Start with the non-executing preflight:

```powershell
python -m autopilot.schedule_i_harness
python -m unittest discover -s plugin/schedule-i/tests -p 'test_*.py' -v
```

Connection, native control, diagnostics and file observation are separate launch-time opt-ins. No model provider, credentials, persistent service or telemetry upload is enabled automatically. A reviewed local decision callback exercises the actual upstream loop; it does not establish hosted-model autonomy or prompt-to-harness generation.

## Verification and limits

**Experimental; full gameplay acceptance is incomplete.** Both selected reference targets are Schedule I `0.4.7f12`, Unity `2022.3.62f2`, MelonLoader `0.7.3`: beta IL2CPP and alternate-beta Mono. Both final runtime builds passed with zero warnings/errors; **51 Python tests and 12 C# safety groups passed**.

One guarded IL2CPP session loaded the bridge and completed a version/PID-verified handshake. The session timed out after workstation UI access became unavailable; no gameplay commands or video recording ran. Cleanup exited the owned game and restored original mod DLLs, configuration and 9,964 save files, with independent hash verification. **Live Mono loading, Pack Rat interaction, real input/cancellation and video remain unverified.**

Pose observations identify a camera and an explicitly uncertain controller candidate. Verified player authority, navigation, inventory/quest semantics, arbitrary reflection, source export, save/reset commands and multiplayer control are unsupported. Reviewed Python callbacks remain cooperative code, not a hard process sandbox. Unity package import and IL postprocessing remain untested.

See the [validation record](docs/validation.md), [runtime evidence](docs/runtime-evidence.md) and [durable handoff](docs/HANDOFF-2026-10-31.md). Game assemblies, assets, saves, private logs and raw backups/video are not distributed.

## Upstream capabilities

The [preserved upstream README](README.upstream.md) describes the original Unreal, Unity and Godot system and its optional hosted/account flows. Use the dedicated retail Schedule I path above for this fork. The original [contributor guide](CONTRIBUTING.md) and [license](LICENSE) remain available.

Development and the default branch are `schedule-i`. Publish to your personal fork; retain the upstream remote for fetching updates.
