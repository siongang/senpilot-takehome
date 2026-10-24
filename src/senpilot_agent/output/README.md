# ZIP and response builder

`archive.py` and `response.py` turn saved downloads and structured matter data into the two final artifacts: a verified ZIP and a factual email body.

Create safe, unique filenames inside one job directory, then build a ZIP containing only files that downloaded successfully. For a zero-count category, create an empty ZIP so the reply still includes the requested attachment. Reopen the archive and compare its entries with the document records. Record both the number of selected document rows and the number of files in the ZIP if a document exposes multiple files.

The reply should include the matter number, title/description, status, type, category, observed date labels and values, outcome, all five tab counts, their aggregate as category listings, and the actual downloaded count for the requested type. The aggregate is not a unique-file count because a document can appear in multiple tabs. Use a deterministic template; PDF-content summarization is outside the assignment's required path.

See the [result email checklist](../../../BUILD_PLAN.md#result-email-and-size-handling).
