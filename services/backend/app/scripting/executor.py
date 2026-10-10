"""dry-run 执行器（任务 5.3/5.4）。

把生成/手写脚本放入子进程，注入 etl_sdk 运行时并置 SYNETRA_DRY_RUN=1 运行。
`writers` 在 dry-run 下只记录"本应写入"的统计，绝不写目标；执行交回 stdout 中的
SYNETRA_REPORT 标记以获取干跑报告。子进程运行受超时限制。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # services/backend

_PREAMBLE = """
import sys, os, types, json, atexit
import app.scripting.etl_runtime as _etl_sdk
_sdk = types.ModuleType("etl_sdk")
_sdk.readers = _etl_sdk.readers
_sdk.transforms = _etl_sdk.transforms
_sdk.writers = _etl_sdk.writers
sys.modules["etl_sdk"] = _sdk
etl_sdk = _sdk
task_config = json.loads(os.environ.get("SYNETRA_TASK_CONFIG_JSON", "{}"))
def _flush_dry_run_report():
    print("SYNETRA_REPORT:" + json.dumps(_etl_sdk.writers.report(), ensure_ascii=False))
atexit.register(_flush_dry_run_report)
"""


class DryRunResult:
    def __init__(self, ok: bool, report: list[dict], stderr: str):
        self.ok = ok
        self.report = report
        self.stderr = stderr

    def total_records(self) -> int:
        return sum(r.get("rows", 0) for r in self.report)


class RunResult:
    """真实执行结果（不强制 dry-run，writers/readers 走真实 DB 路径）。"""

    def __init__(self, ok: bool, stdout: str, stderr: str, returncode: int):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    def total_records(self) -> int:
        """从 stdout 解析 SYNETRA_SCRIPT_WRITTEN_ROWS 标记获取真实写入行数。"""
        for line in (self.stdout or "").splitlines():
            if line.startswith("SYNETRA_SCRIPT_WRITTEN_ROWS="):
                try:
                    return int(line.split("=", 1)[1].strip())
                except ValueError:
                    return 0
        return 0


def dry_run_script(
    source: str,
    task_config: dict | None = None,
    sample: list[dict] | None = None,
    timeout: float = 30.0,
) -> DryRunResult:
    """以 dry-run 运行脚本，返回是否成功与目标写入统计（不落盘/不写真实目标）。"""
    script = _PREAMBLE + "\n" + source
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["SYNETRA_DRY_RUN"] = "1"
    env["SYNETRA_TASK_CONFIG_JSON"] = json.dumps(task_config or {}, ensure_ascii=False)
    env["SYNETRA_SAMPLE_JSON"] = json.dumps(sample or [], ensure_ascii=False)

    payload = base64.b64encode(script.encode("utf-8")).decode()
    runner = (
        "import base64,tempfile,sys,os\n"
        "src=base64.b64decode(sys.argv[1]).decode('utf-8')\n"
        "with tempfile.NamedTemporaryFile('w',suffix='.py',delete=False,dir='/tmp') as f:\n"
        "    f.write(src); path=f.name\n"
        "try:\n"
        "    code=compile(src, path, 'exec')\n"
        "    exec(code, {})\n"
        "finally:\n"
        "    os.unlink(path)\n"
    )
    proc = subprocess.run(
        [sys_executable(), "-c", runner, payload],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(_BACKEND_DIR),
        env=env,
        check=False,
    )
    report: list[dict] = []
    out = proc.stdout or ""
    for line in out.splitlines():
        if line.startswith("SYNETRA_REPORT:"):
            report = json.loads(line[len("SYNETRA_REPORT:"):])
    return DryRunResult(ok=proc.returncode == 0, report=report, stderr=proc.stderr)


def sys_executable() -> str:
    """子进程解释器：优先使用当前进程解释器，可通过 SYNETRA_PYTHON 覆盖。"""
    return os.environ.get("SYNETRA_PYTHON", str(subprocess.sys.executable))


def run_real_script(source: str, task_config: dict | None = None,
                    timeout: float = 120.0) -> RunResult:
    """真实运行脚本：不置 SYNETRA_DRY_RUN，readers/writers 走真实 DB 写入路径。"""
    script = _PREAMBLE + "\n" + source
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("SYNETRA_DRY_RUN", None)
    env["SYNETRA_TASK_CONFIG_JSON"] = json.dumps(task_config or {}, ensure_ascii=False)
    env["SYNETRA_SAMPLE_JSON"] = json.dumps([], ensure_ascii=False)

    payload = base64.b64encode(script.encode("utf-8")).decode()
    runner = (
        "import base64,tempfile,sys,os\n"
        "src=base64.b64decode(sys.argv[1]).decode('utf-8')\n"
        "with tempfile.NamedTemporaryFile('w',suffix='.py',delete=False,dir='/tmp') as f:\n"
        "    f.write(src); path=f.name\n"
        "try:\n"
        "    code=compile(src, path, 'exec')\n"
        "    exec(code, {})\n"
        "finally:\n"
        "    os.unlink(path)\n"
    )
    proc = subprocess.run(
        [sys_executable(), "-c", runner, payload],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(_BACKEND_DIR),
        env=env,
        check=False,
    )
    return RunResult(ok=proc.returncode == 0, stdout=proc.stdout or "",
                     stderr=proc.stderr or "", returncode=proc.returncode)


__all__ = ["DryRunResult", "RunResult", "dry_run_script", "run_real_script", "sys_executable"]