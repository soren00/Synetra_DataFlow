"""LLM 配置管理与自然语言建任务草稿路由。

- GET    /api/llm/config          读取配置（key 仅掩码尾 4 位，不含明文）
- PUT    /api/llm/config          保存配置即热切换（api_key 可空=不更新）
- POST   /api/llm/config/test     用提交/生效配置调用模型列表接口验连通
- DELETE /api/llm/config/key      吊销 API Key（清除密文并重注册为降级态）
- POST   /api/llm/tasks/draft     多轮会话生成 TaskConfig 草稿（骨架轮 → 源表结构轮）
- POST   /api/llm/tasks/draft/revise  在会话历史上追加修订轮
- POST   /api/llm/expression      按提示词生成单字段受限表达式（白名单校验 + 受限修复轮）
- GET    /api/llm/generations     AI 生成与创建记录（服务端分页 page/page_size + 状态筛选）
- GET    /api/llm/skills          技能包清单（admin）
- GET    /api/llm/skills/{name}   技能包内容原文（admin）
- PUT    /api/llm/skills/{name}   保存技能包内容（admin；保存前一致性校验 + 原子写 + 审计）

多轮契约：响应返回模型可见 `history`，客户端下一次请求原样回传；`history` 非法（角色/结构/
轮次顺序）返回 422 且不调用 LLM；回传的 `system` 视同非法（系统段落由服务端重建）。

配置管理仅 admin 可访问；草稿生成供 developer 及以上使用。
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_role
from app.core.config import get_settings
from app.llm import skill_lint
from app.llm.config import apply_effective, effective_llm_config, stored_llm_config
from app.llm.expression import MAX_PROMPT_CHARS, generate_expression
from app.llm.provider import ProviderNotConfigured, ProviderRequestError, get_provider
from app.llm.session import InvalidHistory
from app.llm.task_draft import DraftGenerationError, generate_draft, generate_revise
from app.models.access import Role
from app.store import Store


class LLMConfigPut(BaseModel):
    provider: str = "deepseek"
    base_url: str = ""
    model: str = ""
    api_key: str | None = None  # 空/缺省表示不更新（保存时保持原密文）


class DraftRequest(BaseModel):
    description: str
    project: str | None = None  # 可选：不指定则草稿 project 为空，创建时再定
    connections: list[dict] = Field(default_factory=list)
    source_conn_key: str | None = None  # 用户下拉指定源连接（优先于 LLM 骨架输出）
    target_conn_key: str | None = None  # 用户下拉指定目标连接（优先于 LLM 骨架输出）
    history: list[dict] | None = None  # 多轮会话历史（上一次响应原样回传）；缺省则单轮退化


class ExpressionRequest(BaseModel):
    prompt: str  # 用户提示词（如「手机号中间四位打码」）
    source_field: str = ""  # 源字段名（作为可引用的变量名候选）
    target_field: str = ""  # 目标字段名（上下文说明用）
    columns: list[str] = Field(default_factory=list)  # 源列名清单（仅列名，不含样例数据行）


class ReviseRequest(BaseModel):
    draft: dict
    instruction: str
    project: str | None = None
    connections: list[dict] = Field(default_factory=list)
    history: list[dict] | None = None  # 多轮会话历史（上一次响应原样回传）；缺省则单轮退化


router = APIRouter()


def get_store() -> Store:
    return Store(get_settings().data_dir)


StoreDep = Annotated[Store, Depends(get_store)]
AdminDep = Annotated[object, Depends(require_role(Role.ADMIN))]
DeveloperDep = Annotated[object, Depends(require_role(Role.DEVELOPER))]


@router.get("/llm/config")
def get_llm_config(_: AdminDep, store: StoreDep) -> dict:
    """读取生效配置视图：存储值优先、env 兜底；key 仅掩码。"""
    stored = stored_llm_config(store)
    settings = get_settings()
    return {
        "provider": stored["provider"] or settings.llm_provider,
        "base_url": stored["base_url"] or settings.deepseek_base_url,
        "model": stored["model"] or settings.deepseek_model,
        "api_key_masked": stored["api_key_masked"],
        "has_api_key": stored["has_api_key"],
        "updated_at": stored["updated_at"],
        "updated_by": stored["updated_by"],
    }


@router.put("/llm/config")
def put_llm_config(req: LLMConfigPut, auth: Annotated[object, Depends(require_role(Role.ADMIN))],
                   store: StoreDep) -> dict:
    """保存配置并热切换：按新参数重新注册同名 provider（覆盖）。"""
    settings = get_settings()
    provider = req.provider.strip() or settings.llm_provider
    base_url = req.base_url.strip() or settings.deepseek_base_url
    model = req.model.strip() or settings.deepseek_model
    store.save_llm_config(provider, base_url, model,
                          api_key=(req.api_key or None),
                          updated_by=auth.username)
    apply_effective(store, settings)  # 热切换：同名覆盖注册
    store.log_event(actor=auth.username, project="", action="llm_config_save",
                    detail={"provider": provider, "base_url": base_url,
                            "model": model, "key_updated": bool(req.api_key)})
    stored = stored_llm_config(store)
    return {
        "provider": provider, "base_url": base_url, "model": model,
        "api_key_masked": stored["api_key_masked"],
        "has_api_key": stored["has_api_key"],
    }


@router.post("/llm/config/test")
def test_llm_config(req: LLMConfigPut, _: AdminDep, store: StoreDep) -> dict:
    """连通性测试：调供应商模型列表接口验证凭据；可用模型列表供前端下拉。"""
    settings = get_settings()
    eff = effective_llm_config(store, settings)
    provider_name = req.provider.strip() or eff["provider"]
    base_url = req.base_url.strip() or eff["base_url"]
    model = req.model.strip() or eff["model"]
    # 测试优先用本次提交的 key；未提交则用已存/环境的生效 key
    api_key = (req.api_key or "").strip() or eff["api_key"]
    try:
        from app.llm.deepseek import DeepSeekProvider
        from app.llm.provider import ProviderAuthError, ProviderNotConfigured

        prov = DeepSeekProvider(api_key=api_key, base_url=base_url, model=model)
        models = prov.list_models()
    except ProviderNotConfigured as e:
        return {"ok": False, "reason": str(e), "models": []}
    except ProviderAuthError as e:
        return {"ok": False, "reason": str(e), "models": []}
    except Exception as e:  # noqa: BLE001 - 底层 HTTP/解析异常转可读原因
        return {"ok": False, "reason": f"连通性测试失败: {e}", "models": []}
    return {
        "ok": True,
        "provider": provider_name,
        "models": [m.id for m in models],
        "reason": "",
    }


@router.delete("/llm/config/key")
def revoke_llm_key(auth: Annotated[object, Depends(require_role(Role.ADMIN))],
                   store: StoreDep) -> dict:
    """吊销已保存的 API Key：清除密文并重注册为降级态（生成请求将返回 503 引导）。"""
    store.clear_llm_api_key()
    apply_effective(store, get_settings())
    store.log_event(actor=auth.username, project="", action="llm_key_revoke",
                    detail={"provider": get_settings().llm_provider})
    return {"revoked": True, "has_api_key": False}


# ---- AI 管理：技能包（决策 11/12） ----

class SkillFilesPut(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)  # 文件名 → 内容原文


def _atomic_write(path: Path, content: str) -> None:
    """原子写入：临时文件 + 替换，避免并发下的生成请求读到半写文件。

    保留目标文件既有的换行风格（仓库内技能包为 CRLF）：否则每次保存都会把整份文件
    的换行符改掉，在版本控制里表现为整文件重写。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        existing = path.read_bytes()
        if b"\r\n" in existing and "\r\n" not in content:
            content = content.replace("\r\n", "\n").replace("\n", "\r\n")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-skill-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@router.get("/llm/skills")
def list_skills(_: AdminDep) -> list:
    """技能包清单（管理员专属）：名称、描述、内容指纹、最近修改时间与随包文件。"""
    return skill_lint.list_skills()


@router.get("/llm/skills/{name}")
def get_skill(name: str, _: AdminDep) -> dict:
    """技能包内容原文（管理员专属），供审阅与编辑。"""
    try:
        files = skill_lint.load_skill_files(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if "SKILL.md" not in files:
        raise HTTPException(status_code=404, detail="skill not found")
    return {**skill_lint.skill_meta(name), "contents": files}


@router.put("/llm/skills/{name}")
def put_skill(name: str, req: SkillFilesPut, auth: AdminDep, store: StoreDep) -> dict:
    """保存技能包内容：路径安全 → 保存前一致性校验 → 原子写入 → 审计。

    校验不通过时拒绝写盘并返回具体原因，磁盘内容保持改动前的状态；
    校验通过后内容对后续生成请求即时生效（无需重启）。
    """
    try:
        directory = skill_lint.skill_path(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if not (directory / "SKILL.md").is_file():
        raise HTTPException(status_code=404, detail="技能包不存在（本次不支持新建技能包）")
    if not req.files:
        raise HTTPException(status_code=422, detail="未提交任何文件内容")
    for filename in req.files:
        reason = skill_lint.validate_filename(filename)
        if reason:
            raise HTTPException(status_code=422, detail=reason)

    merged = dict(skill_lint.load_skill_files(name))
    merged.update(req.files)
    issues = skill_lint.lint_skill_files(merged)
    if issues:
        raise HTTPException(status_code=422,
                            detail="技能包校验未通过：" + "；".join(issues))

    before = skill_lint.fingerprint(name)
    for filename, content in req.files.items():
        _atomic_write(directory / filename, content)
    after = skill_lint.fingerprint(name)
    store.log_event(actor=auth.username, project="", action="llm_skill_save",
                    detail={"skill": name, "before": before, "after": after,
                            "files": sorted(req.files)})
    return {**skill_lint.skill_meta(name), "saved": True, "before": before, "after": after}


# ---- 自然语言建任务草稿（T4/T5/T6） ----

def _generation_provider():
    try:
        return get_provider(get_settings().llm_provider)
    except KeyError:
        raise HTTPException(status_code=503, detail="LLM provider 未就绪，请先保存模型配置") from None


@router.post("/llm/tasks/draft")
def llm_draft(req: DraftRequest,
              auth: Annotated[object, Depends(require_role(Role.DEVELOPER))],
              store: StoreDep) -> dict:
    """多轮生成：在第一轮识别骨架、第二轮注入源表结构输出完整 TaskConfig 草稿。

    携带上一次响应返回的 `history` 时在既有会话上续接；缺省则单轮退化（行为同旧版）。
    """
    provider = _generation_provider()
    try:
        result = generate_draft(store, provider, req.description, req.project,
                                req.connections, actor=auth.username,
                                source_conn_key=req.source_conn_key,
                                target_conn_key=req.target_conn_key,
                                history=req.history)
    except InvalidHistory as e:
        raise HTTPException(status_code=422, detail=f"会话历史非法：{e}") from e
    except DraftGenerationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ProviderRequestError as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{e}") from e
    except ProviderNotConfigured as e:
        raise HTTPException(status_code=503, detail=f"LLM 未配置：{e}") from e
    return result.to_dict()


# ---- AI 生成与创建记录（AI ETL 路径的留痕浏览） ----

GENERATION_STATUSES = ("success", "failed")
GENERATION_PAGE_SIZES = (10, 20, 50)
GENERATION_DEFAULT_PAGE_SIZE = 20


def _normalize_page_size(value: int) -> int:
    """只允许 10/20/50 三档，其余值就近归一到最近的合法档位（与 /runs 同口径）。"""
    if value in GENERATION_PAGE_SIZES:
        return value
    return min(GENERATION_PAGE_SIZES, key=lambda s: (abs(s - value), s))


@router.get("/llm/generations")
def list_generations(_: DeveloperDep, store: StoreDep, project: str | None = None,
                     status: str | None = None, page: int = 1,
                     page_size: int = GENERATION_DEFAULT_PAGE_SIZE) -> dict:
    """AI 生成与创建记录：分页与筛选均由服务端裁决（契约与 `GET /runs` 一致）。"""
    if status and status not in GENERATION_STATUSES:
        raise HTTPException(status_code=422, detail=f"非法状态: {status}")
    size = _normalize_page_size(page_size)
    total = store.generation_totals(project=project, status=status)["count"]
    pages = max(1, -(-total // size))
    current = min(max(1, page), pages)
    return {
        "records": store.list_generations(project=project, status=status, limit=size,
                                          offset=(current - 1) * size),
        "total": total,
        "page": current,
        "page_size": size,
        "pages": pages,
        "status_counts": store.generation_status_counts(project=project),
        "allowed_statuses": list(GENERATION_STATUSES),
    }


@router.post("/llm/expression")
def llm_expression(req: ExpressionRequest,
                   auth: Annotated[object, Depends(require_role(Role.DEVELOPER))],
                   store: StoreDep) -> dict:
    """按提示词生成单字段受限表达式：白名单校验不通过时追加受限修复轮。

    生成结果仅作为建议（前端回填，不自动保存）；上下文不含源表样例数据行。
    """
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="请填写表达式需求提示词")
    if len(req.prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(status_code=422, detail=f"提示词过长（上限 {MAX_PROMPT_CHARS} 字符）")
    provider = _generation_provider()
    try:
        result = generate_expression(store, provider, req.prompt,
                                     source_field=req.source_field,
                                     target_field=req.target_field,
                                     columns=req.columns, actor=auth.username)
    except ProviderRequestError as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{e}") from e
    except ProviderNotConfigured as e:
        raise HTTPException(status_code=503, detail=f"LLM 未配置：{e}") from e
    return result.to_dict()


@router.post("/llm/tasks/draft/revise")
def llm_draft_revise(req: ReviseRequest,
                     auth: Annotated[object, Depends(require_role(Role.DEVELOPER))],
                     store: StoreDep) -> dict:
    """修订轮：在会话历史上追加修订轮；未提供 `history` 时退回「草稿 JSON + 指令」单轮。"""
    provider = _generation_provider()
    try:
        result = generate_revise(store, provider, req.draft, req.instruction,
                                 req.project, actor=auth.username, history=req.history)
    except InvalidHistory as e:
        raise HTTPException(status_code=422, detail=f"会话历史非法：{e}") from e
    except DraftGenerationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ProviderRequestError as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{e}") from e
    except ProviderNotConfigured as e:
        raise HTTPException(status_code=503, detail=f"LLM 未配置：{e}") from e
    return result.to_dict()
