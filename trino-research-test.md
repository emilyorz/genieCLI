# Trino Query Optimization Report

**Date:** 2026-04-04 01:18
**Model:** qwen3.5:4b
**Metric:** cpu_time_ms (lower is better)
**Verify runs:** 3 (median)
**Result validation:** full row-level equivalence check

## Summary

| Metric      | Value          |
| ----------- | -------------- |
| Baseline    | 21.0           |
| Best        | 13.0           |
| Improvement | -8.0 (-38.1%)  |
| Iterations  | 5 (2 kept)     |
| Row count   | 10 (preserved) |

## Iteration History

| #   | Status         | Metric | Delta | Hypothesis                     |
| --- | -------------- | ------ | ----- | ------------------------------ |
| 1   | exec_failed    | 21.0   | +0.0  | WITH direct_reports_count AS ( |
| 2   | worse          | 21.0   | +0.0  | SELECT                         |
| 3   | improved       | 20.0   | -1.0  | SELECT                         |
| 4   | improved       | 13.0   | -7.0  | WITH direct_reports_count AS ( |
| 5   | semantic_drift | 15.0   | +2.0  | WITH employees_with_mgr AS (   |

## Original SQL

```sql
SELECT
    e.employee_id,
    e.first_name || ' ' || e.last_name AS full_name,
    COALESCE(e.commission_pct, 0) AS commission,
    CASE
        WHEN e.department_id = 10 THEN 'Admin'
        WHEN e.department_id = 20 THEN 'Marketing'
        WHEN e.department_id = 30 THEN 'IT'
        ELSE 'Other'
    END AS dept_name,
    date_diff('day', e.hire_date, CURRENT_DATE) AS days_employed,
    d.department_name,
    (SELECT COUNT(*) FROM employees_full e2 WHERE e2.manager_id = e.employee_id) AS direct_reports
FROM employees_full e
LEFT JOIN departments d ON e.department_id = d.department_id
ORDER BY e.salary DESC
FETCH FIRST 100 ROWS ONLY
```

## Optimized SQL

```sql
WITH direct_reports_count AS (
    SELECT manager_id, COUNT(*) AS report_count
    FROM employees_full
    GROUP BY manager_id
)
SELECT
    e.employee_id,
    e.first_name || ' ' || e.last_name AS full_name,
    COALESCE(e.commission_pct, 0) AS commission,
    CASE
        WHEN e.department_id = 10 THEN 'Admin'
        WHEN e.department_id = 20 THEN 'Marketing'
        WHEN e.department_id = 30 THEN 'IT'
        ELSE 'Other'
    END AS dept_name,
    date_diff('day', e.hire_date, CURRENT_DATE) AS days_employed,
    d.department_name,
    COALESCE(dr.report_count, 0) AS direct_reports
FROM employees_full e
INNER JOIN departments d ON e.department_id = d.department_id
LEFT JOIN direct_reports_count dr ON e.employee_id = dr.manager_id
ORDER BY e.salary DESC
FETCH FIRST 100 ROWS ONLY
```
