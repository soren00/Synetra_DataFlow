/* I. 元数据与血缘：血缘图、字段映射明细 */
import { test, expect } from "@playwright/test";
import { ADMIN, SOURCE_TABLE, TARGET_TABLE } from "../fixtures/env.js";
import { openAs, shot, authHeaders } from "../fixtures/session.js";

test.describe("I. 元数据与血缘", () => {
  test.beforeEach(async ({ page }) => {
    await openAs(page, ADMIN, "血缘图谱");
  });

  test("LN-01 血缘图按任务聚合展示源→目标边", async ({ page }, info) => {
    await expect(page.locator("main h1")).toHaveText("元数据与血缘");
    await expect(page.locator(".mac-callout--warning")).toHaveCount(0);
    const card = page.locator(".mac-card", { hasText: "血缘图" });
    await expect(card).toContainText("来源");
    await expect(card).toContainText("目标");
    const rows = card.locator(".mac-table tbody tr");
    await expect(rows.first()).toBeVisible();
    expect(await rows.count()).toBeGreaterThan(0);
    await expect(card).toContainText(SOURCE_TABLE);
    await expect(card).toContainText(TARGET_TABLE);
    await shot(page, "LN-01-血缘图", info);
  });

  test("LN-02 字段映射明细为字段级血缘", async ({ page }, info) => {
    const card = page.locator(".mac-card", { hasText: "字段映射明细" });
    await expect(card).toContainText("来源字段");
    await expect(card).toContainText("目标字段");
    const rows = card.locator("tbody tr");
    await expect(rows.first()).toBeVisible();
    const text = await rows.first().textContent();
    expect(text, "应存在字段级血缘").toContain("字段级");
    await expect(card).toContainText("order_no");
    await shot(page, "LN-02-字段明细", info);
  });

  test("LN-03 任务数统计与血缘条目数量自洽", async ({ page }, info) => {
    const card = page.locator(".mac-card", { hasText: "字段映射明细" });
    await expect(card.locator(".mac-card__sub")).toContainText(/\d+ 个任务/);
    const tasksResp = await page.request.get("/api/tasks", { headers: await authHeaders(page.request) });
    const tasks = await tasksResp.json();
    const sub = await card.locator(".mac-card__sub").textContent();
    expect(sub).toContain(`${tasks.length} 个任务`);
    await shot(page, "LN-03-任务数", info);
  });
});
