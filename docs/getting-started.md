# Getting started

The verified game setup is **Brotato 1.1.15.4 on macOS through Steam, solo play**. You must own the game. The repository contains the agent and an original mod, not Brotato's assets or decompiled source. Windows, Linux and other game versions need separate verification.

If a coding agent will help with the integration, give it the [TypeSafe AI skill](https://www.skills.sh/typesafe-ai/skills/typesafe-ai):

```bash
npx skills add https://github.com/typesafe-ai/skills --skill typesafe-ai
```

This installer requires Node.js/`npx`; Python gameplay does not. Live Jev requests require your own `TYPESAFE_API_KEY`. Offline checks need neither that key nor a running game.

## Install the harness

Install Python 3.10+ and Git. Video rendering also needs `ffmpeg` on PATH. If you already use Homebrew on macOS, `brew install ffmpeg` installs it.

```bash
git clone https://github.com/banjtheman/jev-brotato.git
cd jev-brotato
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[video]'
```

Replace `python3` in the environment-creation command with your Python 3.10+ executable if needed. All subsequent `python` commands assume this environment is active. Run commands from the cloned repository: the launcher and rendering tools are repository scripts, and the editable install keeps the agent next to them.

The controller itself uses the Python standard library. `.[video]` adds Pillow for rendering and its tests; it does not install the external `ffmpeg` executable. If you only need the controller, `python -m pip install -e .` is enough, but the complete test suite includes Pillow-based rendering tests.

## Check offline before going live

```bash
python tools/doctor.py --offline
python -m brotato_agent demo
python -m unittest discover -s tests -v
python tools/package_mod.py
```

The offline doctor checks the source/Python setup and skips game/key requirements. The demo uses a synthetic observation and a labeled deterministic policy. None of these commands calls Jev or starts the game. Packaging produces `dist/Jev-BrotatoBridge.zip` without installing it.

Create `.env` from `.env.example` if it does not already exist, then edit the file locally:

```dotenv
TYPESAFE_API_KEY=your_key_here
```

Never paste the real key into an agent conversation or issue. Do not print the file when checking configuration. Existing environment variables take precedence over `.env`; the file is not executed as a shell script. For another file, put the global option before the command: `python -m brotato_agent --env /path/to/private.env demo --live`.

First check the game installation and whether the key is configured, without printing it or contacting the API:

```bash
python tools/doctor.py
```

The repository ships only a placeholder `.env.example`, never a usable `.env`. This next command makes **one real API request** against a synthetic observation:

```bash
python -m brotato_agent demo --live
```

It reports the chosen action, probabilities, model, latency and usage. It verifies API integration, not live game control.

## Launch and record

Close an existing Brotato instance first. Keep Steam available. In terminal 1, from the repository with the environment active:

```bash
python tools/launch_brotato.py
```

The launcher finds the usual macOS Steam installation, packages the bridge and starts the game with it. If your installation is elsewhere, provide the executable:

```bash
python tools/launch_brotato.py --executable "/path/to/Brotato.app/Contents/MacOS/Brotato"
```

This path override is not a promise of support for other operating systems. Use `python tools/launch_brotato.py --check` for a load/compile check that immediately quits; run it while another bridged game is not using the port.

Once the game reaches its title screen, open terminal 2, enter the same repository and activate the environment. Start recording **before** beginning the first wave:

```bash
source .venv/bin/activate
python -m brotato_agent run --session my-run-01 --seconds 2400 --max-calls 6000 --max-cost 0.50
```

Manually choose a character, starting weapon and difficulty, then enter the run within two minutes. The controller takes over supported combat and menus. A shorter first recording is also useful:

```bash
python -m brotato_agent run --session smoke-01 --seconds 120 --max-calls 300 --max-cost 0.05
```

Every session needs a new name using letters, digits, dashes or underscores. Existing sessions are not overwritten. The runner stops on death, victory, a configured limit, a recording failure, five consecutive Jev failures, or no available action for 120 seconds. Time spent on setup, menus and pauses counts toward the wall-clock limit.

Ctrl+C stops the controller. Manual movement wins over the active movement lease, but the running agent can send another command afterward; stop the controller to keep control. The game may save normal run progress. There is no automatic retry of a lost run or automatic endless mode.

The bridge is active only when launched this way. No base PCK is modified, and no Workshop subscription is changed. Launch Brotato normally to play without this development bridge.

## Read results and export video

The session directory under `recordings/` contains:

| File | Contents |
| --- | --- |
| `metadata.json` | Model, observed setup, request limits and price assumptions. Unknown setup fields remain unknown. |
| `decisions.jsonl` | Phase observations and completed decisions, including candidates, usage and applied/rejected status. |
| `frames.jsonl`, `frames/` | Actual viewport frames and their capture times. |
| `summary.json` | Terminal outcome, highest observed wave, totals, errors and latency statistics. |

The recorder targets a PNG every 250 ms at the game's viewport resolution. Actual times are retained. Frame capture and all generated logs stay local; `recordings/` is ignored by Git. The harness does not include the historical run's raw recordings in fresh clones.

After the run finishes:

```bash
python tools/render_timelapse.py recordings/my-run-01 --speed 4 --menu-speed 1 --intro 0
```

The output is a 1920×1080 H.264 MP4, PNG poster and `.edit.json` interval map. Combat is 4×; menus, upgrades, crates, transitions and the final result are 1×. No artificial decision pauses are inserted. The three-second outro summarizes the observed outcome.

To start directly with gameplay, find its beginning in your recording and pass that source time. For example, **only if your opening wave begins around source second 60**:

```bash
python tools/render_timelapse.py recordings/my-run-01 --speed 4 --menu-speed 1 --start-at 60 --intro 0 --output recordings/my-run-01/readable-menus.mp4
```

`--start-at` and `--poster-at` are seconds from the original recording's start, not the shortened video's timeline. `--intro 0` removes the opening card; `--outro 0` removes the closing card. The dashboard's recorded clock still refers to the source recording.

```bash
python tools/render_timelapse.py recordings/my-run-01 --preview-only --poster-at 90
```

The renderer keeps frames, phases and completed model results synchronized. It shows a pending request without displaying its eventual choice early. Costs and tokens are cumulative at the displayed source time. The final card uses the authoritative session summary, including usage retained from rejected responses.

## Cost and time limits

Recorded runs currently estimate cost at **$0.042 per million input tokens, with output tokens free**, checked September 22, 2026 against [TypeSafe's model pricing](https://docs.typesafe.ai/models). The rate is in `PRICE_PER_MILLION` in [session.py](../brotato_agent/session.py), and each session saves its pricing assumptions. Verify pricing before later experiments.

`--max-cost` is a stop condition based on known reported usage, not a provider billing cap. A final request can cross it. Responses without reliable usage increment `unknown_usage_requests`; their unknown cost is not silently invented. The time and request limits still apply.

The `jev-latest` alias can resolve to a new model over time. Record the returned model ID, game version, setup and policy revision when comparing experiments. A completed test suite or a synthetic demo does not establish a gameplay result.

## Troubleshooting and other commands

| Symptom | What to check |
| --- | --- |
| Connection refused | Start the game with `tools/launch_brotato.py`; a normal Steam launch does not inject the bridge. |
| Port 4243 unavailable | Close another bridged game. Only one game listener and one controller client are expected. |
| Standalone observation cannot connect | Stop the active runner first; the bridge accepts one client at a time. |
| Agent waits at a menu | Character/weapon/difficulty selection and pauses are manual. Select setup and start before the 120-second no-action timeout. |
| Authentication failure | Verify `TYPESAFE_API_KEY` (or `JEV_KEY`) locally and check for an overriding environment variable. Do not share the key. |
| Expired decisions | Inspect actual latency in the log; stale combat actions are intentionally discarded. |
| Missing frames / failed recording | Inspect the session summary and game log. Keep the game window available; do not label missing footage as recorded play. |
| Renderer cannot import PIL | Activate the correct environment and install `python -m pip install -e '.[video]'`. |
| `ffmpeg` missing | Install it separately and confirm `ffmpeg -version` works in the rendering terminal. |

With the runner stopped, observe the live game without an API call:

```bash
python -m brotato_agent observe
```

For combat-only control with manual menus and no video recording:

```bash
python -m brotato_agent play --seconds 60 --max-calls 120
```

Add `--policy heuristic` for the explicitly labeled deterministic comparison policy without API requests. `play` writes to `runs/`, whereas `run` writes a recorded session to `recordings/`.

See the [bridge protocol](../mods-unpacked/Jev-BrotatoBridge/README.md), [agent prompts](agent-prompts.md), and [baseline result](full-run-01.md) for further detail.
