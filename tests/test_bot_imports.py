"""Each bot must import the way its workflow actually runs it.

`python <bot>/<bot>.py` puts the *script's* directory on sys.path, not the repo
root — so a bot importing `common` only works if its workflow's PYTHONPATH says
so. That is invisible locally (an interactive `python -c` has cwd on the path
and papers over it) and fatal on the next cron, so it gets a test.
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# bot entry point -> the workflow that runs it
BOTS = {
    "zenodo_bot/zenodo_bot.py": "zenodo_run.yml",
    "citation_bot/citation_bot.py": "citation_run.yml",
}


def workflow_pythonpath(workflow_name):
    """The PYTHONPATH a workflow exports, as written in its YAML."""
    text = (WORKFLOWS / workflow_name).read_text(encoding="utf-8")
    match = re.search(r"^\s*PYTHONPATH:\s*(\S+)\s*$", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"{workflow_name} declares no PYTHONPATH")
    return match.group(1)


class BotsImportUnderTheirWorkflowEnvironment(unittest.TestCase):
    def test_each_bot_imports_cleanly(self):
        for entry_point, workflow in BOTS.items():
            with self.subTest(bot=entry_point):
                script_dir = str((REPO_ROOT / entry_point).parent)
                probe = (
                    "import sys, importlib.util;"
                    f"sys.path[0] = {script_dir!r};"
                    "spec = importlib.util.spec_from_file_location("
                    f"'__botmain__', {entry_point!r});"
                    "m = importlib.util.module_from_spec(spec);"
                    "spec.loader.exec_module(m)"
                )
                result = subprocess.run(
                    [sys.executable, "-c", probe],
                    cwd=REPO_ROOT,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "PYTHONPATH": workflow_pythonpath(workflow),
                    },
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"{entry_point} failed to import under the PYTHONPATH in "
                    f"{workflow}:\n{result.stderr}",
                )

    def test_the_workflow_pythonpath_includes_the_repo_root(self):
        for entry_point, workflow in BOTS.items():
            with self.subTest(bot=entry_point):
                entries = workflow_pythonpath(workflow).split(":")
                self.assertIn(
                    ".",
                    entries,
                    f"{workflow} must put the repo root on PYTHONPATH so "
                    f"`common/` resolves",
                )


if __name__ == "__main__":
    unittest.main()
