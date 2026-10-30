import copy
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from brotato_agent.combat import CombatHistory, LIMITS, prepare_combat
from brotato_agent.jev import Judgment
from brotato_agent.policy import INSTRUCTIONS, prepare_decision
from brotato_agent.session import run_session


def snapshot(now=1000, wave=1):
    return {"phase": "combat", "wave": wave, "scene_id": 1, "observed_at_ms": now,
            "combat_observation_version": "combat-obs-v2",
            "player": {"position": {"x": 100, "y": 100}, "health": 20,
                       "max_health": 20, "speed": 200},
            "arena": {"min": {"x": 0, "y": 0}, "max": {"x": 500, "y": 500}},
            "enemies": [{"id": 1, "type": "chaser", "position": {"x": 130, "y": 100},
                         "velocity": {"x": -50, "y": 0}, "health": 10, "max_health": 12,
                         "damage": 3, "is_boss": False}],
            "projectiles": [], "materials": [], "consumables": [],
            "build": {"weapons": [{"name": "SMG", "tier": 0, "weapon_stats": "Damage: 3\nRange: 400"}],
                      "stats": {"stat_armor": 5}, "items": []}}


class CombatProfileTests(unittest.TestCase):
    def test_baseline_is_exact_original_context_and_both_profiles_keep_actions(self):
        state = snapshot()
        before = copy.deepcopy(state)
        original = prepare_decision(state)
        summary, actions, instructions = prepare_combat(state)
        self.assertEqual((summary, actions), original)
        self.assertEqual(instructions, INSTRUCTIONS)
        richer, new_actions, _ = prepare_combat(state, "combat-v2")
        self.assertEqual(actions, new_actions)
        self.assertEqual(state, before)
        self.assertEqual(richer["build"]["stats"], "armor: 5")
        self.assertIn("Damage: 3; Range: 400", richer["build"]["weapons"][0])

    def test_relative_positions_absolute_velocity_and_unknowns_are_explicit(self):
        state = snapshot()
        state["projectiles"] = [{"position": {"x": 90, "y": 95}, "velocity": {"x": 10, "y": 20}}]
        state["spawn_markers"] = [{"position": {"x": 80, "y": 100}, "remaining_s": .75}]
        state["consumables"] = [{"position": {"x": 110, "y": 100}}]
        context, _, _ = prepare_combat(state, "combat-v2")
        self.assertIn("chaser | 30,0 | -50,0 | 10/12 | 3 | no", context["nearby_enemies"])
        self.assertEqual(context["nearby_projectiles"], "-10,-5 | 10,20 | ?")
        self.assertEqual(context["visible_spawn_markers_dx_dy_seconds"], "-20,0,0.8")
        self.assertEqual(context["nearby_consumables_type_dx_dy"], "? | 10,0")
        self.assertIn("E: 1,30", context["crowd_sectors_count_nearest_distance"])

    def test_nearest_limits_do_not_remove_crowd_totals_or_raw_data(self):
        state = snapshot()
        state["enemies"] = [dict(state["enemies"][0], id=i,
                                 position={"x": 100 + i, "y": 100}) for i in range(20, 0, -1)]
        before = copy.deepcopy(state)
        context, _, _ = prepare_combat(state, "combat-v2")
        self.assertEqual(len(context["nearby_enemies"].split("; ")), LIMITS["nearby_enemies"])
        self.assertTrue(context["nearby_enemies"].startswith("chaser | 1,0"))
        self.assertEqual(context["details_shown_total_observed"]["nearby_enemies"], "8/20")
        self.assertEqual(context["observed_enemy_types"], {"chaser": 20})
        self.assertIn("E: 20,1", context["crowd_sectors_count_nearest_distance"])
        self.assertEqual(state, before)

    def test_history_starts_without_inventing_spawns_and_expires_old_events(self):
        history = CombatHistory()
        state = snapshot()
        self.assertEqual(history.update(state)["first_seen_enemies"], {})
        state["observed_at_ms"] = 1500
        state["enemies"].append(dict(state["enemies"][0], id=2))
        state["player"]["health"] = 17
        context = history.update(state)
        self.assertEqual(context["first_seen_enemies"], {"chaser": 1})
        self.assertEqual(context["observed_hp_decreases"], 3)
        state["observed_at_ms"] = 3600
        self.assertEqual(history.update(state)["first_seen_enemies"], {})
        state["observed_at_ms"] = 3700
        state["wave"] = 2
        self.assertEqual(history.update(state)["seconds_observed"], 0)
        self.assertEqual(history.update(state)["observed_hp_decreases"], 0)
        history.reset()
        self.assertEqual(history.update(state)["first_seen_enemies"], {})
        state.pop("observed_at_ms")
        self.assertFalse(history.update(state)["available"])

    def run_mock_session(self, states):
        bridge = MagicMock()
        bridge.observe.side_effect = states
        bridge.request.side_effect = lambda command, **kwargs: (
            {"now_ms": 0, "started_at_ms": 0, "active": True} if command == "record_start"
            else {"active": False, "frames": 0})
        client = MagicMock()
        client.choose.return_value = Judgment("west", 1.0, {"west": 1.0}, 10, "test-model",
                                               {"input_tokens": 10, "output_tokens": 1})
        args = SimpleNamespace(session="combat-profile-test", seconds=60, max_calls=10,
                               max_cost=1, port=4243, combat_profile="combat-v2", shop_profile="shop-v2")
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
        return summary, records, metadata, bridge, client

    def test_session_records_exact_inputs_and_uses_history_between_requests(self):
        first = snapshot()
        first["seq"] = 1
        second = snapshot(1500)
        second["seq"] = 2
        second["enemies"].append(dict(second["enemies"][0], id=2))
        summary, records, metadata, bridge, client = self.run_mock_session([
            first, second, {"phase": "gameover", "wave": 2, "terminal": True}])
        decisions = [r for r in records if r["event"] == "decision"]
        self.assertEqual(summary["combat_profile"], "combat-v2")
        self.assertEqual(metadata["prompt_versions"]["combat"], "combat-v2")
        self.assertEqual(decisions[1]["decision_state"]["recent_activity"]["first_seen_enemies"], {"chaser": 1})
        for record, call in zip(decisions, client.choose.call_args_list):
            self.assertEqual(record["prompt_version"], "combat-v2")
            self.assertEqual((record["decision_state"], record["model_options"], record["instructions"]), call.args)
            self.assertEqual(record["candidates"], prepare_decision(record["state"])[1])
        self.assertEqual(bridge.move.call_count, 2)

    def test_v2_rejects_stale_bridge_before_any_request_or_movement(self):
        state = snapshot()
        state.pop("combat_observation_version")
        summary, _, _, bridge, client = self.run_mock_session([state])
        self.assertEqual(summary["outcome"], "runner_error")
        client.choose.assert_not_called()
        bridge.move.assert_not_called()


if __name__ == "__main__":
    unittest.main()
