# Schedule I compiled-player contract

This is the personal MelonLoader runtime bridge, engine `schedule_i`, not a Unity source project.
Use `autopilot.schedule_i_harness.run_harness` with a reviewed Harness instance and an explicitly supplied
decision callback/client. It executes the original Autopilot Runtime/Harness loop and writes a local trace.

Only the bridge's advertised typed operations are available: context, session status/stop, ping, leased digital
keys/mouse motion, camera/controller-candidate pose, bounded object discovery/transforms/view, and controller
dimensions. All coordinates use the upstream kit frame (X forward, Y right, Z up, centimetres).

`at` exists only when the main camera has a CharacterController ancestor. This is a candidate, not verified
Schedule I local-player authority. `eye` and view rotation are camera observations. Unknown speed, inventory,
quest state, navigation, UI semantics and player identity must not be invented. Project/source export, arbitrary
`rc` reads/writes/calls, save/reset, pause/restart, UI actions and multiplayer authority are unsupported.

The operator must start a disposable test installation/session and explicitly opt into the bridge and control.
Each held input has a renewable short lease; focus loss/F8/stop/disconnect/unload release owned inputs. Native
control is time-bounded independently of Python. No account, SDK client, telemetry or service is selected by
this adapter. Choosing and authorizing a coding agent and a decision provider is a separate step.
