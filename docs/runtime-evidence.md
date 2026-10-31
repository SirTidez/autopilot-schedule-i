# Initial Schedule I runtime evidence

Inspected read-only on 2026-10-31. This document records version evidence without distributing game binaries, private logs, account data, save files or absolute personal filesystem paths.

## Installed Steam beta

| Evidence | Observation | Limit |
| --- | --- | --- |
| Steam appmanifest for app 3164500 | beta branch; installed build `25796331` | Installed manifest, not a claim about the latest public release |
| Existing MelonLoader startup header | Schedule I `0.4.7f12`; Unity `2022.3.62f2`; Windows x64; IL2CPP | Existing log last modified 2026-10-23; no fresh game launch during discovery |
| Loader directory and version file | `0.8.0-ci.2575`; `net6`; generated IL2CPP assemblies available | Assembly availability does not prove that a new mod will load |
| Separate authoring ProjectVersion | Unity `2022.3.62f2` | Authoring version only |

The earlier Unity `2022.3.32f1` reference is not the version observed in the installed game's startup header. The port targets Unity 2022.3 APIs and must still be exercised against each actual game/loader build.

## Separate Mono reference tree

A separate alternate-beta installation contains `MonoBleedingEdge`, `Schedule I_Data/Managed`, MelonLoader `net35` and `net6`, and legacy/new Unity input assemblies. Its retained startup log is old and incomplete. Its game version and loader/runtime acceptance are **unverified**; do not describe it as the current Steam beta or evidence of current Mono support.

Use explicit reference-directory build parameters. Never copy a game's managed or generated assemblies into this repository. Build output belongs in ignored local directories and installation remains a separate manual step.

## Architecture decision

Upstream Autopilot `48966d174939487cc759fdf8560f2bed6e717676` is an editor-oriented playtesting service with a Unity 6 package, code-reading agents and optional hosted services. Schedule I is a shipped Unity 2022.3 player with no source project or editor package-loading seam. Changing `package.json` alone cannot port that system.

The fork therefore separates three boundaries:

1. A local Windows input adapter owns bounded actions and input release. It must validate the foreground game's executable and reject focus loss rather than send gameplay input into other applications.
2. An optional MelonLoader bridge observes the active scene and main camera pose, together with runtime/focus metadata. It does not infer a player body pose, add arbitrary game reflection commands, patch gameplay, launch an agent, or upload observations.
3. The existing Unity package receives API guards for 2022.3 authoring. Editor tests and generated-code behavior require a later editor validation window.

The initial Schedule I path supports scripted replay and observations. It does not automatically connect the upstream websocket service, generate harnesses from proprietary game source, supply cloud inference, or establish full autonomous gameplay. Future service integration must preserve explicit user control over providers and data sharing.

## Acceptance still required

Offline tests should prove action validation, bounded holds, foreground checks, cancellation and release through simulated backends. Compiling against local references proves API binding only.

After source review and in a coordinated idle window, test the bridge and adapter on a disposable single-player save. Verify loader startup, observations, game focus, movement/interaction bindings, cancellation, foreground loss, menu transitions and quitting while actions are held. Repeat separately for each supported Mono and IL2CPP game version. Do not run these checks on an existing multiplayer session or treat them as save-compatibility proof.
