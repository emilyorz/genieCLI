SELECT b.drbl_id, b.part6,
  CASE WHEN EXISTS (
    SELECT 1 FROM csfrmask_info m
    WHERE m.drbl_id = b.drbl_id AND m.flag = 'Y'
  ) THEN 1 ELSE 0 END AS has_mask,
  CASE WHEN EXISTS (
    SELECT 1 FROM csfrgross_die g
    WHERE g.part6 = b.part6
  ) THEN 1 ELSE 0 END AS has_die
FROM base b
