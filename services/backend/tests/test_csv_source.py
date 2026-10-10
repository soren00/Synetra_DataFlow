"""任务 2.4 验收：指向 CSV 后可读取表结构（列/类型/行数）。"""

from __future__ import annotations

from pathlib import Path

from app.sources.csv_reader import read_csv_structure


def test_read_csv_structure(tmp_path: Path):
    p = tmp_path / "orders.csv"
    p.write_text(
        "id,name,qty,unit_price,active\n"
        "1,apple,2,9.5,true\n"
        "2,banana,5,3.0,false\n"
        "3,cherry,7,12.75,true\n",
        encoding="utf-8",
    )
    struct = read_csv_structure(str(p))
    assert struct["row_count"] == 3
    types = {c["name"]: c["type"] for c in struct["columns"]}
    assert types == {
        "id": "number",
        "name": "string",
        "qty": "number",
        "unit_price": "number",
        "active": "boolean",
    }
    assert struct["preview"][0][0] == "1"


def test_missing_csv_raises(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        read_csv_structure(str(tmp_path / "nope.csv"))


def test_mixed_column_is_string(tmp_path: Path):
    p = tmp_path / "mix.csv"
    p.write_text("code\nA1\nB2\nC\n")
    types = {c["name"]: c["type"] for c in read_csv_structure(str(p))["columns"]}
    assert types["code"] == "string"