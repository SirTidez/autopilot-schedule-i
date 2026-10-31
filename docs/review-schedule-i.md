# Schedule I integration review

Review scope: `autopilot/schedule_i_harness.py`, the registered engine/profile, and upstream Runtime/kit/CLI changes.
This review used source inspection and simulated peers; it did not launch a game, install a mod, or call a provider.

Findings sent to the parent implementation owner before overlapping edits:

1. **Cancellation must reach the request boundary and setup.** `run_harness` checks cancellation through Runtime's
   loop stop callback, but Runtime runs `h.setup(game)` before that check. `RestrictedLink.validate` does not inspect
   the cancellation event. Pre-cancelled or slow setup/executor code can therefore send new input after cancellation.
   Make the link cancellation-aware, revoke non-cleanup operations on stop, and independently send stop/release on
   cancellation or deadline rather than waiting for a cooperative Runtime tick. Native leases remain an independent
   fallback, not a replacement for the requested Python cancellation.

2. **Wildcard kit imports still import the SDK.** `kit.__all__` lists `Choice`, `Noul`, and `Score`; Python's documented
   `from autopilot.kit import *` resolves those names through `__getattr__` immediately. A normal upstream-style local
   harness therefore still requires the hosted SDK merely to import. Lazy callable constructors can preserve the
   exports while importing the SDK only when an extra typed question is explicitly constructed.

3. **Stop must revoke subsequent requests.** `RestrictedLink.stop` sends cleanup frames but leaves the Python wrapper
   usable. A delayed observer/executor can keep reading/posting after teardown. The C# stop latch blocks further native
   control, but explicit wrapper revocation is still needed for orderly Python ownership and cleanup.

4. **Arbitrary reviewed Python is cooperative, not a hard process sandbox.** Upstream `observer.shutdown(wait=True)`
   waits for an in-flight `observe`; setup, generator steps and callbacks can also block. ThreadPoolExecutor workers
   can keep Python alive at exit even after `shutdown(wait=False)`. Native control remains time-bounded, but a hard
   process-duration guarantee requires process isolation or separately enforced cooperative-task contracts.

The explicit client/callback/single-option route check prevents implicit provider selection. No default client creation
was found in Runtime; `telemetry=None` bypasses still/media collection. The retail `cli.prepare`/`cmd_run` guards reject
the editor source-export/restart path. `ScheduleI.pose` preserves camera/controller-candidate uncertainty. Trace files
remain local; an explicitly supplied client owns any data transfer performed by that client.

Additional hardening opportunity: `bridge.serve_local` currently uses the upstream unlimited WebSocket receive size.
The `schedule-i` role should match the mod's 64 KiB message bound without changing other engine profiles.

## Fixes applied after coordination

- The editor `run_prompt` and service entry/rebind paths now reject Schedule I before key loading, classification,
  feed startup, memory or account sends. Executed entry-function tests use tripwires for those side effects.
- Pre-cancelled runs send cleanup and fail before constructing Game/Runtime or calling setup. Runtime also checks
  its stop callback before setup. Restricted requests check cancellation and a wall deadline, and stop permanently
  revokes non-cleanup requests. An independent daemon watchdog sends release/stop during blocked setup/observe/
  executor work. Native leases and focus/F8 controls remain independent.
- Callback exceptions and invalid decisions set the cancellation event and record a safety fault. No SDK/provider
  fallback is selected. The normal kit wildcard import is SDK-free; Choice/Noul/Score constructor proxies return
  real SDK instances only when explicitly constructed. `isinstance`/`issubclass` checks delegate to the SDK.
  Code requiring exact SDK class identity or SDK model subclassing should explicitly import from `typesafe_sdk`.
- Relative mouse actions are fully budgeted before dispatch, quantized, and split into at most 200 counts per
  native request, within 4000 total counts per wrapper session. Cancellation and async faults are checked between
  chunks. Mouse-count or request-budget rejection emits none of the rejected logical action.

The optional typed `ui` read is allowed only when advertised by the connected bridge; it is not required for older
runtime peers, and `ui_act` remains unsupported. Base capability checks still require the same nine runtime operations.

Validation: 36 offline Python tests passed, including all 27 existing tests. Added regressions cover early provider
gates, wildcard imports, pre-cancel, cleanup while setup is blocked, stop revocation, callback errors, mouse chunks
and optional UI capability enforcement.
No game launch, deployment, OS input or provider calls were used for this verification.

The cooperative Python limitation above remains explicit: arbitrary reviewed Python can block a thread or process.
The watchdog revokes input independently, but cannot safely kill that Python code. Hard process isolation was not
added as an unreviewed production change. The caller should use bounded/cooperative callbacks and harness functions.
