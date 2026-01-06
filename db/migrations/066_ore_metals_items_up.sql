BEGIN;

INSERT INTO items (
  name, rarity, code, category, descr,
  base_value, stack_max, weight, tradable, bind_on_pickup,
  slot, atk, defense, hp, mp, level_req, class_req,
  is_global, amount, stackable, sell_price, npc_key, is_archived,
  created_at, updated_at
) VALUES
  -- Олово
  ('Оловʼяна руда', 'Добротний', 'ore_metal_olovo', 'ore_metal',
   'Сірувата руда з мʼяким блиском. Після виплавки дає олово для сплавів і припоїв.',
   9, 99, 1, true, false,
   NULL, 0, 0, 0, 0, 1, NULL,
   false, 1, true, 9, NULL, false,
   NOW(), NOW()),

  -- Золото
  ('Золота руда', 'Рідкісний', 'ore_metal_zoloto', 'ore_metal',
   'Жила з теплим жовтим відтінком. Рідкісний метал, який цінують ювеліри й застави.',
   28, 99, 1, true, false,
   NULL, 0, 0, 0, 0, 1, NULL,
   false, 1, true, 28, NULL, false,
   NOW(), NOW()),

  -- Свинець
  ('Свинцева руда', 'Добротний', 'ore_metal_svynec', 'ore_metal',
   'Важка темна руда. Дає свинець, який беруть для литва та важких сумішей.',
   11, 99, 1, true, false,
   NULL, 0, 0, 0, 0, 1, NULL,
   false, 1, true, 11, NULL, false,
   NOW(), NOW()),

  -- Цинк
  ('Цинкова руда', 'Добротний', 'ore_metal_tsynk', 'ore_metal',
   'Світла руда з різким металевим відблиском. Потрібна для сплавів і захисного покриття.',
   10, 99, 1, true, false,
   NULL, 0, 0, 0, 0, 1, NULL,
   false, 1, true, 10, NULL, false,
   NOW(), NOW()),

  -- Ртуть (як “руда”, без хімії — ігровий ресурс)
  ('Ртутна руда', 'Рідкісний', 'ore_metal_rtut', 'ore_metal',
   'Незвична руда з “живим” блиском. З неї добувають ртуть для особливих сумішей та ремесла.',
   18, 99, 1, true, false,
   NULL, 0, 0, 0, 0, 1, NULL,
   false, 1, true, 18, NULL, false,
   NOW(), NOW())

ON CONFLICT (code) DO NOTHING;

COMMIT;
