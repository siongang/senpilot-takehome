# Request parsing

`parser.py` uses OpenAI structured outputs to interpret an incoming email's subject and newest body text. Set `OPENAI_API_KEY`; model, timeout, and bounded SDK retries are configurable in `.env.example`.

Accept one matter number with the form M followed by five digits and exactly one of Exhibits, Key Documents, Other Documents, Transcripts, or Recordings. Match without regard to case and normalize the returned category spelling. Treat missing or conflicting values as a clarification case instead of guessing.

The output contract remains `ParsedRequest`: matter number, canonical document type, and a clarification reason when missing or ambiguous. Local validation rejects malformed matter numbers or numbers absent from the input. Operational failures raise `RequestParsingError` with a `retryable` flag rather than requesting clarification. There is no regex fallback. Intake is saved in SQLite before parsing, and the parser does not call AgentMail or the UARB site.

See the [request checklist](../../../BUILD_PLAN.md#email-intake-and-request-parsing).
