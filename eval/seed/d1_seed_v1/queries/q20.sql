SELECT a.id FROM a JOIN b ON a.id = b.id WHERE EXISTS (SELECT 1 FROM c WHERE c.a_id = a.id)
