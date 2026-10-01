"""Smoke-test DuckDB: те же CSV, что грузятся в ClickHouse."""
from pathlib import Path
import duckdb

root = Path(__file__).resolve().parents[1]
data = root / "data" / "sample"
con = duckdb.connect(str(root / "retail_local.duckdb"))

for name in ("dim_store", "dim_product", "dim_date", "fact_sales"):
    path = data / f"{name}.csv"
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM read_csv_auto('{path.as_posix()}')")

row = con.execute(
    """
    SELECT s.region, sum(f.amount) AS revenue
    FROM fact_sales f
    JOIN dim_store s ON f.store_id = s.store_id
    GROUP BY 1
    ORDER BY 2 DESC
    """
).fetchall()
print("Revenue by region:")
for r in row:
    print(f"  {r[0]}: {r[1]}")
print("OK →", root / "retail_local.duckdb")
