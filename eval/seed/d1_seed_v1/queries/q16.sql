WITH step1 AS (
  SELECT f.id, f.k FROM fact f
),
step2 AS (
  SELECT s.*, d1.n FROM step1 s LEFT JOIN dim d1 ON s.k = d1.k
),
step3 AS (
  SELECT s.*, d1.n2 FROM step2 s LEFT JOIN dim d1 ON s.k = d1.k
),
step4 AS (
  SELECT s.*, d1.n3 FROM step3 s LEFT JOIN dim d1 ON s.k = d1.k
)
SELECT * FROM step4
