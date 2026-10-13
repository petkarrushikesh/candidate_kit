# Implementation decisions

1. **Indexes.** Unique `employees.emp_code` and attendance `(emp_code, date)` indexes enforce identity and one daily record. Employee `(department, joined_on)` serves filtered headcount; `(joined_on, department)` serves unfiltered month-end headcount; `(department, emp_code)` supports filtered employee ordering. Attendance `(date, emp_code)` supports list ordering, `(date, status)` supports status/date filters, and `(emp_code, punch_in)` finds the latest punch. I rejected a standalone status index because status has few values.

2. **Punch-in race.** Both requests may pass employee lookup and attempt insertion. The unique `(emp_code, date)` index allows one insert; MongoDB rejects the other with a duplicate-key error, which the API converts to 409.

3. **Ties.** The leaderboard uses MongoDB competition rank on total late minutes, then filters `rank <= limit`. Everyone tied at the cutoff is included; tied rows are ordered by employee code.

4. **Headcount.** The summary starts from employees who joined by month-end, then uses a left lookup for logs. It counts distinct employees, so employees with no logs still count.

5. **At 100x data.** I would precompute daily department metrics and refresh them as attendance changes, reducing repeated scans and joins for analytics.
