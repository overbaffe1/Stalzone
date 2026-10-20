# SZMarket — STALCRAFT auction analytics (EAPI + craft/arbitrage)

Десктопный анализатор аукциона STALCRAFT: тянет цены из официального EAPI,
копит историю в SQLite, считает **что выгодно скрафтить** и **что выгодно
перепродать** с учётом комиссии аукциона 5%.

- API: официальный STALCRAFT EAPI (`https://eapi.stalcraft.net`), secret-based auth
  (`Client-Id` + `Client-Secret`, без токенов).
- База предметов: [EXBO-Studio/stalzone-database](https://github.com/EXBO-Studio/stalzone-database)
  (названия/иконки по `itemId`, автообновление при патчах).
- Рецепты: формат совместим с `konti1ks/stalzone-main` (`data/recipes.json`).
- Только чтение: приложение никогда ничего не покупает/не выставляет.

## Быстрый старт (твой ПК, Windows)

```powershell
# 1. Клонируй репозиторий и перейди в него
git clone https://github.com/overbaffe1/Stalzone.git
cd Stalzone

# 2. Зависимости UI
pip install -r requirements-ui.txt

# 3. Креды: скопируй .env.example в .env и впиши свой Client Secret
copy .env.example .env
notepad .env

# 4. Запуск окна рядом с игрой
python -m ui.app
```

В окне: сверху регион (`RU/EU/NA/SEA` — **регион твоего персонажа**, не лаунчера!)
и список `itemId` через запятую → «🔄 Refresh prices» → «💰 Top deals» / «🧪 Top crafts».

Где брать `itemId`: поиск по названию в
[stalzone-database](https://github.com/EXBO-Studio/stalzone-database)
(`ru/items/.../*.json`, имя файла = ID). Примеры: `y1q9` — «Гадюка»,
`g4lk0` — военная аптечка, `96430` — аптечка индивидуальная.

## Тесты

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

Unit-тесты — на фикстурах, без сети. Живые проверки EAPI гоняются в CI:

- `tools/smoke_eapi.py` — публичное демо-API (без секретов, всегда в CI);
- `tools/smoke_prod.py` — продакшн EAPI, только если заданы Secrets
  `STALCRAFT_CLIENT_ID` / `STALCRAFT_CLIENT_SECRET` / `STALCRAFT_REGION`
  (или `STALZONE_*` — оба имени поддерживаются). Два места на выбор:
  Settings → Secrets and variables → Actions → **Repository secrets**,
  либо Environment **`Stalzone_config`** (для него в CI есть отдельная
  джоба `prod-smoke`). Без них — чистый SKIP.

## Структура

```
szmarket/        ядро (только stdlib: urllib + sqlite3)
  config.py      .env/ENV настройки, константы (комиссия 5%)
  eapi.py        EAPI-клиент: lots/history, пагинация, 429/retry, паузы
  store.py       SQLite: снапшоты лотов (SHA-дедуп) + история продаж
  craft.py       экономика крафта: cost/revenue/profit/ROI
  arbitrage.py   P25/median/P75, ликвидность, скоринг сделок
  catalog.py     загрузчик stalzone-database (имена предметов)
ui/app.py        десктоп MVP (CustomTkinter)
data/
  recipes.json   рецепты убежища (стартер; заменить реальными!)
tools/
  smoke_eapi.py  живой smoke против демо-API (CI)
  smoke_prod.py  живой smoke против прод-API (CI, нужны Secrets)
tests/           pytest, без сети
agent_toolkit/   плейбук агента (GitHub Actions как вычислитель)
```

## Математика (коротко)

- Комиссия 5% с продажи → безубыток: `продажа = покупка / 0.95` (≈×1.0526).
- Крафт в плюс: `productCount × цена_продажи × 0.95 − Σ(ингредиенты) > 0`.
- Сделка: покупка лота + продажа по медиане истории даёт маржу ≥5%,
  при ликвидности (продаж/день) выше порога.

## Дорожная карта

1. [x] Каркас: EAPI-клиент + SQLite + математика + тесты + CI
2. [ ] Узнать реальную схему ответов lots/history из smoke-отчётов, подправить парсинг
3. [ ] Заполнить `data/recipes.json` реальными рецептами убежища
4. [ ] Каталог предметов: имена в UI (подтянуть stalzone-database)
5. [ ] Хоткеи/автодобавление предметов, вотчлист
6. [ ] Опционально: Telegram-сигналы + облачный сбор 24/7 (как stalzovmarket)
7. [ ] Опционально: OCR-хелпер для цен «с рук» из чата
