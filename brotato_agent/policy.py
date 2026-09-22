"""Compute geometry locally; ask Jev to choose among bounded actions.

Distances are approximate center distances, not authoritative collision radii.
No claim of optimal play: this is an inspectable first combat policy.
"""
import math

DIRECTIONS = {
    "north": (0.0, -1.0), "northeast": (2**-.5, -2**-.5),
    "east": (1.0, 0.0), "southeast": (2**-.5, 2**-.5),
    "south": (0.0, 1.0), "southwest": (-2**-.5, 2**-.5),
    "west": (-1.0, 0.0), "northwest": (-2**-.5, -2**-.5),
    "stay": (0.0, 0.0),
}
INSTRUCTIONS = (
    "Choose the next short movement in Brotato using the candidate metrics in the criteria. Weapons attack "
    "automatically. Prioritize surviving: avoid small predicted enemy/projectile "
    "clearance and moving into arena walls. When injured, approach health pickups "
    "if the path is reasonably clear; otherwise collect materials while staying "
    "mobile and close enough for weapons to fire. Candidate distances are approximate "
    "pixel center distances computed by code over the next half second, not collision "
    "guarantees. Prefer larger minimum threat clearance in immediate danger. "
    "If minimum clearances tie because a threat is already close, prefer a larger "
    "endpoint enemy distance to escape. If directions are similarly safe, continue "
    "the previous direction when useful instead of repeatedly reversing."
)


def xy(value):
    value = value or {}
    return float(value.get("x", 0)), float(value.get("y", 0))


def _distance(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])


def prepare_decision(state):
    player = state["player"]
    start = xy(player["position"])
    velocity = xy(player.get("velocity", {}))
    # If the adapter cannot expose movement speed, use a documented estimate.
    speed = float(player.get("speed") or max(200.0, math.hypot(*velocity)))
    horizon = .5
    enemies = state.get("enemies", []) + state.get("bosses", [])
    projectiles = state.get("projectiles", [])
    materials = state.get("items", state.get("materials", []))
    consumables = state.get("consumables", [])
    arena = state.get("arena") or {}
    candidates = {}
    for label, direction in DIRECTIONS.items():
        endpoint = (start[0]+direction[0]*speed*horizon,
                    start[1]+direction[1]*speed*horizon)
        metrics = {"direction": label, "vector": list(direction)}
        for name, entities in [("enemy", enemies), ("projectile", projectiles)]:
            clearance = []
            for entity in entities:
                pos = xy(entity["position"])
                vel = xy(entity.get("velocity", {}))
                # Analytic closest approach under constant relative velocity.
                rx, ry = pos[0]-start[0], pos[1]-start[1]
                vx, vy = vel[0]-direction[0]*speed, vel[1]-direction[1]*speed
                norm = vx*vx+vy*vy
                t = max(0, min(horizon, -(rx*vx+ry*vy)/norm)) if norm else 0
                clearance.append(math.hypot(rx+vx*t, ry+vy*t))
            metrics[f"minimum_{name}_clearance"] = round(min(clearance), 1) if clearance else None
            metrics[f"endpoint_{name}_distance"] = round(min(
                (_distance(endpoint, (xy(e["position"])[0]+xy(e.get("velocity"))[0]*horizon,
                                      xy(e["position"])[1]+xy(e.get("velocity"))[1]*horizon))
                 for e in entities), default=9999), 1) if entities else None
        for name, entities in [("material", materials), ("consumable", consumables)]:
            metrics[f"endpoint_{name}_distance"] = round(min(
                (_distance(endpoint, xy(e["position"])) for e in entities), default=9999), 1) if entities else None
        if "min" in arena and "max" in arena:
            low, high = xy(arena["min"]), xy(arena["max"])
            metrics["wall_clearance"] = min(endpoint[0]-low[0], endpoint[1]-low[1],
                                            high[0]-endpoint[0], high[1]-endpoint[1])
        candidates[label] = metrics
    # Remove predicted wall crossings if at least one direction remains.
    allowed = {k: v for k, v in candidates.items() if v.get("wall_clearance", 1) >= 0}
    candidates = allowed or candidates
    for metrics in candidates.values():
        if "wall_clearance" in metrics:
            metrics["wall_clearance"] = round(metrics["wall_clearance"], 1)
    summary = {
        "game": "Brotato", "phase": "combat", "wave": state.get("wave"),
        "seconds_remaining": state.get("time_left"), "player": player,
        "enemy_count": state.get("enemy_count", len(enemies)),
        "observed_enemies_used": len(enemies), "projectile_count": len(projectiles),
        "prediction_horizon_seconds": horizon, "estimated_speed_pixels_per_second": speed,
        "consumable_note": "Consumables may include crates as well as food; type may be unknown.",
    }
    return summary, candidates


def heuristic_choice(candidates):
    """Separate comparison baseline; never presented as a Jev decision."""
    def rank(pair):
        _, v = pair
        clearances = [v[k] for k in ("minimum_enemy_clearance", "minimum_projectile_clearance") if v[k] is not None]
        clearance = min(clearances, default=500)
        pickup = v["endpoint_material_distance"]
        return min(clearance, 200) - (min(pickup, 1000)*.08 if pickup is not None else 0) + min(v.get("wall_clearance", 100), 100)*.1
    return max(candidates.items(), key=rank)[0]
