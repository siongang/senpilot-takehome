# ZIP and response builder

`archive.py` and `response.py` turn saved downloads and structured matter data into the two final artifacts: a verified ZIP and a factual email body.

Create safe, unique filenames inside one job directory, then build a ZIP containing only files that downloaded successfully. For a zero-count category, create an empty ZIP so the reply still includes the requested attachment. Reopen the archive and compare its entries with the document records. Record both the number of selected document rows and the number of files in the ZIP if a document exposes multiple files.

The reply includes the matter number, title/description, status, type, category, observed date labels and values, outcome, all five tab counts, their aggregate as category listings, and the actual attached count for the requested type. The aggregate is not a unique-file count because a document can appear in multiple tabs. A deterministic template composes the email; PDF-content summarization is outside the assignment's required path.

See the [result email checklist](../../../BUILD_PLAN.md#result-email-and-size-handling).

Partial replies identify failed downloads and size-budget skips. A zero category count is described separately from a nonzero category with no attachable files. When every attempted public download fails, the worker retries retrieval instead of presenting an empty ZIP as successful retrieval. Terminal failure text includes a request reference and avoids exposing internal exception details. If a send was attempted but acceptance could not be confirmed, the notification says delivery may already have occurred.
