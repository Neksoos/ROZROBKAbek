INSERT INTO professions (code, name, descr, kind, min_level, icon)
VALUES (
  'alchemist',
  'Алхімік',
  'Варить зілля, еліксири та настої з трав і рідкісних компонентів.',
  'craft',
  5,
  NULL
)
ON CONFLICT (code) DO UPDATE
SET
  name      = EXCLUDED.name,
  descr     = EXCLUDED.descr,
  kind      = EXCLUDED.kind,
  min_level = EXCLUDED.min_level,
  icon      = EXCLUDED.icon;