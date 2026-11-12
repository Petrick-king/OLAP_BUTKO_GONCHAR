#!/usr/bin/env bash
# Инициализация стенда: создать базу `olist` и пользователя `olist`,
# снять лимит времени на тяжёлые учебные выборки.
#
# Запуск:  bash ./scripts/init_ch.sh
# Повторный запуск безопасен: все объекты создаются через IF NOT EXISTS.
#
# Пользователь `default` в свежих образах ClickHouse закрыт паролем, поэтому
# bootstrap делаем через `docker exec` (идём внутрь контейнера), а дальше
# уже работаем по HTTP от `olist`.
set -euo pipefail

CONTAINER="${CONTAINER:-olist_clickhouse}"
CH_URL="${CH_URL:-http://localhost:8123}"
CH_USER="${CH_USER:-olist}"
CH_PASS="${CH_PASS:-olist}"
CH_DB="${CH_DB:-olist}"

# chc <query> — SQL через нативный клиент ВНУТРИ контейнера (спискает default).
chc() { docker exec -i "$CONTAINER" clickhouse-client --query "$1"; }

# chu <query> — SQL по HTTP от пользователя olist (то, как пойдут Metabase и clickhouse_connect).
chu() { curl -fsS -u "${CH_USER}:${CH_PASS}" "${CH_URL}/?database=${CH_DB}" --data-binary "$1"; }

echo ">> Ждём готовности ClickHouse на ${CH_URL} ..."
for _ in $(seq 1 60); do
  curl -fsS "${CH_URL}/ping" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "${CH_URL}/ping" >/dev/null || { echo "!! ClickHouse не отвечает на ${CH_URL}"; exit 1; }

echo ">> Создаём базу ${CH_DB} ..."
chc "CREATE DATABASE IF NOT EXISTS ${CH_DB}"

echo ">> Создаём пользователя ${CH_USER} ..."
chc "CREATE USER IF NOT EXISTS ${CH_USER} IDENTIFIED WITH plaintext_password BY '${CH_PASS}'"
chc "GRANT ALL PRIVILEGES ON ${CH_DB}.* TO ${CH_USER}"
# З08: чтобы читать раскладку таблицы по партициям из-под рабочего пользователя.
# Без этого system.parts отдаёт «Not enough privileges».
chc "GRANT SELECT ON system.parts TO ${CH_USER}"
chc "ALTER USER ${CH_USER} SETTINGS max_execution_time = 0"

echo
echo ">> Готово. Проверка:"
printf '  ping     : '; curl -fsS "${CH_URL}/ping"; echo
printf '  версия   : '; chc "SELECT version()"
printf '  база     : '; chc "SELECT name FROM system.databases WHERE name = '${CH_DB}'"
printf '  контекст : '; chu "SELECT currentUser(), currentDatabase()"

echo
echo "Дальше:"
echo "  clickhouse-client --host localhost --port 9000 --user ${CH_USER} --password ${CH_PASS} --database ${CH_DB}"
echo "  python ./scripts/smoke_duckdb.py   # локальная лаборатория DuckDB"