"""Launch Brotato with the local bridge; verified on macOS Steam 1.1.15.4."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = Path.home()/"Library/Application Support/Steam/steamapps/common/Brotato/Brotato.app/Contents/MacOS/Brotato"


def resolve_executable(value):
    """Resolve relative paths before the launcher's game-directory cwd change."""
    return Path(value).expanduser().resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, default=DEFAULT,
                        help="Brotato executable (default: macOS Steam installation)")
    parser.add_argument("--check", action="store_true", help="Load/compile the mod, then quit")
    args = parser.parse_args(argv)
    args.executable = resolve_executable(args.executable)
    if not args.executable.is_file():
        parser.error("Brotato executable not found; pass --executable")
    if not os.access(args.executable, os.X_OK):
        parser.error("Brotato executable is not executable")
    if not (ROOT/"tools/launch_brotato.gd").is_file():
        parser.error("Missing tools/launch_brotato.gd; run from a complete source checkout")
    subprocess.run([sys.executable, str(ROOT/"tools/package_mod.py")], check=True)
    env = os.environ.copy()
    env.pop("JEV_KEY", None)
    env.update(SteamAppId="1942280", SteamGameId="1942280",
               JEV_BRIDGE_ZIP=str(ROOT/"dist/Jev-BrotatoBridge.zip"),
               JEV_RECORDING_ROOT=str(ROOT/"recordings"),
               JEV_BRIDGE_CHECK_ONLY="1" if args.check else "0")
    cmd = [str(args.executable), "--script", str(ROOT/"tools/launch_brotato.gd")]
    cmd += ["--no-window", "--audio-driver", "Dummy"] if args.check else ["--windowed", "--resolution", "1280x720"]
    return subprocess.call(cmd, env=env, cwd=args.executable.parent)


if __name__ == "__main__":
    raise SystemExit(main())
