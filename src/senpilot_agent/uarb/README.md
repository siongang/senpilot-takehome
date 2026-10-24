# UARB browser adapter

`browser.py` owns Playwright navigation and selectors for the [UARB public documents database](https://uarb.novascotia.ca/fmi/webd/UARB15). Website-specific behavior stays here so the worker receives one structured scrape result.

## Required flow

1. Search through Go Directly to Matter and verify the displayed matter number.
2. Read title, status, type, category, date values and their labels, outcome, and all five document-tab counts.
3. Open each nonempty tab, wait for its selected state, and click **Save List**. Download its PDF through the filename control and close the dialog. `lists.py` validates the report identity and extracts document numbers from the table's first column across every page. Require its record count to equal the tab count, then deduplicate numbers across all five categories. Zero-count tabs contribute an empty list without opening a dialog. Reports and IDs are persisted separately from downloaded attachments.
4. For each requested type whose count is nonzero, open that tab. A request with no document type stops after the inventory. Iterate each tab's rows in display order, recording ID, title, date, extension, security label, and category.
5. For up to ten eligible Public files in each requested type, click the row's GO GET IT button, then its filename controls in Download Files. Save those files in a folder named for the document type, verify each file, and close the dialog before the next row.
6. Return structured metadata, counts, complete saved-list IDs and paths, selected document records, and local paths for successful downloads. Record failures and skip Confidential rows when downloading attachments.

Saved lists include confidential records but do not expose attachment multiplicity or establish identity between different document/exhibit numbers. The total is a distinct-number document count, separate from both category listings and ZIP entries. Failed or incomplete list exports raise a retryable download error instead of publishing a partial total.

Automated capture and repeated rows were verified on M12205 for Key Documents and Other Documents. The email worker and manual scrape command run the browser in a supervised process: it stops if saved files exceed 4 MB, an active file transfer exceeds 30 seconds, or the full scrape exceeds five minutes. The email worker sends an explanatory reply without a ZIP for these outcomes. Use the [site notes](../../../UARB_SITE_NOTES.md) for inspected locators and the [browser checklist](../../../BUILD_PLAN.md#uarb-browser-workflow-and-scrape-contract) for remaining cases.
