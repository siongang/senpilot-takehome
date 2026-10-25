# UARB live-site inspection notes

Inspected and automated on October 24, 2026 at <https://uarb.novascotia.ca/fmi/webd/UARB15> using matter `M12205`. These are observations from the public FileMaker WebDirect UI, not a guarantee that its generated DOM IDs will remain stable. Use these notes when maintaining the Playwright browser adapter.

## Readiness

The browser implementation was smoke-tested on October 24, 2026. It retrieved two Key Documents and ten Other Documents from `M12205`, captured the browser downloads, closed each dialog, and created ZIPs that passed integrity checks. The generated header and row IDs differed from the original manual inspection, so the implementation handles both observed sets. Selectors remain site-dependent and need a smoke test after any FileMaker UI change.

## Confirmed workflow

1. The **Go Directly to Matter** field is a FileMaker `div`, not a native `input`. Its visible placeholder is `eg M01234`. Click its inner `.text`, wait for `contenteditable="true"`, type the matter number with key events, then press Tab to commit it. In a local Playwright run, bulk `fill()` displayed `M12205` but led to **No Records Found**; `press_sequentially()` plus Tab opened the matter.
2. There are three buttons named **Search** on the starting page. In some browser runs pressing Tab submitted the direct-matter search automatically and removed its Search button. The implementation waits for the matter tabs first; only if they do not appear does it click `.fm_object_258 button`.
3. Search loads the matter header with all five document tab labels. The document grid is not present until a tab is clicked. Wait for the requested matter number and then for the tab's rows after clicking; the old grid can remain visible briefly during a tab transition.
4. Each tab label initially includes its count, e.g. `Other Documents - 43`. Read all five labels from the matter header before selecting a tab. Do not infer a failed count read as zero. After switching layouts, WebDirect can temporarily render an inactive tab as `Other Documents -` with no count; navigation must match the category name without requiring the count. On October 24, 2026, M12406 showed 29 Other Documents initially, lost that count after opening Recordings, then restored it when Other Documents was selected.
5. Clicking **Transcripts - 0** for `M12205` opened a **No Matching Records** dialog with an **OK** button. Clicking OK returned to the matter header without a document grid. Skip zero-count tabs rather than clicking them.
6. Clicking **GO GET IT** for a public row opens a **Download Files** dialog containing a filename control and **Close**. The filename control is `<div role="button" class="v-button ... fm-download-button ...">`. The user manually verified `97289.pdf`; Playwright then captured that file and repeated the flow on additional rows.
7. The post-download screenshot shows the `97289.pdf` filename control disabled while the **Download Files** dialog remains open. Click **Close** before selecting another document row.

## Locator candidates

Prefer text/role selectors for tabs and per-row controls. Treat `fm_object_*` classes as inspected fallback selectors to verify against a fresh session, since FileMaker can use different layouts for different states.

| Element | Inspected locator or extraction rule | Notes |
| --- | --- | --- |
| Direct matter field | `.fm_object_254 .text` after clicking it | Placeholder `eg M01234`; not a native input. |
| Direct matter Search | `.fm_object_258 button` | Scope it because other Search buttons exist. |
| Matter loaded | Wait for a tab such as `Exhibits - 13`, then read `.fm_object_286 .text` or `.fm_object_219 .text` | Generated header IDs differed between inspections. |
| Five category tabs | `get_by_role("button", name=re.compile(r"^Other Documents - \d+$"))` (substitute category) | Parse counts from all five button names. |
| Selected category rows | `tr.v-grid-row[role="row"]` or `get_by_role("row")` with a GO GET IT button | The grid virtualizes rows; only about 12 were mounted at once for Other Documents. |
| Document download control | Row-scoped `get_by_role("button", name="GO GET IT")` | Do not select the first global GO GET IT button after scrolling or tab changes. |
| Download dialog filename | `[role="button"].fm-download-button` filtered by the expected filename, or `get_by_role("button", name="97289.pdf")` for the inspected row | Click after GO GET IT, then capture the browser download. Do not hardcode `97289.pdf` for other documents. |
| Download dialog Close | Try `get_by_role("button", name="Close")`; exact DOM not inspected | Close after each download or on failure so the next row is accessible. Inspect its HTML only if this semantic locator fails. |
| No-records dialog | `get_by_role("dialog", name="No Matching Records")` | Reached by clicking a zero-count tab; dismiss with its OK button. |

On the first inspected matter-detail layout, the header fields were: `.fm_object_219 .text` matter number, `.fm_object_222 .text` status, `#b0p0o223i0i0r1` title, `.fm_object_231 .text` type, `.fm_object_220 .text` category, `.fm_object_225 .text` date received, `.fm_object_227 .text` second date, and `.fm_object_228 .text` outcome. A fresh Playwright session rendered the same values under `.fm_object_286`, `.fm_object_289`, `#b0p0o290i0i0r1`, `.fm_object_298`, `.fm_object_287`, `.fm_object_292`, `.fm_object_294`, and `.fm_object_295`, respectively. The browser handles these observed alternatives.

On the inspected document-row layout, `.fm_object_201 .text` was document number, `.fm_object_202 .text` title, `.fm_object_203 .text` date, `.fm_object_204 .text` security label, and `.fm_object_308` extension. In the Playwright session, the Key Document number was under `.fm_object_268 .text`; title, date, and security retained the earlier classes. The row's button labels were **Preview** and **GO GET IT**. Rows in Exhibits used exhibit numbers such as `H-1`; Key Documents and Other Documents used numeric IDs such as `97289` and `102674`.

## Sample metadata and counts

The current live header for `M12205` showed:

| Field | Value |
| --- | --- |
| Title - Description | Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000 |
| Status | Closed |
| Type | Water |
| Category | Capital Expenditure Approvals |
| Date Received | 04/07/2025 |
| Second date | 10/23/2025 |
| Outcome | Directions given |

The initial header labeled the second date **Decision Date**. After opening document tabs, the label rendered as **Date Final Submission(s)**. Preserve the label actually observed alongside the value; do not infer that this date represents project approval or matter closure.

The five tab counts were Exhibits 13, Key Documents 6, Other Documents 43, Transcripts 0, Recordings 0: **62 category listings** in total. Some documents may occur in more than one category (for example, Board Order `102674` appeared in both Key Documents and Other Documents), so 62 is not a verified unique-file count. The Exhibits and Other Documents layouts displayed a per-tab `Found Count`; the inspected Key Documents layout did not.

The Exhibits grid included rows labeled **Confidential** as well as **Public**, even though both displayed GO GET IT controls. Preserve the security label and avoid assuming those files are downloadable or suitable for email until an actual download is checked. The first 12 mounted Other Documents rows were public PDFs ordered newest-first on the inspected page; do not claim that ordering is guaranteed for every matter or tab.

## Choosing up to 10 documents

After selecting the requested tab, iterate document **rows**, not the page's global GO GET IT buttons. For each visible row, read the document ID (`.fm_object_201 .text` or `.fm_object_268 .text` on observed Key/Other layouts), title, security label, date and extension. Select rows labeled **Public** in the order the site displays them, stopping after 10 saved documents or when eligible rows are exhausted. The current 4 MB saved-file budget skips larger documents and continues to later rows. Record skipped Confidential and oversized rows and failed downloads. A tab count such as `Key Documents - 6` means at most six rows are available in that category; a count such as `Other Documents - 43` means the worker should attach at most ten from that category.

Click `row.get_by_role("button", name="GO GET IT")` for the current row. In the dialog, read the actual filename from `.fm-download-button`; do not derive it blindly from the document ID because filenames or file types may differ. Click that filename control while capturing the browser download, verify the saved file, then click **Close**. Re-query the grid before selecting the next row because FileMaker can rerender it. The Other Documents grid mounted roughly 12 rows at a time, so scrolling is needed if fewer than 10 eligible/successful rows are available in the initial viewport. If a dialog offers multiple files for one document row, track both the row count and ZIP file count explicitly.

## Save List inventory (October 24, 2026)

Each nonempty document tab offers `get_by_role("button", name="Save List", exact=True)`.
It opens the normal Download Files dialog: Exhibits offered `ExhibitList.pdf`, Key
Documents `KeyDocList.pdf`, and Other Documents `OtherDocumentsList.pdf`. Download
the visible `.fm-download-button` PDF and close the dialog before selecting the
next tab. Wait for the requested tab's `.iwps_button_bar_segment.fm-selected`
state because the preceding grid can remain visible during a layout transition.

These reports include all rows, including Confidential records. M12205 exported
13 Exhibits over two pages, six Key Documents over one page, and 43 Other
Documents over three pages. All six Key Document numbers also occur in Other
Documents, yielding 56 distinct document/exhibit numbers from 62 tab listings.
Exhibits use labels such as `H-4(C)-iii`; other inspected reports use numeric
document numbers. PDF layout extraction preserves the number column and wrapped
descriptions. Validate the title, matter number, table header, and total rows
before accepting a report. Save List reports do not enumerate each document's
attached binaries or map different identifiers to the same underlying file.

A local full scrape with all three report exports and two requested Key Document
attachments completed in 10.4 seconds. The ZIP contained only the two requested
files. Nonzero Transcript and Recording report formats remain to be verified
live; M12205 has zero of both.

## Remaining implementation probes

- Confirm whether the grid scrolls correctly for a matter with many inaccessible rows. The M12205 sample reached ten saved Other Documents by skipping two large PDFs and selecting later mounted rows.
- Test an unknown matter number and one nonzero Transcript or Recording category when time permits. A Confidential row can also be inspected to understand access behavior, but the first version can skip it.

