"""任务 3.1 守卫：仅单条 SELECT，拒绝写/DDL/多语句；字符串内关键词不误判。"""

from __future__ import annotations

from app.queries.guard import validate_read_only


def test_select_is_ok():
    assert validate_read_only("SELECT id, name FROM orders") == []


def test_select_constant_ok():
    assert validate_read_only("SELECT 1") == []


def test_insert_rejected():
    assert validate_read_only("INSERT INTO orders VALUES (1,'a',1,true)") != []


def test_update_rejected():
    assert validate_read_only("UPDATE orders SET qty = 0") != []


def test_drop_rejected():
    assert validate_read_only("DROP TABLE orders") != []


def test_multi_statement_rejected():
    assert "仅允许单条 SELECT" in " ".join(validate_read_only("SELECT 1; SELECT 2"))


def test_delete_rejected():
    assert validate_read_only("DELETE FROM orders") != []


def test_keyword_in_string_not_matched():
    assert validate_read_only("SELECT 'UPDATE orders' AS note") == []


def test_unterminated_union_head_rejected():
    assert validate_read_only("WITH x AS (SELECT 1) UPDATE orders SET qty=0") != []