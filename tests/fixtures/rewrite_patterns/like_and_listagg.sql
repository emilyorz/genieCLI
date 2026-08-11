SELECT b.id,
  LISTAGG(r.tag, ',') WITHIN GROUP (ORDER BY r.tag) AS tags
FROM base b
JOIN rules r
  ON r.sub_lot_type LIKE '%' || b.lot || '%'
 OR strpos(CONCAT(',', r.csv, ','), CONCAT(',', b.tok, ',')) > 0
GROUP BY b.id
