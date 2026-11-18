/* K. 权限与凭据：用户 CRUD、改密、保护规则、Tab 现状、readonly 视角 */
import { test, expect } from "@playwright/test";
import { ADMIN, DEV, RO } from "../fixtures/env.js";
import { openAs, shot, dialog, tableRows, authHeaders } from "../fixtures/session.js";

const uniq = (p) => `${p}-${Date.now().toString().slice(-6)}`;
const NEW_USER = uniq("e2e-user");
const PASSWORD = "e2e-init-pass";

const rowOf = (page, name) => tableRows(page).filter({ hasText: name }).first();

test.describe.serial("K. 权限与凭据", () => {
  test("SEC-01 用户列表、搜索与角色筛选", async ({ page }, info) => {
    await openAs(page, ADMIN, "权限凭据");
    await expect(page.locator("main h1")).toHaveText("权限与凭据");
    await expect(page.locator(".mac-card__sub", { hasText: "当前会话" })).toContainText("admin（管理员）");
    await expect(rowOf(page, "admin")).toBeVisible();
    await expect(rowOf(page, "e2e-dev")).toBeVisible();

    await page.getByPlaceholder("搜索用户名 / 显示名").fill("e2e-dev");
    await expect(tableRows(page)).toHaveCount(1);
    await page.getByPlaceholder("搜索用户名 / 显示名").fill("");
    await expect(tableRows(page).first()).toBeVisible();

    await page.locator(".mac-row select").first().selectOption("developer");
    await expect(tableRows(page)).toHaveCount(1);
    await expect(rowOf(page, "e2e-dev")).toBeVisible();
    await page.locator(".mac-row select").first().selectOption("");
    await shot(page, "SEC-01-用户列表", info);
  });

  test("SEC-02 新建用户并可用新账号登录", async ({ page, request }, info) => {
    await openAs(page, ADMIN, "权限凭据");
    await page.getByRole("button", { name: "新建用户" }).click();
    const dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toHaveText("新建用户");
    await dlg.getByPlaceholder("登录名").fill(NEW_USER);
    await dlg.getByPlaceholder("展示名称").fill("E2E 开发者");
    await dlg.locator(".mac-field", { hasText: "角色" }).locator("select").selectOption("developer");
    await dlg.getByPlaceholder("用户首次登录使用").fill(PASSWORD);
    await dlg.getByRole("button", { name: "创建用户" }).click();
    await expect(dialog(page)).toHaveCount(0);

    const row = rowOf(page, NEW_USER);
    await expect(row).toBeVisible();
    await expect(row).toContainText("开发者");
    await expect(row).toContainText("启用");
    await shot(page, "SEC-02-新建用户", info);

    const login = await request.post("/api/auth/login", { data: { username: NEW_USER, password: PASSWORD } });
    expect(login.ok(), "新用户应可使用初始密码登录").toBeTruthy();
    expect((await login.json()).role).toBe("developer");
  });

  test("SEC-03 编辑用户显示名与角色", async ({ page }, info) => {
    await openAs(page, ADMIN, "权限凭据");
    await rowOf(page, NEW_USER).getByRole("button", { name: "编辑" }).click();
    const dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toContainText(`编辑用户 ${NEW_USER}`);
    await dlg.locator(".mac-field", { hasText: "显示名" }).locator("input").fill("E2E 只读");
    await dlg.locator(".mac-field", { hasText: "角色" }).locator("select").selectOption("readonly");
    await dlg.getByRole("button", { name: "保存修改" }).click();
    await expect(dialog(page)).toHaveCount(0);
    const row = rowOf(page, NEW_USER);
    await expect(row).toContainText("E2E 只读");
    await expect(row).toContainText("只读");
    await shot(page, "SEC-03-编辑用户", info);
  });

  test("SEC-04 管理员重置他人密码", async ({ page, request }, info) => {
    await openAs(page, ADMIN, "权限凭据");
    await rowOf(page, NEW_USER).getByRole("button", { name: "改密" }).click();
    const dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toContainText("重置密码");
    const NEW_PWD = "e2e-reset-pass";
    await dlg.locator('input[type="password"]').fill(NEW_PWD);
    await dlg.getByRole("button", { name: "确定" }).click();
    await expect(dialog(page)).toHaveCount(0);
    const login = await request.post("/api/auth/login", { data: { username: NEW_USER, password: NEW_PWD } });
    expect(login.ok(), "重置后的新密码应可登录").toBeTruthy();
    await shot(page, "SEC-04-重置密码", info);
  });

  test("SEC-05 删除用户", async ({ page, request }, info) => {
    await openAs(page, ADMIN, "权限凭据");
    await rowOf(page, NEW_USER).getByRole("button", { name: "删除" }).click();
    const confirm = dialog(page).filter({ hasText: "删除用户" });
    await expect(confirm).toContainText(NEW_USER);
    await confirm.getByRole("button", { name: "删除" }).click();
    await expect(page.locator(".mac-table tbody", { hasText: NEW_USER })).toHaveCount(0);
    await shot(page, "SEC-05-删除用户", info);
    const list = await (await request.get("/api/users", { headers: await authHeaders(request) })).json();
    expect(list.some((u) => u.username === NEW_USER)).toBeFalsy();
  });

  test("SEC-06 当前登录账号不可自操作（界面屏蔽 + 后端 403）", async ({ page, request }, info) => {
    await openAs(page, ADMIN, "权限凭据");
    const row = rowOf(page, "admin");
    await expect(row).toContainText("当前账号");
    await expect(row.getByRole("button", { name: "编辑" })).toHaveCount(0);
    await expect(row.getByRole("button", { name: "删除" })).toHaveCount(0);
    // 停用自己 → 403（自助停用判定优先于「最后一名管理员」保护）
    const r1 = await request.put("/api/users/admin", {
      headers: await authHeaders(request), data: { enabled: false },
    });
    expect(r1.status(), `自助停用应返回 403，实际 ${r1.status()}`).toBe(403);
    expect((await r1.json()).detail).toContain("不能停用");
    // 删除自己 → 403
    const r2 = await request.delete("/api/users/admin", { headers: await authHeaders(request) });
    expect(r2.status()).toBe(403);
    await shot(page, "SEC-06-自我保护", info);
  });

  test("SEC-07 最后一名管理员不可降级（409）", async ({ request }) => {
    const res = await request.put("/api/users/admin", {
      headers: await authHeaders(request), data: { role: "developer" },
    });
    expect(res.status(), "应保留至少一名启用管理员").toBe(409);
    await expect((await res.json()).detail).toContain("至少需要保留一名启用的管理员");
  });

  test("SEC-08 API 凭据可签发/吊销，审计日志可查询", async ({ page }, info) => {
    await openAs(page, ADMIN, "权限凭据");
    await page.locator(".mac-segmented").getByRole("tab", { name: "API 凭据" }).click();

    await page.getByRole("button", { name: "签发凭据" }).click();
    await expect(page.locator(".mac-callout--warning")).toContainText("仅展示这一次");
    await expect(page.locator(".mac-table tbody tr").first()).toBeVisible();
    await shot(page, "SEC-08-凭据签发", info);

    const revoke = page.getByRole("button", { name: "吊销" }).first();
    if (await revoke.count()) {
      await revoke.click();
      await expect(page.locator(".mac-table tbody")).toContainText("已吊销");
      await shot(page, "SEC-08-凭据吊销", info);
    }

    await page.locator(".mac-segmented").getByRole("tab", { name: "审计日志" }).click();
    await expect(page.locator(".mac-table tbody tr").first()).toBeVisible();
    await shot(page, "SEC-08-审计Tab", info);
  });

  test("SEC-09 readonly 用户打开权限页被拒且无写操作入口", async ({ page }, info) => {
    await openAs(page, RO, "权限凭据");
    await expect(page.locator(".mac-callout--danger")).toContainText("用户列表不可达");
    await expect(page.getByRole("button", { name: "新建用户" })).toHaveCount(0);
    await expect(page.locator(".mac-table tbody")).toContainText("暂无用户");
    await shot(page, "SEC-09-只读视角", info);
  });

  test("SEC-10 修改自己密码：旧密码错误应被拒绝", async ({ page }, info) => {
    await openAs(page, ADMIN, "权限凭据");
    await page.getByRole("button", { name: "修改我的密码" }).click();
    const dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toHaveText("修改我的密码");
    const inputs = dlg.locator('input[type="password"]');
    await inputs.nth(0).fill("wrong-old");
    await inputs.nth(1).fill("newpass123");
    await dlg.getByRole("button", { name: "确定" }).click();
    await expect(page.locator(".mac-callout--danger")).toContainText("重置失败");
    await shot(page, "SEC-10-改密校验", info);
    await dlg.getByRole("button", { name: "取消" }).click();
  });

  test("SEC-11 developer 可进入权限页但无管理入口", async ({ page }, info) => {
    await openAs(page, DEV, "权限凭据");
    await expect(page.locator(".mac-callout--danger")).toContainText("用户列表不可达");
    await expect(page.getByRole("button", { name: "新建用户" })).toHaveCount(0);
    await expect(page.locator(".mac-card__sub", { hasText: "当前会话" })).toContainText("e2e-dev（开发者）");
    await shot(page, "SEC-11-开发者视角", info);
  });
});
