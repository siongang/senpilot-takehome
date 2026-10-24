"""ZIP creation from verified files."""

from __future__ import annotations

import zipfile
from pathlib import Path

from senpilot_agent.models import MatterResult


def create_zip(result: MatterResult, destination: Path) -> Path:
    paths = [Path(path) for path in result.saved_files]
    destination.parent.mkdir(parents=True, exist_ok=True)
    used_names = set()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("Missing or empty downloaded file: " + str(path))
            name = path.name
            suffix = 2
            while name.lower() in used_names:
                name = "{}-{}{}".format(path.stem, suffix, path.suffix)
                suffix += 1
            used_names.add(name.lower())
            archive.write(path, arcname=name)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None or len(archive.infolist()) != len(paths):
            raise ValueError("ZIP verification failed")
    return destination
