# Contributing

Useful contributions include reproducible control fixes, better state/candidate construction, measured policy improvements, recording/rendering improvements and verified support for additional game versions or platforms.

Start with [getting started](docs/getting-started.md) and the [baseline report](docs/full-run-01.md). The only verified game setup is macOS Steam Brotato 1.1.15.4 in solo play. Own and use your own game installation when testing.

## Local development

Create a Python 3.10+ virtual environment, activate it, and install from the repository root:

```bash
python -m pip install -e '.[video]'
python tools/doctor.py --offline
python -m unittest discover -s tests -v
python -m brotato_agent demo
python tools/package_mod.py
```

These checks make no API requests. Video encoding additionally needs `ffmpeg` on PATH. A game-script load check is available on the verified setup with `python tools/launch_brotato.py --check`; close another bridged game before running it.

Keep changes focused. For a behavior fix, add a test that reproduces the failure or checks the meaningful boundary. A rendering change should be visually inspected using a short recording or a clearly labeled synthetic fixture, including its opening, a menu transition and ending. Do not label fixture images as gameplay.

## Keep these properties intact

- Bind game control to localhost, validate legal actions, and preserve manual movement priority and short movement leases.
- Reject stale or mismatched observations. Keep menu actions tied to the scene, phase and candidate fingerprint, and consume each observation before executing it.
- Keep credentials in the local Python environment. Never log a key or include `.env`, private environment files or captured credentials in a PR.
- Keep arithmetic, game legality and execution in code. The prompt constants in [policy.py](brotato_agent/policy.py) and [session.py](brotato_agent/session.py) are the runtime source of truth.
- Preserve timestamp alignment, honest selected/applied status and unknown usage in recordings. Rendering must not call the model or create future decisions early.
- Do not commit commercial game assets, PCKs, extracted game source, saves or unreviewed bulk recordings. New code in this repository is MIT licensed; retain required notices for any third-party code you introduce.

## Report evidence with a change

For a control or compatibility fix, state the operating system, game version, setup, observed failure, changed behavior and checks actually completed. Offline tests alone do not verify a Godot hook in a running game.

For a policy experiment, record the code revision, concrete model ID, game version, character, difficulty, starting weapon, limits and every outcome. Include attempted/applied actions, error categories, known token usage, unknown-usage requests and latency. Compare similar setups across enough runs to distinguish a promising hypothesis from a measured improvement. Keep losses and partial runs in the report.

The historical recordings are not bundled with a fresh clone. Generate your own local recordings or add small synthetic fixtures that are explicitly labeled. When sharing evidence in an issue or PR, use a concise redacted excerpt or an intentionally published artifact; do not link to your private filesystem or ignored local recording files.

Live requests consume the contributor's API allowance. State explicit time/request/cost limits before running an agent-driven experiment. The estimated-cost stop condition is not a provider billing cap. Avoid changing the policy while a measured run is active.

## Pull requests

Describe the concrete problem and the behavior after the change. List relevant verification and any remaining platform or gameplay limitations. For newly supported platforms, include an actual game launch/control test before describing support as verified. Do not claim a win, performance improvement or compatibility result that was not observed.
