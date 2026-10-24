# UARB browser adapter

`browser.py` owns Playwright navigation and selectors for the [UARB public documents database](https://uarb.novascotia.ca/fmi/webd/UARB15). Website-specific behavior stays here so the worker receives one structured scrape result.

## Required flow

1. Search through Go Directly to Matter and verify the displayed matter number.
2. Read title, status, type, category, date values and their labels, outcome, and all five document-tab counts.
3. Open each nonempty tab, wait for its selected state, and click **Save List**. Download its PDF through the filename control and close the dialog. `lists.py` validates the report identity and extracts document numbers from the table's first column across every page. Require its record count to equal the tab count, then deduplicate numbers across all five categories. Zero-count tabs contribute an empty list without opening a dialog. Reports and IDs are persisted separately from downloaded attachments.
4. Reopen the requested tab when its count is nonzero. Iterate its document rows in a documented display order, recording ID, title, date, extension, and security label.
5. For up to ten eligible Public files, click each row's GO GET IT button, then its filename controls in Download Files. Capture and save the browser download, verify it is a nonempty file, and close the dialog before the next row.
6. Return structured metadata, counts, complete saved-list IDs and paths, selected document records, and local paths for successful downloads. Record failures and skip Confidential rows when downloading attachments.

Saved lists include confidential records but do not expose attachment multiplicity or establish identity between different document/exhibit numbers. The total is a distinct-number document count, separate from both category listings and ZIP entries. Failed or incomplete list exports raise a retryable download error instead of publishing a partial total.

Automated capture and repeated rows were verified on M12205 for Key Documents and Other Documents. A 4 MB saved-file budget keeps the resulting ZIP within AgentMail's inline request limit; larger rows are skipped while the scraper looks for later eligible rows. Use the [site notes](../../../UARB_SITE_NOTES.md) for inspected locators and the [browser checklist](../../../BUILD_PLAN.md#uarb-browser-workflow-and-scrape-contract) for remaining cases.
