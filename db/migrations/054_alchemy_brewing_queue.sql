begin;

-- =========================================================
-- 0) SAFETY: unique(code) для ON CONFLICT
-- =========================================================
do $$
begin
  -- craft_materials(code) unique
  if not exists (
    select 1 from pg_constraint where conname = 'craft_materials_code_uniq'
  ) then
    begin
      alter table craft_materials
        add constraint craft_materials_code_uniq unique (code);
    exception when others then null;
    end;
  end if;

  -- items(code) unique
  if not exists (
    select 1 from pg_constraint where conname = 'items_code_uniq'
  ) then
    begin
      alter table items
        add constraint items_code_uniq unique (code);
    exception when others then null;
    end;
  end if;
end $$;


-- =========================================================
-- 1) Таблиці алхімії: рецепти + інгредієнти
-- =========================================================
create table if not exists alchemy_recipes (
  code              text primary key,                  -- alch:potion:healing:t1
  name              text not null,
  prof_key          text not null default 'alchemist',  -- заміниш у seed, якщо треба
  level_req         int  not null default 1,
  brew_time_sec     int  not null,                     -- час варки (сек)
  output_item_code  text not null,                     -- items.code
  output_amount     int  not null default 1,
  notes             text,
  json_data         jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists idx_alchemy_recipes_prof_level
  on alchemy_recipes(prof_key, level_req);

create table if not exists alchemy_recipe_ingredients (
  id            bigserial primary key,
  recipe_code   text not null references alchemy_recipes(code) on delete cascade,
  material_code text not null,                   -- craft_materials.code
  qty           int  not null check (qty > 0),
  role          text not null default 'main',     -- main / solvent / container / catalyst
  unique(recipe_code, material_code, role)
);

create index if not exists idx_alch_ing_recipe
  on alchemy_recipe_ingredients(recipe_code);


-- =========================================================
-- 2) Черга варки (таймер)
-- =========================================================
create table if not exists player_alchemy_queue (
  id              bigserial primary key,
  tg_id           bigint not null,
  recipe_code     text not null references alchemy_recipes(code),
  status          text not null default 'brewing', -- brewing/done/collected/cancelled
  started_at      timestamptz not null default now(),
  finish_at       timestamptz not null,
  output_item_code text not null,                 -- snapshot
  output_amount   int not null default 1,
  meta            jsonb not null default '{}'::jsonb
);

create index if not exists idx_alch_queue_tg_status_finish
  on player_alchemy_queue(tg_id, status, finish_at);

-- CHECK на статус (додаємо, якщо нема)
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'player_alchemy_queue_status_chk'
  ) then
    begin
      alter table player_alchemy_queue
        add constraint player_alchemy_queue_status_chk
        check (status in ('brewing','done','collected','cancelled'));
    exception when others then null;
    end;
  end if;
end $$;


-- =========================================================
-- 3) Функція: оновити чергу (brewing -> done якщо час настав)
--    Викликай при відкритті "Алхімія/Черга" або перед "Забрати".
-- =========================================================
create or replace function alchemy_refresh_queue(p_tg_id bigint)
returns int
language plpgsql
as $$
declare
  v_cnt int;
begin
  update player_alchemy_queue
  set status = 'done'
  where tg_id = p_tg_id
    and status = 'brewing'
    and finish_at <= now();

  get diagnostics v_cnt = row_count;
  return v_cnt;
end;
$$;


-- =========================================================
-- 4) (Опційно) Представлення для UI: показати активні варки
-- =========================================================
create or replace view v_player_alchemy_active as
select
  q.id,
  q.tg_id,
  q.recipe_code,
  r.name as recipe_name,
  q.status,
  q.started_at,
  q.finish_at,
  greatest(0, extract(epoch from (q.finish_at - now()))::int) as seconds_left,
  q.output_item_code,
  q.output_amount,
  q.meta
from player_alchemy_queue q
join alchemy_recipes r on r.code = q.recipe_code
where q.status in ('brewing','done');


commit;
