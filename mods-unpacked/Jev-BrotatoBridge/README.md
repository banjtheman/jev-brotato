# Jev Brotato Bridge

An original, small Godot 3 ModLoader mod. It observes live combat and lets one local
client supply short movement vectors for player one. The game handles attacks.
A menu delegate offers legal purchases, weapon combinations/recycling, upgrades,
crate choices, limited rerolls, and advancing to the next wave. Setup is manual.
The bridge does not edit saves or bypass the game's menu callbacks.

The hook and observed fields were checked against the installed Brotato 1.1.15.4
scripts using Godot's script introspection. Other game versions may change them.
No other mod is required. The repository's `tools/launch_brotato.py` packages and
loads it into the installed macOS Steam game; the ordinary Steam loader does not
load arbitrary local development ZIPs. See the [run instructions](../../README.md).

## Wire protocol

Connect with TCP to `127.0.0.1:4243`. Each request and response is one UTF-8 JSON
object followed by a newline. The bridge echoes a string or number `id` (or null).
Only one client may connect at a time. No credentials, commands, or network
listeners other than this loopback socket are used inside the game. The Python
controller makes Jev requests separately; recording captures the game viewport.

Commands: `observe`, `move`, `release`, `act`, `record_start`, `record_stop`,
`record_status`. There is no arbitrary script execution or save-editing command.

Observe first:

```json
{"id":1,"command":"observe"}
```

The response is `{"id":1,"ok":true,"state":{...}}`, containing:

- `protocol_version`: `2`.
- `seq`: increasing observation number for this bridge instance.
- `observed_at_ms`: Godot's monotonic tick time; not a Unix timestamp.
- `scene_id`: current scene instance ID.
- `phase`: `combat`, `shop`, `upgrade`, `crate`, `paused`, `menu`, `gameover`, or `victory`.
- `terminal`: true for a confirmed death or victory.
- `menu`: `options` keyed by legal action name, a current-state `fingerprint`, and
  `status` (`actions`, `waiting`, `unsupported`, or `terminal`) plus a `reason`.
- `build`: character, owned weapons/items, effective primary stats, level, gold,
  and current purchasing currency. Items are grouped with counts.
- `player`: null outside a run, otherwise `id`, `position`, `velocity`, `health`,
  `max_health`, and `speed`. Speed is the game's current live stat.
- `enemies`: nearest 128 active enemies and bosses, deduplicated.
- `enemy_count`: uncapped enemy/boss count when a player exists.
- `projectiles`: nearest 128 enemy projectiles.
- `materials`: nearest 64 material pickups; `consumables`: nearest 32 consumables.
- `arena`: `{"min":{"x":0,"y":0},"max":{"x":0,"y":0}}`, or null when unavailable.
  These are the player's game-provided position limits.
- `wave`, `time_left`: current game wave value and timer seconds; null if unavailable.
- `controller`: whether a movement lease is active and its vector.

Positions are global game coordinates in pixels, with positive x right and
positive y down. Entity records contain `id`, `position:{x,y}`, and `velocity`.
Velocity is `{x,y}` when the game exposes it, otherwise null; null never means a
known zero velocity. Collections are sorted by distance to the player.

Apply a movement derived from that observation:

```json
{"id":2,"command":"move","observation_seq":1,"x":0.7,"y":-0.7,"ttl_ms":250}
```

A successful response includes `movement:{x,y}` and the effective `ttl_ms`.
Components must be finite numbers. They are clamped to [-1,1], and vectors longer
than one are normalized. The lease is clamped to 50–1000 milliseconds; its default
is 250. Zero movement is valid. The sequence must match the most recent
observation on this connection, which must be no older than 1000 milliseconds
and from the same scene and combat phase.

Stop movement immediately:

```json
{"id":3,"command":"release"}
```

Errors use `{"id":2,"ok":false,"error":"stale_observation"}`. Possible error
codes are `invalid_json`, `invalid_id`, `unknown_command`, `stale_observation`,
`not_in_combat`, `invalid_movement`, `menu_changed`, `invalid_action`, and
`action_unavailable`.

## Menu actions

After observing, select an exact key from `state.menu.options`:

```json
{"id":4,"command":"act","observation_seq":3,"action":"buy_0"}
```

Descriptions include item effects, weapon stats, prices or recycling gains, and
an action `kind`. The server rebuilds the available actions and checks the menu
fingerprint immediately before invoking the native game callback. Menu
observations expire after 30 seconds. Every successful action consumes its
observation before invoking the callback; observe again before another action.
This prevents a replayed request from buying twice. Invalidated or rejected
actions should be followed by a fresh observation.

Action kinds are `buy_weapon`, `buy_item`, `combine`, `recycle_weapon`, `upgrade`,
`take_item`, `recycle_crate`, `reroll`, and `next_wave`. Keys such as `buy_0` are
valid only for their accompanying observation. A purchase may use materials or
max HP depending on the character; the option identifies its currency.

An empty `options` dictionary is explicit: `status:waiting` indicates a native
button delay, wave transition, combat, or pause; `status:unsupported` indicates
setup needing player input; `status:terminal` indicates death or victory. The
runner should wait or stop according to the phase rather than invent an action.

Only solo runs are supported for menu decisions. Shop rerolls are limited to two
per wave and upgrade rerolls to one. Buying respects native affordability,
character restrictions, weapon capacity, and automatic combining. Recycling the
last owned weapon is not offered. The agent cannot enable endless mode or retry
a lost wave. A death is terminal even if the game offers a retry dialog.

## Recording

The recorded runner manages recording automatically:

```bash
python -m brotato_agent run --session my-run-01
```

The low-level recording commands delegate to the recorder child:

```json
{"id":5,"command":"record_start","session":"my-run-01","interval_ms":250}
{"id":6,"command":"record_status"}
{"id":7,"command":"record_stop"}
```

A session name uses 1–80 letters, digits, underscores or hyphens. Existing frame
indexes cannot be overwritten. `interval_ms` defaults to 250 and is clamped to
100–2000. Capture timing is best effort: each saved frame's actual elapsed time is
written to `frames.jsonl`. Captures are PNG files from the game viewport, under
the launcher's absolute `JEV_RECORDING_ROOT` directory. The recorder forces an
overdue viewport draw when macOS suppresses drawing for an occluded window.

Replies include `ok`, `active`, `path`, `frames`, `started_at_ms`, `now_ms`,
`interval_ms`, and `recording_error`. Tick values use Godot's monotonic clock.
Recording stops on `record_stop`, game exit, a capture/write error, or its
40-minute duration limit. A disconnected client does not itself stop recording;
the runner's cleanup reconnects if necessary and requests `record_stop`.

The Python runner writes `decisions.jsonl`, `metadata.json`, and `summary.json`
beside the frames, including probabilities, request latency, input/output tokens,
applied actions, unknown-usage request counts, and estimated cost. Cost is based
on validated reported input usage and the rate stored in that session; missing
usage is not treated as zero billing. API metrics are separate from viewport
capture. See the root README for pricing, limits, and persistent TLS behavior.

After recording finishes, render the timelapse without further API requests:

```bash
python tools/render_timelapse.py recordings/my-run-01 --speed 4
```

The renderer combines captured pixels with completed decisions at their actual
timestamps. It requires Pillow and `ffmpeg`.

## Control lifetime

Movement expires on its deadline, disconnect, scene change, pause, death, or
leaving combat. Manual movement always wins and clears the movement lease. All
other players retain their normal controls. The server continues handling
observations while the game is paused. Connection and buffer processing are
nonblocking, capped per frame, and an overflowing client is disconnected.

The bridge registers only the player's movement-method extension. Disabling the
mod and restarting restores the unmodified game. Installation packaging and the
Python agent are maintained at the repository root.
