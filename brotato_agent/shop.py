"""Versioned shop context and rendering shared by live runs and diagnostics."""
from __future__ import annotations

import copy
import hashlib
import json

SHOP_PROFILES = ("baseline", "shop-v2", "shop-v3", "build-v4", "build-v5")
SHOP_INSTRUCTIONS = (
    "Choose one legal shop action to improve the current Brotato build for the next wave. "
    "You may buy more than one item in this shop: after each purchase you will be asked again. "
    "Buying does not start the next wave. Additional compatible weapons add attacks; owning "
    "a weapon does not make another copy useless. Combining reduces the number of weapons "
    "while raising a tier, so consider the lost weapon as well as the upgrade. Consider "
    "passive items for health, armor, healing, speed, and income, not just damage stats. "
    "Weigh each item's stated benefits and penalties against this build. Spend on useful "
    "affordable improvements before choosing next_wave. If none are useful, consider a cheap "
    "reroll; leave when further spending would not help. Do not buy harmful items or recycle "
    "useful weapons just to spend money. Saving can be useful for an explicit build effect "
    "or a future purchase; a large reserve alone is not an equipment upgrade."
)
SHOP_V3_INSTRUCTIONS = SHOP_INSTRUCTIONS + (
    " Inventory tables: each row follows fields, with shared fields applying to every row. "
    "Weapon slots are zero-based row indices. An option's weapon_slot reuses that owned "
    "weapon's details; description_prefix precedes its description."
)
BUILD_V4_INSTRUCTIONS = (
    "Choose one legal action to build a coherent Brotato loadout and survive all 20 waves. "
    "Use the character, owned weapons' damage scaling, classes, stats, and item effects. "
    "Fill compatible weapon slots early; strengthen their shared damage scaling and attack "
    "speed while maintaining enough health, armor or dodge, healing, and movement speed to "
    "survive. Consider early income and every penalty. Attack type is separate from damage "
    "scaling: projectile weapons can scale with melee damage. Class bonuses apply at the "
    "listed weapon counts and are already included in current stats. Tiers are zero-based "
    "(0=I, 1=II, 2=III, 3=IV). Combining two owned weapons raises their tier but removes one "
    "attack slot; compare that tradeoff before combining. Buy useful affordable improvements "
    "before next_wave; each purchase keeps the shop open. Inspect all shop offers, including "
    "blocked ones, before recycling a weapon for a worthwhile replacement. Recycling is "
    "permanent; never discard useful weapons just to spend money. Reroll when the available "
    "improvements justify its cost and remaining budget; reserve money only for a build "
    "effect or better future use. Choose relevant level-up stats; take useful crate items "
    "or recycle harmful ones. Select only a supplied legal action. "
    "Inventory tables: rows follow fields and shared fields apply to every row. Weapon "
    "slots are zero-based row indices; weapon_slot refers to that owned weapon and "
    "description_prefix precedes its description. A shop offer's action reuses that "
    "action's item details; an item field is a preview, not an additional legal action."
)
BUILD_V5_INSTRUCTIONS = BUILD_V4_INSTRUCTIONS + (
    " build_json and shop_json contain compact JSON objects with the complete observed "
    "build and shop; read their contents as facts."
)


def encode_menu_state(state):
    """Avoid native pretty-print padding, preserving complete JSON objects."""
    value = copy.deepcopy(state)
    for key in ("build", "shop"):
        if key in value:
            if key + "_json" in value:
                raise ValueError("Compact menu field already exists")
            value[key + "_json"] = json.dumps(value.pop(key), ensure_ascii=False,
                                             separators=(",", ":"), allow_nan=False)
    return value


def decode_menu_state(state):
    """Restore the original objects for coverage audits and readable displays."""
    value = copy.deepcopy(state)
    for key in ("build", "shop"):
        if key + "_json" in value:
            if key in value:
                raise ValueError("Ambiguous compact menu field")
            value[key] = json.loads(value.pop(key + "_json"))
    return value


def inventory_table(records):
    """Encode repeated field names/values once, preserving every observed field.

    Heterogeneous records keep their original representation: absent fields and
    explicit nulls must never become indistinguishable.
    """
    if not records or any(set(row) != set(records[0]) for row in records):
        return copy.deepcopy(records)
    fields = list(records[0])
    shared = {key: copy.deepcopy(records[0][key]) for key in fields
              if all(row[key] == records[0][key] for row in records)}
    varying = [key for key in fields if key not in shared]
    return {"fields": varying, "shared": shared,
            "rows": [[copy.deepcopy(row[key]) for key in varying] for row in records]}


def expand_inventory(value):
    """Recover the original inventory for audits and human-readable displays."""
    if isinstance(value, list):
        return copy.deepcopy(value)
    return [{**copy.deepcopy(value["shared"]),
             **dict(zip(value["fields"], copy.deepcopy(row), strict=True))}
            for row in value["rows"]]


def compact_shop(context, candidates, *, all_menus=False):
    """Share inventory fields and recycle details without removing information."""
    state = (budget_context(context, candidates) if context.get("phase") == "shop"
             else copy.deepcopy(context))
    weapons = context["build"]["weapons"]
    state["build"]["weapons"] = inventory_table(weapons)
    state["build"]["items"] = inventory_table(context["build"]["items"])
    options = copy.deepcopy(candidates)
    for key, option in candidates.items():
        kind = option.get("kind")
        if kind not in ({"recycle_weapon", "combine"} if all_menus else {"recycle_weapon"}):
            continue
        try:
            prefix_key = "recycle_" if kind == "recycle_weapon" else "combine_"
            if not key.startswith(prefix_key):
                continue
            slot = int(key.removeprefix(prefix_key))
        except ValueError:
            continue
        if not 0 <= slot < len(weapons):
            continue
        weapon = weapons[slot]
        # Reference only a complete matching source weapon. Any unexpected or
        # partial option stays fully expanded rather than guessing its identity.
        if (not set(weapon).issubset(option)
                or "weapon_slot" in option or "description_prefix" in option
                or any(option[field] != value for field, value in weapon.items()
                       if field != "description")):
            continue
        delta = {field: copy.deepcopy(value) for field, value in option.items()
                 if field not in weapon or value != weapon[field]}
        prefix = ("Permanently recycle this owned weapon. " if kind == "recycle_weapon"
                  else "Combine two matching owned weapons into the next tier. ")
        if option.get("description") == prefix + weapon.get("description", ""):
            delta.pop("description", None)
            delta["description_prefix"] = prefix
        options[key] = {"weapon_slot": slot, **delta}
    if all_menus and "shop" in state:
        for key, offer in state["shop"]["offers"].items():
            # Legal offers reuse exactly matching action details. Unbuyable
            # stock retains complete previews, including price and penalties.
            if key in candidates and offer.get("item") == candidates[key]:
                offer.pop("item")
                offer["action"] = key
    return state, options


def expand_shop_offers(state, options):
    """Recover stock previews shared with legal actions for coverage audits."""
    actions = expand_shop_options(state, options)
    offers = copy.deepcopy(state.get("shop", {}).get("offers", {}))
    for offer in offers.values():
        if "action" in offer:
            offer["item"] = copy.deepcopy(actions[offer.pop("action")])
    return offers


def expand_shop_options(state, options):
    """Recover referenced option fields; used to verify lossless compaction."""
    weapons = expand_inventory(state["build"]["weapons"])
    expanded = {}
    for key, option in options.items():
        if "weapon_slot" not in option:
            expanded[key] = copy.deepcopy(option)
            continue
        value = copy.deepcopy(weapons[option["weapon_slot"]])
        value.update({field: copy.deepcopy(content) for field, content in option.items()
                      if field not in {"weapon_slot", "description_prefix"}})
        if "description_prefix" in option:
            value["description"] = option["description_prefix"] + value.get("description", "")
        expanded[key] = value
    return expanded


def budget_context(state, options):
    """Expose arithmetic from observations without assigning item utility."""
    value = copy.deepcopy(state)
    build = state["build"]
    value["shop_facts"] = {
        "materials_available": build["gold"],
        "purchase_currency_available": build["currency"],
        "owned_weapon_count": len(build["weapons"]),
        "affordable_purchases": [
            {"action": key, "name": offer["name"], "kind": offer["kind"],
             "cost": offer["cost"], "currency": offer.get("currency"),
             "purchase_currency_after_buy": build["currency"] - offer["cost"]}
            for key, offer in options.items() if offer.get("kind") in {"buy_item", "buy_weapon"}
        ],
        "buying_keeps_shop_open": True,
    }
    return value


def readable_options(options):
    """Retain every source field while making descriptions readable as text."""
    return {
        key: "; ".join(
            f"{field.replace('_', ' ')}: "
            f"{value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}"
            for field, value in option.items()
        )
        for key, option in options.items()
    }


def prepare_shop(context, candidates, instructions, profile):
    if profile not in SHOP_PROFILES:
        raise ValueError("Unknown shop profile")
    if profile == "shop-v2" and context.get("phase") == "shop":
        return budget_context(context, candidates), readable_options(candidates), SHOP_INSTRUCTIONS
    if profile == "shop-v3" and context.get("phase") == "shop":
        state, options = compact_shop(context, candidates)
        return state, readable_options(options), SHOP_V3_INSTRUCTIONS
    if profile in {"build-v4", "build-v5"} and context.get("phase") in {"shop", "upgrade", "crate"}:
        state, options = compact_shop(context, candidates, all_menus=True)
        if profile == "build-v5":
            return encode_menu_state(state), readable_options(options), BUILD_V5_INSTRUCTIONS
        return state, readable_options(options), BUILD_V4_INSTRUCTIONS
    return context, candidates, instructions


def profile_metadata(profile, menu_instructions, combat_instructions):
    if profile not in SHOP_PROFILES:
        raise ValueError("Unknown shop profile")
    shop_instructions = {"baseline": menu_instructions, "shop-v2": SHOP_INSTRUCTIONS,
                         "shop-v3": SHOP_V3_INSTRUCTIONS, "build-v4": BUILD_V4_INSTRUCTIONS,
                         "build-v5": BUILD_V5_INSTRUCTIONS}[profile]
    return {
        "shop_profile": profile,
        "prompt_versions": {"combat": "combat-v1", "menu": profile if profile in {"build-v4", "build-v5"} else "menu-v1",
                            "shop": "menu-v1" if profile == "baseline" else profile},
        "instruction_sha256": {
            phase: hashlib.sha256(text.encode()).hexdigest()
            for phase, text in {"combat": combat_instructions, "menu": shop_instructions if profile in {"build-v4", "build-v5"} else menu_instructions,
                                "shop": shop_instructions}.items()
        },
        "shop_option_rendering": {"baseline": "provider-default", "shop-v2": "readable-fields-v1",
                                  "shop-v3": "inventory-tables-and-weapon-references-v1",
                                  "build-v4": "inventory-and-offer-references-v2",
                                  "build-v5": "compact-json-inventory-and-offer-references-v3"}[profile],
    }
