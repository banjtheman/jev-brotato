# Hybrid: Jev moves, GPT-6 Astra builds. The first run won.

On 16 October 2026 a hybrid agent **won a full Brotato run: all 20 waves on Danger 0**,
finishing at 47 HP. Jev 1.13 chose every combat movement; GPT-6 Astra, run through the Codex
CLI at low reasoning effort, chose every shop, level-up and crate action. Setup was selected
by hand: Well-Rounded, starting SMG, `--shop-profile build-v5`, `--combat-profile combat-v2`,
the same settings as the earlier Jev-only build-v5 trials.

The split follows the time each decision has. Combat needs a move every few hundred
milliseconds. The shop, level-ups and crates pause the game, so an answer that takes seconds
costs no health. Astra receives exactly the state, legal options and instructions a decision
model gets for that menu, an output schema allows only a legal option id, and it writes its
reason (and, from wave 16 on, its build plan and the best option it passed on) into
`decisions.jsonl`. A failed or late answer takes no action. See
[`brotato_agent/llm_menu.py`](../brotato_agent/llm_menu.py) and `run --menu-llm` in the README.

## Result

| | Hybrid (this run) | Jev 1.13 alone, build-v5 |
|---|---|---|
| Waves cleared | **20 (victory)** | 11 and 7 in two trials |
| Combat decisions | 5,030 by Jev, about 94 ms median | all by Jev |
| Menu decisions | 135 by Astra, 5.9 s median, 0 errors | by Jev |
| Jev cost | $0.693 (combat-v2 inputs are large) | |
| Astra usage | about 2.93M input and 8.9K output tokens on a Codex subscription; no dollar price recorded | |

Astra's build: six ranged guns sharing ranged-damage scaling, four combines up to tier IV
(Laser Gun IV, Double Barrel Shotgun IV, Shredder IV at the end), 14 weapons and 31 items
bought, 32 rerolls, a 5% shop discount taken early, and crate items that did nothing for guns
recycled for materials (Toolbox, Ice Cube, Defective Steroids, Charcoal, Pencil, Crown).

## How the run was recorded

Two segments of one game, kept as separate recordings (recordings stay on the machine that
made them and are not part of this repository):

- Waves 1 to 16, stopped by the runner's `--max-cost 0.50` cap on Jev's estimated cost, in
  wave 16 combat with 0.8 s left and full HP.
- The same game picked up in the wave-16 shop and played to the win, with `--max-cost 1.00`.
  This segment also logs Astra's plan and runner-up.

`tools/join_brotato_recordings.py` was not used: it needs formal `--continue-from` provenance
from a paused game, which the second segment doesn't have. The game also paused itself twice in
the first segment when its window lost focus (Brotato's `pause_on_focus_lost` setting is on, with
no in-run toggle); the runner waited and resumed.

`tools/render_timelapse.py` titles hybrid runs "Jev + GPT-6 Astra play Brotato" and, for the
LLM's menu decisions, shows its written plan and reason in place of probabilities.

## Limits

One run on the easiest difficulty: it shows the split works, not by how much it beats Jev
alone. Shops and enemy waves differ between runs, and the Jev-only trials are not paired with
this one. Astra's answers are not deterministic: the same recorded shop gave "combine" on one
call and "next wave" on another.
