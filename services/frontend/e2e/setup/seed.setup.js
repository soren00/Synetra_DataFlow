/* 种子项目：在隔离环境里准备"存量数据基线"。
 *
 * 目的：让总览 / 监控 / 血缘 / 定时 / 权限等"读多写少"的页面有真实数据可断言，
 *       而不是只在空态下通过。所有写入均通过后端 HTTP API（与前端同一通道）。
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { test as setup, expect } from "@playwright/test";
import {
  E2E_DIR, ADMIN, DEV, RO, PROJECT, CONN_SRC, CONN_DST, SRC_DB, DST_DB,
  SOURCE_TABLE, TARGET_TABLE, TASK_OK, TASK_BROKEN, TASK_SCHED, FIELDS, writeSeed,
} from "../fixtures/env.js";
import { authHeaders } from "../fixtures/session.js";

const BACKEND = path.resolve(E2E_DIR, "../../backend");
const PY = path.join(BACKEND, ".venv/Scripts/python.exe");
const DBPY = path.join(E2E_DIR, "fixtures/db_setup.py");

/* 夹具数据全部以管理员身份经真实登录取票据后写入（变更 fix-test-report-defects 起）。 */
const as = (request, user = ADMIN) => authHeaders(request, user);

async function ensureUser(request, user) {
  const res = await request.post("/api/users", {
    headers: await as(request),
    data: { username: user.username, role: user.role, password: user.password, display_name: `E2E ${user.role}` },
  });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(`create user ${user.username} failed: ${res.status()} ${await res.text()}`);
  }
}

async function ensureConnection(request, { name, db }) {
  const list = await (await request.get("/api/connections", { headers: await as(request) })).json();
  const hit = list.find((c) => c.name === name);
  if (hit) return hit.conn_key;
  const res = await request.post("/api/connections", {
    headers: await as(request),
    data: {
      project: PROJECT, name, ctype: "mysql", direction: "both",
      meta: { host: "127.0.0.1", port: "3306", db, user: "root" },
      secret: "12345",
    },
  });
  expect(res.ok(), `create connection ${name}`).toBeTruthy();
  return (await res.json()).conn_key;
}

async function ensureProject(request) {
  const res = await request.post("/api/projects", { headers: await as(request), data: { name: PROJECT } });
  if (!res.ok() && res.status() !== 409) {
    throw new Error(`create project failed: ${res.status()} ${await res.text()}`);
  }
}

function taskPayload({ name, srcConn, dstConn, sourceObject, targetObject }) {
  return {
    project: PROJECT,
    name,
    config: {
      version: 1,
      project: PROJECT,
      name,
      source_conn_key: srcConn,
      target_conn_key: dstConn,
      source_object: sourceObject,
      target_object: targetObject,
      mode: "append",
      primary_keys: [],
      fields: FIELDS,
      incremental: { field: "", source: "field", watermark_field: "", first_run_policy: "all" },
      schedule: "",
      schedule_enabled: true,
    },
  };
}

async function findTask(request, name) {
  const tasks = await (await request.get(`/api/tasks?project=${encodeURIComponent(PROJECT)}`, { headers: await as(request) })).json();
  const hit = tasks.find((t) => t.name === name);
  return hit ? hit.task_key : null;
}

async function ensureTask(request, payload) {
  const exist = await findTask(request, payload.name);
  if (exist) return exist;
  const res = await request.post("/api/tasks", { headers: await as(request), data: payload });
  expect(res.ok(), `create task ${payload.name}: ${res.status()}`).toBeTruthy();
  return (await res.json()).task_key;
}

async function runTask(request, key) {
  const res = await request.post(`/api/tasks/${key}/run`, { headers: await as(request), timeout: 90_000 });
  expect(res.ok(), `run ${key}`).toBeTruthy();
  return res.json();
}

async function patchSchedule(request, key, cron) {
  const res = await request.patch(`/api/schedules/${key}`, {
    headers: await as(request), data: { schedule: cron },
  });
  expect(res.ok(), `patch schedule ${key}`).toBeTruthy();
  return res.json();
}

setup("准备隔离库与基线数据", async ({ request }) => {
  setup.setTimeout(240_000);

  // 1) MySQL 侧样例库（源 25 行 / 目标空表）
  execFileSync(PY, [DBPY], { cwd: BACKEND, stdio: "inherit" });

  // 2) 账号：developer / readonly
  await ensureUser(request, DEV);
  await ensureUser(request, RO);

  // 3) 项目与连接
  await ensureProject(request);
  const connSrc = await ensureConnection(request, { name: CONN_SRC, db: SRC_DB });
  const connDst = await ensureConnection(request, { name: CONN_DST, db: DST_DB });

  // 4) 任务：正常链路 / 故障链路 / 定时链路
  const taskOk = await ensureTask(request, taskPayload({
    name: TASK_OK, srcConn: connSrc, dstConn: connDst,
    sourceObject: SOURCE_TABLE, targetObject: TARGET_TABLE,
  }));
  const taskBroken = await ensureTask(request, taskPayload({
    name: TASK_BROKEN, srcConn: connSrc, dstConn: connDst,
    sourceObject: "e2e_missing_table", targetObject: "e2e_missing_out",
  }));
  const taskSched = await ensureTask(request, taskPayload({
    name: TASK_SCHED, srcConn: connSrc, dstConn: connDst,
    sourceObject: SOURCE_TABLE, targetObject: TARGET_TABLE,
  }));

  // 5) 各跑一次，形成成功 / 失败两类运行记录
  const runOk = await runTask(request, taskOk);
  const runFailed = await runTask(request, taskBroken);
  expect(runOk.status, "基线任务应成功").toBe("success");
  expect(runFailed.status, "故障任务应失败").toBe("failed");

  // 6) 定时链路：cron 配置 + 启用
  await patchSchedule(request, taskSched, "*/15 * * * *");

  writeSeed({
    project: PROJECT,
    connSrc, connDst, taskOk, taskBroken, taskSched,
    runOk: runOk.run_id, runFailed: runFailed.run_id,
    preparedAt: new Date().toISOString(),
  });
});
