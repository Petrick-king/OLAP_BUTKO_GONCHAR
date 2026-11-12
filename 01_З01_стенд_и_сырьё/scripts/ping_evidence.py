#!/usr/bin/env python3
"""Снимает доказательства З01: стенд отвечает + сырьё читается.

Запуск:  python 01_З01_стенд_и_сырьё/scripts/ping_evidence.py
Пишет:   01_З01_стенд_и_сырьё/evidence/clickhouse_ping.txt
"""
from __future__ import annotations

import base64
import subprocess
import sys
import urllib.request
from pathlib import Path

CH_HOST = "localhost"
CH_PORT = 8123
USER, PASSWORD, DB = "olist", "olist", "olist"

WORK = Path(__file__).resolve().parents[1]
OUT = WORK / "evidence" / "clickhouse_ping.txt"


def http(sql: str, auth: bool = True) -> str:
    """Выполняет запрос к ClickHouse по HTTP. Ошибка не роняет скрипт,
    а попадает в отчёт строкой — снимок доказательств должен собраться
    всегда, даже если что-то сломалось."""
    req = urllib.request.Request(f"http://{CH_HOST}:{CH_PORT}/?database={DB}")
    req.data = sql.encode("utf-8")
    if auth:
        token = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8").strip()
    except Exception as e:                                   # noqa: BLE001
        body = ""
        if hasattr(e, "read"):
            try:
                body = e.read().decode("utf-8", "replace").strip()
            except Exception:                                # noqa: BLE001
                pass
        return f"!! {type(e).__name__}: {e}\n{body}".strip()


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=60).stdout.strip()
    except Exception as e:                                   # noqa: BLE001
        return f"(не выполнилось: {e})"


def main() -> None:
    lines: list[str] = []
    add = lines.append

    add("# Доказательство, что стенд жив (З01)")
    add("")
    add("Снято скриптом `01_З01_стенд_и_сырьё/scripts/ping_evidence.py`.")
    add("")

    add("## 1. Docker")
    add("### docker compose ps")
    add("```")
    add(sh(["docker", "compose", "ps"]))
    add("```")
    add("")
    add("### образ ClickHouse")
    add("```")
    add(sh(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}  {{.ID}}",
            "clickhouse/clickhouse-server"]))
    add("```")
    add("")

    add("## 2. ClickHouse отвечает")
    add("### curl http://localhost:8123/ping")
    add("```")
    try:
        with urllib.request.urlopen(
                f"http://{CH_HOST}:{CH_PORT}/ping", timeout=10) as r:
            add(r.read().decode("utf-8").strip())
    except Exception as e:                                   # noqa: BLE001
        add(f"!! не отвечает: {e}")
    add("```")
    add("")
    add("### SELECT version(), currentUser(), currentDatabase()  (HTTP, пользователь olist)")
    add("```")
    add(http("SELECT version(), currentUser(), currentDatabase()"))
    add("```")
    add("")
    add("### count() из ClickHouse — база создана и отвечает на запросы")
    add("```sql")
    add("SELECT 'таблиц в базе olist' AS what, count() AS n")
    add("FROM system.tables WHERE database = 'olist';")
    add("```")
    add("```")
    add(http("SELECT 'таблиц в базе olist', count() "
             "FROM system.tables WHERE database = 'olist'"))
    add(http("SELECT name, engine FROM system.tables WHERE database = 'olist' "
             "ORDER BY name"))
    add("```")
    add("")
    add("### данные в ClickHouse (загружены на З07)")
    add("```")
    add(http("SELECT 'fact_sales', count() FROM olist.fact_sales"))
    add(http("SELECT 'GMV_delivered', toString(round(sum(amount), 2)) "
             "FROM olist.fact_sales WHERE order_status = 'delivered'"))
    add("```")
    add("")

    add("## 3. DuckDB читает сырьё")
    add("```")
    add(sh([sys.executable, str(WORK / "scripts" / "smoke_duckdb.py")]))
    add("```")
    add("")
    add("Полный вывод — в `evidence/duckdb_smoke.txt`.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Записано: {OUT}")


if __name__ == "__main__":
    main()