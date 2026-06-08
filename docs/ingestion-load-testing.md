# Ingestion Load Testing

Use `scripts/ingestion_load_test.py` to run repeatable ingestion benchmarks through the real API, Supabase upload URLs, Redis queues, and workers.

## Prerequisites

- Server and workers are running with Docker Compose.
- Supabase schema has been updated with `scripts/schema.supabase.sql`.
- You have a valid app bearer token for a user with a workspace.

## PowerShell Examples

```powershell
cd H:\varun\MultiDoc-RAG\MultiDoc-RAG
$env:RAG_BEARER_TOKEN = "<paste access token>"
python scripts\ingestion_load_test.py --count 10 --scenario valid
python scripts\ingestion_load_test.py --count 25 --scenario valid
python scripts\ingestion_load_test.py --count 50 --scenario valid
python scripts\ingestion_load_test.py --count 10 --scenario mixed
```

The script writes comparable JSON artifacts to:

```text
artifacts/ingestion-load/
```

Each artifact includes prepare/complete responses, final ingestion-run state, current document list, total wall-clock duration, prepare/upload duration, and queue snapshots captured while polling.

## Notes

- The generated PDFs are one-page text PDFs with deterministic names.
- The mixed scenario replaces the final generated file with an invalid text file to confirm isolated failures.
- Use `--timeout-seconds` and `--poll-seconds` to tune long runs.
