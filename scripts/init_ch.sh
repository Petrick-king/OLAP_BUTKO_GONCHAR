#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CH_HTTP="${CH_HTTP:-http://localhost:8123}"

echo "Waiting for ClickHouse at $CH_HTTP ..."
for i in $(seq 1 60); do
  if curl -sf "$CH_HTTP/ping" >/dev/null; then
    break
  fi
  sleep 1
  if [[ $i -eq 60 ]]; then
    echo "ClickHouse не ответил. Запущен ли compose?" >&2
    exit 1
  fi
done

echo "Applying schema..."
# CH HTTP принимает по одному запросу удобнее через clickhouse-client в контейнере
if docker ps --format '{{.Names}}' | grep -q '^olap_clickhouse$'; then
  docker exec -i olap_clickhouse clickhouse-client --multiquery < "$ROOT/init/01_schema.sql"
else
  # fallback: по файлу целиком может не пройти — режем по ;
  python3 - <<PY
from pathlib import Path
import urllib.request
sql = Path("$ROOT/init/01_schema.sql").read_text()
parts = [p.strip() for p in sql.split(";") if p.strip() and not p.strip().startswith("--")]
for p in parts:
    req = urllib.request.Request("$CH_HTTP/", data=p.encode(), method="POST")
    urllib.request.urlopen(req)
print("schema via HTTP OK")
PY
fi

load_csv() {
  local table="$1"
  local file="$2"
  echo "Loading $table"
  if docker ps --format '{{.Names}}' | grep -q '^olap_clickhouse$'; then
    docker exec -i olap_clickhouse clickhouse-client --query "TRUNCATE TABLE IF EXISTS $table"
    docker exec -i olap_clickhouse clickhouse-client --query "INSERT INTO $table FORMAT CSVWithNames" < "$file"
  else
    curl -sf "$CH_HTTP/?query=TRUNCATE%20TABLE%20IF%20EXISTS%20${table// /%20}" >/dev/null || true
    curl -sf "$CH_HTTP/?query=INSERT%20INTO%20${table}%20FORMAT%20CSVWithNames" --data-binary @"$file" >/dev/null
  fi
}

DATA="$ROOT/data/sample"
load_csv "retail_dw.dim_store" "$DATA/dim_store.csv"
load_csv "retail_dw.dim_product" "$DATA/dim_product.csv"
load_csv "retail_dw.dim_date" "$DATA/dim_date.csv"
load_csv "retail_dw.fact_sales" "$DATA/fact_sales.csv"

echo "Counts:"
docker exec -i olap_clickhouse clickhouse-client --query "
SELECT 'dim_store' AS t, count() AS c FROM retail_dw.dim_store
UNION ALL SELECT 'dim_product', count() FROM retail_dw.dim_product
UNION ALL SELECT 'dim_date', count() FROM retail_dw.dim_date
UNION ALL SELECT 'fact_sales', count() FROM retail_dw.fact_sales
FORMAT Pretty
"

echo "OK: retail_dw готов."
