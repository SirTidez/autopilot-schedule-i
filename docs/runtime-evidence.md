# Schedule I runtime evidence - 2026-10-31

This record separates installation discovery, reference compilation and actual loaded runtime evidence.
Proprietary assemblies/assets, private logs, saves and absolute personal paths are excluded.

## Selected required targets

| Target | Installed game / Unity | Loader | Acceptance evidence |
| --- | --- | --- | --- |
| SIMM beta | IL2CPP 0.4.7f12 / 2022.3.62f2 | MelonLoader 0.7.3, net6 | Final build passed; actual owned-game protocol hello verified all versions/runtime/PID |
| SIMM alternate-beta | Mono 0.4.7f12 Alternate / 2022.3.62f2 | MelonLoader 0.7.3, net35 refs | Final Mono build passed; current version independently verified by SIMM discovery; live bridge not yet tested |

The selected alternate-beta's old retained startup log describes f6; current globalgamemanagers inspection
identified f12. The earlier Steam-main discovery found a different loader, 0.8.0-ci.2575, and Steam build 25796331.
That installation is not the chosen test target. Unity 2022.3.32f1 from older work is not the observed current Unity.
Installed versions are not claims about the latest public release.

## Architecture and protocol boundary

Upstream `48966d174939487cc759fdf8560f2bed6e717676` assumes an editor/source-oriented playtesting system and
optional hosted services. A shipped Unity player cannot load its editor package; a version-number change is
insufficient. This fork uses an actual MelonLoader bridge plus the original upstream harness runtime:

1. The mod performs bounded typed Unity scene/camera/controller-candidate and optional uGUI reads on the main
   thread. It connects only to an explicitly enabled literal-loopback WebSocket, identifies runtime/version/PID,
   and accepts no arbitrary reflection/source-export/save/reset requests.
2. Separately enabled native input owns short renewable leases and enforces focus/F8/stop/disconnect release
   with an independent timer and hard control budget. The first live test did not exercise this path.
3. The registered retail engine uses upstream Runtime/Harness/Option/Step through a restricted Link, explicit
   decision callback/single-option route or caller-owned client. No provider/account/data-sharing route is selected.
4. A standalone stdlib replay adapter remains available. Unity 2022.3 editor API guards support separate owned
   authoring projects; package import and engine execution are separate pending validations.

Camera/controller candidates are not verified player identity. uGUI active/alpha hierarchy is not proof of visual
visibility or item semantics. Optional typed UI reads support test evidence, not UI actions or arbitrary methods.

## Actual session and rollback

The guarded beta session loaded Autopilot Schedule I 0.2.0 and emitted protocol 1 with engine `schedule_i`,
adapter `melonloader`, runtime `IL2CPP`, game 0.4.7f12, Unity 2022.3.62f2, loader 0.7.3.0 and the owned PID.
No gameplay command, save fixture or recording occurred because game-window app approval timed out and the
workstation disconnected. The session timer exited its owned game and restored original files.

After reconnect the journal was `finished`, all restoration flags true, no cleanup errors and no unknown new
mod/config/save files. Independent SHA-256 reads matched 5 mod DLLs, 3 UserData files and 9,964 save files.
A read-only privileged process query at 11:37:54 UTC confirmed owned game/test driver absent. Backups and raw
loader evidence remain private and local. No cleanup uncertainty remains for this session.

Both installs contain Pack Rat 3.1.1 according to retained logs, matching Core libraries and B toggle configuration.
Presence is not compatibility proof. The reviewed acceptance harness requires a newly owned disposable save and
observed panel identity; its open/close and cancel checks remain unrun on both runtimes.

Full acceptance still requires separate live Mono and IL2CPP input/observation/cancellation checks and truthful
owned-window video. Do not describe this handshake as completed autonomous playtesting or claim hosted-provider
integration from the explicit-client seam. See `validation.md` and the durable handoff.
