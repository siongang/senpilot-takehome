# Senpilot regulatory document agent

Email the agent one UARB matter number (`M` plus five digits) and one document type: Exhibits, Key Documents, Other Documents, Transcripts, or Recordings. The worker retrieves public documents for that matter, attaches up to ten in a ZIP, and replies with the matter header and all five category counts. The [build plan](./BUILD_PLAN.md), [architecture](./ARCHITECTURE.md), and [live-site notes](./UARB_SITE_NOTES.md) explain the scope and observed website behavior.

## Run locally

Requires Python 3.9+ and Playwright Chromium.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m playwright install chromium
cp .env.example .env
```

Set `AGENTMAIL_API_KEY` and `AGENTMAIL_INBOX` in `.env` for email mode. Keep `.env` private. A direct browser check needs no email credentials:

```sh
.venv/bin/senpilot-agent scrape M12205 'Key Documents'
```

The command prints a path under `runtime/manual/` containing `matter.json`, downloaded files, and a verified ZIP. Set `SENPILOT_MAX_DOCUMENTS` from 1 to 10 to limit a local run. The email worker has two commands:

```sh
.venv/bin/senpilot-agent once
.venv/bin/senpilot-agent run
```

`once` polls the inbox and processes one due job; `run` keeps polling. `senpilot-agent status` shows recent SQLite job states. Set `AGENTMAIL_POLL_AFTER` to a recent UTC timestamp when connecting an existing inbox so older mail is not treated as new requests.

## Current behavior and limits

The browser reads metadata and counts from the UARB matter page, then follows each row's **GO GET IT → filename → Close** flow. It records Confidential rows but only downloads rows labeled Public. FileMaker changes generated field IDs between layouts, so the browser module contains the observed alternatives.

AgentMail limits inline attachment requests to 6 MB. The browser keeps saved files under a conservative 4 MB budget, skipping a file that would exceed it and continuing to later rows. This may yield fewer than ten attachments; the reply reports the actual count. The five tab counts are summed as **category listings**, since a file can appear in more than one tab.

Live browser verification on October 24, 2026 retrieved Key Documents, Other Documents, and Exhibits for `M12205`, with valid PDF signatures and ZIP integrity. A real inbound email for `M12205 Key Documents` completed in one worker cycle: all six Key Documents arrived in a 987 KB ZIP, and the recipient opened it successfully. The remaining submission work is persistent hosting, a public code link, and a Loom demo.

## Project map

- [src](./src/README.md): installable Python package and component guides.
- [scripts](./scripts/README.md): optional operational helpers.
- [tests](./tests/README.md): acceptance checks.
- [deploy](./deploy/README.md): persistent worker hosting.
- [runtime](./runtime/README.md): ignored local database and per-job files.
