extends Node

# Godot 3 / Brotato ModLoader 6. One local client; no scene or save mutations.
const MOD_ID = "Jev-BrotatoBridge"
const PORT = 4243
const MAX_INPUT = 16384
const MAX_OUTPUT = 262144
const MAX_OBSERVATION_AGE_MS = 1000
const MAX_MENU_OBSERVATION_AGE_MS = 30000

var _server = TCP_Server.new()
var _client = null
var _incoming = PoolByteArray()
var _outgoing = PoolByteArray()
var _move = Vector2.ZERO
var _move_expires_ms = 0
var _sequence = 0
var _last_observation_ms = 0
var _last_observation_scene = 0
var _last_observation_phase = "menu"
var _scene_id = 0
var _property_cache = {}
var _menus = null
var _last_menu_fingerprint = ""

func _init():
	ModLoaderMod.install_script_extension(
		"res://mods-unpacked/Jev-BrotatoBridge/extensions/player_movement_behavior.gd")

func _ready():
	pause_mode = Node.PAUSE_MODE_PROCESS
	_menus = load("res://mods-unpacked/Jev-BrotatoBridge/menus.gd").new()
	_menus.name = "Menus"
	add_child(_menus)
	var recorder = load("res://mods-unpacked/Jev-BrotatoBridge/recorder.gd").new()
	recorder.name = "Recorder"
	add_child(recorder)
	add_to_group("jev_brotato_bridge")
	var error = _server.listen(PORT, "127.0.0.1")
	if error != OK:
		ModLoaderLog.error("Cannot bind local bridge on port %s (error %s)" % [PORT, error], MOD_ID)
		set_process(false)
		return
	ModLoaderLog.info("Listening on 127.0.0.1:%s" % PORT, MOD_ID)

func is_listening() -> bool:
	return _server.is_listening()

func _exit_tree():
	_disconnect_client()
	_server.stop()

func _process(_delta):
	var scene = get_tree().current_scene
	var current_id = scene.get_instance_id() if is_instance_valid(scene) else 0
	if current_id != _scene_id:
		_scene_id = current_id
		_invalidate_observation()
	if _phase(scene, _player(scene)) != "combat" or OS.get_ticks_msec() >= _move_expires_ms:
		_release()
	if _client != null and _client.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		_disconnect_client()
	if _server.is_connection_available():
		var connection = _server.take_connection()
		if _client == null:
			_client = connection
			_client.set_no_delay(true)
			_invalidate_observation()
		else:
			# An existing controller must release its connection before another takes over.
			connection.disconnect_from_host()
	if _client == null:
		return
	var available = _client.get_available_bytes()
	if available > 0:
		var packet = _client.get_partial_data(min(available, MAX_INPUT))
		if packet[0] != OK:
			_disconnect_client()
			return
		_incoming += packet[1]
		if _incoming.size() > MAX_INPUT:
			_disconnect_client()
			return
	# Limit work per frame; both reads and writes are nonblocking.
	for _request_index in range(8):
		var newline = -1
		for index in range(_incoming.size()):
			if _incoming[index] == 10:
				newline = index
				break
		if newline < 0:
			break
		var line = ""
		if newline > 0:
			line = _incoming.subarray(0, newline - 1).get_string_from_utf8()
		_incoming = _incoming.subarray(newline + 1, _incoming.size() - 1) if newline + 1 < _incoming.size() else PoolByteArray()
		_handle_line(line)
		if _client == null:
			return
	if _outgoing.size() > 0:
		var sent = _client.put_partial_data(_outgoing)
		if sent[0] != OK:
			_disconnect_client()
		elif sent[1] > 0:
			_outgoing = _outgoing.subarray(sent[1], _outgoing.size() - 1) if sent[1] < _outgoing.size() else PoolByteArray()

func _handle_line(line):
	var parsed = JSON.parse(line)
	if parsed.error != OK or typeof(parsed.result) != TYPE_DICTIONARY:
		_reply({"id": null, "ok": false, "error": "invalid_json"})
		return
	var request = parsed.result
	var id = request.get("id", null)
	if typeof(id) != TYPE_NIL and typeof(id) != TYPE_STRING and typeof(id) != TYPE_INT and typeof(id) != TYPE_REAL:
		_reply({"id": null, "ok": false, "error": "invalid_id"})
		return
	match request.get("command", ""):
		"observe":
			_reply({"id": id, "ok": true, "state": _observe()})
		"release":
			_release()
			_reply({"id": id, "ok": true})
		"move":
			_handle_move(id, request)
		"act":
			_handle_act(id, request)
		"record_start", "record_stop", "record_status":
			var response = $Recorder.handle_request(request)
			response["id"] = id
			_reply(response)
		_:
			_reply({"id": id, "ok": false, "error": "unknown_command"})

func _handle_move(id, request):
	var scene = get_tree().current_scene
	var now = OS.get_ticks_msec()
	var observation_seq = request.get("observation_seq", null)
	var scene_id = scene.get_instance_id() if is_instance_valid(scene) else 0
	if not _finite_number(observation_seq) or observation_seq != _sequence or _last_observation_ms == 0 or now - _last_observation_ms > MAX_OBSERVATION_AGE_MS or scene_id != _last_observation_scene:
		_release()
		_reply({"id": id, "ok": false, "error": "stale_observation"})
		return
	if _last_observation_phase != "combat" or _phase(scene, _player(scene)) != "combat":
		_release()
		_reply({"id": id, "ok": false, "error": "not_in_combat"})
		return
	var x = request.get("x", null)
	var y = request.get("y", null)
	var ttl = request.get("ttl_ms", 250)
	if not _finite_number(x) or not _finite_number(y) or not _finite_number(ttl):
		_release()
		_reply({"id": id, "ok": false, "error": "invalid_movement"})
		return
	# Clamp components before taking length so huge JSON numbers cannot overflow.
	_move = Vector2(clamp(x, -1.0, 1.0), clamp(y, -1.0, 1.0))
	if _move.length_squared() > 1.0:
		_move = _move.normalized()
	var duration = int(clamp(ttl, 50, 1000))
	_move_expires_ms = now + duration
	_reply({"id": id, "ok": true, "movement": _vector(_move), "ttl_ms": duration})

func _handle_act(id, request):
	_release()
	var scene = get_tree().current_scene
	var sequence = request.get("observation_seq", null)
	var scene_id = scene.get_instance_id() if is_instance_valid(scene) else 0
	if not _finite_number(sequence) or sequence != _sequence or _last_observation_ms == 0 or OS.get_ticks_msec() - _last_observation_ms > MAX_MENU_OBSERVATION_AGE_MS or scene_id != _last_observation_scene:
		_reply({"id": id, "ok": false, "error": "stale_observation"})
		return
	var current_menu = _menus.snapshot(scene)
	var action = request.get("action", "")
	if current_menu.phase != _last_observation_phase or current_menu.fingerprint != _last_menu_fingerprint:
		_invalidate_observation()
		_reply({"id": id, "ok": false, "error": "menu_changed"})
		return
	if typeof(action) != TYPE_STRING or not current_menu.options.has(action):
		_reply({"id": id, "ok": false, "error": "invalid_action"})
		return
	# Consume before invoking game code; a duplicate request can never buy twice.
	_invalidate_observation()
	if _menus.execute(action):
		_reply({"id": id, "ok": true, "action": action})
	else:
		_reply({"id": id, "ok": false, "error": "action_unavailable"})

func movement_for(player, manual):
	var scene = get_tree().current_scene
	if player != _player(scene):
		return manual
	if manual.length_squared() > 0.0001:
		# A manual movement clears the lease, preventing it resuming after key-up.
		_release()
		return manual
	if _client == null or _client.get_status() != StreamPeerTCP.STATUS_CONNECTED or OS.get_ticks_msec() >= _move_expires_ms:
		_release()
		return manual
	if player != _player(scene) or _phase(scene, player) != "combat":
		return manual
	return _move

func _observe():
	var scene = get_tree().current_scene
	var player = _player(scene)
	var phase = _phase(scene, player)
	_sequence += 1
	_last_observation_ms = OS.get_ticks_msec()
	_last_observation_scene = scene.get_instance_id() if is_instance_valid(scene) else 0
	_last_observation_phase = phase
	var menu = _menus.snapshot(scene)
	_last_menu_fingerprint = menu.fingerprint
	var state = {
		"protocol_version": 2, "seq": _sequence, "phase": phase,
		"terminal": phase in ["gameover", "victory"],
		"menu": menu, "build": _menus.build_state(),
		"scene": scene.filename if is_instance_valid(scene) else "",
		"viewport_size": _vector(get_tree().root.size),
		"observed_at_ms": _last_observation_ms, "scene_id": _last_observation_scene,
		"player": null, "enemies": [], "projectiles": [], "materials": [], "consumables": [],
		"arena": null, "wave": _read(get_node_or_null("/root/RunData"), "current_wave"), "time_left": null,
		"controller": {"active": _move_expires_ms > _last_observation_ms, "movement": _vector(_move)}
	}
	if not is_instance_valid(player):
		return state
	state.player = _entity(player)
	state.player["health"] = _read(_read(player, "current_stats"), "health")
	state.player["max_health"] = _read(_read(player, "max_stats"), "health")
	state.player["speed"] = _read(_read(player, "current_stats"), "speed")
	var origin = player.global_position
	var spawner = _read(scene, "_entity_spawner")
	if spawner == null:
		spawner = _read(player, "_entity_spawner_ref")
	var enemies = _read(spawner, "enemies", []).duplicate()
	for boss in _read(spawner, "bosses", []):
		if not enemies.has(boss):
			enemies.append(boss)
	state["enemy_count"] = enemies.size()
	state.enemies = _nearest_entities(enemies, origin, 128)
	state.projectiles = _nearest_entities(_nodes(_read(scene, "_enemy_projectiles", [])), origin, 128)
	state.materials = _nearest_entities(_nodes(_read(scene, "_materials_container")), origin, 64)
	state.consumables = _nearest_entities(_nodes(_read(scene, "_consumables_container")), origin, 32)
	var min_pos = _read(player, "_min_pos")
	var max_pos = _read(player, "_max_pos")
	if typeof(min_pos) == TYPE_VECTOR2 and typeof(max_pos) == TYPE_VECTOR2 and max_pos.x > min_pos.x and max_pos.y > min_pos.y:
		state.arena = {"min": _vector(min_pos), "max": _vector(max_pos)}
	state.wave = _read(get_node_or_null("/root/RunData"), "current_wave")
	var timer = _read(scene, "_wave_timer")
	if timer is Timer:
		state.time_left = timer.time_left
	return state

func _player(scene):
	var players = _read(scene, "_players", [])
	if typeof(players) == TYPE_ARRAY and players.size() > 0 and is_instance_valid(players[0]):
		return players[0]
	return null

func _phase(scene, player):
	if _menus != null:
		return _menus.phase(scene, player)
	return "menu"

func _nodes(value):
	if typeof(value) == TYPE_ARRAY:
		return value
	if is_instance_valid(value) and value is Node:
		return value.get_children()
	return []

func _nearest_entities(nodes, origin, limit):
	var ranked = []
	for node in nodes:
		if not is_instance_valid(node) or not node is Node2D or not node.is_inside_tree() or _read(node, "dead", false):
			continue
		ranked.append({"node": node, "distance": node.global_position.distance_squared_to(origin)})
	ranked.sort_custom(self, "_closer")
	var result = []
	for index in range(min(limit, ranked.size())):
		result.append(_entity(ranked[index].node))
	return result

func _closer(a, b):
	return a.distance < b.distance

func _entity(node):
	var velocity = _read(node, "linear_velocity", _read(node, "velocity", _read(node, "_integrate_forces_velocity")))
	return {
		"id": node.get_instance_id(), "position": _vector(node.global_position),
		"velocity": _vector(velocity) if typeof(velocity) == TYPE_VECTOR2 else null
	}

func _read(object, property, fallback = null):
	if typeof(object) == TYPE_DICTIONARY:
		return object.get(property, fallback)
	if typeof(object) != TYPE_OBJECT or not is_instance_valid(object):
		return fallback
	var script = object.get_script()
	var key = str(script.get_instance_id()) if script != null else object.get_class()
	if not _property_cache.has(key):
		var properties = {}
		for entry in object.get_property_list():
			properties[entry.name] = true
		_property_cache[key] = properties
	if _property_cache[key].has(property):
		return object.get(property)
	return fallback

func _vector(value):
	return {"x": value.x, "y": value.y}

func _finite_number(value):
	return (typeof(value) == TYPE_INT or typeof(value) == TYPE_REAL) and not is_nan(value) and not is_inf(value)

func _release():
	_move = Vector2.ZERO
	_move_expires_ms = 0

func _invalidate_observation():
	_release()
	_last_observation_ms = 0
	_last_observation_scene = 0
	_last_menu_fingerprint = ""

func _disconnect_client():
	_invalidate_observation()
	if _client != null:
		_client.disconnect_from_host()
	_client = null
	_incoming = PoolByteArray()
	_outgoing = PoolByteArray()

func _reply(response):
	_outgoing += (JSON.print(response) + "\n").to_utf8()
	if _outgoing.size() > MAX_OUTPUT:
		_disconnect_client()
