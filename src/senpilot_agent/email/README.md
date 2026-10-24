# AgentMail adapter

`agentmail.py` is the REST boundary for listing messages, fetching a full message, and replying in its thread with an inline ZIP attachment. A live inbound request was processed on October 24, 2026; the reply arrived in the recipient's inbox with a 987 KB ZIP that opened successfully.

## Input and output

- Read recent messages from the configured inbox, including message ID, sender, subject, current body, and thread context.
- Ignore messages sent by the agent and messages already recorded in the job store.
- Pass each new inbound message to the request parser and job store.
- Reply to the original message with a plain-text summary and a ZIP attachment. Save the provider's outgoing message ID.

The ZIP must be an actual attachment. AgentMail's inline attachment request limit is 6 MB, so check the encoded request size before sending; an oversized result needs a clear job outcome. Keep the provider API key in an environment variable. Never log message bodies or keys unnecessarily.

See [email intake](../../../BUILD_PLAN.md#email-intake-and-request-parsing) and [result email](../../../BUILD_PLAN.md#result-email-and-size-handling) in the build plan.

Intake now saves the raw request in SQLite before OpenAI parsing. Full-message fetch errors are isolated per message; sent/draft messages, the agent's own sender address, and automatic-reply headers are excluded when supplied by the provider.

Replies use a stable `Idempotency-Key` per logical send, with a distinct key for terminal failure notifications. AgentMail documents this header for replies and requires an identical request on retry; keys expire after 24 hours. The worker saves the exact reply text and ZIP before sending and holds uncertain deliveries for operator review after 23 hours rather than blindly resending. Source: [AgentMail duplicate-send documentation](https://www.agentmail.to/docs/knowledge-base/preventing-duplicate-sends). Requests in `delivery_review` require the operator to check the provider thread before deciding whether to retry. A lost or changed attachment after a send attempt also requires review.

The worker renews long-running job leases using a separate SQLite connection in a heartbeat thread. A stale attempt stops before sending. Downloads use separate attempt directories so expired workers cannot overwrite a newer attempt's files. Failed jobs have a separate bounded failure-notification lifecycle, leaving retrieval status `failed`.
