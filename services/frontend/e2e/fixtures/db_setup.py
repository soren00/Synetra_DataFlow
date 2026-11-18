"""E2E 数据源/目标库准备脚本。

只创建/重建 E2E 专属库 `synetra_e2e_src` 与 `synetra_e2e_dst`，
不触碰 synetra / aitest / synetra_test 等既有库。
用法：python db_setup.py [--drop]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import pymysql

HOST = os.environ.get("E2E_MYSQL_HOST", "127.0.0.1")
PORT = int(os.environ.get("E2E_MYSQL_PORT", "3306"))
USER = os.environ.get("E2E_MYSQL_USER", "root")
PASSWORD = os.environ.get("E2E_MYSQL_PASSWORD", "12345")

SRC = "synetra_e2e_src"
DST = "synetra_e2e_dst"

ORDERS_DDL = """
CREATE TABLE {db}.e2e_orders (
  id INT PRIMARY KEY,
  order_no VARCHAR(32) NOT NULL,
  customer VARCHAR(64) NOT NULL,
  amount DECIMAL(10, 2) NOT NULL,
  status VARCHAR(16) NOT NULL,
  created_at DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CUSTOMERS_DDL = """
CREATE TABLE {db}.e2e_customers (
  id INT PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  city VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CITIES = ["上海", "北京", "深圳", "杭州", "成都"]
STATUSES = ["paid", "pending", "refunded"]


def connect(with_db: bool = False):
    return pymysql.connect(
        host=HOST, port=PORT, user=USER, password=PASSWORD,
        database="information_schema" if with_db else None, charset="utf8mb4",
    )


def reset() -> None:
    conn = connect()
    cur = conn.cursor()
    for db in (SRC, DST):
        cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
        cur.execute(f"CREATE DATABASE `{db}` DEFAULT CHARSET utf8mb4")
    cur.execute(ORDERS_DDL.format(db=SRC))
    cur.execute(CUSTOMERS_DDL.format(db=SRC))
    cur.execute(ORDERS_DDL.format(db=DST))
    cur.execute(CUSTOMERS_DDL.format(db=DST))

    rows = []
    base = date(2026, 1, 1)
    for i in range(1, 26):
        rows.append((
            i, f"SO{i:05d}", f"客户{i % 7 + 1}",
            round(100 + i * 37.5, 2), STATUSES[i % 3], base + timedelta(days=i),
        ))
    cur.executemany(
        f"INSERT INTO {SRC}.e2e_orders (id, order_no, customer, amount, status, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)", rows,
    )
    cur.executemany(
        f"INSERT INTO {SRC}.e2e_customers (id, name, city) VALUES (%s, %s, %s)",
        [(i, f"客户{i}", CITIES[(i - 1) % len(CITIES)]) for i in range(1, 8)],
    )
    conn.commit()
    conn.close()
    print(f"prepared {SRC} (25 orders / 7 customers) and empty {DST}")


def drop() -> None:
    conn = connect()
    cur = conn.cursor()
    for db in (SRC, DST, "synetra_e2e"):
        cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
    conn.commit()
    conn.close()
    print("dropped synetra_e2e_src / synetra_e2e_dst / synetra_e2e")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true")
    ns = ap.parse_args()
    try:
        drop() if ns.drop else reset()
    except Exception as exc:  # noqa: BLE001
        print(f"db_setup failed: {exc}", file=sys.stderr)
        raise
