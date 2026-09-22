# Brotato control research

Checked September 22, 2026. Existing mods demonstrate both structured state export and external control. None inspected provides a complete, verified agent API for the local installation, so the recommended first implementation is a small original bridge.

## Findings

| Project | Useful evidence | Why it is not a drop-in solution |
| --- | --- | --- |
| [Neuro SDK Brotato integration](https://github.com/Adesii/Neuro-sdk-Brotato-Integration) | MIT; last pushed April 4, 2026. WebSocket actions select local movement patterns or a target coordinate. Also contains menu, upgrade and crate actions. | Unfinished shop code. Its manifest declares ModLoader 6.1.0 but no compatible game version. No runtime compatibility test performed. |
| [BrotatoAI](https://github.com/guenichone/brotatoai) | MIT; last pushed October 21, 2024. A mod exports player/enemy/projectile state over TCP and accepts movement commands. Separate code provides a Gymnasium training environment. | Old game-property names, limited movement commands and incomplete message framing. Requires adaptation. |
| [BOTato](https://github.com/boardengineer/botato) | MIT autonomous combat mod; establishes the movement override approach. | Internal steering bot, not an external control API; shopping remains manual. |
| [Full Auto Bot](https://github.com/HelpFreedom/brotato-full-autobot) | GPL-3.0; last pushed June 4, 2026. Source includes movement, shop and upgrade handling. README claims Windows/macOS/Linux support. | No external API. Declared compatibility is Brotato 1.1.15+ and ModLoader 6.x. Its code is a reference, not incorporated into this project. |
| [Brotato Exporter](https://github.com/benw10-1/brotato-exporter) | Run-statistics export and scene-change detection. | Read-only statistics, no combat control or enemy-position feed. Its README mentions WebSocket, but the inspected mod currently posts binary messages over HTTP. |

These are source inspections, not claims that upstream mods have been tested locally. Dates above come from GitHub repository metadata.

## Exact control interfaces inspected

### Neuro integration

The [WebSocket client](https://github.com/Adesii/Neuro-sdk-Brotato-Integration/blob/main/mods-unpacked/Adesi-NeuroIntegration/neuro-sdk/websocket/websocket.gd) connects to the `NEURO_SDK_WS_URL` environment variable and polls at 30 Hz. The external controller runs the server.

Its [action decoder](https://github.com/Adesii/Neuro-sdk-Brotato-Integration/blob/main/mods-unpacked/Adesi-NeuroIntegration/neuro-sdk/messages/incoming/action.gd) expects an envelope whose inner `data` is a JSON **string**:

```json
{"command":"action","data":{"id":"1","name":"move_type","data":"{\"move_type\":\"KeepDistance\"}"}}
```

- [`move_type`](https://github.com/Adesii/Neuro-sdk-Brotato-Integration/blob/main/mods-unpacked/Adesi-NeuroIntegration/broactions/main_stage/move_selection.gd) selects `KeepDistance`, `RunToClosestEnemy`, `Circle`, `Random` or `Idle`. Movement executes locally each frame.
- [`move_to_location`](https://github.com/Adesii/Neuro-sdk-Brotato-Integration/blob/main/mods-unpacked/Adesi-NeuroIntegration/broactions/main_stage/move_to_location.gd) accepts `{"move_to_target":{"x":400,"y":300}}` as that stringified action payload.
- [`main_ext.gd`](https://github.com/Adesii/Neuro-sdk-Brotato-Integration/blob/main/mods-unpacked/Adesi-NeuroIntegration/extensions/main_ext.gd) reports position, health and enemy count every five seconds; it does not provide a detailed combat snapshot.
- [`shop_ext.gd`](https://github.com/Adesii/Neuro-sdk-Brotato-Integration/blob/main/mods-unpacked/Adesi-NeuroIntegration/extensions/shop_ext.gd) loads missing files such as `get_stats.gd` and `steal_weapon.gd`; purchasing and next-wave action registration are commented out. `go_next_wave.gd` is empty and `buy_item.gd` is a placeholder.

### BrotatoAI

The [standalone mod client](https://github.com/guenichone/brotatoai/blob/main/src/BaRRaK-BrotatoAI/extensions/client/ai_client.gd) connects to `127.0.0.1:4242`, sending an eight-byte little-endian payload length followed by UTF-8 JSON. The [movement extension](https://github.com/guenichone/brotatoai/blob/main/src/BaRRaK-BrotatoAI/extensions/entities/units/movement_behaviors/player_movement_behavior.gd) exports player position, velocity and health, enemy positions/velocities, projectile positions/rotations, and wave duration. It accepts raw `up`, `down`, `left`, `right` text.

Received actions are not framed: the code assumes each TCP read contains exactly one action. It also references older fields such as `Main._player`. The separate [Gymnasium implementation](https://github.com/guenichone/brotatoai/blob/main/src/godot_rl/core/godot_env.py) uses port `11008` and four-byte length framing; these are different interfaces.

## Local compatibility and implementation choice

The local Steam application is `~/Library/Application Support/Steam/steamapps/common/Brotato/Brotato.app`. Initial inspection found bundle version `1.1.12.0.beta-3` and a PCK header for Godot 3.7. These identify the installed files; they do not establish compatibility with any upstream mod. The bundled scripts and an actual game launch must verify each accessed property and extension.

Subsequent runtime inspection reported **Brotato 1.1.15.4**; the app bundle version was stale. The original bridge built here loaded successfully and Jev completed a live Danger 0 first-wave smoke test. See the root README for measured results and remaining scope. Upstream mods themselves were not runtime-tested.

Both older and newer mods override `get_movement()` in `res://entities/units/movement_behaviors/player_movement_behavior.gd` and register that extension with `ModLoaderMod.install_script_extension(...)`. This is a concrete integration point for an original bridge. Godot 3 syntax and networking APIs are required; Godot 4 examples should not be copied unchanged.

The [Brotato modding guide](https://steamcommunity.com/sharedfiles/filedetails/?id=2931079751) documents ModLoader and the package structure `mods-unpacked/Author-ModName/{manifest.json,mod_main.gd,...}`. Some editor-version instructions are dated; prefer the installed game's evidence. Any extracted commercial game files should remain local and excluded from this repository.

## Initial combat MVP (historical design)

Build a small original GDScript mod and Python controller using a localhost TCP connection with newline-delimited JSON (NDJSON). The mod exports bounded combat snapshots and accepts bounded movement actions. Buffer incoming bytes until a complete newline-delimited message arrives; use sequence identifiers and short action expiration so delayed model replies cannot steer an obsolete situation. Preserve manual control and stop agent movement on disconnect or timeout.

Keep deterministic geometry, state filtering, action validation and execution in code. Let Jev choose among a small explicit set of candidate movement decisions from the current structured state. Measure real latency before increasing decision frequency; do not assume a remote model can replace the frame-by-frame game loop. The API credential stays in the Python process, outside the mod package.

The initial scope was combat only. The harness now also handles supported shop, upgrade and crate menus and records a run until victory or death. Character/weapon/difficulty selection remains manual, and it does not automatically restart after death. See the [recorded baseline](full-run-01.md) for a measured end-to-end attempt; one attempt does not establish a win rate.

For later build evaluation, [Spud Coach](https://github.com/brendanlefebvre/spud-coach) offers deterministic item/weapon calculations using data extracted from the user's own installation. It is a theorycrafting MCP server, not a game-control API.
