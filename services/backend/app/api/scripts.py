"""脚本生成/手写/dry-run/确认生效/版本化 路由（任务 5.3/5.4/5.5）。

- POST /api/scripts/generate                   LLM 生成脚本（脱敏+RAG；返回脚本与静态校验结果）
- POST /api/scripts/{key}/dry-run              dry-run（不写目标）并做静态校验
- POST /api/scripts/{key}/llm-confirm          dry-run 通过且静态校验通过后写入 current/ + 版本记录
- POST /api/scripts/handwritten                手写路径保存（可选语法校验）
- GET  /api/scripts/{key}/current              当前生效脚本
- GET  /api/scripts/{key}/versions             版本记录（最新在前）
- POST /api/scripts/{key}/rollback            回滚到指定版本
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_role
from app.core.config import get_settings
from app.llm.generator import ScriptGenerator
from app.llm.provider import ProviderNotConfigured, ProviderRequestError, get_provider
from app.models.access import Role
from app.scripting.checks import static_validate, syntax_check
from app.scripting.executor import dry_run_script
from app.scripts import ScriptStore
from app.security.sandbox import (
    build_worker_profile,
    validate_script_network,
)
from app.store import Store


class GenerateRequest(BaseModel):
    task_key: str
    provider: str = ""


class DryRunRequest(BaseModel):
    script: str


class ConfirmRequest(BaseModel):
    script: str
    generated_by: str = "session-user"


class HandwrittenRequest(BaseModel):
    task_key: str
    script: str
    project: str
    name: str = ""
    skip_syntax_check: bool = False
    generated_by: str = "session-user"


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


def _resolve_provider(name: str):
    if not name:
        name = get_settings().llm_provider
    try:
        return get_provider(name)
    except KeyError:
        raise HTTPException(status_code=503, detail=f"LLM provider 未就绪: {name}")


@router.post("/scripts/generate")
def llm_generate(req: GenerateRequest, store: StoreDep, scripts: ScriptsDep) -> dict:
    try:
        cfg = store.get_task(req.task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    provider = _resolve_provider(req.provider)
    try:
        result = ScriptGenerator(provider).generate(cfg.model_dump())
    except ProviderNotConfigured as e:
        raise HTTPException(status_code=503, detail=f"LLM 未配置：{e}")
    except ProviderRequestError as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{e}")
    return {
        "task_key": req.task_key,
        "script": result.script,
        "valid": result.valid,
        "violations": result.violations,
        "redacted_config": result.redacted,
    }


@router.post("/scripts/{task_key}/dry-run")
def script_dry_run(task_key: str, req: DryRunRequest, store: StoreDep) -> dict:
    try:
        cfg = store.get_task(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    violations = static_validate(req.script)
    if not violations:
        res = dry_run_script(req.script, cfg.model_dump())
        return {
            "static_ok": True, "violations": [],
            "dry_run_ok": res.ok,
            "report": res.report, "total_records": res.total_records(),
            "stderr": res.stderr,
        }
    return {"static_ok": False, "violations": violations, "dry_run_ok": False,
            "report": [], "total_records": 0, "stderr": ""}


@router.post("/scripts/{task_key}/llm-confirm")
def llm_confirm(task_key: str, req: ConfirmRequest, _: DeveloperDep,
                store: StoreDep, scripts: ScriptsDep) -> dict:
    """dry-run 通过且人工确认后写入 current/；校验失败不可生效。"""
    try:
        cfg = store.get_task(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    violations = static_validate(req.script)
    if violations:
        raise HTTPException(status_code=422, detail="; ".join(violations))
    res = dry_run_script(req.script, cfg.model_dump())
    if not res.ok:
        raise HTTPException(status_code=422, detail=f"dry-run 失败: {res.stderr.strip()}")
    version = scripts.replace_current(
        task_key, req.script, generated_by=req.generated_by,
        config_snapshot=cfg.model_dump())
    store.log_event(actor=req.generated_by, project=cfg.project,
                    action="script_confirm", detail={"task_key": task_key, "version": version})
    return {"task_key": task_key, "version": version, "total_records": res.total_records()}


@router.post("/scripts/handwritten")
def handwritten_save(req: HandwrittenRequest, _: DeveloperDep,
                     store: StoreDep, scripts: ScriptsDep) -> dict:
    cfg = None
    try:
        cfg = store.get_task(req.task_key)
    except KeyError:
        pass
    if not req.skip_syntax_check:
        errs = syntax_check(req.script)
        if errs:
            raise HTTPException(status_code=422, detail="; ".join(errs))
    # 6.3 网络白名单：脚本出网地址必须落在任务声明端点内，否则拒绝保存并记录
    if cfg is not None:
        profile = build_worker_profile(store, cfg)
        net_violations = validate_script_network(profile, req.script)
        if net_violations:
            store.log_event(actor=req.generated_by, project=req.project,
                            action="handwritten_network_blocked",
                            detail={"task_key": req.task_key, "violations": net_violations})
            raise HTTPException(status_code=422, detail="; ".join(net_violations))
    version = scripts.replace_current(
        req.task_key, req.script, generated_by=req.generated_by,
        config_snapshot=cfg.model_dump() if cfg else {"project": req.project, "name": req.name})
    store.log_event(actor=req.generated_by, project=req.project, action="handwritten_save",
                    detail={"task_key": req.task_key, "version": version})
    return {"task_key": req.task_key, "version": version}


@router.post("/scripts/{task_key}/sandbox")
def sandbox_profile(task_key: str, store: StoreDep) -> dict:
    """返回手写任务的安全沙箱画像：运行身份/白名单/配额/降级说明（任务 6.x）。"""
    try:
        cfg = store.get_task(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    p = build_worker_profile(store, cfg)
    return {
        "task_key": p.task_key, "project": p.project,
        "run_as_user": p.run_as_user, "worker_group": p.worker_group,
        "conn_keys": sorted(p.conn_keys),
        "allowed_endpoints": [str(e) for e in p.allowed_endpoints],
        "quota": {
            "cpu_seconds": p.quota.cpu_seconds,
            "memory_mb": p.quota.memory_mb,
            "disk_mb": p.quota.disk_mb,
            "timeout_seconds": p.quota.timeout_seconds,
        },
        "degraded": p.degraded,
        "notes": p.notes,
    }


@router.get("/scripts/{task_key}/current")
def script_current(task_key: str, scripts: ScriptsDep) -> dict:
    try:
        return {"task_key": task_key, "version": scripts.next_version(task_key) - 1,
                "script": scripts.read_current(task_key)}
    except KeyError:
        raise HTTPException(status_code=404, detail="script not found")


@router.get("/scripts/{task_key}/versions")
def script_versions(task_key: str, scripts: ScriptsDep) -> list:
    return scripts.list_records(task_key)


@router.post("/scripts/{task_key}/rollback")
def script_rollback(task_key: str, version: int, _: DeveloperDep, scripts: ScriptsDep) -> dict:
    try:
        new_version = scripts.rollback(task_key, version, actor="session-user")
    except KeyError:
        raise HTTPException(status_code=404, detail="version not found")
    return {"task_key": task_key, "rolled_to": version, "now_version": new_version}