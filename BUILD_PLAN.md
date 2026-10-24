# Build plan: Senpilot regulatory document agent

This is the execution checklist for the [architecture](./ARCHITECTURE.md). The target is a live email address that accepts one matter number and one document type, downloads up to 10 matching files from the public documents database, and replies with a ZIP plus verified matter metadata and counts.

**Progress on October 24, 2026:** The Python package, SQLite queue, Playwright scraper, ZIP builder, AgentMail adapter, and worker are implemented. Automated runs fetched PDFs from multiple M12205 tabs, including ten Other Documents and a zero-count Transcripts case. One real inbound Key Documents email produced an AgentMail-accepted threaded reply with six PDFs in a ZIP; recipient-side delivery and persistent deployment remain open. The checklist below remains the acceptance plan, not a claim that every item is complete.

## Scope and definition of done

The assignment asks for a three-hour effort. Protect time for a running agent, a tested code link, and a Loom demo under five minutes. A smaller working end-to-end submission is preferable to disconnected components that cannot be demonstrated together.

### Required path (P0)

- [ ] An evaluator can email the published agent address from an ordinary external mailbox.
- [ ] The agent extracts exactly one matter number (`M` plus five digits) and one of Exhibits, Key Documents, Other Documents, Transcripts, or Recordings from that email.
- [x] The worker opens the specified UARB site, searches through **Go Directly to Matter**, and verifies the result is the requested matter.
- [x] It reads the matter header and the counts on all five document tabs.
- [x] It opens the requested tab and downloads up to 10 files using **Go Get It**.
- [x] It creates a valid ZIP containing the files that downloaded successfully.
- [ ] It replies in the incoming email thread with the ZIP attached, category counts, total listings, key metadata, and the actual number downloaded.
- [ ] The agent remains running after submission so the published address works for evaluation.
- [ ] The submission contains a working code link, the agent email address, and a shareable Loom video under five minutes.

### Explicitly deferred until the core path works (P1/P2)

- [ ] P1: Better recovery for intermittent website failures, partial downloads, and archives near the email size limit.
- [ ] P1: Clear clarification replies for missing or ambiguous matter/category.
- [ ] P1: Immediate acknowledgment and status replies such as “is it ready?”
- [ ] P2: Reuse matter context across follow-up emails and process multiple requests from one email.
- [ ] P2: LLM parsing, PDF-content summaries, full document indexing, vector search, and a dashboard.

The first response can be produced from a deterministic template. The screenshots already show the fields needed for the matter summary; reading the PDFs is unnecessary for this requirement.

## Working choices to avoid setup drift

- **Language/browser:** Python with Playwright and Chromium. Keep selectors and page interactions in one browser module.
- **Email:** AgentMail is the current preference because it can provide a live `@agentmail.to` address without custom DNS. If provisioning fails quickly, use an available mailbox/provider with both inbound and outbound support. Provider-specific details stay in the email adapter.
- **Incoming trigger:** Start with polling if it speeds local development. Run the poller continuously on the final host. A webhook is optional and should not change job or scraper interfaces.
- **Queue and database:** One SQLite database on persistent storage; the `jobs` table is the queue. Run one browser job at a time.
- **Files:** Give each job its own temporary directory. Keep downloads and ZIP until delivery succeeds; clean them up through a separate retention policy.
- **Request parsing:** Match a matter number and one canonical category from the email subject and current message body. Validate both. Keep parsing behind a function so it can be upgraded later.
- **Document choice:** Use a stable, documented order, initially the order shown on the requested tab. Record each attempted row and whether its **Go Get It** download succeeded.
- **Response:** Use the site's exact metadata labels and counts. A field that cannot be read is unavailable, not zero. Report the actual ZIP content count.

These are implementation starting points. The live site was inspected and a manual PDF download succeeded; automated retrieval and email-provider behavior still need verification.

## Three-hour sequence and stop rules

Times are cumulative targets. Move time between stages as needed, while reserving the final 15 minutes for the demo and submission materials.

| Time | Stage | Exit evidence |
| --- | --- | --- |
| 0–20 min | Set up repository/runtime and email account. Send a tiny test email with a ZIP attachment to an independent mailbox. | An actual external inbox receives the attached ZIP, or the email-provider blocker is documented. |
| 20–50 min | Prove website navigation and one **Go Get It** download for M12205. | A local document opens correctly and is associated with the expected matter and row. |
| 50–90 min | Extract header fields, five tab counts, and up to 10 rows from the requested tab; automate downloads. | A CLI run produces structured matter data and downloaded files for one requested category. |
| 90–115 min | Add job persistence, ZIP creation, and response formatting. | A job moves through stages and yields a valid archive plus factual email body. |
| 115–145 min | Wire incoming email to the worker and send a threaded result. | A fresh email request runs through the complete pipeline. |
| 145–165 min | Deploy to an always-running host, run an external end-to-end test, and fix the biggest failure. | The published agent address works while the local development process is stopped. |
| 165–180 min | Finish README, code link, and Loom; check the video link in Incognito. | All three submission items are ready before the deadline. |

Stop investigating one integration if it consumes its time block without a successful smoke test. Record the blocker, keep working on independent components, and describe any incomplete path honestly in the demo. In particular, do not spend the entire effort reverse-engineering FileMaker behavior or configuring a custom email domain.

## Repository and runtime setup

- [ ] Create the code repository and commit an initial README describing the assignment input and output.
- [ ] Add Python project dependencies and a reproducible installation command. Pin versions after the first successful run.
- [ ] Install Playwright's Chromium runtime and verify it launches on the development machine and chosen host.
- [ ] Add `.env.example` with variable names for email API key, inbox address, database path, working directory, browser mode, and polling interval. Keep real keys out of Git.
- [ ] Add `.gitignore` for `.env`, local databases, downloads, ZIPs, browser state, and Python caches.
- [ ] Keep the application runnable with one documented command; avoid separate services unless deployment requires them.
- [ ] Decide early which host supports a persistent database/files and Chromium. Verify its running process will remain available after the demo.

Suggested module boundaries: `email_adapter`, `request_parser`, `job_store`, `uarb_browser`, `archive`, `response_builder`, and a small `worker` entry point. Exact filenames can follow the language and framework chosen during implementation.

## Email intake and request parsing

- [ ] Create a real inbox and record its address for the final submission.
- [ ] Confirm mail from an independent mailbox reaches the provider; confirm outgoing mail to that mailbox is allowed.
- [ ] Receive only incoming user messages; skip messages sent by the agent and automatic replies.
- [ ] Keep the provider's incoming message ID, sender, subject, current message body, and threading fields needed for a reply.
- [ ] Strip obvious quoted history and signatures where practical so an old request is not processed again.
- [ ] Extract a matter number with a case-insensitive `M[0-9]{5}` pattern and normalize it to uppercase.
- [ ] Match the five canonical categories case-insensitively; match longer names before generic words such as “documents.”
- [ ] Accept one unambiguous matter/category pair for the first version. If either is absent or conflicting, respond briefly or log the limitation; do not guess and download the wrong matter.
- [ ] Insert a job with a unique incoming message ID so provider redelivery cannot create duplicate retrieval work.
- [ ] Prove a reply with a small ZIP reaches an external inbox before depending on the email API for the real archive.

## UARB browser workflow and scrape contract

Target: <https://uarb.novascotia.ca/fmi/webd/UARB15>. Search, header, tabs, counts, row fields, and the zero-count dialog were inspected live for M12205; a manual PDF download succeeded. See [UARB_SITE_NOTES.md](./UARB_SITE_NOTES.md). Automated download capture and repetition still need verification.

- [ ] Open the public documents database in a clean browser context.
- [ ] Fill **Go Directly to Matter**, press **Search**, and wait for the matter detail view.
- [ ] Read the displayed matter number and fail the lookup if it differs from the request.
- [ ] Read and retain the raw header values: title/description, status, Type, Category, Date Received, the second date and its displayed label, and Outcome.
- [ ] Parse dates for formatting, while keeping their exact site labels. The live initial header showed **Decision Date**; document layouts showed **Date Final Submission(s)**. Do not rename either to a closing or approval date.
- [ ] Read counts from **Exhibits**, **Key Documents**, **Other Documents**, **Transcripts**, and **Recordings** tab labels. Treat a failed read differently from a displayed zero.
- [ ] Sum the five category counts for the requested total and label it as listings across categories until overlap is checked.
- [ ] If the requested tab count is zero, skip opening it; the site may show a **No Matching Records** dialog. Otherwise open the tab and wait for its own rows. Compare `Found Count` with the tab label when that field is present; the inspected Key Documents layout omitted it.
- [ ] For candidate rows, capture document number/ID, title, security label, file extension, date filed, and display position when available.
- [ ] Click the row's **Go Get It** control, then the filename control in the **Download Files** dialog; capture and save the downloaded file into that job's directory. Close the dialog before selecting another row.
- [ ] Verify that the result is a nonempty document file rather than an HTML error page or an incomplete browser download.
- [ ] Stop after 10 successful files or after the available candidate rows are exhausted; record failed or inaccessible rows.
- [ ] If the tab includes Confidential rows, preserve their labels and rely only on files accessible through the public site. Do not confuse the full tab count with the number attached.
- [ ] Check whether pagination, filtering, sorting, or a new tab/window affects the first 10 candidates. Document the chosen order.

The scraper should return one structured result, independent of email: `matter_number`, header metadata, `scraped_at`, five category counts, selected document records, and local paths for successful downloads. This contract makes the scraper testable from a CLI and reusable by the email worker.

## Database, queue, and files

- [ ] Create `jobs` with ID, unique incoming message ID, sender, reply fields, matter/category, status, attempts, next attempt time, lease deadline, ZIP location/size, last error, outgoing message ID, and timestamps.
- [ ] Create `matter_snapshots` with `job_id`, source URL, title/description, status, Type, Category, dates, Outcome, and scrape timestamp.
- [ ] Create `category_counts` with one row for each of the five supported categories per snapshot; store `NULL` plus an observation status when unavailable.
- [ ] Create `documents` for selected rows, including source ID, title, security label, date, extension, display position, download result, saved path, size, checksum, and error.
- [ ] Add an index on job status and next attempt time so the worker can claim the next due job.
- [ ] Move jobs through `queued → scraping → downloading → packaging → sending → completed`; record `retry_pending` and `failed` when appropriate.
- [ ] Claim work transactionally. Reclaim a job after its lease expires if a worker crashes.
- [ ] Persist each stage's useful output. A send retry should reuse the already-created ZIP.
- [ ] Create a ZIP with safe, unique names; open it after creation and confirm its entry count matches successful file records.
- [ ] Store ZIP path, byte size, and checksum. Keep large binary files out of SQLite.

For a three-hour build, prioritize a working single-worker queue over elaborate retry machinery. Database fields for future retries can exist even if the first implementation has only one bounded retry.

## Result email and size handling

- [ ] Build the response from scraped values, not from guesses or document-content summaries.
- [ ] Include the matter number, title/description, status, Type, Category, Date Received, the second date with its observed label, and Outcome when available.
- [ ] Include all five category counts and the total across them. For M12205, the supplied screenshots show 13 + 6 + 43 + 0 + 0 = **62 listings**; scrape current values at runtime.
- [ ] State “downloaded X of Y [requested category]” using actual ZIP entries for X and the site's category count for Y.
- [ ] Attach the ZIP to the email. Verify the outgoing MIME/API request identifies it as a ZIP and uses a meaningful filename.
- [ ] Check the provider's encoded message-size limit before sending. Record an oversized ZIP as a clear limitation; decide whether to send fewer files only after testing real file sizes.
- [ ] Save the outgoing provider message ID and final job state. Provider acceptance and actual delivery are distinct events.
- [ ] If the provider reports a retryable send error, reuse the existing ZIP. If the result is uncertain, check provider records before sending again when possible.

## Verification and demo

- [ ] Run a CLI smoke test for M12205 and one category, confirming the matter header, five counts, and a downloaded file.
- [ ] Run a full email test from a mailbox other than the agent inbox: send a request, receive the result, open the ZIP, and open at least one document.
- [ ] Compare the sent email's metadata and counts with the page shown in the browser at test time.
- [ ] Test a category with fewer than 10 documents, ideally Key Documents, so the “up to 10” rule is visible.
- [ ] Test a zero-count category, ideally Transcripts or Recordings for the supplied M12205 example.
- [ ] Redeliver or reprocess one inbound event and confirm no second job/result is created.
- [ ] Restart the running process and confirm queued work and files remain available.
- [ ] Test while the local development machine is not hosting the worker; the evaluator address must still work.
- [ ] Put setup steps, environment variables, run command, architecture summary, demonstrated behavior, and known limitations in README.
- [ ] Publish the code repository and open its link from a logged-out or Incognito session.
- [ ] Record a Loom video under five minutes: request email, job progress, matter data, received reply, ZIP contents, and a brief explanation of one tradeoff or limitation.
- [ ] Open the Loom share link in Incognito and verify it plays without a login.
- [ ] Submit the code link, Loom link, and agent email address to Paula by October 28, 2026.

## Suggested five-minute demo flow

1. **0:00–0:30:** State the problem and show the email request.
2. **0:30–1:30:** Show the matter lookup, captured header metadata, and five counts.
3. **1:30–2:30:** Show the requested tab, **Go Get It** downloads, and ZIP creation.
4. **2:30–3:30:** Show the received email and open the attached ZIP and one document.
5. **3:30–4:30:** Briefly show job status, code structure, and one obstacle/tradeoff.
6. **4:30–5:00:** State exactly what works and what remains incomplete.

The submission video should show a real successful run if possible. If a component remains unfinished at the three-hour cutoff, demonstrate the completed pieces directly and state the limitation plainly.
