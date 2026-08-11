SELECT a.id
FROM fact a
JOIN dim d ON CAST(a.dim_key AS varchar) = d.k
WHERE a.x IS NOT NULL
