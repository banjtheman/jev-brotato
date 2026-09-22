# Prompts for working with a coding agent

These tasks can be pasted into Codex, Claude Code or another coding agent after opening this repository. They are instructions for the coding agent operating the harness; the actual prompts sent to Jev live in the source code below.

First install the [TypeSafe AI skill](https://www.skills.sh/typesafe-ai/skills/typesafe-ai), then ask your agent to use it for TypeSafe-related work:

```bash
npx skills add https://github.com/typesafe-ai/skills --skill typesafe-ai
```

Node.js/`npx` is needed for skill installation only. The harness itself runs in Python. A **`JEV_KEY` is required for live model calls**, while offline tests and the offline demo need no key.

Use a local `.env` for `JEV_KEY`. Never paste the key into the conversation. Each live experiment below states an explicit budget; adjust it before sending the prompt if you want a different limit.

## Set up and test offline

```text
Set up this Jev/Brotato repository so I can run it locally. Use the installed
typesafe-ai skill and its live documentation guidance. Read README.md,
docs/getting-started.md, pyproject.toml and any applicable local instructions.

Identify a Python 3.10+ interpreter, create or reuse .venv, and install the
editable project with its video extra. Check whether ffmpeg is available.
Use the activated virtual environment's Python for commands. Run
python tools/doctor.py --offline, the offline demo, the test suite and mod
packaging. Do not make API requests or launch a live run for this setup task.

Check that a .env file exists or create it from .env.example without
overwriting an existing credential. Tell me to edit the file locally if the
key is missing; never ask me to paste a key into chat, and never print it.
Once configured, use python tools/doctor.py to check the game/key locally
without sending an API request.

Verify my platform and installed game version where possible. Only macOS
Steam Brotato 1.1.15.4 has been verified by this project. Report actual
results, missing dependencies and the exact next commands; do not claim
the game works based only on offline tests.
```

## Run one bounded recorded experiment

```text
Run one recorded Jev/Brotato experiment using this repository. Use the
typesafe-ai skill and read the setup guide and current CLI first.
I authorize one live credential-check request,
then one recorded run capped at 2400 seconds, 6000 requests and $0.50 in
estimated known input-token cost. Do not increase these limits or retry a
lost run automatically. Never reveal JEV_KEY.

Launch the bridged game, wait for the title screen, then start the runner
with a fresh session name before I begin wave 1. Tell me when to select my
character, starting weapon and difficulty; I will perform that setup. Use
the repository's existing controller, keep its policy unchanged during
this run, and preserve raw decisions and captures.

Stop on the actual terminal state or a configured limit. Read summary.json
and inspect the recorded result. Report the observed outcome, highest wave,
attempted/applied actions, known usage, estimated cost, unknown-usage count
and latency. Include setup and model only when observed or explicitly
provided. Do not turn a timeout, partial run or loss into a success claim.
```

## Improve the shop strategy and measure it

```text
Improve the Jev shop/build policy in this repository. Use the typesafe-ai
skill and its current question-design guidance. Read the baseline report,
brotato_agent/session.py and the game's menu-candidate bridge code.
The first recorded baseline died on wave 10 with 917 materials and six
base-tier mixed weapons. Treat that as evidence to investigate, not proof
that every unspent purchase was a mistake.

First distinguish missing or incorrect legal candidates from model
selection errors. Keep game legality in the bridge. Use the actual prompt
constant and supplied state/candidates rather than a second drifting copy
of the prompt. Make one focused change with a clear hypothesis, inspectable
decision fixtures, and tests for the behavior it is intended to improve.
Do not alter a running experiment or weaken observation freshness checks.

Complete the offline change and show the comparison procedure. Prepare a
bounded live comparison command but do not call the API or start new paid
runs in this task. A later comparison should hold setup/game version/model
and request limits as constant as practical, preserve every result, and
separate strategy effects from seed variation. Report losses and partial
runs honestly; do not claim an improved win rate from one lucky run.
```

## Render gameplay at 4× and menus at 1×

```text
Create a readable video from a completed recording in this repository.
If there are multiple completed sessions, show their names and summaries
so I can choose. Read the selected metadata, summary and timestamps; do not
make API calls or start the game for a rendering task.

Use tools/render_timelapse.py with combat at 4x and menu/result phases at
1x. Start with gameplay rather than an intro card. Determine the correct
source-recording start time instead of blindly copying a 60-second trim.
Use --intro 0 and a new output filename so existing exports are preserved.

Keep actual captured pixels and logged metrics synchronized. Show only
completed decisions at each timestamp, preserve selected-versus-applied
status, and label estimated costs. Do not invent missing data, screenshots,
game results or model explanations.

Inspect the opening, a shop/upgrade section and the ending. Check the MP4
codec, dimensions and duration, and provide the resulting video and poster
paths. Explain that --start-at uses source recorded seconds, not edited
video seconds.
```

## The prompts Jev actually receives

The runtime prompts are intentionally kept in one place each:

| Judgment | Source of truth | Context supplied by code |
| --- | --- | --- |
| Combat movement | [`INSTRUCTIONS` in policy.py](../brotato_agent/policy.py) | Observed player state, approximate candidate geometry, threats and the previous action. |
| Shopping, upgrades and crates | [`MENU_INSTRUCTIONS` in session.py](../brotato_agent/session.py) | Observed build, phase, wave and legal candidates with descriptions/costs. |

Jev returns a typed Choice, probabilities and confidence; it does not supply a prose explanation of its reasoning. To change behavior, edit the appropriate constant or its context/candidate construction, inspect representative decisions, and evaluate the resulting run. Do not describe generated narration as the model's actual thoughts.

The [TypeSafe HTTP API](https://docs.typesafe.ai/api) and [Choice guidance](https://docs.typesafe.ai/primitives/choice) describe the model interface. Execution, arithmetic, game legality, budgets and observation freshness remain ordinary code.
