# UARB browser adapter

`browser.py` owns Playwright navigation and selectors for the [UARB public documents database](https://uarb.novascotia.ca/fmi/webd/UARB15). Website-specific behavior stays here so the worker receives one structured scrape result.

## Required flow

1. Search through Go Directly to Matter and verify the displayed matter number.
2. Read title, status, type, category, date values and their labels, outcome, and all five document-tab counts.
3. Open the requested tab when its count is nonzero. Iterate its document rows in a documented display order, recording ID, title, date, extension, and security label.
4. For up to ten eligible Public rows, click that row's GO GET IT button, then its filename control in Download Files. Capture and save the browser download, verify it is a nonempty file, and close the dialog before the next row.
5. Return structured metadata, counts, selected document records, and local paths for successful downloads. Record failures and skip Confidential rows in the first version.

Automated capture and repeated rows were verified on M12205 for Key Documents and Other Documents. A 4 MB saved-file budget keeps the resulting ZIP within AgentMail's inline request limit; larger rows are skipped while the scraper looks for later eligible rows. Use the [site notes](../../../UARB_SITE_NOTES.md) for inspected locators and the [browser checklist](../../../BUILD_PLAN.md#uarb-browser-workflow-and-scrape-contract) for remaining cases.
