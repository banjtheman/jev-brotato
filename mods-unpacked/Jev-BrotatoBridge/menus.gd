extends Node

# Only offers actions currently allowed by Brotato's own menu controllers.
# Each callback follows the same validation and signal path as its UI button.
var _bridge
var _targets = {}
var _strip_tags = RegEx.new()
var _stat_icons = RegEx.new()
var _rerolls_by_wave = {}

func _ready():
	_bridge = get_parent()
	_strip_tags.compile("\\[[^\\]]*\\]")
	_stat_icons.compile("res://items/stats/([A-Za-z0-9_]+)\\.(?:png|svg|webp)")

func phase(scene, player):
	if not is_instance_valid(scene):
		return "menu"
	if _read(scene, "_is_run_won", false):
		return "victory"
	if _read(scene, "_is_run_lost", false) or _read(scene, "_is_wave_failed", false):
		return "gameover"
	if "end_run.tscn" in scene.filename:
		return "victory" if RunData.run_won else "gameover"
	var container = _upgrade_container(scene)
	if is_instance_valid(container):
		if _visible(_read(container, "_items_container")):
			return "crate"
		if _visible(_read(container, "_upgrades_container")):
			return "upgrade"
	if get_tree().paused:
		return "paused"
	if scene.has_method("_get_shop_items_container") and scene.has_method("_on_GoButton_pressed"):
		return "shop"
	if not RunData.wave_in_progress or not is_instance_valid(player) or not player.is_inside_tree():
		return "menu"
	if _read(player, "dead", false):
		return "gameover"
	if _read(player, "cleaning_up", false) or _read(scene, "_cleaning_up", false) or _read(scene, "_end_wave_timer_timedout", false):
		return "menu"
	return "combat"

func snapshot(scene):
	_targets.clear()
	var current_phase = phase(scene, _bridge._player(scene))
	var options = {}
	var details = {"phase": current_phase, "options": options, "status": "waiting", "reason": "wave_transition"}
	if current_phase in ["gameover", "victory"]:
		details.status = "terminal"
		details.reason = current_phase
	elif current_phase == "shop":
		_shop_options(scene, options)
	elif current_phase in ["upgrade", "crate"]:
		_upgrade_options(scene, current_phase, options)
	elif current_phase == "paused":
		details.reason = "game_paused"
	elif current_phase == "menu":
		if not is_instance_valid(scene) or not scene.has_method("clean_up_room"):
			details.status = "unsupported"
			details.reason = "start_a_run_in_the_game"
	elif current_phase == "combat":
		details.reason = "combat_uses_move"
	if not options.empty():
		details.status = "actions"
		details.reason = ""
	var identity = []
	for key in _targets:
		var target = _targets[key]
		identity.append([key, target.node.get_instance_id(), target.identity])
	var wave = RunData.current_wave
	var gold = RunData.get_player_gold(0) if RunData.get_player_count() > 0 else 0
	details["fingerprint"] = JSON.print([current_phase, scene.get_instance_id() if is_instance_valid(scene) else 0, wave, gold, options, identity]).md5_text()
	return details

func execute(action):
	if not _targets.has(action):
		return false
	var target = _targets[action]
	if not is_instance_valid(target.node) or not target.node.has_method(target.method):
		return false
	# Snapshot is rebuilt and its fingerprint checked immediately before this call.
	if target.kind == "reroll":
		var wave_key = str(RunData.current_wave) + ":" + target.scope
		_rerolls_by_wave[wave_key] = _rerolls_by_wave.get(wave_key, 0) + 1
	target.node.callv(target.method, target.args)
	_targets.clear()
	return true

func build_state():
	var result = {"character": null, "weapons": [], "items": [], "stats": {}, "gold": 0, "currency": 0, "level": 0}
	if RunData.get_player_count() == 0:
		return result
	var character = RunData.get_player_character(0)
	if not is_instance_valid(character):
		return result
	result.character = _item(character)
	result.gold = RunData.get_player_gold(0)
	result.currency = RunData.get_player_currency(0)
	result.level = _read(RunData.players_data[0], "current_level", 0)
	result["health"] = RunData.get_player_current_health(0)
	result["max_health"] = RunData.get_player_max_health(0)
	for weapon in RunData.get_player_weapons(0):
		result.weapons.append(_item(weapon))
	# Group identical owned items, preserving counts and concrete effects.
	var grouped_items = {}
	for item in RunData.get_player_items(0):
		var key = str(_read(item, "my_id", "")) + ":" + str(_read(item, "is_cursed", false))
		if grouped_items.has(key):
			grouped_items[key].count += 1
		else:
			var entry = _item(item)
			entry["count"] = 1
			grouped_items[key] = entry
	result.items = grouped_items.values()
	for stat in _read(RunData, "primary_stats_list", []):
		var stat_name = Keys.hash_to_string.get(stat, str(stat))
		result.stats[stat_name] = Utils.get_stat(stat, 0)
	return result

func _shop_options(shop, options):
	if RunData.get_player_count() != 1:
		return
	var container = shop._get_shop_items_container(0)
	if not is_instance_valid(container):
		return
	var pressed = _read(shop, "_player_pressed_go_button", [false])
	if not pressed.empty() and pressed[0]:
		return
	if _read(container, "_is_delay_active", false):
		return
	var items = _read(container, "_shop_items", [])
	for index in range(items.size()):
		var entry = items[index]
		if not is_instance_valid(entry) or not _read(entry, "active", false):
			continue
		var item = _read(entry, "item_data")
		if not is_instance_valid(item):
			continue
		var price = _read(entry, "value", 0)
		if RunData.get_player_currency(0) < price:
			continue
		if item is WeaponData and not container._can_weapon_be_bought(entry):
			continue
		var option = _item(item)
		option["kind"] = "buy_weapon" if item is WeaponData else "buy_item"
		option["cost"] = price
		option["currency"] = "max_hp" if RunData.get_player_effect_bool(Keys.hp_shop_hash, 0) else "materials"
		_offer(options, "buy_" + str(index), option, container, "on_shop_item_buy_button_pressed", [entry], [item.get_instance_id(), entry.get_instance_id()])
	var locked = RunData.get_player_effect_bool(Keys.lock_current_weapons_hash, 0)
	if not locked:
		var weapons = RunData.get_player_weapons(0)
		for index in range(weapons.size()):
			var weapon = weapons[index]
			if RunData.can_combine(weapon, 0):
				var option = _item(weapon)
				option["kind"] = "combine"
				option["description"] = "Combine two matching owned weapons into the next tier. " + option.description
				option["cost"] = 0
				_offer(options, "combine_" + str(index), option, shop, "_on_item_combine_button_pressed", [weapon, 0], weapon.get_instance_id())
			if weapons.size() > 1:
				var option = _item(weapon)
				option["kind"] = "recycle_weapon"
				option["gain"] = ItemService.get_recycling_value(RunData.current_wave, weapon.value, 0, true)
				option["description"] = "Permanently recycle this owned weapon. " + option.description
				_offer(options, "recycle_" + str(index), option, shop, "_on_item_discard_button_pressed", [weapon, 0], weapon.get_instance_id())
	var prices = _read(shop, "_reroll_price", [])
	var reroll_count = _rerolls_by_wave.get(str(RunData.current_wave) + ":shop", 0)
	if prices.size() > 0 and reroll_count < 2 and RunData.get_player_gold(0) >= prices[0] and RunData.get_player_locked_shop_items(0).size() < ItemService.NB_SHOP_ITEMS:
		_offer(options, "reroll", {"kind": "reroll", "name": "Reroll shop", "description": "Replace unlocked offers. At most two agent rerolls per shop.", "cost": prices[0]}, shop, "_on_RerollButton_pressed", [0], prices[0], "shop")
	_offer(options, "next_wave", {"kind": "next_wave", "name": "Start next wave", "description": "Keep the current build and enter combat.", "cost": 0}, shop, "_on_GoButton_pressed", [0], RunData.current_wave)

func _upgrade_options(scene, current_phase, options):
	var container = _upgrade_container(scene)
	if not is_instance_valid(container) or _read(container, "_button_pressed", false):
		return
	if current_phase == "crate":
		var item = _read(container, "_item_data")
		if not is_instance_valid(item):
			return
		if _button_enabled(_read(container, "_take_button")):
			var option = _item(item)
			option["kind"] = "take_item"
			option["cost"] = 0
			_offer(options, "take", option, container, "_on_TakeButton_pressed", [], item.get_instance_id())
		if _button_enabled(_read(container, "_discard_button")):
			var option = _item(item)
			option["kind"] = "recycle_crate"
			option["gain"] = ItemService.get_recycling_value(RunData.current_wave, item.value, 0, item is WeaponData)
			option["description"] = "Recycle the offered item instead of taking it. " + option.description
			_offer(options, "recycle_crate", option, container, "_on_DiscardButton_pressed", [], item.get_instance_id())
	else:
		var uis = container._get_upgrade_uis()
		for index in range(uis.size()):
			var ui = uis[index]
			if not _visible(ui) or not _button_enabled(_read(ui, "button")):
				continue
			var data = _read(ui, "upgrade_data")
			if not is_instance_valid(data):
				continue
			var option = _item(data)
			option["kind"] = "upgrade"
			option["cost"] = 0
			_offer(options, "upgrade_" + str(index), option, ui, "_on_ChooseButton_pressed", [], data.get_instance_id())
		var price = _read(container, "_reroll_price", 0)
		var reroll_count = _rerolls_by_wave.get(str(RunData.current_wave) + ":upgrade", 0)
		if options.size() > 1 and reroll_count < 1 and _button_enabled(_read(container, "_reroll_button")) and RunData.get_player_gold(0) >= price:
			_offer(options, "reroll", {"kind": "reroll", "name": "Reroll upgrades", "description": "Replace all upgrade choices. At most one agent upgrade reroll per wave.", "cost": price}, container, "_on_RerollButton_pressed", [], price, "upgrade")

func _upgrade_container(scene):
	if not is_instance_valid(scene) or RunData.get_player_count() != 1:
		return null
	var ui = _read(scene, "_upgrades_ui")
	if not _visible(ui):
		return null
	var container = _read(ui, "_player_container1")
	return container if _visible(container) else null

func _offer(options, key, option, node, method, args, identity, scope = ""):
	if not is_instance_valid(node) or not node.has_method(method):
		return
	options[key] = option
	_targets[key] = {"node": node, "method": method, "args": args, "identity": identity, "kind": option.kind, "scope": scope}

func _item(data):
	var result = {"id": "", "name": "", "description": "", "tier": 0, "cursed": false}
	if not is_instance_valid(data):
		return result
	result.id = _read(data, "my_id", "")
	result.name = data.get_name_text() if data.has_method("get_name_text") else str(_read(data, "name", ""))
	result.tier = _read(data, "tier", 0)
	result.cursed = _read(data, "is_cursed", false)
	var descriptions = []
	for effect in _read(data, "effects", []):
		if is_instance_valid(effect) and effect.has_method("get_text"):
			descriptions.append(_plain(effect.get_text(0, false)))
	result.description = PoolStringArray(descriptions).join("; ").substr(0, 2200)
	if data is WeaponData:
		result["weapon_stats"] = _plain(data.get_weapon_stats_text(0)).substr(0, 2200)
		result["weapon_id"] = _read(data, "weapon_id", "")
	return result

func _plain(value):
	var text = str(value)
	# Stat images carry meaning (damage scaling), so retain their readable name.
	# Only the verified stat-icon directory is rewritten; other text is preserved.
	for icon in _stat_icons.search_all(text):
		var label = icon.get_string(1).replace("_", " ").capitalize()
		text = text.replace(icon.get_string(0), " " + label)
	return _strip_tags.sub(text, "", true).strip_edges()

func _visible(node):
	return is_instance_valid(node) and node is CanvasItem and node.is_visible_in_tree()

func _button_enabled(node):
	return _visible(node) and node is BaseButton and not node.disabled

func _read(object, property, fallback = null):
	return _bridge._read(object, property, fallback)
