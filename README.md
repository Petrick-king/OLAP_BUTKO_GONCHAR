# Учебный стенд OLAP
Гончар Александр Иванович
Бутько Пётр Сергеевич

Домен: экзаменационные оценки regent exam 

**Исходные данные**
Все сырые файлы расположены в директории 'data/raw/'

**Стек:** ClickHouse + Metabase (Docker) · DuckDB — локально у студента.

## Быстрый старт

```bash
cd Учебные материалы/_stand
docker compose up -d
chmod +x scripts/init_ch.sh
./scripts/init_ch.sh   # DDL + sample CSV → retail_dw (обязательный шаг)
```


## Порты

| Сервис | Порт |
|--------|------|
| ClickHouse HTTP | 8123 |
| ClickHouse native | 9000 |
| Metabase | 3000 |

