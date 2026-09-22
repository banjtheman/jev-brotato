# Recorded baseline: Jev reached wave 10

On September 22, 2026, `jev-full-run-01` **cleared waves 1–9 and died during wave 10** on Danger 0. It did not win the run. Setup was selected manually: Well-Rounded, starting SMG, Brotato 1.1.15.4. Jev then selected combat movement, upgrades, shop actions and a crate decision. The policy remained unchanged during the recording.

This report summarizes one local recorded run. Its full frame capture, gameplay videos and decision log are not included in the public source repository. A small [machine-readable result](../examples/baseline-result.json) contains the reported aggregate measurements without local paths or credentials. You can generate your own captures and speed-adjusted videos using the [quickstart](getting-started.md).

## Measured result

| Metric | Observed value |
| --- | ---: |
| Terminal state | `gameover`, wave 10 |
| Recorded wall duration | 503.209 s — 8 min 23 s |
| Manual setup before first combat observation | Approximately 59.4 s, included above |
| API requests attempted | 1,517 |
| Actions applied | 1,501 — 98.95% of attempts |
| Input tokens | 2,196,540 |
| Output tokens | 101,004 |
| Requests with unknown usage | 0 |
| Estimated API cost | **$0.09225468**, approximately 9.23 cents |
| Median / p95 API latency | 243.7 / 359.7 ms |
| Captured frames | 1,993 PNGs at 1280×720 |
| Returned model | `jev-1.13.0` |

The cost is an estimate from reported usage, not an invoice: `2,196,540 / 1,000,000 × $0.042`. TypeSafe lists Jev 1.13 at **$0.042 per million input tokens, with output tokens free**, verified September 22, 2026. [Exact pricing source](https://docs.typesafe.ai/models).

Accounting includes usage from responses that failed local decision validation. Summing only successful `judgment` objects would omit 6,910 input and 317 output tokens. The session summary retains that usage.

## Progress and decisions

Each row includes combat and the menus after that wave. “Gold leaving shop” is the observed material balance when Jev selected `next_wave`.

| Wave | Calls / applied | Notable applied decisions | Gold leaving shop |
| --- | ---: | --- | ---: |
| 1 | 72 / 70 | Bought Medical Gun and Scissors. | 5 |
| 2 | 99 / 97 | +3 Max HP, +1 Ranged Damage; bought Sharp Tooth. | 25 |
| 3 | 110 / 109 | +5% Damage; bought Banner. | 18 |
| 4 | 134 / 133 | +1 Ranged Damage; left without purchasing despite affordable offers and two empty weapon slots. | 80 |
| 5 | 149 / 147 | +30 Range, +3 Max HP; bought Mastery, trading 3 Ranged Damage for 6 Melee Damage. | 146 |
| 6 | 170 / 168 | +1 Ranged Damage; bought Rock, Plank and Medical Turret; filled six weapon slots. | 169 |
| 7 | 191 / 190 | +1 Armor, +5% Crit Chance; left the shop without buying. | 348 |
| 8 | 202 / 200 | Bought Blood Leech. Lowest observed combat HP was 10. | 398 |
| 9 | 226 / 224 | Recycled Sharp Bullet; +15% Attack Speed, +3 Armor; bought Coffee. | 678 |
| 10 | 164 / 163 | Died with 14.83 s left on the wave timer; no subsequent shop. | — |

The log contains 1,485 combat requests, 20 shop requests, 11 upgrade requests and one crate request. No reroll, weapon-combination or weapon-recycling action was selected. `stay` was chosen 185 times during combat; that choice is a model decision, not a transport failure.

## Rejections and failures

Exactly 16 attempts were not applied:

| Count | Logged reason | Interpretation |
| ---: | --- | --- |
| 10 | `not_in_combat` | The nine completed waves and the death transition ended combat while a decision was in flight. The bridge rejected the obsolete movement. |
| 2 | `decision expired` | The controller discarded a combat decision older than its 850 ms application limit. |
| 4 | `Jev choice does not match its highest probability.` | Local response validation rejected the returned choice/distribution relationship. These are counted under `api_errors`; they are not evidence of an HTTP outage. |

## What this exposes

**Build spending is the clearest strategy weakness.** The final recorded state has **917 materials and six base-tier weapons**: SMG, Medical Gun, Scissors, Sharp Tooth, Rock and Plank. This mixes ranged and melee scaling. Jev entered wave 10 with 678 materials, and selected no rerolls during the run. At the wave-4 shop, Medical Gun, Knife and Double Barrel Shotgun were legal affordable candidates while two weapon slots were empty; Jev still chose `next_wave`. The completed bridge audit confirmed rerolls were present in all 20 shop decisions, native weapon descriptions and all 16 primary stats were supplied, and no weapon-combination action was legal because all owned weapons were distinct. No shop bridge bug was found in this run. The exported `tier: 0` means Common/Tier I.

Combat instrumentation passed the spot checks performed during the run: sustained movement followed the expected axes, enemy/projectile/pickup lists populated, and the recorded HUD agreed with observed HP. At 260.376 s, for example, recorded frame 1025 shows 22/28 HP, matching adjacent state records.

The movement model remains approximate. At the observed 472 px/s speed, median inference latency corresponds to about 115 pixels of continued movement before a new decision is applied. Candidate geometry currently starts at observation time. A later experiment should account for that delay and reassess direction safety using a fresh observation. This is an improvement hypothesis; this single run does not establish what caused the death or how much any change would help.

This recording demonstrates an integrated game/control/model loop through multiple combat waves and menus. It does not establish a win rate, a strong build policy, or a model comparison.

## Implementation fixes and checks

- Added complete supported-menu orchestration and native action validation, including rejection of replayed purchases.
- Fixed background capture when macOS suppresses normal game drawing; verified real viewport frames and a maximum observed frame gap of 507 ms in this session.
- Reused the HTTPS connection and removed duplicated candidate metrics from the request context.
- Preserved paid token usage when answer validation fails. Later diagnostics also preserve safe answer fields for investigating the four choice/probability mismatches; those fields were not retained in this baseline's error records.
- Corrected phase transitions and final accounting in the video overlay, and distinguished buying from recycling the same item.
- Passed 49 offline tests; checked the actual video format and representative decoded frames. The game log contained no script or parse errors during the run.
