"""The launcher never passes API keys to the game process."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from brotato_agent.jev import KEY_NAMES as CLIENT_KEY_NAMES
from tools import launch_brotato


class LauncherTests(unittest.TestCase):
    def test_game_environment_excludes_every_key_name(self):
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)/"Brotato"
            game.touch()
            game.chmod(0o700)
            keys = {name: "not-a-real-api-key" for name in launch_brotato.KEY_NAMES}
            with patch.dict(launch_brotato.os.environ, keys), \
                 patch.object(launch_brotato.subprocess, "run") as package, \
                 patch.object(launch_brotato.subprocess, "call", return_value=0) as launch:
                self.assertEqual(launch_brotato.main(["--executable", str(game), "--check"]), 0)
        package.assert_called_once()
        env = launch.call_args.kwargs["env"]
        for name in launch_brotato.KEY_NAMES:
            self.assertNotIn(name, env)
        self.assertEqual(env["JEV_BRIDGE_CHECK_ONLY"], "1")

    def test_launcher_strips_every_key_name_the_client_reads(self):
        self.assertEqual(launch_brotato.KEY_NAMES, CLIENT_KEY_NAMES)


if __name__ == "__main__":
    unittest.main()
