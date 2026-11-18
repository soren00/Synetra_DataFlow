/* M. 异常与边界：接口失败不白屏、XSS 转义、会话损坏、无路由现状、非法参数 */
import { test, expect } from "@playwright/test";
import { ADMIN } from "../fixtures/env.js";
import { openAs, shot, dialog, tableRows, SESSION_KEY, authHeaders } from "../fixtures/session.js";

test.describe("M. 异常与边界", () => {
  test("NEG-01 关键接口 500 时页面保持可用（不白屏/不无限加载）", async ({ page }, info) => {
    await page.route("**/api/metrics*", (route) => route.fulfill({ status: 500, body: '{"detail":"boom"}' }));
    await openAs(page, ADMIN, "总览");
    await expect(page.locator("main h1")).toHaveText("总览");
    await expect(page.locator(".mac-stat__label")).toHaveCount(4);
    // 运行历史仍应渲染（该接口未被拦截）
    await expect(page.locator(".mac-table tbody tr").first()).toBeVisible();
    await shot(page, "NEG-01-接口500", info);
  });

  test("NEG-02 恶意文本按纯文本展示，不执行脚本", async ({ page }, info) => {
    const payload = `<img src=x onerror=alert(1)>${Date.now().toString().slice(-6)}`;
    let dialogFired = false;
    page.on("dialog", async (d) => { dialogFired = true; await d.dismiss(); });

    await openAs(page, ADMIN, "连接管理");
    await page.getByRole("button", { name: "新建连接" }).click();
    const dlg = dialog(page);
    await dlg.getByPlaceholder("如 商户库MySQL").fill(payload);
    await dlg.getByPlaceholder("常为 127.0.0.1 / localhost").fill("127.0.0.1");
    await dlg.getByPlaceholder("如 3306（MySQL）/ 5432（PG）").fill("3306");
    await dlg.getByPlaceholder("数据库名").fill("synetra_e2e_src");
    await dlg.getByPlaceholder("登录账号").fill("root");
    await dlg.getByPlaceholder("数据库账号密码").fill("12345");
    await dlg.getByRole("button", { name: "创建连接" }).click();

    const row = tableRows(page).filter({ hasText: "onerror" }).first();
    await expect(row).toBeVisible();
    await expect(row).toContainText("<img src=x onerror=alert(1)>");
    expect(dialogFired, "不应执行注入脚本").toBeFalsy();
    expect(await page.locator(".mac-table img").count()).toBe(0);
    await shot(page, "NEG-02-XSS转义", info);

    // 清理
    const adminHeaders = await authHeaders(page.request);
    const list = await (await page.request.get("/api/connections", { headers: adminHeaders })).json();
    const target = list.find((c) => c.name.includes("onerror"));
    if (target) {
      await page.request.delete(`/api/connections/${target.conn_key}`, { headers: adminHeaders });
    }
  });

  test("NEG-03 会话数据损坏时回到登录页", async ({ page }, info) => {
    await page.goto("/");
    await page.evaluate((k) => window.localStorage.setItem(k, "{not-json"), SESSION_KEY);
    await page.reload();
    await expect(page.locator("#login-user")).toBeVisible();
    await expect(page.locator(".app-side")).toHaveCount(0);
    await shot(page, "NEG-03-会话损坏", info);
  });

  test("NEG-04 切页写入地址栏，刷新停留在当前页", async ({ page }, info) => {
    await openAs(page, ADMIN, "监控告警");
    await expect(page.locator("main h1")).toHaveText("监控与运行历史");
    await expect(page, "切页应写入页面标识").toHaveURL(/#\/monitor$/);

    await page.reload();
    await expect(page.locator("main h1")).toHaveText("监控与运行历史");
    await shot(page, "NEG-04-刷新停留当前页", info);

    // 无效标识回退到默认页且不报错
    await page.goto("/#/not-a-page");
    await expect(page.locator("main h1")).toHaveText("总览");
  });

  test("NEG-05 非法查询参数被后端拒绝（422）而非静默忽略", async ({ request }) => {
    const headers = await authHeaders(request);
    const bad = await request.get("/api/runs?status=not-a-status", { headers });
    expect(bad.status()).toBe(422);
    const pageSize = await request.get("/api/runs?page_size=7", { headers });
    expect((await pageSize.json()).page_size, "非法页大小应就近归一到 10/20/50").toBe(10);
    const pageOver = await request.get("/api/runs?page=999&page_size=10", { headers });
    expect((await pageOver.json()).page, "越界页码应回夹到最后一页").toBeGreaterThanOrEqual(1);
  });

  test("NEG-06 匿名调用业务接口一律 401（无票据即无身份）", async ({ request }, info) => {
    const readPaths = ["/api/tasks", "/api/connections", "/api/runs", "/api/mdm/models", "/api/users"];
    const readStatus = {};
    for (const path of readPaths) {
      readStatus[path] = (await request.get(path)).status();
    }
    expect(Object.values(readStatus).every((s) => s === 401), JSON.stringify(readStatus)).toBeTruthy();

    const write = await request.post("/api/connections", {
      data: { project: "E2E-Demo", name: "anon-blocked", ctype: "mysql", direction: "source", meta: {}, secret: "" },
    });
    expect(write.status(), "匿名建连接应 401").toBe(401);

    const del = await request.delete("/api/connections/not-exist");
    expect(del.status(), "匿名删除应 401").toBe(401);

    info.annotations.push({
      type: "fixed",
      description: `D-01/D-02/D-04 修复后：匿名读写一律 401（${JSON.stringify(readStatus)}）`,
    });
  });
});
