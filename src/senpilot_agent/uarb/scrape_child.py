"""Isolated browser process used by the email worker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from senpilot_agent.uarb.browser import DownloadLimitExceeded, MatterNotFound, scrape_matter


def main() -> None:
    matter, category, output, maximum, headless = sys.argv[1:]
    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = scrape_matter(
            matter, category, output_dir,
            max_documents=int(maximum), headless=headless == "1",
        )
        payload = {"status": "ok", "result": result.to_dict()}
    except MatterNotFound as error:
        payload = {"status": "missing", "error": str(error)[:1000]}
    except DownloadLimitExceeded as error:
        payload = {"status": "limit", "error": str(error)[:1000]}
    except Exception as error:
        payload = {"status": "error", "error": str(error)[:1000]}
    temporary = output_dir / "scrape-result.json.tmp"
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(output_dir / "scrape-result.json")


if __name__ == "__main__":
    main()
