begin;

-- =========================================================
-- 0) player_materials: унікальність (tg_id, material_id)
--    Потрібно для коректного UPSERT у крафтових професіях.
-- =========================================================

-- 0.1) Дедуплікація на випадок, якщо вже є дублікати
with agg as (
  select tg_id, material_id, min(id) as keep_id, sum(qty) as total_qty
  from player_materials
  group by tg_id, material_id
  having count(*) > 1
),
upd as (
  update player_materials pm
  set qty = agg.total_qty,
      updated_at = now()
  from agg
  where pm.id = agg.keep_id
  returning pm.id
)
delete from player_materials pm
using agg
where pm.tg_id = agg.tg_id
  and pm.material_id = agg.material_id
  and pm.id <> agg.keep_id;

-- 0.2) Додаємо унікальний індекс (tg_id, material_id) якщо ще нема
do $$
begin
  if not exists (
    select 1 from pg_indexes
    where schemaname = 'public'
      and indexname = 'uq_player_materials_tg_material'
  ) then
    create unique index uq_player_materials_tg_material
      on player_materials (tg_id, material_id);
  end if;
end $$;

-- =========================================================
-- 1) Blacksmith: рецепти + інгредієнти
-- =========================================================

create table if not exists blacksmith_recipes (
  code            text primary key,
  name            text not null,
  prof_key        text not null default 'blacksmith',
  level_req       int  not null default 1,
  craft_time_sec  int  not null check (craft_time_sec > 0),

  output_kind     text not null check (output_kind in ('material','item')),
  output_code     text not null,
  output_amount   int  not null default 1 check (output_amount > 0),

  type            text not null check (type in ('smelt','forge')),

  notes           text,
  json_data       jsonb not null default '{}'::jsonb,

  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists idx_blacksmith_recipes_prof_type_level
  on blacksmith_recipes(prof_key, type, level_req);

create table if not exists blacksmith_recipe_ingredients (
  id          bigserial primary key,
  recipe_code text not null references blacksmith_recipes(code) on delete cascade,

  input_kind  text not null check (input_kind in ('item','material')),
  input_code  text not null,
  qty         int  not null check (qty > 0),
  role        text not null default 'main',

  unique(recipe_code, input_kind, input_code, role)
);

create index if not exists idx_blacksmith_ing_recipe
  on blacksmith_recipe_ingredients(recipe_code);

-- =========================================================
-- 2) Черга крафту гравця
-- =========================================================

create table if not exists player_blacksmith_queue (
  id            bigserial primary key,
  tg_id         bigint not null,
  recipe_code   text not null references blacksmith_recipes(code) on delete restrict,

  status        text not null default 'crafting',
  started_at    timestamptz not null default now(),
  finish_at     timestamptz not null,

  output_kind   text not null check (output_kind in ('material','item')),
  output_code   text not null,
  output_amount int  not null default 1 check (output_amount > 0),

  meta          jsonb not null default '{}'::jsonb,

  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists idx_player_blacksmith_queue_tg
  on player_blacksmith_queue(tg_id);

create index if not exists idx_player_blacksmith_queue_tg_status
  on player_blacksmith_queue(tg_id, status);

do $$
begin
  if not exists (
    select 1
    from information_schema.table_constraints
    where table_name = 'player_blacksmith_queue'
      and constraint_name = 'player_blacksmith_queue_status_chk'
  ) then
    begin
      alter table player_blacksmith_queue
        add constraint player_blacksmith_queue_status_chk
        check (status in ('crafting','done','collected','cancelled'));
    exception when others then null;
    end;
  end if;
end $$;

-- =========================================================
-- 3) Функція: оновити чергу (crafting -> done якщо час настав)
-- =========================================================

create or replace function blacksmith_refresh_queue(p_tg_id bigint)
returns int
language plpgsql
as $$
declare
  v_cnt int;
begin
  update player_blacksmith_queue
  set status = 'done',
      updated_at = now()
  where tg_id = p_tg_id
    and status = 'crafting'
    and finish_at <= now();

  get diagnostics v_cnt = row_count;
  return v_cnt;
end;
$$;

-- =========================================================
-- 4) View для UI: активні задачі ковальства
-- =========================================================

create or replace view v_player_blacksmith_active as
select
  q.id,
  q.tg_id,
  q.recipe_code,
  r.name as recipe_name,
  r.type as craft_type,
  q.status,
  q.started_at,
  q.finish_at,
  greatest(0, extract(epoch from (q.finish_at - now()))::int) as seconds_left,
  q.output_kind,
  q.output_code,
  q.output_amount,
  q.meta
from player_blacksmith_queue q
join blacksmith_recipes r on r.code = q.recipe_code
where q.status in ('crafting','done');

commit;
