extends Node

# Capture only this game's viewport. No desktop pixels or OS permissions.
var _active = false
var _path = ""
var _started_at_ms = 0
var _next_capture_ms = 0
var _interval_ms = 250
var _frames = 0
var _index = null
var _error = ""
const MAX_DURATION_MS = 2400000

func _ready():
	pause_mode = Node.PAUSE_MODE_PROCESS
	VisualServer.connect("frame_post_draw", self, "_capture")

func _exit_tree():
	_stop()

func _process(_delta):
	# macOS may suppress normal draws for an occluded game window. Force only
	# an overdue recording frame, without changing simulation speed or focus.
	if _active and OS.get_ticks_msec() >= _next_capture_ms + _interval_ms:
		VisualServer.force_draw(false)

func handle_request(request):
	match request.get("command", ""):
		"record_start":
			if _active:
				return {"ok": false, "error": "already_recording"}
			var session = request.get("session", "")
			if typeof(session) != TYPE_STRING or session.length() < 1 or session.length() > 80:
				return {"ok": false, "error": "invalid_session"}
			var valid = RegEx.new()
			valid.compile("^[a-zA-Z0-9_-]+$")
			if valid.search(session) == null:
				return {"ok": false, "error": "invalid_session"}
			var base = OS.get_environment("JEV_RECORDING_ROOT")
			if base.empty() or not base.is_abs_path():
				return {"ok": false, "error": "recording_root_not_configured"}
			_path = base.plus_file(session)
			var directory = Directory.new()
			if directory.file_exists(_path.plus_file("frames.jsonl")):
				return {"ok": false, "error": "session_already_has_frames"}
			if directory.make_dir_recursive(_path.plus_file("frames")) != OK:
				return {"ok": false, "error": "cannot_create_recording_directory"}
			_index = File.new()
			if _index.open(_path.plus_file("frames.jsonl"), File.WRITE) != OK:
				_index = null
				return {"ok": false, "error": "cannot_create_frame_index"}
			_interval_ms = int(clamp(request.get("interval_ms", 250), 100, 2000))
			_frames = 0
			_error = ""
			_started_at_ms = OS.get_ticks_msec()
			_next_capture_ms = _started_at_ms
			_active = true
		"record_stop":
			_stop()
	return _status()

func _status():
	return {"ok": true, "active": _active, "path": _path, "frames": _frames,
		"started_at_ms": _started_at_ms, "now_ms": OS.get_ticks_msec(),
		"interval_ms": _interval_ms, "recording_error": _error}

func _stop():
	_active = false
	if _index != null:
		_index.close()
		_index = null

func _capture():
	var now = OS.get_ticks_msec()
	if not _active or now < _next_capture_ms:
		return
	if now - _started_at_ms > MAX_DURATION_MS:
		_error = "duration_limit"
		_stop()
		return
	_next_capture_ms = now + _interval_ms
	var captured = get_tree().root.get_texture().get_data()
	if captured == null or captured.empty():
		_error = "empty_viewport"
		_stop()
		return
	captured.flip_y()
	_frames += 1
	var relative_path = "frames/frame_%06d.png" % _frames
	if captured.save_png(_path.plus_file(relative_path)) != OK:
		_error = "cannot_write_frame"
		_stop()
		return
	_index.store_line(JSON.print({"frame": _frames, "path": relative_path,
		"elapsed_ms": now - _started_at_ms, "width": captured.get_width(),
		"height": captured.get_height()}))
	_index.flush()
