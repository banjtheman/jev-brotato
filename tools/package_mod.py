"""Build the mod ZIP offline; local gameplay is verified on macOS Steam 1.1.15.4.

The optional non-Steam install path is provided for experimentation, not verified
compatibility. The Steam launcher loads the ZIP without a base-game modification.
"""
import argparse
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent.parent
MOD_ID = "Jev-BrotatoBridge"
DEFAULT_GAME = Path.home()/"Library/Application Support/Steam/steamapps/common/Brotato"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--game-dir", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--standalone", action="store_true", help="Experimental: use game/mods for non-Steam builds")
    args = parser.parse_args(argv)
    if args.install and not args.standalone:
        parser.error("Steam builds filter local Workshop mods; use tools/launch_brotato.py. Non-Steam installs require --standalone.")
    source = ROOT/"mods-unpacked"/MOD_ID
    for required in ("manifest.json", "mod_main.gd", "menus.gd", "recorder.gd", "extensions/player_movement_behavior.gd"):
        if not (source/required).is_file():
            parser.error(f"Missing mod file: {required}")
    destination = ROOT/"dist"/(MOD_ID+".zip")
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(source.rglob("*")):
            if path.is_file() and path.suffix in (".gd", ".json", ".md"):
                package.write(path, path.relative_to(ROOT))
    print(destination)
    if args.install:
        game = args.game_dir.expanduser().resolve()
        if not game.is_dir() or not any((game/name).exists() for name in ("Brotato.app", "Brotato.exe", "Brotato.x86_64", "Brotato.pck")):
            parser.error("game-dir must be the Brotato install folder containing its application or PCK")
        target = game/"mods"/destination.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = ROOT/".local"/"mod-backups"
            backup.mkdir(parents=True, exist_ok=True)
            import time
            shutil.copy2(target, backup/(str(time.time_ns())+"-"+target.name))
        shutil.copy2(destination, target)
        print(f"Installed {target}\nRestart Brotato to load it. Remove this ZIP to uninstall.")


if __name__ == "__main__":
    main()
