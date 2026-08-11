SELECT b.id, r.rule_name
FROM base b
JOIN rules r
  ON COALESCE(r.product_2, '') = b.product
 AND CONCAT(r.a, r.b) = b.key
