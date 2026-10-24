"""Transfer limits keep one browser job from monopolizing the email worker."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from senpilot_agent.uarb import supervisor
from senpilot_agent.uarb.browser import DownloadLimitExceeded


class SupervisorTests(unittest.TestCase):
    def test_child_process_reports_browser_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Matter number must look"):
                supervisor.supervised_scrape_matter(
                    "invalid", "Exhibits", Path(directory), max_documents=1,
                )

    def test_growing_file_stops_child_at_attachment_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            fake = Mock(pid=12345)
            fake.poll.return_value = None

            def write_large_file(_):
                target = folder / "Exhibits" / "video.mp4"
                target.parent.mkdir()
                with target.open("wb") as stream:
                    stream.truncate(supervisor.MAX_SAVED_BYTES + 1)

            with patch.object(supervisor.subprocess, "Popen", return_value=fake), patch.object(
                supervisor, "_stop"
            ) as stopped, patch.object(supervisor.time, "sleep", side_effect=write_large_file):
                with self.assertRaisesRegex(DownloadLimitExceeded, "size limit"):
                    supervisor.supervised_scrape_matter("M12205", "Exhibits", folder)
            stopped.assert_called_once_with(fake)
            self.assertFalse((folder / "Exhibits" / "video.mp4").exists())

    def test_stalled_active_download_stops_child(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            marker = folder / "Recordings" / ".active-download"
            marker.parent.mkdir()
            marker.write_text("recording.mp4")
            old = time.time() - supervisor.FILE_SECONDS - 5
            os.utime(marker, (old, old))
            fake = Mock(pid=12345)
            fake.poll.return_value = None
            with patch.object(supervisor.subprocess, "Popen", return_value=fake), patch.object(
                supervisor, "_stop"
            ) as stopped:
                with self.assertRaisesRegex(DownloadLimitExceeded, "too long"):
                    supervisor.supervised_scrape_matter("M12205", "Recordings", folder)
            stopped.assert_called_once_with(fake)


if __name__ == "__main__":
    unittest.main()
