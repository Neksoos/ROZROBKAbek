begin;

-- =========================================================
-- 0) Таблиці алхімії (якщо раптом ще не створені)
-- =========================================================
create table if not exists alchemy_recipes (
  code             text primary key,
  name             text not null,
  prof_key         text not null default 'alchemist',
  level_req        int  not null default 1,
  brew_time_sec    int  not null,
  output_item_code text not null,
  output_amount    int  not null default 1,
  notes            text,
  json_data        jsonb not null default '{}'::jsonb,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create index if not exists idx_alchemy_recipes_prof_level
  on alchemy_recipes(prof_key, level_req);

create table if not exists alchemy_recipe_ingredients (
  id            bigserial primary key,
  recipe_code   text not null references alchemy_recipes(code) on delete cascade,
  material_code text not null,
  qty           int  not null check (qty > 0),
  role          text not null default 'main',
  unique(recipe_code, material_code, role)
);

create index if not exists idx_alch_ing_recipe
  on alchemy_recipe_ingredients(recipe_code);

-- =========================================================
-- 1) Upsert 20 рецептів (під перші 20 potions)
-- =========================================================
insert into alchemy_recipes (
  code, name, prof_key, level_req, brew_time_sec,
  output_item_code, output_amount, notes, json_data
)
values
-- HEAL HP (t1/t2/t3)
('alch:potion:healing:t1','Настій живиці','alchemist',1,180,'potion_healing_t1',1,'Базове лікування.', jsonb_build_object('tier',1,'group','heal')),
('alch:potion:healing:t2','Настій живиці міцний','alchemist',3,360,'potion_healing_t2',1,'Міцніше лікування.', jsonb_build_object('tier',2,'group','heal')),
('alch:potion:healing:t3','Еліксир живиці','alchemist',6,720,'potion_healing_t3',1,'Сильне лікування.', jsonb_build_object('tier',3,'group','heal')),

-- MANA (t1/t2/t3)
('alch:potion:mana:t1','Настій синь-роси','alchemist',1,180,'potion_mana_t1',1,'Базова мана.', jsonb_build_object('tier',1,'group','mana')),
('alch:potion:mana:t2','Настій синь-роси міцний','alchemist',3,360,'potion_mana_t2',1,'Міцніша мана.', jsonb_build_object('tier',2,'group','mana')),
('alch:potion:mana:t3','Еліксир синь-роси','alchemist',6,720,'potion_mana_t3',1,'Сильна мана.', jsonb_build_object('tier',3,'group','mana')),

-- REGEN HP (t1/t2)
('alch:potion:regen_hp:t1','Відвар повільної рани','alchemist',2,240,'potion_regen_hp_t1',1,'Реген HP.', jsonb_build_object('tier',1,'group','regen_hp')),
('alch:potion:regen_hp:t2','Відвар повільної рани міцний','alchemist',4,420,'potion_regen_hp_t2',1,'Реген HP (міцний).', jsonb_build_object('tier',2,'group','regen_hp')),

-- REGEN MP (t1/t2)
('alch:potion:regen_mp:t1','Настій тихої течії','alchemist',2,240,'potion_regen_mp_t1',1,'Реген MP.', jsonb_build_object('tier',1,'group','regen_mp')),
('alch:potion:regen_mp:t2','Настій тихої течії міцний','alchemist',5,480,'potion_regen_mp_t2',1,'Реген MP (міцний).', jsonb_build_object('tier',2,'group','regen_mp')),

-- DEFENSE (t1/t2)
('alch:potion:armor:t1','Відвар кам’яної шкіри','alchemist',4,360,'potion_armor_t1',1,'Захист.', jsonb_build_object('tier',1,'group','defense')),
('alch:potion:armor:t2','Еліксир кам’яної шкіри','alchemist',7,600,'potion_armor_t2',1,'Захист (міцний).', jsonb_build_object('tier',2,'group','defense')),

-- ATTACK (t1/t2)
('alch:potion:power:t1','Настій бойового жару','alchemist',4,360,'potion_power_t1',1,'Атака.', jsonb_build_object('tier',1,'group','attack')),
('alch:potion:power:t2','Еліксир бойового жару','alchemist',7,600,'potion_power_t2',1,'Атака (міцний).', jsonb_build_object('tier',2,'group','attack')),

-- MAGIC RESIST (t1/t2)
('alch:potion:magic_resist:t1','Настій м’якого щита','alchemist',3,300,'potion_magic_resist_t1',1,'Магзахист.', jsonb_build_object('tier',1,'group','magic_def')),
('alch:potion:magic_resist:t2','Еліксир м’якого щита','alchemist',6,540,'potion_magic_resist_t2',1,'Магзахист (міцний).', jsonb_build_object('tier',2,'group','magic_def')),

-- SPEED (t1/t2)
('alch:potion:haste:t1','Настій швидкого кроку','alchemist',3,300,'potion_haste_t1',1,'Швидкість.', jsonb_build_object('tier',1,'group','speed')),
('alch:potion:haste:t2','Еліксир швидкого кроку','alchemist',6,540,'potion_haste_t2',1,'Швидкість (міцний).', jsonb_build_object('tier',2,'group','speed')),

-- ACCURACY (t1/t2)
('alch:potion:accuracy:t1','Настій ясного ока','alchemist',3,300,'potion_accuracy_t1',1,'Точність.', jsonb_build_object('tier',1,'group','accuracy')),
('alch:potion:accuracy:t2','Еліксир ясного ока','alchemist',6,540,'potion_accuracy_t2',1,'Точність (міцний).', jsonb_build_object('tier',2,'group','accuracy')),

-- CRIT (t1/t2)
('alch:potion:crit:t1','Настій гострої миті','alchemist',4,360,'potion_crit_t1',1,'Крит.', jsonb_build_object('tier',1,'group','crit')),
('alch:potion:crit:t2','Еліксир гострої миті','alchemist',7,600,'potion_crit_t2',1,'Крит (міцний).', jsonb_build_object('tier',2,'group','crit')),

-- NIGHT VISION (t1)
('alch:potion:night_vision:t1','Настій нічного зору','alchemist',6,540,'potion_night_vision_t1',1,'Нічний зір.', jsonb_build_object('tier',1,'group','utility')),

-- FORTITUDE (t1)
('alch:potion:fortitude:t1','Настій міцного серця','alchemist',6,540,'potion_fortitude_t1',1,'Макс HP.', jsonb_build_object('tier',1,'group','buff_max_hp'))

on conflict (code) do update set
  name = excluded.name,
  prof_key = excluded.prof_key,
  level_req = excluded.level_req,
  brew_time_sec = excluded.brew_time_sec,
  output_item_code = excluded.output_item_code,
  output_amount = excluded.output_amount,
  notes = excluded.notes,
  json_data = excluded.json_data,
  updated_at = now();


-- =========================================================
-- 2) Перезапис інгредієнтів тільки для цих 20 рецептів
-- =========================================================
with r(code) as (
  values
  ('alch:potion:healing:t1'),('alch:potion:healing:t2'),('alch:potion:healing:t3'),
  ('alch:potion:mana:t1'),('alch:potion:mana:t2'),('alch:potion:mana:t3'),
  ('alch:potion:regen_hp:t1'),('alch:potion:regen_hp:t2'),
  ('alch:potion:regen_mp:t1'),('alch:potion:regen_mp:t2'),
  ('alch:potion:armor:t1'),('alch:potion:armor:t2'),
  ('alch:potion:power:t1'),('alch:potion:power:t2'),
  ('alch:potion:magic_resist:t1'),('alch:potion:magic_resist:t2'),
  ('alch:potion:haste:t1'),('alch:potion:haste:t2'),
  ('alch:potion:accuracy:t1'),('alch:potion:accuracy:t2'),
  ('alch:potion:crit:t1'),('alch:potion:crit:t2'),
  ('alch:potion:night_vision:t1'),
  ('alch:potion:fortitude:t1')
)
delete from alchemy_recipe_ingredients i
using r
where i.recipe_code = r.code;

-- =========================================================
-- 3) Інгредієнти (правило: container + solvent + herbs)
--    T1: 3 трави (2+1), T2: 5 (3+2), T3: 10 (6+4)
-- =========================================================

-- HEAL
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:healing:t1','alch_flask_simple',1,'container'),
('alch:potion:healing:t1','alch_base_water',1,'solvent'),
('alch:potion:healing:t1','alch_dried_podorozhnyk_shyrokyi',2,'main'),
('alch:potion:healing:t1','alch_dried_romashka_poleva',1,'main'),

('alch:potion:healing:t2','alch_flask_reinforced',1,'container'),
('alch:potion:healing:t2','alch_base_spirit',1,'solvent'),
('alch:potion:healing:t2','alch_dried_derevii_zapashnyi',3,'main'),
('alch:potion:healing:t2','alch_dried_zvirobii_zvychainyi',2,'main'),

('alch:potion:healing:t3','alch_flask_etched',1,'container'),
('alch:potion:healing:t3','alch_base_resin',1,'solvent'),
('alch:potion:healing:t3','alch_dried_kryvavytsia_chervona',6,'main'),
('alch:potion:healing:t3','alch_dried_zolototysiachnyk_spravzhnii',4,'main');

-- MANA
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:mana:t1','alch_flask_simple',1,'container'),
('alch:potion:mana:t1','alch_base_water',1,'solvent'),
('alch:potion:mana:t1','alch_dried_miata_poleva',2,'main'),
('alch:potion:mana:t1','alch_dried_chebrets_povzuchyi',1,'main'),

('alch:potion:mana:t2','alch_flask_reinforced',1,'container'),
('alch:potion:mana:t2','alch_base_spirit',1,'solvent'),
('alch:potion:mana:t2','alch_dried_verbena_likarska',3,'main'),
('alch:potion:mana:t2','alch_dried_miata_lisova_temna',2,'main'),

('alch:potion:mana:t3','alch_flask_etched',1,'container'),
('alch:potion:mana:t3','alch_base_resin',1,'solvent'),
('alch:potion:mana:t3','alch_dried_rodiola_rozheva',6,'main'),
('alch:potion:mana:t3','alch_dried_shafran_hirskyi',4,'main');

-- REGEN HP
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:regen_hp:t1','alch_flask_simple',1,'container'),
('alch:potion:regen_hp:t1','alch_base_water',1,'solvent'),
('alch:potion:regen_hp:t1','alch_dried_lopukh_velykyi',2,'main'),
('alch:potion:regen_hp:t1','alch_dried_romashka_poleva',1,'main'),

('alch:potion:regen_hp:t2','alch_flask_reinforced',1,'container'),
('alch:potion:regen_hp:t2','alch_base_spirit',1,'solvent'),
('alch:potion:regen_hp:t2','alch_dried_derevii_zapashnyi',3,'main'),
('alch:potion:regen_hp:t2','alch_dried_zvirobii_zvychainyi',2,'main');

-- REGEN MP
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:regen_mp:t1','alch_flask_simple',1,'container'),
('alch:potion:regen_mp:t1','alch_base_water',1,'solvent'),
('alch:potion:regen_mp:t1','alch_dried_miata_poleva',2,'main'),
('alch:potion:regen_mp:t1','alch_dried_materynka_lisova',1,'main'),

('alch:potion:regen_mp:t2','alch_flask_reinforced',1,'container'),
('alch:potion:regen_mp:t2','alch_base_spirit',1,'solvent'),
('alch:potion:regen_mp:t2','alch_dried_verbena_likarska',3,'main'),
('alch:potion:regen_mp:t2','alch_dried_rodiola_rozheva',2,'main');

-- DEFENSE
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:armor:t1','alch_flask_reinforced',1,'container'),
('alch:potion:armor:t1','alch_base_water',1,'solvent'),
('alch:potion:armor:t1','alch_dried_kropyva_dvodomna',2,'main'),
('alch:potion:armor:t1','alch_dried_polyn_zvychainyi',1,'main'),

('alch:potion:armor:t2','alch_flask_etched',1,'container'),
('alch:potion:armor:t2','alch_base_spirit',1,'solvent'),
('alch:potion:armor:t2','alch_dried_barvinok_lisovyi',3,'main'),
('alch:potion:armor:t2','alch_dried_polyn_zvychainyi',2,'main');

-- ATTACK
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:power:t1','alch_flask_reinforced',1,'container'),
('alch:potion:power:t1','alch_base_spirit',1,'solvent'),
('alch:potion:power:t1','alch_dried_kropyva_dvodomna',2,'main'),
('alch:potion:power:t1','alch_dried_chebrets_povzuchyi',1,'main'),

('alch:potion:power:t2','alch_flask_etched',1,'container'),
('alch:potion:power:t2','alch_base_spirit',1,'solvent'),
('alch:potion:power:t2','alch_dried_zolototysiachnyk_spravzhnii',3,'main'),
('alch:potion:power:t2','alch_dried_diahyl_aromatnyi',2,'main');

-- MAGIC RESIST
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:magic_resist:t1','alch_flask_reinforced',1,'container'),
('alch:potion:magic_resist:t1','alch_base_water',1,'solvent'),
('alch:potion:magic_resist:t1','alch_dried_materynka_lisova',2,'main'),
('alch:potion:magic_resist:t1','alch_dried_chebrets_povzuchyi',1,'main'),

('alch:potion:magic_resist:t2','alch_flask_etched',1,'container'),
('alch:potion:magic_resist:t2','alch_base_resin',1,'solvent'),
('alch:potion:magic_resist:t2','alch_dried_bilozir_bolotianyi',3,'main'),
('alch:potion:magic_resist:t2','alch_dried_rodiola_rozheva',2,'main');

-- SPEED
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:haste:t1','alch_flask_reinforced',1,'container'),
('alch:potion:haste:t1','alch_base_spirit',1,'solvent'),
('alch:potion:haste:t1','alch_dried_miata_poleva',2,'main'),
('alch:potion:haste:t1','alch_dried_polyn_zvychainyi',1,'main'),

('alch:potion:haste:t2','alch_flask_etched',1,'container'),
('alch:potion:haste:t2','alch_base_spirit',1,'solvent'),
('alch:potion:haste:t2','alch_dried_verbena_likarska',3,'main'),
('alch:potion:haste:t2','alch_dried_miata_lisova_temna',2,'main');

-- ACCURACY
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:accuracy:t1','alch_flask_reinforced',1,'container'),
('alch:potion:accuracy:t1','alch_base_water',1,'solvent'),
('alch:potion:accuracy:t1','alch_dried_derevii_zapashnyi',2,'main'),
('alch:potion:accuracy:t1','alch_dried_romashka_poleva',1,'main'),

('alch:potion:accuracy:t2','alch_flask_etched',1,'container'),
('alch:potion:accuracy:t2','alch_base_spirit',1,'solvent'),
('alch:potion:accuracy:t2','alch_dried_rodiola_rozheva',3,'main'),
('alch:potion:accuracy:t2','alch_dried_verbena_likarska',2,'main');

-- CRIT
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:crit:t1','alch_flask_reinforced',1,'container'),
('alch:potion:crit:t1','alch_base_spirit',1,'solvent'),
('alch:potion:crit:t1','alch_dried_polyn_zvychainyi',2,'main'),
('alch:potion:crit:t1','alch_dried_zvirobii_zvychainyi',1,'main'),

('alch:potion:crit:t2','alch_flask_etched',1,'container'),
('alch:potion:crit:t2','alch_base_resin',1,'solvent'),
('alch:potion:crit:t2','alch_dried_kryvavytsia_chervona',3,'main'),
('alch:potion:crit:t2','alch_dried_horytsvit_vesnianyi',2,'main');

-- NIGHT VISION (3 трави одного виду — ок, бо 3>1 і це “рідкісне”)
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:night_vision:t1','alch_flask_etched',1,'container'),
('alch:potion:night_vision:t1','alch_base_spirit',1,'solvent'),
('alch:potion:night_vision:t1','alch_dried_miata_lisova_temna',3,'main');

-- FORTITUDE
insert into alchemy_recipe_ingredients(recipe_code, material_code, qty, role) values
('alch:potion:fortitude:t1','alch_flask_etched',1,'container'),
('alch:potion:fortitude:t1','alch_base_resin',1,'solvent'),
('alch:potion:fortitude:t1','alch_dried_rodiola_rozheva',2,'main'),
('alch:potion:fortitude:t1','alch_dried_valeriana_lisova',1,'main');

commit;
