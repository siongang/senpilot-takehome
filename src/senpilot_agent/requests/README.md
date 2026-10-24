# Request parsing

`parser.py` uses OpenAI structured outputs to interpret an incoming email's subject and newest body text. Set `OPENAI_API_KEY`; model, timeout, and bounded SDK retries are configurable in `.env.example`.

Accept one or more matter numbers with the form M followed by five digits, and any number of Exhibits, Key Documents, Other Documents, Transcripts, or Recordings. Types may be shared across all matters or assigned to individual matters. An explicit request to send all documents selects all five supported types. Questions asking only for status, counts, or matter facts select a summary without attachments, even when a document type is mentioned. A bare matter number with no document type is also valid. Match without regard to case and normalize the returned category spelling. Treat a missing matter or an ambiguous selection as clarification instead of guessing.

The output contract remains `ParsedRequest`: matter numbers, a comma-separated canonical document-type list for the first matter, per-matter selections when they differ, a summary-only flag per matter, and a clarification reason when the matter is missing or the selection is ambiguous. Local validation rejects malformed matter numbers or numbers absent from the input, and rejects incomplete per-matter mappings. Operational failures raise `RequestParsingError` with a `retryable` flag rather than requesting clarification. There is no regex fallback. Intake is saved in SQLite before parsing, and the parser does not call AgentMail or the UARB site.

For ambiguous requests, OpenAI also selects a bounded reason: matter choice, document-type choice, unclear matter/type mapping, or other. Local templates turn that reason into a specific clarification question.

When a sender replies in the same thread to a completed clarification, the worker supplies the saved original request and question to the parser. Short matter or document-type answers are resolved against that saved selection; longer replies go through OpenAI with the same context. Other threads and senders do not inherit it.

See the [request checklist](../../../BUILD_PLAN.md#email-intake-and-request-parsing).
