"""主数据管理 API（任务 13.2/13.3/13.4）。

- POST /api/mdm/models                    定义/更新实体字段与约束
- GET  /api/mdm/models                    列出实体
- POST /api/mdm/models/{entity}/records    新增记录（质量校验 + 唯一约束）
- GET  /api/mdm/models/{entity}/records    列出
- PUT  /api/mdm/models/{entity}/records/{id}  更新
- DELETE /api/mdm/models/{entity}/records/{id} 删除
- GET  /api/mdm/models/{entity}/records/{id}/history 变更历史
- POST /api/mdm/models/{entity}/sync      同步分发（记录 + 审计）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import require_role
from app.core.config import get_settings
from app.mdm.quality import FieldDef, MasterModel, validate_record
from app.models.access import Role
from app.store import Store


class ModelUpsert(BaseModel):
    entity: str
    label: str = ""
    fields: list[dict] = Field(default_factory=list)


class RecordIn(BaseModel):
    record: dict = Field(default_factory=dict)


router = APIRouter()


def get_store() -> Store:
    return Store(get_settings().data_dir)


StoreDep = Annotated[Store, Depends(get_store)]
DeveloperDep = Annotated[object, Depends(require_role(Role.DEVELOPER))]


def _model_of(store: Store, entity: str) -> MasterModel:
    model = store.get_model(entity)
    if not model:
        raise HTTPException(status_code=404, detail=f"实体未定义: {entity}")
    return model


def _check_unique(model: MasterModel, store: Store, entity: str,
                  record: dict, exclude_rid: str | None = None) -> None:
    """唯一约束：目标字段值已存在（排除待更新记录自身）则拒绝（13.3）。"""
    for f in model.fields:
        if not f.unique or record.get(f.name) in (None, ""):
            continue
        for existing in store.list_records(entity):
            if (existing.get(f.name) == record.get(f.name)
                    and existing.get("_id")
                    and existing["_id"] != exclude_rid):
                raise HTTPException(status_code=422, detail=f"{f.name} 已存在且要求唯一")


@router.post("/mdm/models", status_code=status.HTTP_201_CREATED)
def upsert_model(req: ModelUpsert, _: DeveloperDep, store: StoreDep) -> dict:
    model = MasterModel(entity=req.entity, label=req.label,
                        fields=[FieldDef(**f) for f in req.fields])
    store.save_model(model)
    return model.model_dump()


@router.get("/mdm/models")
def list_models(store: StoreDep) -> list:
    return [m.model_dump() for m in store.list_models()]


@router.post("/mdm/models/{entity}/records", status_code=status.HTTP_201_CREATED)
def create_record(entity: str, req: RecordIn, _: DeveloperDep, store: StoreDep) -> dict:
    model = _model_of(store, entity)
    violations = validate_record(model, req.record)
    if violations:
        raise HTTPException(status_code=422, detail="; ".join(violations))
    _check_unique(model, store, entity, req.record)
    rid = store.create_record(entity, req.record)
    return {"record_id": rid, "record": store.get_record(entity, rid)}


@router.get("/mdm/models/{entity}/records")
def list_records(entity: str, store: StoreDep) -> list:
    _model_of(store, entity)
    return store.list_records(entity)


@router.put("/mdm/models/{entity}/records/{record_id}")
def update_record(entity: str, record_id: str, req: RecordIn, _: DeveloperDep, store: StoreDep) -> dict:
    model = _model_of(store, entity)
    if store.get_record(entity, record_id) is None:
        raise HTTPException(status_code=404, detail="record not found")
    violations = validate_record(model, req.record)
    if violations:
        raise HTTPException(status_code=422, detail="; ".join(violations))
    _check_unique(model, store, entity, req.record, exclude_rid=record_id)
    store.update_record(entity, record_id, req.record)
    return {"record_id": record_id, "record": store.get_record(entity, record_id)}


@router.delete("/mdm/models/{entity}/records/{record_id}")
def delete_record(entity: str, record_id: str, _: DeveloperDep, store: StoreDep) -> dict:
    if not store.delete_record(entity, record_id):
        raise HTTPException(status_code=404, detail="record not found")
    return {"record_id": record_id, "deleted": True}


@router.get("/mdm/models/{entity}/records/{record_id}/history")
def record_history(entity: str, record_id: str, store: StoreDep) -> list:
    return store.record_history(entity, record_id)


@router.post("/mdm/models/{entity}/sync")
def sync_dispatch(entity: str, _: DeveloperDep, store: StoreDep) -> dict:
    _model_of(store, entity)  # 校验实体存在
    records = store.list_records(entity)
    store.log_event(actor="session-user", project=entity, action="mdm_sync",
                    detail={"entity": entity, "dispatched": len(records)})
    return {"entity": entity, "dispatched": len(records), "record_ids": [r.get("_id") for r in records]}