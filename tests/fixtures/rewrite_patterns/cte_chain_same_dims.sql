WITH step1 AS (
  SELECT b.id, b.val FROM base b
),
step2 AS (
  SELECT s.*, d.flag AS f2
  FROM step1 s
  LEFT JOIN dim d ON d.id = s.id
),
step3 AS (
  SELECT s.*, d.flag AS f3,
    CASE WHEN d.flag = 'Y' THEN 1 ELSE 0 END AS c3
  FROM step2 s
  LEFT JOIN dim d ON d.id = s.id
),
step4 AS (
  SELECT s.*, d.flag AS f4
  FROM step3 s
  LEFT JOIN dim d ON d.id = s.id
)
SELECT * FROM step4
