/* A. 平台与认证：登录、会话、角色可见性、身份伪造 */
import { test, expect } from "@playwright/test";
import { ADMIN, DEV, RO } from "../fixtures/env.js";
import { actAs, gotoPage, loginViaForm, waitApp, shot, SESSION_KEY } from "../fixtures/session.js";

test.describe("A. 平台与认证", () => {
  test("AUTH-01 未登录访问渲染登录页", async ({ page }, info) => {
    await page.goto("/");
    await expect(page.locator("#login-user")).toBeVisible();
    await expect(page.locator("#login-pass")).toBeVisible();
    await expect(page.locator("#login-user")).toHaveAttribute("placeholder", "admin");
    await expect(page.locator(".app-side")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "登录" })).toBeVisible();
    await shot(page, "AUTH-01-登录页", info);
  });

  test("AUTH-02 错误口令被拒绝并给出提示", async ({ page }, info) => {
    await page.goto("/");
    await page.fill("#login-user", ADMIN.username);
    await page.fill("#login-pass", "wrong-password");
    await page.getByRole("button", { name: "登录" }).click();
    await expect(page.locator(".mac-callout--danger")).toContainText("用户名或密码错误");
    await expect(page.locator(".app-side")).toHaveCount(0);
    await shot(page, "AUTH-02-错误口令", info);
  });

  test("AUTH-03 admin 登录后可进入总览且菜单齐全", async ({ page }, info) => {
    await loginViaForm(page, ADMIN);
    await waitApp(page);
    await expect(page.locator("main.app-main h1")).toHaveText("总览");
    const tabs = page.locator(".app-side").getByRole("tab");
    await expect(tabs).toHaveCount(11);
    for (const label of ["总览", "连接管理", "只读查询", "ETL 项目", "AI ETL 任务", "定时任务",
      "血缘图谱", "监控告警", "主数据", "权限凭据", "AI 管理"]) {
      await expect(page.locator(".app-side").getByRole("tab", { name: label, exact: true })).toBeVisible();
    }
    await expect(page.locator(".mac-avatar").first()).toHaveAttribute("title", "admin");
    await shot(page, "AUTH-03-admin登录", info);
  });

  test("AUTH-04 刷新后会话保持（localStorage）", async ({ page }, info) => {
    await loginViaForm(page, ADMIN);
    await waitApp(page);
    await page.reload();
    await waitApp(page);
    await expect(page.locator("main.app-main h1")).toHaveText("总览");
    const stored = JSON.parse(
      (await page.evaluate((k) => window.localStorage.getItem(k), SESSION_KEY)) || "{}",
    );
    // 会话含身份与签名票据；票据是后续请求的唯一身份来源
    expect(stored.username).toBe("admin");
    expect(stored.role).toBe("admin");
    expect(typeof stored.token).toBe("string");
    expect(stored.token.length).toBeGreaterThan(20);
    await shot(page, "AUTH-04-刷新保持", info);
  });

  test("AUTH-05 退出登录回到登录页", async ({ page }, info) => {
    await loginViaForm(page, ADMIN);
    await waitApp(page);
    await page.locator('button[title="退出登录"]').click();
    await expect(page.locator("#login-user")).toBeVisible();
    const stored = await page.evaluate((k) => window.localStorage.getItem(k), SESSION_KEY);
    expect(stored).toBeNull();
    await shot(page, "AUTH-05-退出登录", info);
  });

  test("AUTH-07 developer 不可见「AI 管理」", async ({ page }, info) => {
    await actAs(page, DEV);
    await page.goto("/");
    await waitApp(page);
    await expect(page.locator(".app-side").getByRole("tab")).toHaveCount(10);
    await expect(page.locator(".app-side").getByRole("tab", { name: "AI 管理", exact: true })).toHaveCount(0);
    await shot(page, "AUTH-07-developer菜单", info);
  });

  test("AUTH-08 readonly 不可见「AI 管理」", async ({ page }, info) => {
    await actAs(page, RO);
    await page.goto("/");
    await waitApp(page);
    await expect(page.locator(".app-side").getByRole("tab")).toHaveCount(10);
    await expect(page.locator(".app-side").getByRole("tab", { name: "AI 管理", exact: true })).toHaveCount(0);
    await shot(page, "AUTH-08-readonly菜单", info);
  });

  test("AUTH-09 未持有效票据的请求一律 401", async ({ request }) => {
    const noHeader = await request.get("/api/users");
    expect(noHeader.status(), "无票据不应放行").toBe(401);

    // 伪造/过期票据同样不授予任何身份
    const forged = await request.get("/api/users", { headers: { Authorization: "Bearer v1.eyJ1IjoiYWRtaW4ifQ.AAAA" } });
    expect(forged.status(), "伪造票据不应被采信").toBe(401);

    // 读接口也不再对匿名开放
    const conns = await request.get("/api/connections");
    expect(conns.status(), "匿名读连接应 401").toBe(401);
  });

  test("AUTH-10 登录页仅承诺存在的初始账号", async ({ page }, info) => {
    await page.goto("/");
    await expect(page.locator("form")).toContainText("admin / admin");
    await expect(page.locator("form"), "不应再承诺不存在的 dev 账号").not.toContainText("dev / dev");
    // 未创建的账号登录仍被拒绝
    await page.fill("#login-user", "dev");
    await page.fill("#login-pass", "dev");
    await page.getByRole("button", { name: "登录" }).click();
    await expect(page.locator(".mac-callout--danger")).toContainText("用户名或密码错误");
    await shot(page, "AUTH-10-登录文案", info);
  });

  test("AUTH-11 顶部主题切换与导航项可点", async ({ page }, info) => {
    await actAs(page, ADMIN);
    await page.goto("/");
    await waitApp(page);
    const themeBtn = page.getByRole("button", { name: "切换主题" });
    await themeBtn.click();
    await themeBtn.click();
    for (const label of ["连接管理", "只读查询", "监控告警"]) {
      await gotoPage(page, label);
    }
    await expect(page.locator(".mac-toast, .mac-callout--danger")).toHaveCount(0);
    await shot(page, "AUTH-11-导航遍历", info);
  });
});
