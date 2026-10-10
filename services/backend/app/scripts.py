"""物理脚本生成与版本化存储（任务 5.5）。

对每个任务维护 scripts/<task_key>/ 目录：
- current.py         当前生效脚本
- versions/<v>.py     历史版本
- versions.json       版本记录（版本号/配置快照/生成人/时间/脚本路径）
写入 current.py 采用临时文件 + os.replace 原子替换；版本记录追加而非覆盖。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

_CURRENT = "current.py"
_VERSIONS_DIR = "versions"
_RECORDS = "versions.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ScriptStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _task_dir(self, task_key: str) -> Path:
        d = self.root / "scripts" / task_key
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _records_path(self, task_key: str) -> Path:
        return self._task_dir(task_key) / _RECORDS

    def _load_records(self, task_key: str) -> list[dict]:
        p = self._records_path(task_key)
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []

    def next_version(self, task_key: str) -> int:
        recs = self._load_records(task_key)
        return (recs[-1].get("version", 0) if recs else 0) + 1  # 从 1 起

    def current_path(self, task_key: str) -> Path:
        return self._task_dir(task_key) / _CURRENT

    def has_current(self, task_key: str) -> bool:
        return self.current_path(task_key).exists()

    def read_current(self, task_key: str) -> str:
        p = self.current_path(task_key)
        if not p.exists():
            raise KeyError(task_key)
        return p.read_text(encoding="utf-8")

    def replace_current(self, task_key: str, source: str, *, generated_by: str,
                        config_snapshot: dict | None = None) -> int:
        """原子替换 current.py 并追加一条版本记录；返回新版本号。"""
        task_dir = self._task_dir(task_key)
        version_dir = task_dir / _VERSIONS_DIR
        version_dir.mkdir(parents=True, exist_ok=True)

        version = self.next_version(task_key)
        # 先落历史版本文件
        (version_dir / f"{version}.py").write_text(source, encoding="utf-8")

        # 原子替换 current.py
        tmp = task_dir / f".{_CURRENT}.tmp"
        tmp.write_text(source, encoding="utf-8")
        os.replace(tmp, task_dir / _CURRENT)

        rec = {
            "version": version,
            "config_snapshot": config_snapshot or {},
            "generated_by": generated_by,
            "time": _now(),
            "script_path": str(task_dir / _CURRENT),
        }
        recs = self._load_records(task_key)
        recs.append(rec)
        self._records_path(task_key).write_text(
            json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
        return version

    def list_records(self, task_key: str) -> list[dict]:
        recs = self._load_records(task_key)
        return recs[::-1]  # 最新在前

    def read_version(self, task_key: str, version: int) -> str:
        p = self._task_dir(task_key) / _VERSIONS_DIR / f"{version}.py"
        if not p.exists():
            raise KeyError((task_key, version))
        return p.read_text(encoding="utf-8")

    def remove_task(self, task_key: str) -> None:
        """删除该 task_key 的脚本目录（current/versions/版本记录）；不存在则静默跳过。

        任务删除时随脚本产物一并清理，避免遗留孤儿脚本目录。
        """
        d = self.root / "scripts" / task_key
        if d.exists():
            shutil.rmtree(d)

    def rollback(self, task_key: str, version: int, *, actor: str = "system") -> int:
        """回滚到指定版本：原子替换 current.py 并追加一条回滚版本记录（保留 history）。"""
        source = self.read_version(task_key, version)
        config_snapshot: dict | None = None
        recs = self._load_records(task_key)
        for r in reversed(recs):
            if r.get("version") == version:
                config_snapshot = r.get("config_snapshot")
                break
        snapshot = {"rollback_from": version}
        if config_snapshot:
            snapshot.update(config_snapshot)
        new_version = self.replace_current(
            task_key, source, generated_by=actor, config_snapshot=snapshot)
        return new_version