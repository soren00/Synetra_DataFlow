"""主数据管理（MDM）模型与质量校验（任务 13）。"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


class FieldDef(BaseModel):
    name: str
    type: str = "string"          # string/int/float/boolean/date/email/phone
    required: bool = False
    unique: bool = False
    format: str | None = None     # 如 email / phone / date(YYYY-MM-DD)


class MasterModel(BaseModel):
    entity: str                   # customer/product/org ...
    label: str = ""
    fields: list[FieldDef] = Field(default_factory=list)

    def field_names(self) -> set[str]:
        return {f.name for f in self.fields}


class MasterRecord(BaseModel):
    record_id: str
    entity: str
    record: dict[str, Any]
    updated_at: str = ""


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+?[0-9][0-9\- ]{6,}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_record(model: MasterModel | None, record: dict) -> list[str]:
    """返回质量违规列表（13.3）：非空、格式、类型。唯一校验在数据层做（需查库）。"""
    if model is None:
        return ["实体未定义"]
    known = model.field_names()
    violations: list[str] = []
    for f in model.fields:
        val = record.get(f.name)
        if f.required and (val is None or val == ""):
            violations.append(f"{f.name} 为必填")
            continue
        if val is None or val == "":
            continue
        if f.type == "int":
            if not isinstance(val, int) or isinstance(val, bool):
                violations.append(f"{f.name} 需整数")
        elif f.type == "float":
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                violations.append(f"{f.name} 需数值")
        elif f.type == "boolean" and not isinstance(val, bool):
            violations.append(f"{f.name} 需布尔")
        if f.format == "email" and (not isinstance(val, str) or not _EMAIL.match(val)):
            violations.append(f"{f.name} 邮箱格式非法")
        if f.format == "phone" and (not isinstance(val, str) or not _PHONE.match(val)):
            violations.append(f"{f.name} 电话格式非法")
        if f.format == "date" and (not isinstance(val, str) or not _DATE.match(val)):
            violations.append(f"{f.name} 日期格式非法(YYYY-MM-DD)")
    for key in record:
        if key == "_id":  # 内部主键，非业务字段
            continue
        if key not in known:
            violations.append(f"未知字段 {key}")
    return violations