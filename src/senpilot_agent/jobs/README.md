# SQLite job store

`store.py` provides the durable SQLite queue and progress record. A single worker is enough for the first deployment.

The database keeps a unique inbound message ID, sender/reply context, parsed request, status, attempt count, next attempt time, lease deadline, serialized scrape result, ZIP path, outgoing message ID, and errors. Downloaded files stay under the job's runtime directory, not inside SQLite. The build plan proposes additional normalized snapshot tables for a later iteration.

Create a job before acknowledging a new inbound message. Claim due work transactionally; an expired lease allows recovery after a worker crash. Preserve the scrape and ZIP across send retries. The job's actual successful file count must come from saved files, not the tab count or the number of clicks.

`enqueue_unparsed` saves intake before OpenAI extraction; `save_parse_result` persists interpretation inside the existing claim. Worker writes use `update_claimed`, fenced by attempt and lease. A separate thread-owned connection renews the lease. Crash recovery consumes the bounded attempt budget. Additive migrations preserve older databases.

Delivery stores exact reply text and the first-send time for idempotent retries. Terminal failure notifications have independent bounded claims, leases, timestamps, and outgoing IDs; sending a failure notice does not mark retrieval completed. Jobs in `delivery_review` require the operator to inspect the original email thread before manual resolution.

See [database, queue, and files](../../../BUILD_PLAN.md#database-queue-and-files).
