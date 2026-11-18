/* Synetra 全功能 E2E 配置
 *
 * 设计要点：
 * 1) 双 webServer 由 Playwright 托管，后端强制指向隔离元数据库 synetra_e2e + 独立 data 目录，
 *    绝不触碰开发库 synetra 与用户现有连接/任务；
 * 2) 本地调度器关闭（LOCAL_SCHEDULER_ENABLED=false），避免 cron 在用例执行期间插队产生运行记录；
 * 3) 串行 + 默认无头：本套用例是"模拟人工操作"的验收型用例，时序稳定优先于速度；
 *    需要观察真实点击过程时置 E2E_HEADED=1；
 * 4) 证据链：每用例关键步骤全页截图落在 reports/e2e/<run>/ 与 reports/e2e/screenshots/。
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, "../../..");          // d:/Projects/synetra
const BACKEND = path.resolve(here, "../../backend");  // services/backend
const REPORTS = path.join(ROOT, "reports/e2e");
const DATA_DIR = path.join(BACKEND, "data-e2e");

const MYSQL = {
  host: process.env.E2E_MYSQL_HOST || "127.0.0.1",
  port: process.env.E2E_MYSQL_PORT || "3306",
  user: process.env.E2E_MYSQL_USER || "root",
  password: process.env.E2E_MYSQL_PASSWORD || "12345",
};

const BACKEND_URL = "http://127.0.0.1:8000";
const FRONTEND_URL = "http://127.0.0.1:5173";

// 每次运行使用独立目录：Playwright 启动时会清空 outputDir，
// 若固定目录会被宿主 safe-delete 守卫（>500 文件）拦截而整轮失败。
const RUN_ID = process.env.E2E_RUN_ID
  || new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
const RUN_DIR = path.join(REPORTS, RUN_ID);

export default defineConfig({
  testDir: here,
  outputDir: path.join(RUN_DIR, "artifacts"),
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: [
    ["list"],
    ["json", { outputFile: path.join(RUN_DIR, "results.json") }],
    ["html", { outputFolder: path.join(RUN_DIR, "html"), open: "never" }],
  ],
  use: {
    baseURL: FRONTEND_URL,
    channel: "chrome",
    // 默认无头：106 条用例若 headed 会反复开关浏览器窗口，Windows 上表现为桌面持续闪烁。
    // 需要观察真实操作过程时：$env:E2E_HEADED=1; npx playwright test ...
    headless: process.env.E2E_HEADED !== "1",
    viewport: { width: 1440, height: 900 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    screenshot: "only-on-failure",
    // 不录屏（ffmpeg 未安装）、不留 trace（成功用例的临时 trace 清理会被宿主 safe-delete 守卫拦截，
    // 产生与产品无关的噪声失败）；证据统一由「每用例关键步骤截图」承担。
    video: "off",
    trace: "off",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    launchOptions: { args: ["--disable-blink-features=AutomationControlled"] },
  },
  projects: [
    {
      name: "seed",
      testMatch: /setup[\\/].*\.setup\.js/,
      use: { headless: true },
    },
    {
      name: "chromium",
      dependencies: ["seed"],
      testMatch: /specs[\\/].*\.spec\.js/,
    },
  ],
  webServer: [
    {
      command: `"${path.join(BACKEND, ".venv/Scripts/python.exe").replace(/\\/g, "/")}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000`,
      cwd: BACKEND,
      url: `${BACKEND_URL}/health`,
      reuseExistingServer: false,
      timeout: 180_000,
      // Windows 下 Playwright 用系统代码页(GBK)解析子进程输出，后端中文日志会触发解码异常，
      // 因此不再回传后端 stdout/stderr；服务可用性由 /health 探针保证。
      stdout: "ignore",
      stderr: "ignore",
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
        SYNETRA_METADATA_DB_DSN: `mysql://${MYSQL.user}:${MYSQL.password}@${MYSQL.host}:${MYSQL.port}/synetra_e2e`,
        SYNETRA_DATA_DIR: DATA_DIR.replace(/\\/g, "/"),
        SYNETRA_LOCAL_SCHEDULER_ENABLED: "false",
        // 失败重试置零：故障注入用例（如源表不存在）必须秒级返回，而不是等 2×60s 重试
        SYNETRA_RETRY_TIMES: "0",
        SYNETRA_RETRY_INTERVAL_S: "1",
        SYNETRA_LLM_PROVIDER: "deepseek",
        SYNETRA_DEEPSEEK_API_KEY: "",
        SYNETRA_SMTP_HOST: "",
      },
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort",
      cwd: path.resolve(here, ".."),
      url: FRONTEND_URL,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});

export { ROOT, BACKEND, REPORTS, DATA_DIR, MYSQL, BACKEND_URL, FRONTEND_URL };
