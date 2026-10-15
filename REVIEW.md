# REVIEW.md

## Scope

Reviewed the attendance API for correctness, validation, concurrency, auditability, analytics, and MongoDB query performance. The following checklist records the key defects and risks addressed during implementation. Function names and fixes should correspond to the final `app/main.py`.

| # | Where | What is wrong or risky | How to detect it | Fix / expected behavior |
|---|---|---|---|---|
| 1 | Timestamp parsing | Accepting seconds, floats, strings, or out-of-range values can produce invalid instants. | Submit a timestamp in seconds or a non-integer value. | Validate epoch milliseconds and reject invalid values with `422`. |
| 2 | Timestamp conversion | Keeping fractional seconds can make calculations and stored values inconsistent. | Submit a timestamp containing milliseconds. | Truncate instants to whole seconds before calculations and storage. |
| 3 | Attendance date calculation | Using UTC date instead of IST date can assign attendance to the wrong day. | Test a punch near midnight UTC. | Derive the attendance date using IST rules. |
| 4 | Overnight shifts | Treating every shift as ending on the same calendar day miscalculates overnight attendance. | Test a shift such as 22:00–06:00. | Handle the previous-day start and next-day end correctly. |
| 5 | Late-arrival calculation | Applying the grace period incorrectly or rounding elapsed minutes can produce incorrect results. | Test exactly 10 minutes late and one second beyond the grace period. | Apply the strict grace-period rule and floor whole elapsed minutes. |
| 6 | Overtime calculation | Counting overtime before the minimum threshold creates incorrect totals. | Test 29 minutes and 30 minutes after shift end. | Count whole overtime minutes only when the duration reaches 30 minutes. |
| 7 | Work-hour calculation | Binary floating-point rounding may disagree with the required decimal rounding. | Test durations near a two-decimal rounding boundary. | Round to two decimal places using half-up semantics. |
| 8 | Attendance uniqueness | Checking for an existing record before insertion alone is vulnerable to concurrent requests. | Send simultaneous punch-in requests for the same employee and date. | Enforce a unique `(emp_code, date)` index and return `409` for the conflicting request. |
| 9 | Manual correction history | Recording unchanged fields or overwriting existing history weakens the audit trail. | Submit a correction with unchanged and changed fields. | Append a correction entry containing only actual changes, preserving prior history. |
| 10 | Concurrent corrections | Read-modify-write operations can lose a correction history entry under concurrency. | Send overlapping PATCH requests for the same record. | Use an atomic update or compare-and-set strategy that prevents silent lost updates. |
| 11 | Analytics implementation | Fetching all records and calculating summaries in Python does not scale well. | Inspect the implementation and test a larger dataset. | Use MongoDB aggregation pipelines for analytics. |
| 12 | Department trend | Missing dates and weekends can disappear from the output, affecting the moving average. | Test a range containing weekends and dates with no logs. | Generate one row per requested date and calculate the rolling window in the database. |
| 13 | Query performance | Queries without appropriate indexes may scan the full attendance collection. | Inspect `explain` output using `executionStats`. | Create suitable indexes at startup and inspect winning query plans. |
| 14 | Explain serialization | BSON-specific values such as timestamps may not be directly JSON serializable. | Call an explain endpoint and inspect its HTTP response. | Convert explain output to JSON-compatible values before returning it. |

## Items checked

- Timestamp boundaries, IST date handling, late-arrival and overtime thresholds.
- Attendance uniqueness and correction-history behavior.
- Analytics aggregation and missing-date handling.
- Index usage and explain output serialization.

The final review should be consistent with the fixes actually present in `app/main.py`. Any item not implemented or verified should be corrected or removed before submission.
