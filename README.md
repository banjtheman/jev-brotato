# Jev plays Brotato

An open-source harness for letting [TypeSafe's Jev](https://docs.typesafe.ai/) play Brotato through a local game mod. It reads structured game state, asks Jev to choose a legal action, applies it, and records the run with probabilities, latency, token usage and estimated cost.

You choose the character, starting weapon and difficulty. The agent handles movement, supported shop actions, upgrades and crates until death, victory or a configured limit. Brotato handles automatic attacks.

**Verified setup: macOS, Steam, Brotato 1.1.15.4, solo play.** Windows, Linux and other game versions are unverified. You need your own copy of Brotato and a TypeSafe API key; no game assets or commercial game source are included.

## TypeSafe skill and API key

For live model decisions, you need **`JEV_KEY`** in your local `.env`. The offline demo and tests do not need a key.

When using a coding agent to set up or change the integration, install the [TypeSafe AI skill](https://www.skills.sh/typesafe-ai/skills/typesafe-ai):

```bash
npx skills add https://github.com/typesafe-ai/skills --skill typesafe-ai
```

The skill teaches the agent to use TypeSafe's current docs, typed judgments and prompting patterns. Node.js/`npx` is needed only for this skill installer; the game harness runs in Python. See the [agent prompts](docs/agent-prompts.md) for tasks to paste after installation.

## Try it

Prerequisites: Python 3.10+, Git, your Steam installation of Brotato, and `ffmpeg` on PATH for video export. Pillow is installed by the `video` extra below.

```bash
git clone https://github.com/banjtheman/jev-brotato.git
cd jev-brotato
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[video]'
python tools/doctor.py --offline
python -m brotato_agent demo
python -m unittest discover -s tests -v
```

Use your Python 3.10+ executable for the virtual-environment creation command. After activation, every `python` command uses that environment. The demo and tests above make no API calls and need no running game.

The repository includes only a placeholder `.env.example`. Copy it to `.env`, then edit the new file locally to set `JEV_KEY`. Keep the key out of chat, screenshots and commits. `.env` is ignored by Git. Check the local game/key configuration without an API call, then test the connection with one real, billable request:

```bash
python tools/doctor.py
python -m brotato_agent demo --live
```

Close any existing Brotato instance. In **terminal 1**, with the virtual environment active:

```bash
python tools/launch_brotato.py
```

Wait for the title screen. In **terminal 2**, enter the same repository and activate the same environment:

```bash
source .venv/bin/activate
python -m brotato_agent run --session my-run-01 --seconds 2400 --max-calls 6000 --max-cost 0.50
```

Now select your character, weapon and difficulty in the game and start the wave within two minutes. Starting the recorder on the title screen captures the opening wave. The command uses real API calls; its limits are 40 minutes, 6,000 requests and $0.50 in estimated known input-token cost. Use a new session name for each run.

Press **Ctrl+C** in terminal 2 to stop the agent. Manual movement takes priority over the current movement command; stop the controller to retain control. Normal game actions use Brotato's usual save behavior.

See [getting started](docs/getting-started.md) for shorter experiments, troubleshooting, pricing and recording details. To have a coding agent help, use the [copy-and-paste agent prompts](docs/agent-prompts.md).

## Turn the run into a video

After recording finishes:

```bash
python tools/render_timelapse.py recordings/my-run-01 --speed 4 --menu-speed 1 --intro 0
```

This produces a silent 1920×1080 MP4, a dashboard poster and an edit map in the session directory. Combat plays at 4×; shops, upgrades, crates, transitions and the final result play at 1×. The dashboard shows actual gameplay and completed decisions, with their probabilities, latency, throughput, tokens and estimated cost.

To omit setup, add `--start-at 60` only if gameplay starts around second 60 in **your source recording**. This is a recorded elapsed time, not a position in the edited video. Use `--preview-only --poster-at 90` to inspect a poster from source second 90 without encoding a video. Rendering never calls Jev or controls the game.

## What happened in the first recorded run?

Danger 0, Well-Rounded, starting SMG: **cleared waves 1–9 and died on wave 10**. The session made 1,517 requests, applied 1,501 actions and used an estimated **$0.0923** in API usage. Median latency was **243.7 ms**. It finished with 917 unspent materials and six base-tier weapons—an obvious area for improving the build policy.

The [baseline report](docs/full-run-01.md) records the result and limitations. This single loss does not establish a win rate. Recordings are generated locally and are not included in a fresh clone.

## How it works

| Component | Responsibility |
| --- | --- |
| [Godot bridge](mods-unpacked/Jev-BrotatoBridge/README.md) | Observes the game and exposes bounded actions on `127.0.0.1:4243`. |
| [Combat policy](brotato_agent/policy.py) | Calculates movement candidates; `INSTRUCTIONS` asks Jev to choose one. |
| [Session runner](brotato_agent/session.py) | Handles combat and menus, budgets, recording and accounting; `MENU_INSTRUCTIONS` guides build decisions. |
| [Jev client](brotato_agent/jev.py) | Calls the typed Choice API and validates its response. |
| [Renderer](tools/render_timelapse.py) | Joins captured frames and completed decisions by recorded time. |

The launcher injects the original mod ZIP for that launch. It does not edit the game's base PCK or change Workshop subscriptions. Starting Brotato normally omits this development bridge. The API key stays in Python and is removed from the game's environment.

This remains an experimental controller: movement geometry is approximate, network latency matters, setup and pauses are manual, and winning builds are not guaranteed. The cost limit is an estimate, not a billing-enforced cap; one request can cross it, and unknown usage is reported separately. [Details and current price assumptions](docs/getting-started.md#cost-and-time-limits).

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for verification and experiment reporting. The harness is [MIT licensed](LICENSE); Brotato and third-party services retain their own terms. [Research notes](docs/research.md) explain the existing mods investigated before building this bridge.
