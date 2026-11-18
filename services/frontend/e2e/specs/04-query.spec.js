/* D. 只读查询 / 接口调试：库表树、SQL 执行、只读守卫、错误路径、REST 模式 */
import { test, expect } from "@playwright/test";
import { ADMIN } from "../fixtures/env.js";
import { openAs, shot } from "../fixtures/session.js";

const SQL_BOX = "textarea.mac-textarea";

async function selectConn(page, name) {
  const sel = page.locator("aside.mac-workbench__side select").first();
  await expect(sel).toBeVisible();
  const readOpts = async () => sel.locator("option").evaluateAll(
    (os) => os.map((o) => ({ value: o.value, text: (o.textContent || "").trim() })));
  // 连接列表为异步加载，首帧只有「（无可用连接）」占位项
  await expect.poll(async () => (await readOpts()).some((o) => o.text.startsWith(name)), { timeout: 20_000 })
    .toBeTruthy();
  const hit = (await readOpts()).find((o) => o.text.startsWith(name));
  expect(hit).toBeTruthy();
  await sel.selectOption(hit.value);
}

test.describe("D. 只读查询 / 接口调试", () => {
  test.beforeEach(async ({ page }) => {
    await openAs(page, ADMIN, "只读查询");
  });

  test("QR-01 关系库模式枚举库表树", async ({ page }, info) => {
    await expect(page.locator("main h1")).toHaveText("只读查询 / 接口调试");
    await selectConn(page, "e2e-mysql-src");
    await expect(page.locator(".mac-tree")).toBeVisible();
    await expect(page.locator(".mac-tree__item", { hasText: "e2e_orders" })).toHaveCount(1);
    await expect(page.locator(".mac-tree__item", { hasText: "e2e_customers" })).toHaveCount(1);
    await shot(page, "QR-01-库表树", info);
  });

  test("QR-02 点击表名填入基础查询语句", async ({ page }, info) => {
    await selectConn(page, "e2e-mysql-src");
    await page.locator('.mac-tree__item[title="点击填入查询语句：e2e_orders"]').click();
    await expect(page.locator(SQL_BOX)).toHaveValue(/e2e_orders/);
    await expect(page.locator(SQL_BOX)).toHaveValue(/SELECT/i);
    await shot(page, "QR-02-填入语句", info);
  });

  test("QR-03 执行 SELECT 返回结果表格", async ({ page }, info) => {
    await selectConn(page, "e2e-mysql-src");
    await page.locator(SQL_BOX).fill("SELECT id, order_no, amount FROM e2e_orders ORDER BY id LIMIT 5;");
    await page.getByRole("button", { name: "执行" }).click();
    const card = page.locator(".mac-card", { hasText: "查询结果" }).last();
    await expect(card).toContainText("行");
    await expect(card.locator(".mac-table tbody tr")).toHaveCount(5);
    await expect(card.locator(".mac-table thead")).toContainText("order_no");
    await shot(page, "QR-03-查询结果", info);
  });

  test("QR-04 Ctrl+Enter 快捷执行", async ({ page }, info) => {
    await selectConn(page, "e2e-mysql-src");
    await page.locator(SQL_BOX).fill("SELECT COUNT(*) AS cnt FROM e2e_orders;");
    await page.locator(SQL_BOX).focus();
    await page.keyboard.press("Control+Enter");
    const card = page.locator(".mac-card", { hasText: "查询结果" }).last();
    await expect(card).toContainText("1 行");
    await shot(page, "QR-04-快捷执行", info);
  });

  test("QR-05 写操作被只读守卫拒绝", async ({ page }, info) => {
    await selectConn(page, "e2e-mysql-src");
    await page.locator(SQL_BOX).fill("DELETE FROM e2e_orders WHERE id = 1;");
    await page.getByRole("button", { name: "执行" }).click();
    const err = page.locator(".mac-callout--danger");
    await expect(err).toContainText("查询失败");
    await expect(err).toContainText(/只读|仅允许|禁止/i);
    // 守卫必须真的没删：再查一次确认数据完整
    await page.locator(SQL_BOX).fill("SELECT COUNT(*) AS cnt FROM e2e_orders;");
    await page.getByRole("button", { name: "执行" }).click();
    await expect(page.locator(".mac-card", { hasText: "查询结果" }).last()).toContainText("1 行");
    await shot(page, "QR-05-只读守卫", info);
  });

  test("QR-06 SQL 语法错误给出可读提示", async ({ page }, info) => {
    await selectConn(page, "e2e-mysql-src");
    await page.locator(SQL_BOX).fill("SELECT * FORM e2e_orders;");
    await page.getByRole("button", { name: "执行" }).click();
    await expect(page.locator(".mac-callout--danger")).toContainText("查询失败");
    await shot(page, "QR-06-语法错误", info);
  });

  test("QR-07 关键字搜索与类型筛选", async ({ page }, info) => {
    await selectConn(page, "e2e-mysql-src");
    await page.getByPlaceholder("搜索表或视图").fill("customers");
    await expect(page.locator(".mac-tree__item")).toHaveCount(1);
    await expect(page.locator(".mac-tree")).toContainText("e2e_customers");
    await page.getByPlaceholder("搜索表或视图").fill("zzz-no-match");
    await expect(page.getByText("没有匹配的对象")).toBeVisible();
    await shot(page, "QR-07-搜索筛选", info);
  });

  test("QR-08 REST/API 模式：无 API 连接时给出明确提示", async ({ page }, info) => {
    await page.locator(".mac-segmented").getByRole("tab", { name: "REST/API" }).click();
    await expect(page.locator("main")).toContainText("请求调试");
    await expect(page.locator("aside.mac-workbench__side")).toContainText("仅 REST/API");
    await page.getByRole("button", { name: "执行" }).click();
    await expect(page.locator(".mac-callout--danger")).toContainText("无可用的连接");
    await shot(page, "QR-08-REST模式", info);
  });

  test("QR-09 切换连接后左列筛选条件被重置", async ({ page }, info) => {
    await selectConn(page, "e2e-mysql-src");
    await page.getByPlaceholder("搜索表或视图").fill("customers");
    await selectConn(page, "e2e-mysql-dst");
    await expect(page.getByPlaceholder("搜索表或视图")).toHaveValue("");
    // 目标库同时存在 e2e_orders 与 e2e_orders_out，需按整名精确匹配
    await expect(page.locator(".mac-tree__name", { hasText: /^e2e_orders$/ })).toHaveCount(1);
    await shot(page, "QR-09-切换连接", info);
  });
});
