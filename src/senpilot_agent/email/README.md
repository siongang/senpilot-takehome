# AgentMail adapter

`agentmail.py` is the REST boundary for listing messages, fetching a full message, and replying in its thread with an inline ZIP attachment. A live inbound request was processed on October 24, 2026; AgentMail returned a sent reply in the same thread with a 987 KB ZIP. Recipient-side delivery confirmation is pending.

## Input and output

- Read recent messages from the configured inbox, including message ID, sender, subject, current body, and thread context.
- Ignore messages sent by the agent and messages already recorded in the job store.
- Pass each new inbound message to the request parser and job store.
- Reply to the original message with a plain-text summary and a ZIP attachment. Save the provider's outgoing message ID.

The ZIP must be an actual attachment. AgentMail's inline attachment request limit is 6 MB, so check the encoded request size before sending; an oversized result needs a clear job outcome. Keep the provider API key in an environment variable. Never log message bodies or keys unnecessarily.

See [email intake](../../../BUILD_PLAN.md#email-intake-and-request-parsing) and [result email](../../../BUILD_PLAN.md#result-email-and-size-handling) in the build plan.
