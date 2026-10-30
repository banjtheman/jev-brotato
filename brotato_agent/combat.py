"""Versioned combat context; full observations remain in the recording.

The v2 profile changes information and instructions, never legal movements or
their geometric scores. Fixed limits apply equally to every provider.
"""
from collections import Counter, deque
import copy
import hashlib
import math
from pathlib import PurePosixPath

from .policy import INSTRUCTIONS, prepare_decision, xy

COMBAT_PROFILES = ("baseline", "combat-v2")
LIMITS = {"nearby_enemies": 8, "nearby_projectiles": 6, "spawn_markers": 4,
          "materials": 4, "consumables": 4, "weapons": 6, "passive_items": 32}
COMBAT_INSTRUCTIONS = (
    "Choose the next short movement in Brotato. Weapons attack automatically. "
    "Survive all 20 waves: dodge approaching enemies and projectiles, escape crowds "
    "and walls, and use weapon range to keep attacking while collecting materials "
    "and safe healing pickups. Consider enemy health, damage, movement, attack "
    "animation and visible spawn markers alongside the candidate clearances. "
    "A stationary action can be useful briefly, but remaining still as threats "
    "approach can be fatal. When equally safe, maintain useful movement rather "
    "than repeatedly reversing. Distances are approximate pixel center distances "
    "over 0.5 seconds, not collision guarantees; prefer larger endpoint threat "
    "distance to escape when minimum clearances tie. Relative positions are from "
    "the player: +x east, +y south; velocities are absolute pixels/second. "
    "Tables show nearest entities, while crowd sectors cover all observed enemies. "
    "? means unknown. Cooldowns and body radii are observed fields, not guaranteed "
    "attack times or hitboxes. First-seen activity is an observation-based estimate, "
    "not an exact spawn rate or future wave schedule."
)


def _number(value):
    if type(value) in (int, float) and math.isfinite(value):
        return round(value, 1)
    return None


def _cell(value):
    if value is None:
        return "?"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if type(value) in (int, float):
        value = _number(value)
        return "?" if value is None else str(int(value)) if value == int(value) else str(value)
    return str(value).replace("|", "/").replace("\n", " ")


def _type(entity):
    return entity.get("type") or (PurePosixPath(entity["source_scene"]).stem
                                   if entity.get("source_scene") else "?")


def _relative(entity, origin):
    pos = xy(entity.get("position"))
    return [_number(pos[0] - origin[0]), _number(pos[1] - origin[1])]


def _pair(values):
    return ",".join(_cell(v) for v in values)


def _nearest(entities, origin, count):
    return sorted(entities, key=lambda e: math.dist(xy(e.get("position")), origin))[:count]


class CombatHistory:
    """First observations establish a baseline; menus/new waves reset history."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.key = None
        self.seen = set()
        self.events = deque()
        self.health = None
        self.started_ms = None
        self.last_ms = None

    def update(self, state):
        now = state.get("observed_at_ms")
        key = (state.get("scene_id"), state.get("wave"))
        enemies = state.get("enemies", []) + state.get("bosses", [])
        ids = {e["id"] for e in enemies if e.get("id") is not None}
        hp = _number((state.get("player") or {}).get("health"))
        if type(now) not in (int, float) or not math.isfinite(now):
            self.reset()
            return {"window_seconds": 2, "available": False}
        if key != self.key or self.last_ms is None or now < self.last_ms:
            self.reset()
            self.key, self.started_ms = key, now
            self.seen = ids.copy()
        else:
            first_seen = Counter(_type(e) for e in enemies
                                 if e.get("id") in ids - self.seen)
            loss = max(0, self.health - hp) if self.health is not None and hp is not None else 0
            self.events.append((now, first_seen, loss))
            self.seen.update(ids)
        self.health, self.last_ms = hp, now
        while self.events and self.events[0][0] < now - 2000:
            self.events.popleft()
        activity = Counter()
        for _, counts, _ in self.events:
            activity.update(counts)
        return {"window_seconds": 2, "available": True,
                "seconds_observed": round(min(2000, now - self.started_ms) / 1000, 2),
                "first_seen_enemies": dict(sorted(activity.items())),
                "observed_hp_decreases": round(sum(e[2] for e in self.events), 1)}


def combat_metadata(profile):
    if profile not in COMBAT_PROFILES:
        raise ValueError("Unknown combat profile")
    instructions = COMBAT_INSTRUCTIONS if profile == "combat-v2" else INSTRUCTIONS
    return {"combat_profile": profile,
            "combat_prompt_version": "combat-v2" if profile == "combat-v2" else "combat-v1",
            "combat_instruction_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
            "combat_input_limits": LIMITS.copy() if profile == "combat-v2" else None,
            "combat_input_note": "Nearest-detail tables plus observed crowd sectors; visible spawn markers only; full raw observations recorded." if profile == "combat-v2" else "Original geometric candidate metrics."}


def prepare_combat(state, profile="baseline", history=None):
    combat_metadata(profile)  # Validate before preparing anything.
    summary, candidates = prepare_decision(state)
    if profile == "baseline":
        return summary, candidates, INSTRUCTIONS
    summary = copy.deepcopy(summary)
    origin = xy(state["player"]["position"])
    enemies = state.get("enemies", []) + state.get("bosses", [])
    projectiles = state.get("projectiles", [])
    summary["observation_version"] = state.get("combat_observation_version")
    summary["arena"] = state.get("arena")
    summary["table_format"] = "Rows separated by semicolons; columns separated by |. Positions relative to player; velocity absolute; ? unknown."
    summary["nearby_enemies_columns"] = "type | dx,dy | vx,vy | hp/max | damage | boss | animation | cooldown_s | body_radius | attack_behavior"
    summary["nearby_enemies"] = "; ".join(
        " | ".join([_cell(_type(e)), _pair(_relative(e, origin)),
                    _pair(xy(e["velocity"])) if e.get("velocity") else "?,?",
                    _cell(e.get("health")) + "/" + _cell(e.get("max_health")),
                    _cell(e.get("damage")), _cell(e.get("is_boss")),
                    _cell(e.get("animation")), _cell(e.get("attack_cooldown_s")),
                    _cell(e.get("body_radius")),
                    PurePosixPath(e["attack_behavior"]).stem if e.get("attack_behavior") else "?"])
        for e in _nearest(enemies, origin, LIMITS["nearby_enemies"]))
    sectors = [[0, None] for _ in range(8)]
    for e in enemies:
        dx, dy = _relative(e, origin)
        sector = int((math.atan2(dy, dx) + math.pi / 8) % (2 * math.pi) / (math.pi / 4))
        distance = round(math.hypot(dx, dy), 1)
        sectors[sector][0] += 1
        sectors[sector][1] = min(distance, sectors[sector][1]) if sectors[sector][1] is not None else distance
    summary["crowd_sectors_count_nearest_distance"] = "; ".join(
        f"{label}: {_pair(values)}" for label, values in zip(
            ("E", "SE", "S", "SW", "W", "NW", "N", "NE"), sectors))
    summary["observed_enemy_types"] = dict(sorted(Counter(_type(e) for e in enemies).items()))
    summary["nearby_projectiles_columns"] = "dx,dy | vx,vy | damage"
    summary["nearby_projectiles"] = "; ".join(
        " | ".join([_pair(_relative(e, origin)),
                    _pair(xy(e["velocity"])) if e.get("velocity") else "?,?", _cell(e.get("damage"))])
        for e in _nearest(projectiles, origin, LIMITS["nearby_projectiles"]))
    markers = state.get("spawn_markers", [])
    summary["visible_spawn_markers_dx_dy_seconds"] = "; ".join(
        _pair(_relative(e, origin) + [_number(e.get("remaining_s"))])
        for e in _nearest(markers, origin, LIMITS["spawn_markers"]))
    for key, entities in (("materials", state.get("items", state.get("materials", []))),
                          ("consumables", state.get("consumables", []))):
        summary[f"nearby_{key}_type_dx_dy"] = "; ".join(
            (f"{_cell(_type(e))} | " if key == "consumables" else "") + _pair(_relative(e, origin))
            for e in _nearest(entities, origin, LIMITS[key]))
    build = state.get("build") or {}
    character = build.get("character")
    summary["build"] = {
        "character": character.get("name") if isinstance(character, dict) else character,
        "level": build.get("level"), "materials": build.get("gold"),
        "stats": "; ".join(f"{k.removeprefix('stat_')}: {_cell(v)}" for k, v in (build.get("stats") or {}).items()),
        "weapons": [f"{w.get('name', '?')} (tier {_cell(w.get('tier'))}, cursed {_cell(w.get('cursed'))}): "
                    + str(w.get("weapon_stats") or "?").replace("\n", "; ")
                    for w in build.get("weapons", [])[:LIMITS["weapons"]]],
        "passive_items_name_count": "; ".join(
            f"{i.get('name', '?')} x{i.get('count', 1)}" for i in build.get("items", [])[:LIMITS["passive_items"]]),
    }
    summary["recent_activity"] = (history or CombatHistory()).update(state)
    summary["details_shown_total_observed"] = {
        key: f"{min(LIMITS[key], len(entities))}/{len(entities)}"
        for key, entities in (("nearby_enemies", enemies), ("nearby_projectiles", projectiles),
                              ("spawn_markers", markers), ("materials", state.get("items", state.get("materials", []))),
                              ("consumables", state.get("consumables", [])),
                              ("weapons", build.get("weapons", [])), ("passive_items", build.get("items", [])))
    }
    return summary, candidates, COMBAT_INSTRUCTIONS
