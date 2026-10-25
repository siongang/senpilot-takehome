"""Verify cleanup eligibility and its deployment guard without touching live data."""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "cleanup.sh"
REVISION = "a" * 40


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = self.root / "data"
        self.jobs = self.data / "jobs"
        self.jobs.mkdir(parents=True)
        self.connection = sqlite3.connect(self.data / "jobs.sqlite3")
        self.addCleanup(self.connection.close)
        self.connection.execute(
            "CREATE TABLE jobs (id TEXT, status TEXT, updated_at TEXT, "
            "failure_notification_status TEXT)"
        )
        self.python = SCRIPT.read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]

    def add_job(self, number, status, days_old=10, notice=None, database_row=True):
        job_id = "00000000-0000-0000-0000-{:012x}".format(number)
        folder = self.jobs / job_id
        folder.mkdir()
        for name in ("downloads-attempt-1", "archive-attempt-2", "keep"):
            (folder / name).mkdir()
            (folder / name / "file").write_text("saved")
        if database_row:
            updated = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
            self.connection.execute("INSERT INTO jobs VALUES (?, ?, ?, ?)",
                                    (job_id, status, updated, notice))
            self.connection.commit()
        return folder

    def run_python(self, dry):
        return subprocess.run([sys.executable, "-c", self.python, "7", str(int(dry)),
                               str(self.data)], capture_output=True, text=True)

    def test_only_old_finished_job_artifacts_are_removed(self):
        eligible = [
            self.add_job(1, "completed"), self.add_job(2, "ignored"),
            self.add_job(3, "failed", notice="sent"),
            self.add_job(4, "failed", notice="exhausted"),
        ]
        retained = [
            self.add_job(5, "failed", notice="pending"),
            self.add_job(6, "retry_pending"),
            self.add_job(7, "delivery_review"),
            self.add_job(8, "sending"),
            self.add_job(9, "completed", days_old=1),
            self.add_job(10, "completed", database_row=False),
        ]
        external = self.root / "external"
        external.mkdir()
        (external / "file").write_text("safe")
        (eligible[0] / "downloads-attempt-3").symlink_to(external, target_is_directory=True)
        (self.jobs / "not-a-job-id").mkdir()
        (self.jobs / "00000000-0000-0000-0000-000000000011").symlink_to(
            external, target_is_directory=True)

        dry = self.run_python(True)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(dry.stdout.count("would remove"), 8)
        self.assertTrue((eligible[0] / "downloads-attempt-1").exists())

        result = self.run_python(False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("removing"), 8)
        for folder in eligible:
            self.assertFalse((folder / "downloads-attempt-1").exists())
            self.assertFalse((folder / "archive-attempt-2").exists())
            self.assertTrue((folder / "keep" / "file").exists())
        for folder in retained:
            self.assertTrue((folder / "downloads-attempt-1" / "file").exists())
            self.assertTrue((folder / "archive-attempt-2" / "file").exists())
        self.assertTrue((eligible[0] / "downloads-attempt-3").is_symlink())
        self.assertTrue((external / "file").exists())
        self.assertTrue((self.jobs / "not-a-job-id").exists())

    @unittest.skipUnless(sys.platform == "linux" and shutil.which("flock"),
                         "Shell guard uses Linux flock")
    def test_mismatched_running_image_refuses_before_docker_run(self):
        base = self.root / "base"
        base.mkdir()
        (base / "deployed-revision").write_text(REVISION + "\n")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        marker = self.root / "docker-run"
        docker = bin_dir / "docker"
        docker.write_text("#!/bin/sh\n"
                          "case \"$*\" in\n"
                          "  *'image inspect'*) echo sha256:expected;;\n"
                          "  *'Config.Image'*) echo senpilot-agent:" + REVISION + ";;\n"
                          "  *'--format {{.Image}}'*) echo sha256:wrong;;\n"
                          "  *'run --rm'*) touch \"$RUN_MARKER\";;\n"
                          "esac\n")
        docker.chmod(0o755)
        environment = {**os.environ, "PATH": str(bin_dir) + ":" + os.environ["PATH"],
                       "SENPILOT_BASE": str(base), "RUN_MARKER": str(marker)}
        result = subprocess.run(["bash", str(SCRIPT), "--dry-run"], env=environment,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to clean", result.stderr)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
