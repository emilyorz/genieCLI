SELECT o.id,
  CASE WHEN EXISTS (
    SELECT 1 FROM lineitem l WHERE l.order_id = o.id AND l.qty > 0
  ) THEN 1 ELSE 0 END AS has_li
FROM orders o
