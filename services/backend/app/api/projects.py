"""项目管理 API（ETL 任务归属；项目只管理任务，不接管连接/调度/血缘）。

- POST   /api/projects          创建项目（developer+；重名 409）
- GET    /api/projects          项目清单（含各项目任务数；请求时幂等播种存量任务的 project 值）
- DELETE /api/projects/{name}   删除项目（developer+；仍含任务的项目返回 409）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import AuthContext, require_role
from app.core.config import get_settings
from app.models.access import Role
from app.store import Store


class ProjectCreate(BaseModel):
    name: str


router = APIRouter()


def get_store() -> Store:
    return Store(get_settings().data_dir)


StoreDep = Annotated[Store, Depends(get_store)]
DeveloperDep = Annotated[AuthContext, Depends(require_role(Role.DEVELOPER))]


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(req: ProjectCreate, auth: DeveloperDep, store: StoreDep) -> dict:
    """创建项目；名称为空或重名返回 409 并给出可读原因。"""
    try:
        project = store.create_project(req.name, created_by=auth.username)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    store.log_event(actor=auth.username, project=project["name"], action="project_create",
                    detail={"name": project["name"]})
    return project


@router.get("/projects")
def list_projects(store: StoreDep) -> list:
    """项目清单。读取前做一次幂等播种，保证存量任务的 project 值可见且可被删除校验覆盖。"""
    store.seed_projects_from_tasks()
    return store.list_projects()


@router.delete("/projects/{name}")
def delete_project(name: str, auth: DeveloperDep, store: StoreDep) -> dict:
    """删除项目：非空项目拒绝（不级联删除任务），保证任务不会因删项目而消失。"""
    count = store.project_task_count(name)
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"项目「{name}」下仍有 {count} 个任务，请先清空或迁移后再删除",
        )
    if not store.delete_project(name):
        raise HTTPException(status_code=404, detail="project not found")
    store.log_event(actor=auth.username, project=name, action="project_delete",
                    detail={"name": name})
    return {"name": name, "deleted": True}
