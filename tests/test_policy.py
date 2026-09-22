"""Geometry tests use explicit scenarios independent of Jev or a game install."""

import unittest

from brotato_agent.policy import DIRECTIONS, heuristic_choice, prepare_decision


def entity(x, y, vx=0, vy=0):
    return {"position": {"x": x, "y": y}, "velocity": {"x": vx, "y": vy}}


def state(**extra):
    return {"player": {**entity(0, 0), "speed": 200}, **extra}


class PolicyTests(unittest.TestCase):
    def test_crossing_projectile_is_detected_between_sample_endpoints(self):
        # Eastbound player and southbound projectile meet at (50, 0) at t=.25.
        _, candidates = prepare_decision(state(projectiles=[entity(50, -50, 0, 200)]))
        self.assertEqual(candidates["east"]["minimum_projectile_clearance"], 0)
        self.assertGreater(candidates["west"]["minimum_projectile_clearance"], 50)

    def test_relative_velocity_preserves_distance_for_matching_velocities(self):
        _, candidates = prepare_decision(state(enemies=[entity(50, 0, 200, 0)]))
        self.assertEqual(candidates["east"]["minimum_enemy_clearance"], 50)

    def test_closest_approach_is_clamped_to_start_when_threat_recedes(self):
        _, candidates = prepare_decision(state(enemies=[entity(50, 0, 200, 0)]))
        self.assertEqual(candidates["stay"]["minimum_enemy_clearance"], 50)

    def test_closest_approach_is_clamped_to_prediction_horizon(self):
        # A collision would occur at t=2; the half-second prediction reaches 150.
        _, candidates = prepare_decision(state(enemies=[entity(200, 0, -100, 0)]))
        self.assertEqual(candidates["stay"]["minimum_enemy_clearance"], 150)

    def test_stationary_threat_and_diagonal_movement_use_normalized_speed(self):
        _, candidates = prepare_decision(state(enemies=[entity(100, 0)], items=[entity(0, 100)]))
        self.assertEqual(candidates["east"]["minimum_enemy_clearance"], 0)
        self.assertEqual(candidates["west"]["minimum_enemy_clearance"], 100)
        self.assertEqual(candidates["southeast"]["endpoint_material_distance"], 76.5)

    def test_endpoint_threat_distance_predicts_threat_motion(self):
        # Player stays put, enemy moves from x=50 to x=150 by the endpoint.
        _, candidates = prepare_decision(state(enemies=[entity(50, 0, 200, 0)]))
        self.assertEqual(candidates["stay"]["endpoint_enemy_distance"], 150)

    def test_arena_excludes_outward_paths_but_keeps_boundary_endpoint(self):
        summary, candidates = prepare_decision(state(arena={
            "min": {"x": 0, "y": -100}, "max": {"x": 100, "y": 100},
        }))
        self.assertNotIn("west", candidates)
        self.assertNotIn("northwest", candidates)
        self.assertNotIn("southwest", candidates)
        self.assertIn("east", candidates)
        self.assertEqual(candidates["east"]["wall_clearance"], 0)
        self.assertNotIn("candidates", summary, "Do not send duplicated candidate metrics in state and criteria")

    def test_wall_legality_is_checked_before_rounding_display_metrics(self):
        # Rounded -0.0 must not turn a crossing into a permitted move.
        _, candidates = prepare_decision(state(arena={
            "min": {"x": -200, "y": -200}, "max": {"x": 99.96, "y": 200},
        }))
        self.assertNotIn("east", candidates)
        self.assertIn("stay", candidates)

    def test_missing_entities_and_arena_produce_all_actions_with_unknown_clearance(self):
        summary, candidates = prepare_decision(state())
        self.assertEqual(set(candidates), set(DIRECTIONS))
        self.assertIsNone(candidates["north"]["minimum_enemy_clearance"])
        self.assertIsNone(candidates["north"]["minimum_projectile_clearance"])
        self.assertIsNone(candidates["north"]["endpoint_material_distance"])
        self.assertEqual(summary["enemy_count"], 0)

    def test_bosses_and_material_alias_contribute_to_decision(self):
        summary, candidates = prepare_decision(state(bosses=[entity(100, 0)], materials=[entity(-100, 0)]))
        self.assertEqual(summary["observed_enemies_used"], 1)
        self.assertEqual(candidates["east"]["minimum_enemy_clearance"], 0)
        self.assertEqual(candidates["west"]["endpoint_material_distance"], 0)

    def test_baseline_prefers_safe_pickup_when_there_are_no_threats(self):
        _, candidates = prepare_decision(state(items=[entity(100, 0)]))
        self.assertEqual(heuristic_choice(candidates), "east")


if __name__ == "__main__":
    unittest.main()
