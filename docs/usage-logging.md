# Usage logging

The API Worker writes one Analytics Engine datapoint per `/api/search` and `/api/chat/completions` call to dataset `ftc_helper_usage` (binding `USAGE`).

| Field | Meaning |
|---|---|
| `blobs[2]` / `indexes[0]` | SHA-256 of client IP, truncated to 16 hex chars (approximate unique users) |
| `blobs[3]` | Cloudflare `CF-IPCountry` (ISO 3166-1 alpha-2), empty if unknown |
| `blobs[4]` | Cloudflare colo / PoP code (e.g. `IAD`) |
| `blobs[0]` | `search` or `chat` |
| `blobs[1]` | query text (max 2000 chars) |
| `doubles[0]` | 1 = ok, 0 = error |
| `doubles[1]` | fused chunk count |
| `doubles[2]` | duration ms |

Query example (Account Analytics Engine SQL API):

```sql
SELECT
  blob1 AS endpoint,
  blob2 AS query,
  blob3 AS ip_hash,
  blob4 AS country,
  blob5 AS colo,
  double1 AS ok,
  double2 AS chunks,
  double3 AS ms,
  index1 AS user_key
FROM ftc_helper_usage
WHERE timestamp > NOW() - INTERVAL '7' DAY
ORDER BY timestamp DESC
LIMIT 100
```

Past queries from before this change were not stored (observability was off; request bodies were never logged).
