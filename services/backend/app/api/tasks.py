"""ETL 任务配置与字段选择向导（任务 4.1 / 4.2 / 4.3）。

- POST /api/tasks            保存任务配置（校验转换规则后落库；携带 ai_trace 时同路径写创建记录）
- GET  /api/tasks            列出
- GET  /api/tasks/{key}      详情
- POST /api/tasks/{key}/preview  预览字段映射（应用规则到样例）
- POST /api/wizard/prefill   用只读查询/API 调试的样例列预填向导草稿
"""

from __future__ import annotations

import contextlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_role
from app.core.config import get_settings
from app.llm.rag import skill_fingerprints
from app.llm.redact import redact_object, redact_text
from app.metadata import Catalog
from app.metadata.catalog import field_lineage_from_config
from app.models.access import Role
from app.models.task_config import FieldRule, TaskConfig, TransformKind
from app.scripting.gen_config import render_config_script
from app.scripts import ScriptStore
from app.store import Store
from app.transform import apply_rules, validate_rules


class LLMTrace(BaseModel):
    """AI 生成路径的创建留痕：仅由对话式建任务页携带，手工建任务时不传。

    `prompts` 为本次的提示词链（首次描述 + 历次修订指令）；`draft` 为草稿快照（诊断用）。
    """

    prompts: list[str] = Field(default_factory=list)
    draft: dict = Field(default_factory=dict)
    provider: str = ""
    model: str = ""
    skills: list[str] = Field(default_factory=list)
    conversation_id: str = ""


class TaskSaveRequest(BaseModel):
    project: str
    name: str
    config: TaskConfig
    task_key: str | None = None  # 提供则更新该任务（改配 -> 版本递增），否则新建
    ai_trace: LLMTrace | None = None  # 可选：AI 路径的提示词链，用于写创建记录


class PreviewRequest(BaseModel):
    sample: list[dict[str, object]] = Field(default_factory=list)


class PrefillRequest(BaseModel):
    project: str
    name: str
    source_conn_key: str
    target_conn_key: str
    source_object: str = ""
    target_object: str = ""
    columns: list[str] = Field(default_factory=list)


router = APIRouter()


def get_store() -> Store:
    return Store(get_settings().data_dir)


def get_scripts() -> ScriptStore:
    settings = get_settings()
    root = settings.scripts_root or f"{settings.data_dir}"
    return ScriptStore(root)


StoreDep = Annotated[Store, Depends(get_store)]
ScriptsDep = Annotated[ScriptStore, Depends(get_scripts)]
DeveloperDep = Annotated[object, Depends(require_role(Role.DEVELOPER))]


def _record_ai_creation(store: Store, trace: LLMTrace | None, *, actor: str, project: str,
                        name: str, status: str, task_key: str = "", error: str = "") -> None:
    """写一条 AI 路径的创建记录（成功/失败都写）；尽力而为，不阻断任务保存。

    提示词与草稿快照先经既有脱敏（DSN 口令与敏感键→占位符），明文凭证不落库。
    """
    if trace is None:
        return
    settings = get_settings()
    provider_name = trace.provider or settings.llm_provider
    model_name = trace.model
    skills = list(trace.skills or [])
    if not model_name or not skills:
        if not model_name:
            from app.llm.provider import get_provider

            with contextlib.suppress(KeyError):
                model_name = getattr(get_provider(provider_name), "model_name", "") or ""
            model_name = model_name or settings.deepseek_model
        if not skills:
            skills = skill_fingerprints()
    try:
        redacted_draft, _ = redact_object(trace.draft or {})
        store.save_generation_record(
            actor=actor, project=project, status=status, task_key=task_key, task_name=name,
            error=error, prompts=[redact_text(p) for p in (trace.prompts or [])],
            draft=redacted_draft, provider=provider_name, model=model_name,
            skills=skills, conversation_id=trace.conversation_id,
        )
    except Exception as e:  # noqa: BLE001 - 留痕失败不阻断任务保存
        store.log_event(actor=actor, project=project, action="llm_generation_record_failed",
                        detail={"name": name, "error": str(e)})


@router.post("/tasks")
def save_task(req: TaskSaveRequest, auth: DeveloperDep, store: StoreDep,
              scripts: ScriptsDep) -> dict:
    cfg = req.config
    cfg.project = req.project
    cfg.name = req.name
    # 校验转换规则（含表达式白名单）；失败则不保存，但先留下失败记录（AI 路径）
    violations = validate_rules(cfg.fields)
    if violations:
        detail = "; ".join(violations)
        _record_ai_creation(store, req.ai_trace, actor=auth.username, project=req.project,
                            name=req.name, status="failed", error=detail)
        raise HTTPException(status_code=422, detail=detail)
    key = store.save_task(cfg, task_key=req.task_key)
    store.log_event(actor="session-user", project=req.project, action="task_save",
                    detail={"task_key": key, "name": cfg.name, "version": cfg.version})
    # 静态推导字段级血缘并入库（任务 10.3）
    Catalog(get_settings().data_dir).store_lineage(key, field_lineage_from_config(cfg.model_dump()))
    # 保存配置成功后，自动从配置确定性生成执行脚本并写入 current/，
    # 使任务保存后即可点击「运行」执行，不再提示"尚无已确认脚本"。
    # 脚本生成是尽力而为：配置已落库，脚本写入失败仅记录日志，不阻断保存。
    script_key = key
    try:
        auto_script = render_config_script(cfg)
        scripts.replace_current(
            script_key, auto_script, generated_by="auto-config",
            config_snapshot=cfg.model_dump())
    except Exception as e:  # noqa: BLE001 - 尽力而为，失败不抛给用户
        store.log_event(actor="session-user", project=req.project, action="script_autogen_failed",
                        detail={"task_key": script_key, "name": cfg.name, "error": str(e)})
    # 任务已落库后再写成功记录，保证记录中的 task_key 一定指向真实任务
    _record_ai_creation(store, req.ai_trace, actor=auth.username, project=req.project,
                        name=req.name, status="success", task_key=key)
    return {"task_key": key, "config": cfg.model_dump()}


@router.get("/tasks")
def list_tasks(store: StoreDep, project: str | None = None) -> list:
    return store.list_tasks(project)


@router.delete("/tasks/{task_key}")
def delete_task(task_key: str, _: DeveloperDep, store: StoreDep, scripts: ScriptsDep) -> dict:
    if not store.delete_task(task_key):
        raise HTTPException(status_code=404, detail="任务不存在")
    # 顺带清理该任务脚本目录（尽力而为：脚本删除失败不阻断任务删除）
    try:
        scripts.remove_task(task_key)
    except Exception as e:  # noqa: BLE001 - 脚本清理为可选行为，失败仅记录日志
        store.log_event(actor="session-user", project="",
                        action="task_script_cleanup_failed",
                        detail={"task_key": task_key, "error": str(e)})
    store.log_event(actor="session-user", project="", action="task_delete",
                    detail={"task_key": task_key})
    return {"deleted": True, "task_key": task_key}


@router.get("/tasks/{task_key}")
def get_task(task_key: str, store: StoreDep) -> dict:
    try:
        return store.get_task(task_key).model_dump()
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")


@router.get("/tasks/{task_key}/history")
def task_history(task_key: str, store: StoreDep) -> list:
    try:
        return store.get_task_history(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")


@router.post("/tasks/{task_key}/rollback")
def task_rollback(task_key: str, _: DeveloperDep, store: StoreDep, version: int = 1) -> dict:
    try:
        cfg = store.rollback_task(task_key, version, saved_by="session-user")
    except KeyError:
        raise HTTPException(status_code=404, detail="version not found")
    store.log_event(actor="session-user", project=cfg.get("project", ""),
                    action="task_rollback", detail={"task_key": task_key, "version": version})
    return cfg


@router.post("/tasks/{task_key}/preview")
def preview_task(task_key: str, req: PreviewRequest, store: StoreDep) -> dict:
    try:
        cfg = store.get_task(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    violations = validate_rules(cfg.fields)
    if violations:
        raise HTTPException(status_code=422, detail="; ".join(violations))
    return {"mapping": apply_rules(cfg.fields, req.sample)}


@router.post("/wizard/prefill")
def wizard_prefill(req: PrefillRequest, store: StoreDep) -> dict:
    """用查询/调试样例列预填向导：字段按 direct 映射逐列生成。"""
    fields = [FieldRule(source=c, target=c, kind=TransformKind.DIRECT) for c in req.columns]
    cfg = TaskConfig(
        project=req.project, name=req.name,
        source_conn_key=req.source_conn_key, target_conn_key=req.target_conn_key,
        source_object=req.source_object, target_object=req.target_object,
        fields=fields,
    )
    return {"draft": cfg.model_dump()}