extends SceneTree

# The Steam build loads only subscribed Workshop items. Load our development
# ZIP explicitly without changing the game's PCK or Workshop subscriptions.
func _init():
	var zip_path = OS.get_environment("JEV_BRIDGE_ZIP")
	if zip_path.empty() or not ProjectSettings.load_resource_pack(zip_path):
		printerr("JEV: Could not load bridge ZIP: ", zip_path)
		quit(1)
		return
	var loader = root.get_node("ModLoader")
	var store = root.get_node("ModLoaderStore")
	if loader.has_node("Jev-BrotatoBridge"):
		call_deferred("_start_game")
		return
	loader._init_mod_data("Jev-BrotatoBridge", zip_path)
	store.mod_data["Jev-BrotatoBridge"].load_manifest()
	var script = load("res://mods-unpacked/Jev-BrotatoBridge/mod_main.gd")
	if script == null:
		printerr("JEV: Bridge script did not compile")
		quit(1)
		return
	var bridge = script.new()
	bridge.name = "Jev-BrotatoBridge"
	loader.add_child(bridge)
	call_deferred("_start_game")

func _start_game():
	var bridge = root.get_node_or_null("ModLoader/Jev-BrotatoBridge")
	if bridge == null or not bridge.is_listening():
		printerr("JEV: Bridge failed to start; is another game using port 4243?")
		quit(1)
		return
	if OS.get_environment("JEV_BRIDGE_CHECK_ONLY") == "1":
		print("JEV: Bridge loaded successfully")
		quit()
		return
	var error = change_scene(ProjectSettings.get_setting("application/run/main_scene"))
	if error != OK:
		printerr("JEV: Failed to start game scene: ", error)
		quit(1)
		return
	call_deferred("_finish_bootstrap")

func _finish_bootstrap():
	# Under --script the autoloads are already ready before the splash exists.
	# Replay its missed readiness callback, preserving settings and save checks.
	if current_scene != null and current_scene.filename == "res://pause.tscn" and current_scene.has_method("_on_progress_data_ready"):
		current_scene._on_progress_data_ready()
