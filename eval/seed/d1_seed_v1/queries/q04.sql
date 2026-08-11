SELECT *
FROM t1 a
JOIN t2 b ON UPPER(a.code) = b.code
