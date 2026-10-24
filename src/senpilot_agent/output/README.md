# ZIP and response builder

`archive.py` and `response.py` turn saved downloads and structured matter data into the two final artifacts: a verified ZIP and a factual email body.

Create safe, unique filenames inside one job directory, then build a ZIP containing only files that downloaded successfully. Each requested document type is stored in its own folder. For a requested zero-count category, create an empty ZIP so the reply still includes the requested attachment. A summary-only request creates no ZIP and does not ask about downloading files. A bare matter number creates no ZIP and asks whether to retrieve any of the five document types. Reopen the archive and compare its entries with the document records.

The reply leads with matter number, title, and description, then status, type, category, observed date labels and values, and outcome. It lists all five tab counts and a short document total from the Save List reports. It states the actual attached count for each requested type. A deterministic template composes the email; document-content summarization is outside the assignment's required path.

See the [result email checklist](../../../BUILD_PLAN.md#result-email-and-size-handling).

Partial replies identify failed downloads and size-budget skips. A zero category count is described separately from a nonzero category with no attachable files. When every attempted public download fails, the worker retries retrieval instead of presenting an empty ZIP as successful retrieval. Terminal failure text includes a request reference and avoids exposing internal exception details. If a send was attempted but acceptance could not be confirmed, the notification says delivery may already have occurred.
