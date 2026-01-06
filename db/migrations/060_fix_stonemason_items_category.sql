UPDATE items
SET category = 'ks',
    updated_at = NOW()
WHERE code IN (
  'ore_gem_burshtyn',
  'ore_gem_kvarts',
  'ore_gem_yashma',
  'ore_gem_oniks',
  'ore_gem_ametyst',
  'ore_gem_granat',
  'ore_gem_malakhit',
  'ore_gem_nefryt',
  'ore_gem_sapfir',
  'ore_gem_rubin',
  'ore_gem_opal',
  'ore_gem_smarahd',
  'ore_gem_hirskyi_kryshtal',
  'ore_gem_almaz'
);