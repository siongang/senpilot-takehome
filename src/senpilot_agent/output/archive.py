"""ZIP creation from verified files."""

from __future__ import annotations

import zipfile
from pathlib import Path

from senpilot_agent.models import MatterResult


def _folder_name(category: str) -> str:
    return category.strip().replace("\\", "-").replace("/", "-")


def create_zip(result: MatterResult, destination: Path) -> Path:
    """Pack successful downloads, one folder per document type when the type is known."""
    entries = []
    for document in result.successful_documents:
        folder = _folder_name(document.category)
        for raw in document.saved_paths:
            entries.append((Path(raw), folder))
    destination.parent.mkdir(parents=True, exist_ok=True)
    used_names = set()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, folder in entries:
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("Missing or empty downloaded file: " + str(path))
            name = path.name
            arcname = "{}/{}".format(folder, name) if folder else name
            suffix = 2
            while arcname.lower() in used_names:
                name = "{}-{}{}".format(path.stem, suffix, path.suffix)
                arcname = "{}/{}".format(folder, name) if folder else name
                suffix += 1
            used_names.add(arcname.lower())
            archive.write(path, arcname=arcname)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None or len(archive.infolist()) != len(entries):
            raise ValueError("ZIP verification failed")
    return destination
