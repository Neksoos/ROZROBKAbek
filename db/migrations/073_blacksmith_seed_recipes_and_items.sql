begin;

-- =========================================================
-- 0) Output items для кування (екіп)
-- =========================================================

insert into items (
  name, rarity, code, category, descr,
  base_value, stack_max, weight, tradable, bind_on_pickup,
  slot, atk, defense, hp, mp, level_req, class_req,
  is_global, amount, stackable, sell_price, npc_key, is_archived,
  created_at, updated_at
) values
  (
    'Залізний клинок',
    'Добротний',
    'smith_weapon_zaliznyi_klynok',
    'equip',
    'Простий, але надійний клинок з кованого заліза.',
    35, 1, 2, true, false,
    'weapon', 7, 0, 0, 0, 5, null,
    false, 1, false, 35, null, false,
    now(), now()
  ),
  (
    'Залізний шолом',
    'Добротний',
    'smith_helmet_zaliznyi',
    'equip',
    'Кований шолом, що захищає голову від ударів.',
    30, 1, 2, true, false,
    'helmet', 0, 3, 0, 0, 5, null,
    false, 1, false, 30, null, false,
    now(), now()
  ),
  (
    'Залізний обладунок',
    'Добротний',
    'smith_armor_zaliznyi',
    'equip',
    'Кований нагрудник з заліза. Важкий, але міцний.',
    60, 1, 5, true, false,
    'armor', 0, 6, 10, 0, 5, null,
    false, 1, false, 60, null, false,
    now(), now()
  )
on conflict (code) do update set
  name       = excluded.name,
  rarity     = excluded.rarity,
  category   = excluded.category,
  descr      = excluded.descr,
  base_value = excluded.base_value,
  stack_max  = excluded.stack_max,
  weight     = excluded.weight,
  tradable   = excluded.tradable,
  bind_on_pickup = excluded.bind_on_pickup,
  slot       = excluded.slot,
  atk        = excluded.atk,
  defense    = excluded.defense,
  hp         = excluded.hp,
  mp         = excluded.mp,
  level_req  = excluded.level_req,
  class_req  = excluded.class_req,
  is_global  = excluded.is_global,
  amount     = excluded.amount,
  stackable  = excluded.stackable,
  sell_price = excluded.sell_price,
  npc_key    = excluded.npc_key,
  is_archived = excluded.is_archived,
  updated_at = now();

-- =========================================================
-- 1) Рецепти (мінімум MVP: 3 плавки + 3 кування)
-- =========================================================

insert into blacksmith_recipes (
  code, name, prof_key, level_req, craft_time_sec,
  output_kind, output_code, output_amount,
  type, notes, json_data
) values
  -- SMELT (ore -> ingot material)
  (
    'bs:smelt:zalizna',
    'Плавка: Залізний злиток',
    'blacksmith',
    1,
    90,
    'material',
    'smith_ingot_zalizna',
    1,
    'smelt',
    'Переплавка залізної руди у злиток.',
    jsonb_build_object('tier',1,'group','smelt')
  ),
  (
    'bs:smelt:midna',
    'Плавка: Мідний злиток',
    'blacksmith',
    1,
    90,
    'material',
    'smith_ingot_midna',
    1,
    'smelt',
    'Переплавка мідної жили у злиток.',
    jsonb_build_object('tier',1,'group','smelt')
  ),
  (
    'bs:smelt:marhantseva',
    'Плавка: Марганцевий злиток',
    'blacksmith',
    1,
    120,
    'material',
    'smith_ingot_marhantseva',
    1,
    'smelt',
    'Переплавка марганцевої руди у злиток.',
    jsonb_build_object('tier',1,'group','smelt')
  ),

  -- FORGE (ingots + fuel -> equip item)
  (
    'bs:forge:zaliznyi_klynok',
    'Кування: Залізний клинок',
    'blacksmith',
    1,
    180,
    'item',
    'smith_weapon_zaliznyi_klynok',
    1,
    'forge',
    'Базова зброя з заліза.',
    jsonb_build_object('tier',1,'group','forge','slot','weapon')
  ),
  (
    'bs:forge:zaliznyi_sholom',
    'Кування: Залізний шолом',
    'blacksmith',
    1,
    180,
    'item',
    'smith_helmet_zaliznyi',
    1,
    'forge',
    'Базовий шолом з заліза.',
    jsonb_build_object('tier',1,'group','forge','slot','helmet')
  ),
  (
    'bs:forge:zaliznyi_obladunok',
    'Кування: Залізний обладунок',
    'blacksmith',
    1,
    240,
    'item',
    'smith_armor_zaliznyi',
    1,
    'forge',
    'Базовий обладунок з заліза.',
    jsonb_build_object('tier',1,'group','forge','slot','armor')
  )
on conflict (code) do update set
  name = excluded.name,
  prof_key = excluded.prof_key,
  level_req = excluded.level_req,
  craft_time_sec = excluded.craft_time_sec,
  output_kind = excluded.output_kind,
  output_code = excluded.output_code,
  output_amount = excluded.output_amount,
  type = excluded.type,
  notes = excluded.notes,
  json_data = excluded.json_data,
  updated_at = now();

-- =========================================================
-- 2) Інгредієнти (перезапис тільки для цих 6 рецептів)
-- =========================================================

with r(code) as (
  values
    ('bs:smelt:zalizna'),
    ('bs:smelt:midna'),
    ('bs:smelt:marhantseva'),
    ('bs:forge:zaliznyi_klynok'),
    ('bs:forge:zaliznyi_sholom'),
    ('bs:forge:zaliznyi_obladunok')
)
delete from blacksmith_recipe_ingredients i
using r
where i.recipe_code = r.code;

-- SMELT: ore (item) -> ingot (material)
insert into blacksmith_recipe_ingredients(recipe_code, input_kind, input_code, qty, role) values
  ('bs:smelt:zalizna',     'item',     'ore_ruda_zalizna',          3, 'main'),
  ('bs:smelt:midna',       'item',     'ore_midna_zhyla',           3, 'main'),
  ('bs:smelt:marhantseva', 'item',     'ore_marhantseva_ruda',      3, 'main');

-- FORGE: ingots (material) + coal (item fuel)
insert into blacksmith_recipe_ingredients(recipe_code, input_kind, input_code, qty, role) values
  -- Клинок
  ('bs:forge:zaliznyi_klynok',     'material', 'smith_ingot_zalizna', 3, 'main'),
  ('bs:forge:zaliznyi_klynok',     'item',     'ore_vuhilna_zhyla',   1, 'fuel'),

  -- Шолом
  ('bs:forge:zaliznyi_sholom',     'material', 'smith_ingot_zalizna', 2, 'main'),
  ('bs:forge:zaliznyi_sholom',     'item',     'ore_vuhilna_zhyla',   1, 'fuel'),

  -- Обладунок
  ('bs:forge:zaliznyi_obladunok',  'material', 'smith_ingot_zalizna', 5, 'main'),
  ('bs:forge:zaliznyi_obladunok',  'item',     'ore_vuhilna_zhyla',   2, 'fuel');

commit;
