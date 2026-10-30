extends Reference

# Limits belong to a game run, not to the lifetime of the bridge or recorder.
const LIMITS = {"shop": 2, "upgrade": 1}
var _limits = LIMITS.duplicate()
var _counts = {}
var _first_wave_scene = 0
var _last_wave = 0
var generation = 0

func _init():
	if OS.get_environment("JEV_BRIDGE_NATIVE_REROLLS") == "1":
		use_native_limits()

func use_native_limits():
	# Null means the native button and affordability checks supply the limit.
	_limits = {"shop": null, "upgrade": null}

func limit(scope):
	return _limits.get(scope)

func observe(current_phase, wave, scene_id):
	if not current_phase in ["combat", "shop", "upgrade", "crate"] or wave <= 0:
		return
	# Also detect restarting a game abandoned in shop 1. Pauses, reconnects
	# and later waves retain their quota; a fresh wave-1 combat scene does not.
	var new_first_wave = current_phase == "combat" and wave == 1 and scene_id != _first_wave_scene
	if wave < _last_wave or new_first_wave:
		_counts.clear()
		generation += 1
		_first_wave_scene = 0
	if current_phase == "combat" and wave == 1:
		_first_wave_scene = scene_id
	_last_wave = wave

func used(wave, scope):
	return _counts.get(str(wave) + ":" + scope, 0)

func allows(wave, scope):
	if not _limits.has(scope):
		return false
	return limit(scope) == null or used(wave, scope) < limit(scope)

func consume(wave, scope):
	if not allows(wave, scope):
		return false
	var key = str(wave) + ":" + scope
	_counts[key] = used(wave, scope) + 1
	return true

func snapshot(wave):
	return {"run_generation": generation,
		"shop_used": used(wave, "shop"), "shop_limit": limit("shop"),
		"upgrade_used": used(wave, "upgrade"), "upgrade_limit": limit("upgrade")}
