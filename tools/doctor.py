"""Read-only setup checks; never launches Brotato or contacts Jev/the bridge."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import re
import shlex
import shutil
import sys

if __package__:
    from .launch_brotato import DEFAULT, KEY_NAMES, ROOT, resolve_executable
else:
    from launch_brotato import DEFAULT, KEY_NAMES, ROOT, resolve_executable


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


SOURCE_FILES = (
    "pyproject.toml",
    "brotato_agent/__main__.py",
    "brotato_agent/jev.py",
    "brotato_agent/bridge.py",
    "brotato_agent/session.py",
    "tools/launch_brotato.gd",
    "tools/package_mod.py",
    "mods-unpacked/Jev-BrotatoBridge/manifest.json",
    "mods-unpacked/Jev-BrotatoBridge/mod_main.gd",
    "mods-unpacked/Jev-BrotatoBridge/menus.gd",
    "mods-unpacked/Jev-BrotatoBridge/recorder.gd",
    "mods-unpacked/Jev-BrotatoBridge/extensions/player_movement_behavior.gd",
)


def _dotenv_keys(path):
    """Read the agent's simple .env format without setting process variables.

    Match load_env's quoting/comment rules. Parse every assignment so malformed
    files fail here as they do in the agent. Return only KEY_NAMES entries, and
    never include file contents in errors.
    """
    try:
        content = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    keys = {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            raise ValueError("Invalid environment file")
        name, value = match.groups()
        if value.startswith(("'", '"')):
            parts = shlex.split(value, comments=True, posix=True)
            if len(parts) != 1:
                raise ValueError("Invalid environment file")
            value = parts[0]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if "\x00" in value:
            raise ValueError("Invalid environment file")
        if name in KEY_NAMES:
            keys[name] = value
    return keys


def check_key(env_path, environ=None):
    """Return only configuration status; never return credentials or snippets.

    Resolve the key as the agent does: per name, an environment variable (even an
    empty one) wins over the .env file, and the first nonempty name in KEY_NAMES is used.
    """
    environ = os.environ if environ is None else environ
    try:
        file_keys = _dotenv_keys(env_path)
    except (OSError, UnicodeError, ValueError):
        return Check("FAIL", "API key", "Environment file is unreadable or malformed; no values displayed.")
    name = key = source = ""
    for candidate in KEY_NAMES:
        value = environ.get(candidate, file_keys.get(candidate, "")).strip()
        if value:
            name, key = candidate, value
            source = "environment" if candidate in environ else ".env file"
            break
    if not key or key == "your_typesafe_api_key":
        return Check("FAIL", "API key", "Not configured; set TYPESAFE_API_KEY in the environment or your .env file.")
    if any(ord(char) < 33 or ord(char) > 126 for char in key):
        return Check("FAIL", "API key", f"{name} must be one printable ASCII token.")
    return Check("OK", "API key", f"{name} configured via {source}; value hidden and authentication not tested.")


def collect_checks(*, root=ROOT, executable=DEFAULT, env_path=None, offline=False):
    root = Path(root)
    env_path = root/".env" if env_path is None else Path(env_path).expanduser().resolve()
    checks = [Check("OK" if sys.version_info >= (3, 10) else "FAIL", "Python",
                    f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}; requires 3.10 or newer.")]
    missing = [name for name in SOURCE_FILES if not (root/name).is_file()]
    checks.append(Check("FAIL" if missing else "OK", "Source layout",
                        "Missing: " + ", ".join(missing) if missing else "Required Python, launcher and mod files are present."))
    if offline:
        checks.extend((Check("SKIP", "Brotato", "Offline mode; game installation not required."),
                       Check("SKIP", "API key", "Offline mode; credentials not read or required.")))
    else:
        game = resolve_executable(executable)
        if not game.is_file():
            checks.append(Check("FAIL", "Brotato", f"Executable missing: {game}; pass --executable PATH."))
        elif not os.access(game, os.X_OK):
            checks.append(Check("FAIL", "Brotato", "File is present but is not executable."))
        else:
            checks.append(Check("OK", "Brotato", f"Executable present: {game}; version and mod loading not tested."))
        checks.append(check_key(env_path))
    try:
        pillow = importlib.util.find_spec("PIL") is not None
    except (ImportError, ValueError):
        pillow = False
    checks.append(Check("OK" if pillow else "WARN", "Pillow (optional)",
                        "Available for video rendering." if pillow else 'Not found; install the video extra with pip install -e ".[video]".'))
    ffmpeg = shutil.which("ffmpeg")
    checks.append(Check("OK" if ffmpeg else "WARN", "ffmpeg (optional)",
                        "Found on PATH for video rendering." if ffmpeg else "Not found on PATH; required only to encode video."))
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, default=DEFAULT,
                        help="Brotato executable (default: macOS Steam installation)")
    parser.add_argument("--env", type=Path, default=ROOT/".env", help="Local environment file (default: checkout/.env)")
    parser.add_argument("--offline", action="store_true", help="Check source and Python only; skip game and credentials")
    args = parser.parse_args(argv)
    print("Gameplay verified only on macOS Steam Brotato 1.1.15.4 (Godot 3). Other builds are unverified.")
    print("Read-only checks: no game launch, network requests, bridge connection or environment changes.")
    checks = collect_checks(executable=args.executable, env_path=args.env, offline=args.offline)
    for check in checks:
        print(f"[{check.status}] {check.name}: {check.detail}")
    return int(any(check.status == "FAIL" for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
