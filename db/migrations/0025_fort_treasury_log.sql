INSERT INTO fort_treasury (zastava_id, chervontsi, kleynody)
SELECT id, 0, 0 FROM zastavy
ON CONFLICT DO NOTHING;