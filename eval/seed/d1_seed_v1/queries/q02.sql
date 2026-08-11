SELECT a.id, b.name
FROM orders a
JOIN customers b ON COALESCE(a.cust_id, 0) = b.id
WHERE a.dt >= DATE '2024-01-01'
