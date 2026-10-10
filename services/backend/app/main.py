"""FastAPI 应用入口、健康检查与连接管理路由。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import (
    access_router,
    auth_router,
    catalog_router,
    connections_router,
    llm_router,
    mdm_router,
    metadata_router,
    monitor_router,
    projects_router,
    query_router,
    schedule_router,
    scripts_router,
    tasks_router,
)
from app.core.config import get_settings
from app.store import Store

settings = get_settings()


def _bootstrap_users() -> None:
    """幂等种子化默认账号并补齐默认密码，保证可登录（7.1）；仅创建 admin（不预置演示账号）。"""
    store = Store(settings.data_dir)
    name, role = ("admin", "admin")
    u = store.get_user(name)
    if not u or not u.get("password_hash"):
        store.upsert_user(name, role, password="admin")


_local_scheduler = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _local_scheduler
    _bootstrap_users()
    # 启动即注册 LLM provider，使环境变量配置（Settings.llm_provider/deepseek_*）生效（T1）
    from app.llm.deepseek import auto_register

    auto_register(settings)
    _log_llm_skill_versions()
    if settings.local_scheduler_enabled:
        from app.schedule import LocalScheduler

        _local_scheduler = LocalScheduler(
            Store(settings.data_dir),
            interval_s=settings.local_scheduler_interval_s,
        )
        _local_scheduler.start()
    try:
        yield
    finally:
        if _local_scheduler:
            _local_scheduler.stop()
            _local_scheduler = None


def _log_llm_skill_versions() -> None:
    """启动即记录本进程看到的 LLM 技能包指纹，用于排查「服务是否在跑旧规范」。

    生效边界（实测坑）：
    - 技能包**内容**在每次生成请求时从磁盘读取 → 只改 SKILL.md/examples.md 无需重启；
    - 而**注入点、提示词、校验回环等代码**是模块级的 → 必须重启进程才生效。
    因此若生成结果与技能包不符，先比对本事件的 ts 与代码/技能包的修改时间。
    """
    from app.llm.rag import skill_fingerprints

    Store(settings.data_dir).log_event(
        actor="system", project="", action="llm_skill_loaded",
        detail={"skills": skill_fingerprints(),
                "note": "技能包内容每次请求实时读取；注入/提示词/回环等代码变更需重启进程"})


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

app.include_router(auth_router, prefix="/api")
app.include_router(access_router, prefix="/api")
app.include_router(catalog_router, prefix="/api")
app.include_router(connections_router, prefix="/api")
app.include_router(llm_router, prefix="/api")
app.include_router(mdm_router, prefix="/api")
app.include_router(metadata_router, prefix="/api")
app.include_router(monitor_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(query_router, prefix="/api")
app.include_router(schedule_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(scripts_router, prefix="/api")


@app.get("/health")
def health() -> dict:
    """存活/就绪探针：返回服务与版本信息。"""
    return {"status": "ok", "service": settings.app_name, "version": __version__}