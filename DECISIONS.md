# DECISIONS.md

## 1. Which indexes did you create, and why?

I use a unique compound index on `(emp_code, date)` to enforce one attendance record per employee per day. An `(emp_code, date)` index also supports employee-month queries, while date-oriented indexes support attendance listing and department analytics. The leaderboard benefits from an index aligned with date, status, and late-minute filtering/sorting. The explain endpoint helps inspect whether MongoDB selects an index scan for the requested query.

## 2. What happens when two identical punch-ins arrive simultaneously?

Both requests may initially observe that no attendance record exists. The unique `(emp_code, date)` index is the final protection against duplicates: one insert succeeds, while the conflicting insert must be translated into HTTP `409 Conflict`. This makes the database constraint essential rather than relying only on an application-side existence check.

## 3. What does the leaderboard return for a tie at the cutoff?

The leaderboard uses competition ranking, so tied employees share a rank and the next rank is skipped. The result limit is applied after ranking, and all employees tied at the cutoff rank are included. A deterministic secondary sort by employee code makes output ordering stable.

## 4. How does the department summary count employees with no logs?

Headcount is based on employee records whose `joined_on` date is on or before the period end, not on attendance records. Therefore, an eligible employee with no attendance logs is still included in headcount. Attendance metrics are aggregated separately so missing logs do not become fabricated attendance records.

## 5. What would you change for 100 times the data?

I would benchmark realistic workloads, inspect execution statistics, and tune compound indexes to match actual filters and sorts. I would also evaluate pagination costs, aggregation memory use, connection-pool settings, and whether frequently requested summaries need pre-aggregation. Any change would be guided by measured query plans and correctness tests rather than adding indexes indiscriminately.