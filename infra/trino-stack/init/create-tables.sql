-- ============================================================================
-- Sample tables for genieCLI Trino testing
-- Run after stack is healthy:
--   docker exec -it trino trino < infra/trino-stack/init/create-tables.sql
-- ============================================================================

-- ── Iceberg schema ──────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS iceberg.warehouse
WITH (location = 's3a://warehouse/');

-- ── Sample: employees (tests partition filter, basic queries) ────────────────
CREATE TABLE IF NOT EXISTS iceberg.warehouse.employees (
    id        INTEGER,
    name      VARCHAR,
    dept      VARCHAR,
    salary    DECIMAL(10, 2),
    hire_date DATE
)
WITH (
    format = 'PARQUET',
    partitioning = ARRAY['dept']
);

INSERT INTO iceberg.warehouse.employees VALUES
    (1,  'Alice Chen',    'engineering', 120000.00, DATE '2023-01-15'),
    (2,  'Bob Wang',      'engineering', 115000.00, DATE '2023-03-20'),
    (3,  'Carol Li',      'product',     105000.00, DATE '2022-06-01'),
    (4,  'David Lin',     'product',     110000.00, DATE '2023-08-10'),
    (5,  'Eve Huang',     'data',         95000.00, DATE '2024-01-05'),
    (6,  'Frank Wu',      'data',        100000.00, DATE '2022-11-30'),
    (7,  'Grace Tsai',    'engineering', 130000.00, DATE '2021-04-15'),
    (8,  'Henry Chang',   'ops',          85000.00, DATE '2024-06-01'),
    (9,  'Iris Yang',     'ops',          90000.00, DATE '2023-12-01'),
    (10, 'Jack Liu',      'engineering', 125000.00, DATE '2022-09-15');

-- ── Sample: orders (tests date partition, joins) ────────────────────────────
CREATE TABLE IF NOT EXISTS iceberg.warehouse.orders (
    order_id    INTEGER,
    customer_id INTEGER,
    amount      DECIMAL(10, 2),
    status      VARCHAR,
    order_date  DATE
)
WITH (
    format = 'PARQUET',
    partitioning = ARRAY['order_date']
);

INSERT INTO iceberg.warehouse.orders VALUES
    (1001, 1, 250.00,  'completed', DATE '2025-01-15'),
    (1002, 2, 1500.00, 'completed', DATE '2025-01-20'),
    (1003, 1, 75.00,   'pending',   DATE '2025-02-01'),
    (1004, 3, 320.00,  'completed', DATE '2025-02-10'),
    (1005, 2, 890.00,  'cancelled', DATE '2025-03-01'),
    (1006, 4, 150.00,  'completed', DATE '2025-03-15'),
    (1007, 5, 2200.00, 'pending',   DATE '2025-04-01');

-- ── Sample: oracle_legacy (deliberately has Oracle-ish patterns for linter testing)
CREATE TABLE IF NOT EXISTS iceberg.warehouse.oracle_legacy (
    emp_id    INTEGER,
    emp_name  VARCHAR,
    mgr_id    INTEGER,
    dept_code VARCHAR
);

INSERT INTO iceberg.warehouse.oracle_legacy VALUES
    (1, 'Boss',    NULL, 'D001'),
    (2, 'Manager', 1,    'D001'),
    (3, 'Worker',  2,    'D001'),
    (4, 'Intern',  2,    'D002');

-- ── Memory catalog for quick ephemeral tests ────────────────────────────────
CREATE SCHEMA IF NOT EXISTS memory.test;

CREATE TABLE IF NOT EXISTS memory.test.numbers AS
SELECT * FROM (VALUES (1), (2), (3), (4), (5)) AS t(n);

-- ── Verify ──────────────────────────────────────────────────────────────────
SELECT 'employees' AS tbl, COUNT(*) AS rows FROM iceberg.warehouse.employees
UNION ALL
SELECT 'orders', COUNT(*) FROM iceberg.warehouse.orders
UNION ALL
SELECT 'oracle_legacy', COUNT(*) FROM iceberg.warehouse.oracle_legacy
UNION ALL
SELECT 'memory.numbers', COUNT(*) FROM memory.test.numbers;
