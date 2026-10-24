# Local commands

This folder is reserved for optional development and operations helpers. Business logic stays in the package under `src`.

The package entry point already supports a direct UARB scrape, a single inbox poll and job run, and a continuous worker loop. See the root [README](../README.md#run-locally) for exact commands. Future scripts here should wrap those commands rather than duplicate their logic.

The direct scrape is the quickest way to inspect saved PDFs and the ZIP before connecting AgentMail. The continuous worker is the command that must remain running on the final host. Document the exact invocation in the root README once implemented.
