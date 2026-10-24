# Request parsing

`parser.py` uses OpenAI structured outputs to interpret an incoming email's subject and newest body text. Set `OPENAI_API_KEY`; model, timeout, and bounded SDK retries are configurable in `.env.example`.

Accept one or more matter numbers with the form M followed by five digits, and any number of Exhibits, Key Documents, Other Documents, Transcripts, or Recordings. The same types apply to every listed matter. A matter with no document type is valid. Match without regard to case and normalize the returned category spelling. Treat a missing matter or an ambiguous selection as clarification instead of guessing.

The output contract remains `ParsedRequest`: matter numbers, a comma-separated canonical document-type list (empty when none were requested), and a clarification reason when the matter is missing or the selection is ambiguous. Local validation rejects malformed matter numbers or numbers absent from the input. Operational failures raise `RequestParsingError` with a `retryable` flag rather than requesting clarification. There is no regex fallback. Intake is saved in SQLite before parsing, and the parser does not call AgentMail or the UARB site.

For ambiguous requests, OpenAI also selects a bounded reason: matter choice, document-type choice, matter/type mapping, or other. Local templates turn that reason into a specific clarification question. A mapping clarification explains that one email supports one shared set of document types across its matters.

See the [request checklist](../../../BUILD_PLAN.md#email-intake-and-request-parsing).
