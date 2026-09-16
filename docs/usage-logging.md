# Usage logging

The API Worker writes one Analytics Engine datapoint per `/api/search` and `/api/chat/completions` call to dataset `ftc_helper_usage` (binding `USAGE`).

| Field | Meaning |
|---|---|
| `indexes[0]` / `blobs[2]` | SHA-256 of client IP, truncated to 16 hex chars (approximate unique users) |
| `blobs[0]` | `search` or `chat` |
| `blobs[1]` | query text (max 2000 chars) |
| `doubles[0]` | 1 = ok, 0 = error |
| `doubles[1]` | fused chunk count |
| `doubles[2]` | duration ms |

Query example (Account Analytics Engine SQL API):

```sql
SELECT
  blob1 AS query,
  blob2 AS ip_hash,
  double1 AS chunks,
  double2 AS ms,
  index1 AS user_key
FROM ftc_helper_usage
WHERE timestamp > NOW() - INTERVAL '7' DAY
ORDER BY timestamp DESC
LIMIT 100
```

Past queries from before this change were not stored (observability was off; request bodies were never logged).
