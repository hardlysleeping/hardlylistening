"""The autodeploy rollback, exercised instead of asserted on paper.

`tools/autodeploy.sh` backs up the Python tree, refuses to restart on a file that will not parse,
restarts on a change and rolls back when health never answers. Until this file existed, only the
first half of that sentence had ever been observed: the rollback path was documented and never run.

It runs here against a throwaway tree, a stub `systemctl` (which only records that it was asked)
and a stub `curl` (which answers whatever the test decided), through the `AUTODEPLOY_*` variables
the script already reads. The installed unit sets none of them, so this changes nothing about a
real deploy. No real service, no real backup directory and no real source file is touched.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "autodeploy.sh"
SYSTEMCTL_STUB = """#!/bin/bash
echo "$*" >> "$STUB_LOG"
exit 0
"""
CURL_STUB = """#!/bin/bash
cat "$STUB_HEALTH"
"""
DATE_STUB = """#!/bin/bash
# One increasing stamp per call, so two runs cannot order themselves by the clock.
n=$(cat "$STUB_CLOCK")
echo $((n + 1)) > "$STUB_CLOCK"
printf '2026010%s-0000%s\\n' "$n" "$n"
"""


class Autodeploy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        (self.repo / "web").mkdir(parents=True)
        self.write("server.py", "print('v1')\n")
        self.write("web/index.html", "<p>v1</p>\n")
        self.backups = root / "backups"
        self.backups.mkdir()
        self.state = root / "state.pysum"
        self.bin = root / "bin"
        self.bin.mkdir()
        self.stub("systemctl", SYSTEMCTL_STUB)
        self.stub("curl", CURL_STUB)
        self.stub("date", DATE_STUB)
        (root / "clock").write_text("1")
        (root / "health").write_text("200")
        (root / "systemctl.log").write_text("")
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    # -- the throwaway world ---------------------------------------------------------------
    def write(self, name, text):
        (self.repo / name).write_text(text)

    def read(self, name):
        return (self.repo / name).read_text()

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def run_deploy(self):
        env = dict(os.environ)
        env.update({
            "PATH": str(self.bin) + os.pathsep + env["PATH"],
            "AUTODEPLOY_REPO": str(self.repo),
            "AUTODEPLOY_BACKUPS": str(self.backups),
            "AUTODEPLOY_STATE": str(self.state),
            "AUTODEPLOY_UNIT": "test.service",
            "AUTODEPLOY_HEALTH": "http://127.0.0.1:1/api/health",
            "AUTODEPLOY_HEALTH_TRIES": "3",
            "STUB_LOG": str(self.root / "systemctl.log"),
            "STUB_HEALTH": str(self.root / "health"),
            "STUB_CLOCK": str(self.root / "clock"),
        })
        done = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
        return done.returncode, done.stdout + done.stderr

    def restarts(self):
        log = (self.root / "systemctl.log").read_text().strip()
        return [line for line in log.splitlines() if line]

    def backup_dirs(self):
        return sorted(path.name for path in self.backups.iterdir() if path.is_dir())

    # -- the cases -------------------------------------------------------------------------
    def test_the_first_run_records_a_baseline_and_restarts_nothing(self):
        code, out = self.run_deploy()
        self.assertEqual(code, 0, out)
        self.assertIn("baseline recorded", out)
        self.assertEqual(self.restarts(), [])
        self.assertTrue(self.state.is_file())

    def test_a_python_change_restarts_once_and_records_the_new_hash(self):
        self.run_deploy()
        before = self.state.read_text()
        self.write("server.py", "print('v2')\n")
        code, out = self.run_deploy()
        self.assertEqual(code, 0, out)
        self.assertIn("healthy after restart", out)
        self.assertEqual(self.restarts(), ["--user restart test.service"])
        self.assertNotEqual(self.state.read_text(), before)
        self.assertEqual(len(self.backup_dirs()), 1)      # the v1 tree was kept first

    def test_a_web_only_change_is_not_a_restart(self):
        self.run_deploy()
        self.write("web/index.html", "<p>v2</p>\n")
        code, out = self.run_deploy()
        self.assertEqual(code, 0, out)
        self.assertIn("no python change", out)
        self.assertEqual(self.restarts(), [])

    def test_a_syntax_error_restores_the_previous_python_and_leaves_the_service_alone(self):
        self.run_deploy()
        self.write("server.py", "print('v2')\n")          # a good deploy, so a previous tree exists
        self.assertEqual(self.run_deploy()[0], 0)
        self.assertEqual(self.read("server.py"), "print('v2')\n")

        self.write("server.py", "def broken(:\n")
        code, out = self.run_deploy()
        self.assertEqual(code, 1, out)
        self.assertIn("syntax error", out)
        self.assertIn("server.py", out)
        self.assertEqual(self.read("server.py"), "print('v2')\n")   # the previous python is back
        self.assertEqual(self.restarts(), ["--user restart test.service"])   # only the good one

    def test_a_service_that_never_gets_healthy_is_rolled_back_and_restarted_again(self):
        self.run_deploy()
        self.write("server.py", "print('v2')\n")
        self.assertEqual(self.run_deploy()[0], 0)
        before = self.state.read_text()

        (self.root / "health").write_text("500")
        self.write("server.py", "print('v3')\n")          # parses, but the server will not answer
        code, out = self.run_deploy()
        self.assertEqual(code, 1, out)
        self.assertIn("rolling back", out)
        self.assertEqual(self.read("server.py"), "print('v2')\n")
        # one for the good v2, one for v3 and one after the rollback put v2 back
        self.assertEqual(self.restarts(),
                         ["--user restart test.service"] * 3)
        # The state stays at the last version that actually worked, so the next run tries again.
        self.assertEqual(self.state.read_text(), before)

    def test_the_script_reads_none_of_the_live_paths_when_it_is_pointed_elsewhere(self):
        text = SCRIPT.read_text()
        for name in ("AUTODEPLOY_REPO", "AUTODEPLOY_BACKUPS", "AUTODEPLOY_STATE",
                     "AUTODEPLOY_UNIT", "AUTODEPLOY_HEALTH", "AUTODEPLOY_HEALTH_TRIES"):
            self.assertIn(name, text)
        # Every baked-in path is a default, not a hard-coded use: the live names appear only on
        # the `:-` side of an assignment.
        lines = [line for line in text.splitlines()
                 if "hl-deploy-backups" in line or "hardlylistening.service" in line]
        self.assertTrue(lines)
        self.assertTrue(all(":-" in line for line in lines), lines)


if __name__ == "__main__":
    unittest.main()