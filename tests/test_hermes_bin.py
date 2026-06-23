import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import daily_editor  # noqa: E402
import rss_analyst  # noqa: E402


class HermesBinResolutionTests(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=True)
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def test_default_path_is_absolute(self):
        self.assertEqual(rss_analyst.get_hermes_bin(), "/home/dimitri/.local/bin/hermes")
        self.assertEqual(daily_editor.get_hermes_bin(), "/home/dimitri/.local/bin/hermes")

    def test_env_var_overrides_default(self):
        os.environ["HERMES_BIN"] = "/opt/hermes/bin/hermes"
        self.assertEqual(rss_analyst.get_hermes_bin(), "/opt/hermes/bin/hermes")
        self.assertEqual(daily_editor.get_hermes_bin(), "/opt/hermes/bin/hermes")

    def test_run_hermes_uses_resolved_binary_in_rss_analyst(self):
        with mock.patch.dict(os.environ, {"HERMES_BIN": "/custom/hermes"}, clear=True):
            completed = mock.Mock(returncode=0, stdout="ok\n", stderr="")
            with mock.patch.object(rss_analyst.subprocess, "run", return_value=completed) as run_mock:
                output = rss_analyst.run_hermes("prompt")

        self.assertEqual(output, "ok")
        self.assertEqual(run_mock.call_args.args[0][0], "/custom/hermes")

    def test_run_hermes_uses_resolved_binary_in_daily_editor(self):
        with mock.patch.dict(os.environ, {"HERMES_BIN": "/custom/hermes"}, clear=True):
            completed = mock.Mock(returncode=0, stdout="ok\n", stderr="")
            with mock.patch.object(daily_editor.subprocess, "run", return_value=completed) as run_mock:
                output = daily_editor.run_hermes("prompt")

        self.assertEqual(output, "ok")
        self.assertEqual(run_mock.call_args.args[0][0], "/custom/hermes")


if __name__ == "__main__":
    unittest.main()
