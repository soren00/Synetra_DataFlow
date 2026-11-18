/* 会话注入与页面导航助手。
 *
 * 身份只来自服务端签发的会话票据（变更 fix-test-report-defects 起）：
 *  - actAs() 先以该账号真实调用 /api/auth/login 换 token，再把含 token 的会话
 *    写入 localStorage（等价"以该角色登录后刷新"），不再靠请求头自称身份；
 *  - 直接调接口（page.request / request）时用 authHeaders() 取 Authorization 头；
 *  - 前端暂无 URL 路由，切页统一走侧边栏 role=tab 点击，禁止 page.goto('/xxx')。
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";
import { ADMIN } from "./env.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const SHOT_DIR = path.resolve(here, "../../../../reports/e2e/screenshots");

export const SESSION_KEY = "synetra.session";

/** 以指定账号真实登录，返回登录响应（含 token / expires_at）。 */
export async function loginToken(request, user) {
  const resp = await request.post("/api/auth/login", {
    data: { username: user.username, password: user.password },
  });
  if (!resp.ok()) {
    throw new Error(`登录失败（${user.username}）：${resp.status()} ${await resp.text()}`);
  }
  return resp.json();
}

/** 直接调接口用的鉴权头。 */
export async function authHeaders(request, user = ADMIN) {
  const body = await loginToken(request, user);
  return { Authorization: `Bearer ${body.token}` };
}

/** 让后续所有导航都带上指定角色会话（等价"该用户登录态"）。 */
export async function actAs(page, user) {
  const body = await loginToken(page.request, user);
  const session = JSON.stringify({
    username: body.username,
    role: body.role,
    token: body.token,
    exp: body.expires_at,
  });
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [SESSION_KEY, session],
  );
}

/** 真走登录表单（保留 1 条真实登录路径用例）。 */
export async function loginViaForm(page, user = ADMIN) {
  await page.goto("/");
  await page.fill("#login-user", user.username);
  await page.fill("#login-pass", user.password);
  await page.getByRole("button", { name: "登录" }).click();
}

/** 等待应用壳渲染完成。 */
export async function waitApp(page) {
  await expect(page.locator(".app-side nav.mac-side")).toBeVisible();
  await expect(page.locator("main.app-main h1")).toBeVisible();
}

/** 侧边栏切页（无 URL 路由，只能点击）。 */
export async function gotoPage(page, label) {
  await page.locator(".app-side").getByRole("tab", { name: label, exact: true }).click();
  await expect(page.locator(".app-side .mac-nav-item--active")).toContainText(label);
  await expect(page.locator("main.app-main h1")).toBeVisible();
}

/** 一步到位：以指定角色进入应用，可选直接切到某个页面。 */
export async function openAs(page, user, pageLabel) {
  await actAs(page, user);
  await page.goto("/");
  await waitApp(page);
  if (pageLabel) await gotoPage(page, pageLabel);
}

/** 关键步骤截图：落盘 + 附到报告。 */
export async function shot(page, name, testInfo) {
  const safe = String(name).replace(/[^\w\u4e00-\u9fa5.-]+/g, "_").slice(0, 80);
  const file = path.join(SHOT_DIR, `${safe}.png`);
  await page.screenshot({ path: file, fullPage: true });
  if (testInfo) {
    await testInfo.attach(safe, { path: file, contentType: "image/png" });
  }
  return file;
}

/** 模态框定位（统一 role=dialog）。 */
export function dialog(page) {
  return page.locator('[role="dialog"]');
}

/** 关闭模态：Modal 头部有 aria-label="关闭" 的图标按钮，页脚同名文本按钮需用 last() 消歧。 */
export async function closeDialog(dlg) {
  await dlg.getByRole("button", { name: "关闭" }).last().click();
}

/** 表格行文本断言助手。 */
export function tableRows(page) {
  return page.locator(".mac-table tbody tr");
}
