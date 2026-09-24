"""Setup diagnostics stay offline and never print or export credentials."""

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import doctor
from tools.launch_brotato import resolve_executable


class DoctorTests(unittest.TestCase):
    def test_key_is_redacted_and_environment_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory)/".env"
            env.write_text("export JEV_KEY='secret-value-123' # local\nLABEL=${JEV_KEY}\n")
            with patch.dict(os.environ, {}, clear=True):
                before = dict(os.environ)
                result = doctor.check_key(env)
                self.assertEqual(result.status, "OK")
                self.assertNotIn("secret-value-123", repr(result))
                self.assertEqual(dict(os.environ), before)

    def test_environment_wins_including_empty_values(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory)/".env"
            env.write_text("JEV_KEY=file-secret\n")
            self.assertEqual(doctor.check_key(env, {"JEV_KEY": ""}).status, "FAIL")
            result = doctor.check_key(env, {"JEV_KEY": "environment-secret"})
            self.assertEqual(result.status, "OK")
            self.assertIn("via environment", result.detail)
            self.assertNotIn("environment-secret", repr(result))

    def test_resolves_names_in_the_same_order_as_the_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory)/".env"
            env.write_text("TYPESAFE_API_KEY=typesafe-secret\nJEV_KEY=legacy-secret\n")
            result = doctor.check_key(env, {})
            self.assertEqual(result.status, "OK")
            self.assertIn("TYPESAFE_API_KEY configured via .env file", result.detail)
            self.assertNotIn("secret", repr(result))
            # Like the client, an empty preferred name falls back to JEV_KEY.
            result = doctor.check_key(env, {"TYPESAFE_API_KEY": ""})
            self.assertEqual(result.status, "OK")
            self.assertIn("JEV_KEY configured via .env file", result.detail)
            # A placeholder under the preferred name is what the client would send.
            env.write_text("TYPESAFE_API_KEY=your_typesafe_api_key\nJEV_KEY=legacy-secret\n")
            self.assertEqual(doctor.check_key(env, {}).status, "FAIL")

    def test_missing_placeholder_and_malformed_values_are_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory)/".env"
            self.assertEqual(doctor.check_key(env, {}).status, "FAIL")
            for content in ("JEV_KEY=your_typesafe_api_key\n", "JEV_KEY='secret value'\n",
                            "JEV_KEY=secret-value\nBAD='secret-value\n",
                            "secret-value without assignment\n"):
                env.write_text(content)
                result = doctor.check_key(env, {})
                self.assertEqual(result.status, "FAIL")
                self.assertNotIn("secret", repr(result))

    def test_cli_redacts_key_and_does_not_launch_or_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory)/".env"
            env.write_text("JEV_KEY=secret-cli-value\n")
            game = Path(directory)/"Brotato"
            game.touch()
            game.chmod(0o700)
            output = StringIO()
            with patch.dict(os.environ, {}, clear=True), redirect_stdout(output), \
                 patch("subprocess.Popen", side_effect=AssertionError("Must not launch")), \
                 patch("socket.socket", side_effect=AssertionError("Must not connect")):
                result = doctor.main(["--env", str(env), "--executable", str(game)])
            self.assertEqual(result, 0)
            self.assertNotIn("secret-cli-value", output.getvalue())
            self.assertIn("authentication not tested", output.getvalue())

    def test_offline_skips_game_and_does_not_read_credentials(self):
        with patch.object(doctor, "check_key", side_effect=AssertionError("Must not read key")):
            checks = doctor.collect_checks(offline=True, executable="/no-game-installed")
        self.assertEqual({c.name for c in checks if c.status == "SKIP"}, {"Brotato", "API key"})
        self.assertFalse(any(c.status == "FAIL" for c in checks))

    def test_missing_source_is_a_failure_even_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            checks = doctor.collect_checks(root=directory, offline=True)
        source = next(c for c in checks if c.name == "Source layout")
        self.assertEqual(source.status, "FAIL")
        self.assertIn("mod_main.gd", source.detail)

    def test_executable_paths_expand_home_and_resolve_before_cwd_change(self):
        self.assertEqual(resolve_executable("~/Games/Brotato"), Path.home()/"Games/Brotato")
        relative = Path("test game")/"Brotato"
        self.assertEqual(resolve_executable(relative), Path.cwd()/relative)


if __name__ == "__main__":
    unittest.main()
