-- 1) Додаємо колонку для shop-матеріалів (флакони/основи)
ALTER TABLE craft_materials
ADD COLUMN IF NOT EXISTS appearance_text text;

-- 2) Заповнюємо тільки shop-інгредієнти алхімії
UPDATE craft_materials
SET appearance_text =
  CASE code
    WHEN 'alch_flask_simple' THEN
      'Невеликий скляний флакон із товстими стінками. Скло трохи каламутне, всередині дрібні бульбашки. Закоркований деревʼяною пробкою, перевʼязаною мотузкою.'
    WHEN 'alch_flask_reinforced' THEN
      'Міцний флакон із прозорого скла, оперезаний тонким металевим дротом. Пробка щільна, залита темним воском. На склі видно сліди багаторазового використання.'
    WHEN 'alch_flask_etched' THEN
      'Витончений флакон із чистого скла. По поверхні — дрібні рунічні насічки, що ледь блимають у світлі. Пробка ущільнена темною смолою.'

    WHEN 'alch_base_water' THEN
      'Прозора, кристально чиста рідина без запаху. На світлі виглядає “порожньою”, ніби джерельна вода з глибини.'
    WHEN 'alch_base_spirit' THEN
      'Прозора рідина з легким блиском. Має різкий чистий запах. Якщо збовтати — швидко вкривається дрібними бульбашками.'
    WHEN 'alch_base_resin' THEN
      'Густа темнувата рідина з бурштиновим відтінком. Повільно стікає зі стінок, лишаючи тонку липку плівку. Пахне смолою й димом.'
    ELSE appearance_text
  END
WHERE source_type = 'shop'
  AND code IN (
    'alch_flask_simple','alch_flask_reinforced','alch_flask_etched',
    'alch_base_water','alch_base_spirit','alch_base_resin'
  );


-- 3) Додаємо колонку для зіль (items)
ALTER TABLE items
ADD COLUMN IF NOT EXISTS appearance_text text;

-- 4) (приклад) заповнюємо для твого тестового зілля
UPDATE items
SET appearance_text =
  'Бурштинова рідина з легкою каламуттю. На дні — тонкий травʼяний осад. Пахне теплим відваром і смолою; на світлі рідина грає золотистими відблисками.'
WHERE category = 'potion'
  AND code = 'potion_healing_t1';