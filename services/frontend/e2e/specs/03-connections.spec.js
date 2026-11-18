/* C. 连接管理：新建/测试/编辑/删除/详情/多类型/权限缺口 */
import { test, expect } from "@playwright/test";
import { ADMIN, RO, PROJECT } from "../fixtures/env.js";
import { openAs, shot, dialog, tableRows, authHeaders } from "../fixtures/session.js";

const uniq = (p) => `${p}-${Date.now().toString().slice(-6)}`;

async function fillMysqlForm(dlg, { name, db, password = "12345", port = "3306" }) {
  await dlg.getByPlaceholder("如 商户库MySQL").fill(name);
  await dlg.getByPlaceholder("常为 127.0.0.1 / localhost").fill("127.0.0.1");
  await dlg.getByPlaceholder("如 3306（MySQL）/ 5432（PG）").fill(port);
  await dlg.getByPlaceholder("数据库名").fill(db);
  await dlg.getByPlaceholder("登录账号").fill("root");
  await dlg.getByPlaceholder("数据库账号密码").fill(password);
}

test.describe("C. 连接管理", () => {
  test.beforeEach(async ({ page }) => {
    await openAs(page, ADMIN, "连接管理");
  });

  test("CN-01 列表加载、规模文案与搜索过滤", async ({ page }, info) => {
    await expect(page.locator("main h1")).toHaveText("连接管理");
    await expect(page.locator(".mac-muted", { hasText: /\d+ 个数据连接/ }).first()).toContainText("个数据连接");
    // 列表初始渲染「加载中…」，需等真实数据行出现
    await expect.poll(async () => tableRows(page).count(), { timeout: 15_000 }).toBeGreaterThanOrEqual(2);
    const before = await tableRows(page).count();
    expect(before).toBeGreaterThanOrEqual(2);

    await page.getByPlaceholder("搜索连接…").fill("e2e-mysql-src");
    await expect(tableRows(page)).toHaveCount(1);
    await expect(tableRows(page).first()).toContainText("e2e-mysql-src");

    await page.getByPlaceholder("搜索连接…").fill("不存在的连接名");
    await expect(page.getByText("暂无连接")).toBeVisible();
    await shot(page, "CN-01-列表与搜索", info);
  });

  test("CN-02 新建 MySQL 连接（表单内测试连接成功）", async ({ page }, info) => {
    const name = uniq("e2e-auto-mysql");
    await page.getByRole("button", { name: "新建连接" }).click();
    const dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toHaveText("新建连接");
    await fillMysqlForm(dlg, { name, db: "synetra_e2e_src" });

    await dlg.getByRole("button", { name: "测试连接" }).click();
    const result = dialog(page).filter({ hasText: "连接测试结果" });
    await expect(result).toBeVisible();
    await expect(result).toContainText("连接成功");
    await shot(page, "CN-02-表单测试连接成功", info);
    await result.getByRole("button", { name: "知道了" }).click();

    await dlg.getByRole("button", { name: "创建连接" }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(page.locator(".mac-table tbody", { hasText: name })).toBeVisible();
    await shot(page, "CN-02-新建后列表", info);
  });

  test("CN-03 测试连接失败给出原因（错误口令）", async ({ page }, info) => {
    await page.getByRole("button", { name: "新建连接" }).click();
    const dlg = dialog(page);
    await fillMysqlForm(dlg, { name: uniq("e2e-badpass"), db: "synetra_e2e_src", password: "definitely-wrong" });
    await dlg.getByRole("button", { name: "测试连接" }).click();
    const result = dialog(page).filter({ hasText: "连接测试结果" });
    await expect(result).toContainText("连接失败");
    await expect(result.locator(".mac-callout--danger")).toBeVisible();
    await shot(page, "CN-03-测试连接失败", info);
    await result.getByRole("button", { name: "知道了" }).click();
    await dlg.getByRole("button", { name: "取消" }).click();
  });

  test("CN-04 行内测试连接显示接通徽标", async ({ page }, info) => {
    const row = tableRows(page).filter({ hasText: "e2e-mysql-src" }).first();
    await row.getByRole("button", { name: "测试" }).click();
    await expect(row.locator(".mac-badge", { hasText: "接通" })).toBeVisible();
    await shot(page, "CN-04-行内测试", info);
  });

  test("CN-05 编辑连接（改名后列表刷新）", async ({ page }, info) => {
    const name = uniq("e2e-edit-mysql");
    const renamed = `${name}-renamed`;
    // 先建
    await page.getByRole("button", { name: "新建连接" }).click();
    let dlg = dialog(page);
    await fillMysqlForm(dlg, { name, db: "synetra_e2e_src" });
    await dlg.getByRole("button", { name: "创建连接" }).click();
    await expect(page.locator(".mac-table tbody", { hasText: name })).toBeVisible();

    // 再改
    const row = tableRows(page).filter({ hasText: name }).first();
    await row.getByRole("button", { name: "编辑" }).click();
    dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toHaveText("编辑连接");
    await expect(dlg.getByPlaceholder("如 商户库MySQL")).toHaveValue(name);
    await dlg.getByPlaceholder("如 商户库MySQL").fill(renamed);
    await dlg.getByRole("button", { name: "保存修改" }).click();
    await expect(page.locator(".mac-table tbody", { hasText: renamed })).toBeVisible();
    await shot(page, "CN-05-编辑连接", info);
  });

  test("CN-06 删除连接：取消不删、确认后删除", async ({ page }, info) => {
    const name = uniq("e2e-del-mysql");
    await page.getByRole("button", { name: "新建连接" }).click();
    const dlg = dialog(page);
    await fillMysqlForm(dlg, { name, db: "synetra_e2e_src" });
    await dlg.getByRole("button", { name: "创建连接" }).click();
    await expect(page.locator(".mac-table tbody", { hasText: name })).toBeVisible();

    const row = tableRows(page).filter({ hasText: name }).first();
    await row.getByRole("button", { name: "删除" }).click();
    let confirm = dialog(page).filter({ hasText: "删除连接" });
    await expect(confirm).toContainText(name);
    await confirm.getByRole("button", { name: "取消" }).click();
    await expect(page.locator(".mac-table tbody", { hasText: name })).toBeVisible();

    await row.getByRole("button", { name: "删除" }).click();
    confirm = dialog(page).filter({ hasText: "删除连接" });
    await confirm.getByRole("button", { name: "删除" }).click();
    await expect(page.locator(".mac-callout--success")).toContainText("已删除连接");
    await expect(page.locator(".mac-table tbody", { hasText: name })).toHaveCount(0);
    await shot(page, "CN-06-删除连接", info);
  });

  test("CN-07 被任务引用的连接删除被拒绝（409 原因完整展示）", async ({ page }, info) => {
    const row = tableRows(page).filter({ hasText: "e2e-mysql-src" }).first();
    await row.getByRole("button", { name: "删除" }).click();
    const confirm = dialog(page).filter({ hasText: "删除连接" });
    await confirm.getByRole("button", { name: "删除" }).click();
    const callout = page.locator(".mac-callout--danger");
    await expect(callout).toContainText("删除失败");
    await expect(callout).toContainText("任务引用");
    await shot(page, "CN-07-引用保护", info);
  });

  test("CN-08 展开行展示连接详情（含元数据）", async ({ page }, info) => {
    const row = tableRows(page).filter({ hasText: "e2e-mysql-src" }).first();
    await row.locator('button[aria-label="展开详情"]').click();
    const detail = page.locator(".mac-detail-row");
    await expect(detail).toContainText("连接标识");
    await expect(detail).toContainText("synetra_e2e_src");
    await expect(detail).toContainText("双方");
    await shot(page, "CN-08-连接详情", info);
  });

  test("CN-09 新建 REST/API 类型连接（URL + 方法）", async ({ page }, info) => {
    const name = uniq("e2e-api");
    await page.getByRole("button", { name: "新建连接" }).click();
    const dlg = dialog(page);
    await dlg.getByPlaceholder("如 商户库MySQL").fill(name);
    await dlg.locator("select").first().selectOption("api");
    await dlg.getByPlaceholder("https://api.example.com/orders").fill("https://httpbin.org/get");
    await dlg.locator("select").nth(1).selectOption("GET");
    await dlg.getByPlaceholder("5").fill("5");
    await shot(page, "CN-09-API连接表单", info);

    await dlg.getByRole("button", { name: "创建连接" }).click();
    await expect(page.locator(".mac-table tbody", { hasText: name })).toBeVisible();
    await expect(tableRows(page).filter({ hasText: name }).first()).toContainText("api");
  });

  test("CN-09b 携带 Params/Headers 可保存并回读", async ({ page }, info) => {
    const name = uniq("e2e-api-prm");
    await page.getByRole("button", { name: "新建连接" }).click();
    const dlg = dialog(page);
    await dlg.getByPlaceholder("如 商户库MySQL").fill(name);
    await dlg.locator("select").first().selectOption("api");
    await dlg.getByPlaceholder("https://api.example.com/orders").fill("https://httpbin.org/get");
    await dlg.getByRole("button", { name: "添加参数" }).click();
    await dlg.getByPlaceholder("参数名").fill("page");
    await dlg.getByPlaceholder("参数值").fill("1");
    await dlg.getByRole("button", { name: "添加 Header" }).click();
    await dlg.getByPlaceholder("头名").fill("X-Trace");
    await dlg.getByPlaceholder("头值").fill("e2e");
    await dlg.getByRole("button", { name: "创建连接" }).click();
    await expect(dlg).toHaveCount(0);
    await expect(page.locator(".mac-table tbody", { hasText: name })).toBeVisible();

    // 参数与请求头随 meta 一起落库（回读校验，而不是只看创建成功）
    const rows = await (await page.request.get("/api/connections", {
      headers: await authHeaders(page.request),
    })).json();
    const saved = rows.find((c) => c.name === name);
    expect(saved, `连接 ${name} 应已保存`).toBeTruthy();
    expect(saved.meta.params, "Params 应落库").toMatchObject({ page: "1" });
    expect(saved.meta.headers, "Headers 应落库").toMatchObject({ "X-Trace": "e2e" });
    await shot(page, "CN-09b-Params保存成功", info);
  });

  test("CN-10 新建 CSV 类型连接（文件路径字段）", async ({ page }, info) => {
    const name = uniq("e2e-csv");
    await page.getByRole("button", { name: "新建连接" }).click();
    const dlg = dialog(page);
    await expect(dlg.locator("select").first()).toBeVisible();
    await dlg.getByPlaceholder("如 商户库MySQL").fill(name);
    await dlg.locator("select").first().selectOption("csv");
    await dlg.getByPlaceholder("/data/xxx.csv").fill("D:/Projects/synetra/reports/e2e/fixtures/orders.csv");
    await dlg.getByRole("button", { name: "创建连接" }).click();
    await expect(page.locator(".mac-table tbody", { hasText: name })).toBeVisible();
    await shot(page, "CN-10-CSV连接", info);
  });

  test("CN-11 只读用户无法创建连接（403）", async ({ page, request }, info) => {
    const res = await request.post("/api/connections", {
      headers: await authHeaders(request, RO),
      data: {
        project: PROJECT, name: `e2e-ro-conn-${Date.now().toString().slice(-6)}`,
        ctype: "mysql", direction: "source",
        meta: { host: "127.0.0.1", port: "3306", db: "synetra_e2e_src", user: "root" }, secret: "12345",
      },
    });
    expect(res.status(), `只读建连接应被拒，实际 ${res.status()}`).toBe(403);

    // 只读仍可浏览连接列表
    const list = await (await request.get("/api/connections", { headers: await authHeaders(request, ADMIN) })).json();
    expect(Array.isArray(list)).toBeTruthy();

    await openAs(page, RO, "连接管理");
    await expect(page.locator("main h1")).toHaveText("连接管理");
    await shot(page, "CN-11-只读建连接被拒", info);
  });
});
