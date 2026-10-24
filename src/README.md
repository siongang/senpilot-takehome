# Application source

This folder contains the installable Python package in [senpilot_agent](./senpilot_agent/README.md). Code here should be usable from both a local CLI and the continuously running email worker.

The package owns configuration, shared result types, and orchestration. Its component folders own one external boundary or transformation each. The browser module must return structured data and saved file paths without knowing about AgentMail; the email module must not contain UARB selectors.

The CLI now produces a matter snapshot and ZIP for M12205. The remaining integration milestone is an incoming email that runs the same pipeline and receives a threaded reply.
