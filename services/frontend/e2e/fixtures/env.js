/* E2E 常量与种子状态读取：所有用例共享同一份命名约定 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const E2E_DIR = path.resolve(here, "..");
export const STATE_FILE = path.join(E2E_DIR, ".state", "seed.json");

export const ADMIN = { username: "admin", password: "admin", role: "admin" };
export const DEV = { username: "e2e-dev", password: "e2e-pass", role: "developer" };
export const RO = { username: "e2e-ro", password: "e2e-pass", role: "readonly" };

export const PROJECT = "E2E-Demo";
export const CONN_SRC = "e2e-mysql-src";
export const CONN_DST = "e2e-mysql-dst";
export const CONN_CSV = "e2e-csv-src";

export const SRC_DB = "synetra_e2e_src";
export const DST_DB = "synetra_e2e_dst";
export const SOURCE_TABLE = "e2e_orders";
export const TARGET_TABLE = "e2e_orders_out";

export const TASK_OK = "e2e-task-orders";
export const TASK_BROKEN = "e2e-task-broken";
export const TASK_SCHED = "e2e-task-sched";

export const FIELDS = [
  { source: "id", target: "id", kind: "direct" },
  { source: "order_no", target: "order_no", kind: "direct" },
  { source: "customer", target: "customer", kind: "direct" },
  { source: "amount", target: "amount", kind: "direct" },
  { source: "status", target: "status", kind: "direct" },
  { source: "created_at", target: "created_at", kind: "direct" },
];

export function readSeed() {
  if (!fs.existsSync(STATE_FILE)) return {};
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
  } catch {
    return {};
  }
}

export function writeSeed(data) {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(data, null, 2), "utf8");
}
