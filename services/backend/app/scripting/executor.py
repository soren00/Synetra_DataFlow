"""脚本执行器（dry-run 与真实执行）。

把生成/手写脚本放入子进程，注入 `etl_sdk` 运行时后执行：
- dry-run 置 `SYNETRA_DRY_RUN=1`：writers 与流水线目标步骤只记录"本应写入"的统计；
- 真实执行不置该变量，读写走真实连接；
- 增量水位经 `SYNETRA_WATERMARK` 注入，执行成功的最大水位经 stdout 标记回传给调用方。

脚本按约定打印标记：`SYNETRA_REPORT`（旧 writers 报告）、`SYNETRA_STEP_METRICS`（步级指标）、
`SYNETRA_STEP_FAILED`（失败节点）、`SYNETRA_SCRIPT_WRITTEN_ROWS`、`SYNETRA_SCRIPT_WATERMARK`。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

from app.core.config import get_settings

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # services/backend

_PREAMBLE = """
import sys, os, types, json, atexit
import app.scripting.etl_runtime as _etl_sdk
_sdk = types.ModuleType("etl_sdk")
_sdk.readers = _etl_sdk.readers
_sdk.transforms = _etl_sdk.transforms
_sdk.writers = _etl_sdk.writers
_sdk.run_pipeline = _etl_sdk.run_pipeline
_sdk.PipelineRunError = _etl_sdk.PipelineRunError
sys.modules["etl_sdk"] = _sdk
etl_sdk = _sdk
task_config = json.loads(os.environ.get("SYNETRA_TASK_CONFIG_JSON", "{}"))
def _flush_dry_run_report():
    print("SYNETRA_REPORT:" + json.dumps(_etl_sdk.writers.report(), ensure_ascii=False))
atexit.register(_flush_dry_run_report)
"""

_RUNNER = (
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


def _parse_marker(stdout: str, marker: str) -> str | None:
    for line in (stdout or "").splitlines():
        if line.startswith(marker + "="):
            return line[len(marker) + 1:].strip()
    return None


def _parse_step_metrics(stdout: str) -> list[dict]:
    raw = _parse_marker(stdout, "SYNETRA_STEP_METRICS")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_step_failed(stdout: str) -> dict:
    raw = _parse_marker(stdout, "SYNETRA_STEP_FAILED")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class DryRunResult:
    def __init__(self, ok: bool, report: list[dict], stderr: str,
                 step_metrics: list[dict] | None = None):
        self.ok = ok
        self.report = report
        self.stderr = stderr
        self.step_metrics = step_metrics or []

    def total_records(self) -> int:
        return sum(r.get("rows", 0) for r in self.report)


class RunResult:
    """真实执行结果（不强制 dry-run，writers/readers 走真实 DB 路径）。"""

    def __init__(self, ok: bool, stdout: str, stderr: str, returncode: int):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.step_metrics: list[dict] = _parse_step_metrics(stdout)
        self.failed_step: dict = _parse_step_failed(stdout)
        self.watermark: str | None = _parse_marker(stdout, "SYNETRA_SCRIPT_WATERMARK")

    def total_records(self) -> int:
        """从 stdout 解析 SYNETRA_SCRIPT_WRITTEN_ROWS 标记获取真实写入行数。"""
        for line in (self.stdout or "").splitlines():
            if line.startswith("SYNETRA_SCRIPT_WRITTEN_ROWS="):
                try:
                    return int(line.split("=", 1)[1].strip())
                except ValueError:
                    return 0
        return 0


def _prepare_env(task_config: dict | None, *, dry_run: bool,
                 watermark: str = "", run_id: str = "") -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    if dry_run:
        env["SYNETRA_DRY_RUN"] = "1"
    else:
        env.pop("SYNETRA_DRY_RUN", None)
    env["SYNETRA_TASK_CONFIG_JSON"] = json.dumps(task_config or {}, ensure_ascii=False)
    env["SYNETRA_WATERMARK"] = watermark or ""
    env["SYNETRA_RUN_ID"] = run_id or ""
    # 清理异常退出遗留的溢出目录（超过 6 小时的）
    try:
        from app.etl_sdk.pipeline import cleanup_stale_spill

        cleanup_stale_spill(Path(get_settings().data_dir) / "spill")
    except Exception:  # noqa: BLE001, S110 - 清理失败不影响执行
        pass
    return env


def _exec(source: str, env: dict, timeout: float) -> subprocess.CompletedProcess:
    script = _PREAMBLE + "\n" + source
    payload = base64.b64encode(script.encode("utf-8")).decode()
    return subprocess.run(
        [sys_executable(), "-c", _RUNNER, payload],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(_BACKEND_DIR),
        env=env,
        check=False,
    )


def dry_run_script(
    source: str,
    task_config: dict | None = None,
    sample: list[dict] | None = None,
    timeout: float = 30.0,
    watermark: str = "",
    run_id: str = "",
) -> DryRunResult:
    """以 dry-run 运行脚本，返回是否成功与目标写入统计（不落盘/不写真实目标）。"""
    env = _prepare_env(task_config, dry_run=True, watermark=watermark, run_id=run_id)
    env["SYNETRA_SAMPLE_JSON"] = json.dumps(sample or [], ensure_ascii=False)
    proc = _exec(source, env, timeout)
    report: list[dict] = []
    out = proc.stdout or ""
    for line in out.splitlines():
        if line.startswith("SYNETRA_REPORT:"):
            report = json.loads(line[len("SYNETRA_REPORT:"):])
    step_metrics = _parse_step_metrics(out)
    if not report:
        # 流水线脚本在 dry-run 下不经过 writers，改用「本应写入」标记构造报告
        raw = _parse_marker(out, "SYNETRA_SCRIPT_WRITTEN_ROWS")
        if raw is not None and step_metrics:
            try:
                report = [{"rows": int(raw), "kind": "pipeline_dry_run"}]
            except ValueError:
                report = []
    return DryRunResult(ok=proc.returncode == 0, report=report,
                        stderr=proc.stderr, step_metrics=step_metrics)


def sys_executable() -> str:
    """子进程解释器：优先使用当前进程解释器，可通过 SYNETRA_PYTHON 覆盖。"""
    return os.environ.get("SYNETRA_PYTHON", str(subprocess.sys.executable))


def run_real_script(source: str, task_config: dict | None = None,
                    timeout: float = 120.0, watermark: str = "",
                    run_id: str = "") -> RunResult:
    """真实运行脚本：不置 SYNETRA_DRY_RUN，readers/writers 走真实 DB 写入路径。"""
    env = _prepare_env(task_config, dry_run=False, watermark=watermark, run_id=run_id)
    env["SYNETRA_SAMPLE_JSON"] = json.dumps([], ensure_ascii=False)
    proc = _exec(source, env, timeout)
    return RunResult(ok=proc.returncode == 0, stdout=proc.stdout or "",
                     stderr=proc.stderr or "", returncode=proc.returncode)


__all__ = ["DryRunResult", "RunResult", "dry_run_script", "run_real_script", "sys_executable"]
