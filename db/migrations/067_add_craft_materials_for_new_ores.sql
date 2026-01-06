-- add_craft_materials_for_new_ores
-- 2025-12-20
-- КРАФТ-МАТЕРІАЛИ ДЛЯ НОВИХ РУД (олово/золото/цинк/свинець/ртуть)
-- ВАЖЛИВО: це SQL-міграція (без Alembic/Python-шапки)

INSERT INTO craft_materials (code, name, descr, profession, source_type, rarity)
VALUES
    ('smith_ingot_olovianyi',  'Олов’яний злиток',   'М’який злиток олова для припоїв, дрібних деталей і домішок у сплави.', 'коваль', 'metal', 'Добротний'),
    ('smith_ingot_tsynkovyi',  'Цинковий злиток',    'Злиток цинку для покриттів, сплавів і майстерних кріплень.',           'коваль', 'metal', 'Добротний'),
    ('smith_ingot_svyntsevyi', 'Свинцевий злиток',   'Важкий злиток свинцю для грузил, форм і специфічних виробів.',         'коваль', 'metal', 'Рідкісний'),
    ('smith_ingot_zolotyi',    'Золотий злиток',     'Чистий золотий злиток для коштовної оправи та особливих замовлень.',   'коваль', 'metal', 'Вибраний'),
    ('alch_rtiut_chysta',      'Чиста ртуть',        'Рідкісний небезпечний реагент для сильних настоїв і тонких реакцій.',  'зілляр', 'mixed', 'Рідкісний')
ON CONFLICT (code) DO UPDATE SET
    name        = EXCLUDED.name,
    descr       = EXCLUDED.descr,
    profession  = EXCLUDED.profession,
    source_type = EXCLUDED.source_type,
    rarity      = EXCLUDED.rarity,
    updated_at  = now();