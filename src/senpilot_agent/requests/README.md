# Request parsing

`parser.py` turns an incoming email's subject and newest body text into one validated request. The first version is deterministic; it does not need an LLM.

Accept one matter number with the form M followed by five digits and exactly one of Exhibits, Key Documents, Other Documents, Transcripts, or Recordings. Match without regard to case and normalize the returned category spelling. Treat missing or conflicting values as a clarification case instead of guessing.

The output contract is a matter number, canonical document type, and a reason when parsing fails. The parser should not call AgentMail or the UARB site. Later language flexibility can replace the parser without changing the retrieval pipeline.

See the [request checklist](../../../BUILD_PLAN.md#email-intake-and-request-parsing).
