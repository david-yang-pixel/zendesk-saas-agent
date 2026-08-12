# Asynchronous processing and document indexing

Use this route for documents, datasets, ingestion, indexing, uploads, or other
work that remains waiting, paused, queued, or processing.

## Execution chain

`customer request -> API persistence -> tenant queue append -> broker dispatch -> worker receipt -> per-tenant execution -> document/segment persistence -> completion`

## Investigation order

1. Query the affected tenant's recent/incomplete documents with
   `document_indexing_status`. Establish status, pause flag, timestamps, errors,
   and segment counts before checking quota.
2. Use returned dataset/document identifiers only as internal pivots. Search the
   tenant-scoped API logs for queue acceptance and the matching dataset worker logs
   for receipt and progress.
   When the fixed status view is insufficient, use tenant-scoped `sql_query` to
   inspect the complete document/dataset/segment lifecycle and tenant-scoped
   `signoz_query` to follow exact API, queue, and worker events.
3. If API acceptance exists but no worker receipt exists, inspect the relevant
   worker deployment around that exact window, including normal and priority
   workers. Do not restrict the search to error severity.
4. Read runtime-aligned source for the queue/finalizer path. Check whether a prior
   task must finish to release the next task and whether restart/cancellation can
   strand that state.
5. Verify recovery from a new document state transition or segment creation. A
   repeated upload alone is not recovery.

## Completion gate

Quota capacity, documentation about possible statuses, or no broad error logs are
adjacent facts. They cannot resolve a stuck-processing ticket without current
object state and the first missing execution hop.
