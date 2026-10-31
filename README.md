# Autopilot for Schedule I

SirTidez's personal Schedule I-focused fork of [RamenVR Autopilot](https://github.com/ramenvr/autopilot), based on upstream commit `48966d174939487cc759fdf8560f2bed6e717676`.

This fork adapts Autopilot's playtesting tools to the shipped Windows game and its Unity 2022.3 modding environment. It preserves the upstream Apache-2.0 license and history. It is an independent personal project, unaffiliated with TVGS or RamenVR.

## Schedule I path

The shipped game cannot load an editor package. Use the dedicated [Schedule I adapter and setup guide](plugin/schedule-i/README.md) for the runtime path:

- A dependency-free Python entry point for bounded local input replay, with foreground-process checks, cancellation and input release.
- An optional MelonLoader observation bridge built against your own local game references. Game assemblies and assets are never distributed here.
- Unity 2022.3 API compatibility changes in the original editor package for separate authoring projects. An authoring project is not required for the runtime adapter.

The Schedule I entry point does not start Aura, sign in, install an agent, or call a model provider. Upstream model-driven exploration, harness generation and editor integrations remain separate capabilities; the Schedule I adapter is currently a local scripted testing foundation, not verified autonomous gameplay.

## Compatibility and verification

The initial target is Schedule I `0.4.7f12`, Unity `2022.3.62f2`, Windows x64, IL2CPP, with MelonLoader `0.8.0-ci.2575`. A separate Mono reference tree is available for build checks. See [runtime evidence](docs/runtime-evidence.md) for the distinction between observed versions, offline validation and pending game tests.

**Status: experimental.** The [validation record](docs/validation.md) covers 14 passing offline tests, both observer builds and a Unity 2022.3 source API check. These checks do not establish in-game compatibility. No mod was installed into the live game and no gameplay automation was run during the initial port. Follow the adapter guide's manual acceptance checklist in a disposable solo save before relying on it.

## Upstream capabilities

The [preserved upstream README](README.upstream.md) describes the original Unreal, Unity and Godot system, including its optional hosted services and account flows. Those instructions are not the Schedule I setup path. The original [contributor guide](CONTRIBUTING.md) and [license](LICENSE) remain available.

Development is on the `schedule-i` branch. Push changes only to your own fork; keep the `ramenvr/autopilot` remote for fetching upstream updates.
