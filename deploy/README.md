# Deployment

`Dockerfile` packages the worker and Chromium for an always-running host. A local laptop process is useful during development, but the evaluator's email address must work after the demo.

Provide the AgentMail API key and inbox address through the host's secret/environment settings. Mount the runtime directory on persistent storage. Run one worker process initially so browser jobs and SQLite claims remain simple. Confirm the process restarts after failure and that queued work survives a restart.

Build from the repository root with `docker build -f deploy/Dockerfile -t senpilot-agent .`. Provide `AGENTMAIL_API_KEY` and `AGENTMAIL_INBOX` as host secrets, set `AGENTMAIL_POLL_AFTER` to a recent UTC timestamp, mount persistent storage at `/app/runtime`, and run one replica. Do not bake `.env` into the image. The chosen host and external delivery test are still pending.

Before submission, verify an external sender can email the agent while the local development process is stopped. Document the chosen host and exact launch command in the root README.

See the [hosting guidance](../ARCHITECTURE.md#hosting-and-validation-gates).
