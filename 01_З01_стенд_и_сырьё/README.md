# Стенд: ClickHouse (+ Metabase по профилю) для курса OLAP

Файл поднимает **тот самый стенд**, который проверяется на зачёте: ClickHouse
на MergeTree. Metabase добавлен отдельным профилем `bi` — на З01 он не нужен,
но на З10–З12 без него не обойтись, поэтому compose сразу готов и к нему.

## Быстрый старт

```bash
cd 01_З01_стенд_и_сырьё
docker compose up -d clickhouse          # только ClickHouse
bash ./scripts/init_ch.sh                 # создать БД и пользователя
curl http://localhost:8123/ping           # -> Ok.
```

> На Windows `bash` берётся из **Git Bash** или **WSL**. Если ни того, ни
> другого нет — выполните те же команды вручную: `docker exec -i olist_clickhouse
> clickhouse-client --query "..."` (см. `scripts/init_ch.sh`).

Metabase (нужен с З10):

```bash
docker compose --profile bi up -d
# -> http://localhost:3000   (логин / пароль: см. .env рядом)
```

Остановить / снести с данными:

```bash
docker compose down        # контейнеры остановлены, тома сохранены
docker compose down -v     # контейнеры и данные удалены
```

## Порты

| Порт | Что это | Кто ходит |
|------|---------|-----------|
| `8123` | HTTP-интерфейс ClickHouse | `curl`, Metabase, `clickhouse_connect` |
| `9000` | нативный протокол (для клиента `clickhouse-client`) | `clickhouse-client`, `clickhouse_connect` |
| `3000` | Metabase (профиль `bi`) | браузер |

## Доступ

| Что | Значение |
|-----|----------|
| хост | `localhost` |
| пользователь | `olist` |
| пароль | `olist` |
| база | `olist` |

## Где лежит сырьё

```
OLAP/
├── raw data/              ← оригинальный архив Olist (не изменялся)
└── data/raw/              ← рабочая папка сырья (junction на raw data)
    └── *.csv              ← 9 CSV, описание в 01_З01_стенд_и_сырьё/data/raw/ОПИСАНИЕ_ФАЙЛОВ.md
```

Скрипты из `03`–`07` читают сырьё по пути `../../data/raw/*.csv` относительно
своей папки. Путь можно переопределить переменной окружения `RAW_DIR`.

## Smoke-проверка DuckDB

DuckDB — локальная файловая база, она нужна как «лаборатория» (З03–З06)
до того, как появится серверный ClickHouse (З07).

```bash
pip install duckdb
python ./scripts/smoke_duckdb.py
```

Скрипт откроет `../../data/raw`, посчитает строки и деньги в каждом файле и
напечатает сводку. Ничего не создаёт — только читает.

## Снятие доказательств

```bash
python ./scripts/ping_evidence.py
```

Собирает в `evidence/clickhouse_ping.txt`: состояние контейнера,
`ping → Ok.`, версию сервера, список таблиц в `olist` и контрольные
числа (в том числе `GMV_delivered`, загруженный на З07).

## ФИО, группа, домен

| Поле | Значение |
|------|----------|
| ФИО | *подставь своё* |
| Группа | *подставь свою* |
| Домен проекта | **E-commerce маркетплейс (розничная онлайн-торговля)** |
| Источник данных | Brazilian E-Commerce Public Dataset by Olist (2016–2018) |
| Главная метрика | Оборот доставленных заказов, `SUM(amount)` — см. [З02 `passport.md`](../02_З02_паспорт_star/passport.md). В сырье поле называется `price`; в витрине — `amount` (см. Приложение В паспорта) |

## Что сдано по З01

- стенд поднимается двумя командами из этого README;
- `data/raw/` — 9 CSV, описание в `data/raw/ОПИСАНИЕ_ФАЙЛОВ.md`;
- домен зафиксирован;
- доказательства — в [`evidence/`](evidence/).