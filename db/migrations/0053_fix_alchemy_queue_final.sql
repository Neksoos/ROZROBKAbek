begin;

-- 1) Додаємо відсутні колонки (idempotent)
alter table player_alchemy_queue
  add column if not exists output_item_code text;

alter table player_alchemy_queue
  add column if not exists output_amount int not null default 1;


-- 2) Видаляємо проблемний view, якщо він є
drop view if exists v_player_alchemy_active;


-- 3) Створюємо view ЗНОВУ, але вже КОРЕКТНО
create view v_player_alchemy_active as
select
  q.id,
  q.tg_id,
  q.recipe_code,
  r.name as recipe_name,
  q.status,
  q.started_at,
  q.finish_at,
  greatest(
    0,
    extract(epoch from (q.finish_at - now()))::int
  ) as seconds_left,
  q.output_item_code,
  q.output_amount,
  q.meta
from player_alchemy_queue q
join alchemy_recipes r
  on r.code = q.recipe_code
where q.status in ('brewing','done');

commit;