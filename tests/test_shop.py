from contextlib import ExitStack
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from brotato_agent.jev import Judgment
from brotato_agent.session import MENU_INSTRUCTIONS, run_session
from brotato_agent.shop import (SHOP_INSTRUCTIONS, SHOP_V3_INSTRUCTIONS, compact_shop,
                                BUILD_V4_INSTRUCTIONS, expand_inventory, expand_shop_offers,
                                expand_shop_options, inventory_table, prepare_shop,
                                encode_menu_state, decode_menu_state, BUILD_V5_INSTRUCTIONS)


class ShopProfileTests(unittest.TestCase):
    def test_compact_json_retains_unicode_quotes_nulls_and_nested_future_fields(self):
        state = {"phase": "shop", "menu_actions_this_wave": 3,
                 "build": {"items": [{"description": 'Odd "item"\n−3 Range', "future": None}],
                           "weapons": [], "nested": {"future": [True, 1.25, "é"]}},
                 "shop": {"offers": {"buy_0": {"locked": True, "blocked_by": ["native"]}}}}
        before = copy.deepcopy(state)
        encoded = encode_menu_state(state)
        self.assertEqual(decode_menu_state(encoded), before)
        self.assertEqual(state, before)
        self.assertIsInstance(encoded["build_json"], str)
        self.assertNotIn("build", encoded)
        self.assertEqual(encoded["menu_actions_this_wave"], 3)
        with self.assertRaises(ValueError):
            encode_menu_state({"build": {}, "build_json": "{}"})

    def test_build_v5_compact_json_restores_all_menu_inventory_and_actions(self):
        for phase in ("shop", "crate", "upgrade"):
            context = {"phase": phase, "build": {"gold": 30, "currency": 30,
                       "items": [{"name": "Plant", "description": "+3 Regen; -1% Life Steal"}],
                       "weapons": [{"name": "SMG", "description": "", "tier": 0}]}}
            options = {"take": {"name": "Plant", "kind": "take"}}
            state, rendered, instructions = prepare_shop(context, options, MENU_INSTRUCTIONS, "build-v5")
            original, original_options = compact_shop(context, options, all_menus=True)
            self.assertEqual(decode_menu_state(state), original)
            self.assertEqual(list(rendered), list(original_options))
            self.assertEqual(instructions, BUILD_V5_INSTRUCTIONS)

    def test_build_v4_retains_blocked_stock_classes_budget_and_combine_identity(self):
        weapon = {"id": "weapon_smg_1", "name": "SMG", "tier": 0, "description": "",
                  "weapon_stats": "Damage: 3 (50% Ranged Damage)", "attack_type": "ranged",
                  "classes": ["Gun"]}
        buy = {**weapon, "kind": "buy_weapon", "cost": 20, "currency": "materials"}
        preview = {**buy, "name": "Laser Gun IV", "tier": 3, "cost": 160}
        stock = {"buy_0": {"item": buy, "affordable": True, "buyable": True, "locked": False, "blocked_by": []},
                 "buy_1": {"item": preview, "affordable": False, "buyable": False, "locked": True,
                           "blocked_by": ["insufficient_currency", "native_weapon_purchase_restriction"]}}
        context = {"phase": "shop", "wave": 5, "build": {"gold": 40, "currency": 40,
                   "weapons": [weapon] * 6, "items": [], "stats": {"stat_range": 50},
                   "weapon_slots": {"used": 6, "free": 0}, "weapon_classes": {"Gun": 6}},
                   "reroll_budget": {"run_generation": 2, "shop_used": 0, "shop_limit": 2},
                   "shop": {"offers": stock, "reroll_cost": 5,
                            "weapon_class_rules": {"Gun": {"2": "+10 Range", "6": "+50 Range"}}}}
        candidates = {"buy_0": buy, "combine_0": {**weapon, "kind": "combine", "cost": 0,
                        "description": "Combine two matching owned weapons into the next tier. "},
                      "next_wave": {"name": "Start next wave", "kind": "next_wave", "cost": 0}}
        before = copy.deepcopy((context, candidates))
        state, options = compact_shop(context, candidates, all_menus=True)
        self.assertEqual(expand_inventory(state["build"]["weapons"]), context["build"]["weapons"])
        self.assertEqual(expand_shop_options(state, options), candidates)
        self.assertEqual(expand_shop_offers(state, options), stock)
        self.assertEqual(state["reroll_budget"], context["reroll_budget"])
        self.assertEqual(state["shop"]["weapon_class_rules"], context["shop"]["weapon_class_rules"])
        self.assertEqual(options["combine_0"]["weapon_slot"], 0)
        self.assertNotIn("buy_1", options)
        self.assertEqual((context, candidates), before)
        _, rendered, instructions = prepare_shop(context, candidates, MENU_INSTRUCTIONS, "build-v4")
        self.assertEqual(list(rendered), list(candidates))
        self.assertEqual(instructions, BUILD_V4_INSTRUCTIONS)

    def test_build_v4_compacts_inventory_for_level_ups_and_crates(self):
        for phase in ("upgrade", "crate"):
            context = {"phase": phase, "build": {"weapons": [{"name": "SMG", "classes": ["Gun"]}],
                       "items": [{"name": "Plant", "description": "+3 Regen; -1 Life Steal", "count": 2}]}}
            options = {"choose": {"kind": phase, "name": "Plant", "description": "+3 Regen; -1 Life Steal"}}
            state, rendered, instructions = prepare_shop(context, options, MENU_INSTRUCTIONS, "build-v4")
            self.assertEqual(expand_inventory(state["build"]["items"]), context["build"]["items"])
            self.assertEqual(list(rendered), list(options))
            self.assertEqual(instructions, BUILD_V4_INSTRUCTIONS)

    def test_compact_shop_preserves_inventory_effects_legal_options_and_hp_currency(self):
        weapon = {"name": "SMG", "id": "weapon_smg_1", "tier": 0, "cursed": False,
                  "description": "", "weapon_stats": "Damage: 3\nCooldown: 0.167s",
                  "future_stat": {"critical": 2}}
        weapons = [weapon, {**weapon, "name": "Laser Gun", "id": "weapon_laser_gun_1",
                            "description": "Pierces enemies", "weapon_stats": "Damage: 40"}]
        items = [{"name": "Bat", "count": 2, "description": "+2% Life Steal; -2 Harvesting",
                  "cursed": False, "unknown": None},
                 {"name": "Plant", "count": 1, "description": "+3 HP Regeneration; -1% Life Steal",
                  "cursed": False, "unknown": {"a": 7}}]
        context = {"phase": "shop", "wave": 9, "build": {"gold": 385, "currency": 20,
                   "weapons": weapons, "items": items, "stats": {"stat_max_hp": 20}}}
        options = {"buy_0": {"name": "Coffee", "kind": "buy_item", "cost": 6,
                             "currency": "max_hp", "description": "+10% Attack Speed; -2% Damage"},
                   "recycle_0": {**weapon, "description": "Permanently recycle this owned weapon. ",
                                 "kind": "recycle_weapon", "gain": 9},
                   "recycle_1": {**weapons[1], "description": "A different custom effect",
                                 "kind": "recycle_weapon", "gain": 12},
                   "next_wave": {"kind": "next_wave", "name": "Start next wave", "cost": 0}}
        before = copy.deepcopy((context, options))
        state, compact = compact_shop(context, options)
        self.assertEqual(expand_inventory(state["build"]["weapons"]), weapons)
        self.assertEqual(expand_inventory(state["build"]["items"]), items)
        self.assertEqual(expand_shop_options(state, compact), options)
        self.assertEqual(list(compact), list(options))
        self.assertEqual(state["shop_facts"]["affordable_purchases"][0]["purchase_currency_after_buy"], 14)
        self.assertEqual((context, options), before)
        prepared, readable, instructions = prepare_shop(context, options, MENU_INSTRUCTIONS, "shop-v3")
        self.assertEqual(prepared, state)
        self.assertEqual(list(readable), list(options))
        self.assertIn("weapon slot: 0", readable["recycle_0"])
        self.assertEqual(instructions, SHOP_V3_INSTRUCTIONS)

    def test_inventory_compaction_keeps_absent_fields_distinct_from_null(self):
        rows = [{"name": "Plant"}, {"name": "Bat", "optional": None}]
        self.assertEqual(expand_inventory(inventory_table(rows)), rows)

    def test_sequential_purchases_use_updated_budget_and_keep_legal_action_ids(self):
        def observation(seq, gold, offers, weapons):
            return {"seq": seq, "phase": "shop", "wave": 1,
                    "build": {"character": {"name": "Well-Rounded"}, "gold": gold,
                              "currency": gold, "weapons": weapons, "items": [], "stats": {}},
                    "menu": {"options": offers}}

        smg = {"kind": "buy_weapon", "name": "SMG", "cost": 23,
               "currency": "materials", "weapon_stats": "Damage: 3\nCooldown: 0.167s"}
        laser = {"kind": "buy_weapon", "name": "Laser Gun", "cost": 17,
                 "currency": "materials", "weapon_stats": "Damage: 40"}
        leave = {"kind": "next_wave", "name": "Start next wave", "cost": 0}
        states = [observation(1, 43, {"buy_0": smg, "buy_1": laser, "next_wave": leave}, [{"name": "SMG"}]),
                  observation(2, 20, {"buy_1": laser, "next_wave": leave}, [{"name": "SMG"}] * 2),
                  observation(3, 3, {"next_wave": leave}, [{"name": "SMG"}] * 2 + [{"name": "Laser Gun"}]),
                  {"seq": 4, "phase": "gameover", "wave": 2, "terminal": True}]
        untouched = copy.deepcopy(states)
        bridge = MagicMock()
        bridge.observe.side_effect = states
        bridge.request.side_effect = lambda command, **kwargs: (
            {"now_ms": 0, "started_at_ms": 0, "active": True} if command == "record_start"
            else {"active": False, "frames": 0}
        )
        client = MagicMock()
        client.choose.side_effect = [Judgment(choice, 1.0, {choice: 1.0}, 10, "test-model",
                                             {"input_tokens": 10, "output_tokens": 1})
                                     for choice in ("buy_0", "buy_1", "next_wave")]
        args = SimpleNamespace(session="shop-profile-test", seconds=60, max_calls=10,
                               max_cost=1, port=4243, shop_profile="shop-v2")
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch("brotato_agent.session.ROOT", Path(directory)))
            factory = stack.enter_context(patch("brotato_agent.session.BridgeClient"))
            factory.return_value.__enter__.return_value = bridge
            stack.enter_context(patch("brotato_agent.session.JevClient", return_value=client))
            stack.enter_context(patch("brotato_agent.session.time.sleep"))
            stack.enter_context(patch("builtins.print"))
            summary = run_session(args)
            path = Path(directory) / "recordings" / args.session
            records = [json.loads(line) for line in (path / "decisions.jsonl").read_text().splitlines()]
            metadata = json.loads((path / "metadata.json").read_text())
        decisions = [r for r in records if r["event"] == "decision"]
        self.assertEqual(states, untouched)
        self.assertEqual(summary["applied_actions"], 3)
        self.assertEqual(summary["shop_profile"], "shop-v2")
        self.assertEqual(metadata["prompt_versions"]["shop"], "shop-v2")
        self.assertEqual(metadata["requested_model"], "jev-latest")
        self.assertEqual([r["choice"] for r in decisions], ["buy_0", "buy_1", "next_wave"])
        self.assertEqual([r["decision_state"]["shop_facts"]["purchase_currency_available"] for r in decisions], [43, 20, 3])
        self.assertEqual(decisions[0]["decision_state"]["shop_facts"]["affordable_purchases"][0]["purchase_currency_after_buy"], 20)
        for record, state, call in zip(decisions, states, client.choose.call_args_list):
            self.assertEqual(record["candidates"], state["menu"]["options"])
            self.assertEqual(set(record["model_options"]), set(record["candidates"]))
            self.assertEqual(record["model_options"], call.args[1])
            self.assertEqual(record["instructions"], SHOP_INSTRUCTIONS)
            self.assertTrue(record["applied"])
        acts = [call.kwargs for call in bridge.request.call_args_list if call.args[0] == "act"]
        self.assertEqual(acts, [{"observation_seq": 1, "action": "buy_0"},
                               {"observation_seq": 2, "action": "buy_1"},
                               {"observation_seq": 3, "action": "next_wave"}])
        bridge.move.assert_not_called()

    def test_baseline_and_non_shop_inputs_keep_original_representation(self):
        options = {"take": {"name": "Some item", "kind": "take"}}
        for phase, profile in [("shop", "baseline"), ("crate", "shop-v2"),
                               ("upgrade", "shop-v2"), ("combat", "shop-v2"),
                               ("crate", "shop-v3"), ("upgrade", "shop-v3"), ("combat", "shop-v3")]:
            context = {"phase": phase, "build": {}}
            result = prepare_shop(context, options, MENU_INSTRUCTIONS, profile)
            self.assertIs(result[0], context)
            self.assertIs(result[1], options)
            self.assertEqual(result[2], MENU_INSTRUCTIONS)

    def test_purchase_budget_uses_currency_instead_of_materials_for_hp_shops(self):
        context = {"phase": "shop", "build": {"gold": 100, "currency": 20, "weapons": []}}
        options = {"buy_0": {"kind": "buy_item", "name": "Odd \"item\"", "cost": 6,
                             "currency": "max_hp", "description": "+3 HP\n−1 Damage"},
                   "next_wave": {"kind": "next_wave", "cost": 0}}
        before = copy.deepcopy((context, options))
        state, rendered, _ = prepare_shop(context, options, MENU_INSTRUCTIONS, "shop-v2")
        offer = state["shop_facts"]["affordable_purchases"][0]
        self.assertEqual(offer["purchase_currency_after_buy"], 14)
        self.assertEqual(offer["currency"], "max_hp")
        self.assertIn(options["buy_0"]["description"], rendered["buy_0"])
        self.assertEqual(set(rendered), set(options))
        self.assertEqual((context, options), before)


if __name__ == "__main__":
    unittest.main()
