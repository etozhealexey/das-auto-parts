# Das-Auto-Parts

Оптовая платформа автозапчастей для СТО и магазинов. FastAPI + PostgreSQL, серверные шаблоны Jinja2.
Дизайн: макет на Tilda (эталонная вёрстка главной — `design-ref/home.html`).

## Быстрый старт

```bash
cp .env.example .env            # при первом запуске
docker compose up -d db         # либо локальный PostgreSQL, см. ниже
uv sync
uv run alembic upgrade head
uv run python -m scripts.seed
uv run uvicorn app.main:app --reload
```

Сайт: http://localhost:8000, JSON API: http://localhost:8000/docs

Локальный PostgreSQL вместо Docker:

```bash
createuser -s dasauto && psql -c "alter user dasauto password 'dasauto'" && createdb -O dasauto dasauto
```

## Цены с autoeuro.ru

Цены берутся через официальный API v2 (https://api.autoeuro.ru/doc/v2). Ключ выдаётся юрлицам
в личном кабинете shop.autoeuro.ru (раздел API). В `.env`:

```
PRICE_PROVIDER=autoeuro
AUTOEURO_API_KEY=<ключ>
AUTOEURO_DELIVERY_KEY=        # пусто — возьмётся первый вариант из /get_deliveries
```

Без ключа работает `PRICE_PROVIDER=demo` с тестовыми предложениями, чтобы проверять интерфейс.

Схема: поиск по артикулу → `search_brands` (какие бренды выпускают деталь) → `search_items` по каждому
бренду (цена, наличие, срок, аналоги). Один поиск пользователя = 1 + число брендов запросов к API
(обычно 3–5). Исходные предложения поставщика кэшируются в таблице `price_offers` на
`PRICE_CACHE_TTL_MINUTES` минут; лимит API — 3000 поисков в сутки, поэтому кэш важен.

Ценовая политика (`app/services/pricing/policy.py`) применяется при выдаче, кэш хранит исходные цены:

```
PRICE_MARKUP_PERCENT=15   # цена клиента = цена поставщика × 1.15, округление до копейки
DELIVERY_EXTRA_DAYS=1     # срок клиента = срок поставщика + 1 день (delivery_time и delivery_time_max)
```

Цена поставщика наружу не отдаётся: шаблоны показывают только клиентскую, JSON API использует `OfferOut`.

Поставщик в выдаче — это тип предложения autoeuro: собственный склад Авто-Евро (с названием склада),
партнёрское предложение или официальный дилер. Имена партнёров API не раскрывает.

Проверить ключ и посмотреть варианты получения (delivery_key) для `.env`:

```bash
uv run python -m scripts.autoeuro_check 0446533471
```

Сроки приходят в местном времени точки получения; выбранная точка определяет часовой пояс.
В `.env` выбран пункт в Санкт-Петербурге.

## Регистрация, корзина, заказы

- Регистрация и вход: `/register`, `/login`, выход `POST /logout`. Пароли хэшируются scrypt (стандартная
  библиотека), сессия — подписанная cookie (`SECRET_KEY` в `.env`, обязательно сменить на проде).
- Корзина работает и без входа (токен в cookie-сессии); после входа анонимная корзина переезжает к клиенту.
  Цена позиции берётся из кэша предложений на сервере, а не из запроса браузера.
- Оформление `/checkout` доступно после входа: самовывоз или доставка, контакт, комментарий. Заказ получает
  номер `DAP-#####`, позиции фиксируют цену поставщика и цену клиента. Оплата по счёту, онлайн-оплаты нет.
- Каждый заказ клиента передаётся в autoeuro от нашего аккаунта (`POST /create_order/`, позиции по `offer_key`).
  Отправка идёт в фоне сразу после оформления; неудачные попытки повторяет цикл раз в
  `SUPPLIER_ORDERS_RETRY_MINUTES` минут (до `SUPPLIER_ORDERS_MAX_ATTEMPTS` раз, заказы не старше 3 дней),
  вручную: `uv run python -m scripts.resend_orders`. Статус и номер у поставщика видны на странице заказа.
- Защита: `SUPPLIER_ORDERS_ENABLED=false` по умолчанию. Включать только на боевом сервере: каждая отправка
  создаёт настоящий заказ и списывает баланс в autoeuro. Плательщик — `AUTOEURO_PAYER_KEY` или первый из `/get_payers`.
- Демо-клиент для локальной разработки создаётся сидом, логин и пароль в `scripts/seed.py`.

## Структура

```
app/
  main.py              приложение FastAPI
  config.py            настройки из .env
  db.py, models.py     подключение и модели SQLAlchemy
  routers/pages.py     HTML-страницы
  routers/auth.py      регистрация, вход, выход
  routers/cart.py      корзина и оформление заказа
  routers/api.py       JSON API (/api/v1)
  services/auth.py     пароли и проверка регистрации
  services/cart.py     корзина, слияние после входа, создание заказа
  services/pricing/    источники цен: base (контракт), autoeuro, demo, service (кэш)
  templates/, static/  шаблоны и стили по макету
alembic/               миграции
scripts/seed.py        начальные данные
```
