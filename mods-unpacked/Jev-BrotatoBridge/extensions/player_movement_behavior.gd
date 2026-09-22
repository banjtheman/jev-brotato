extends "res://entities/units/movement_behaviors/player_movement_behavior.gd"

# Preserve the game's input semantics, including keyboard/controller priority.
func get_movement() -> Vector2:
	var manual = .get_movement()
	var bridges = get_tree().get_nodes_in_group("jev_brotato_bridge")
	if bridges.empty():
		return manual
	return bridges[0].movement_for(_parent, manual)
