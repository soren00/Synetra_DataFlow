/* J. 主数据管理：实体定义、字段解析、详情展开；记录 CRUD 无 UI 入口（转 API 验证） */
import { test, expect } from "@playwright/test";
import { ADMIN, DEV } from "../fixtures/env.js";
import { openAs, shot, dialog, tableRows, authHeaders } from "../fixtures/session.js";

const ENTITY = `e2e_customer_${Date.now().toString().slice(-6)}`;

test.describe.serial("J. 主数据管理", () => {
  test("MDM-01 实体列表可加载（空态或列表）", async ({ page }, info) => {
    await openAs(page, ADMIN, "主数据");
    await expect(page.locator("main h1")).toHaveText("主数据管理");
    await expect(page.locator(".mac-card", { hasText: "主数据实体" })).toBeVisible();
    await expect(page.locator(".mac-table thead")).toContainText("同步状态");
    await shot(page, "MDM-01-实体列表", info);
  });

  test("MDM-02 定义实体：按行解析字段定义并落库", async ({ page }, info) => {
    await openAs(page, ADMIN, "主数据");
    await page.getByRole("button", { name: "定义实体" }).click();
    const dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toHaveText("定义实体");
    await dlg.getByPlaceholder("customer").fill(ENTITY);
    await dlg.getByPlaceholder("客户").fill("E2E 客户主数据");
    await dlg.locator("textarea").fill("id,int,required,unique\nemail,string,unique,format=email\nname,string,required");
    await shot(page, "MDM-02-定义实体表单", info);
    await dlg.getByRole("button", { name: "创建实体" }).click();
    await expect(dialog(page)).toHaveCount(0);

    const row = tableRows(page).filter({ hasText: ENTITY }).first();
    await expect(row).toBeVisible();
    await expect(row).toContainText("E2E 客户主数据");
    // 三行定义 → 3 个字段（该表未给数值列挂 .num，按单元格文本断言）
    const cells = (await row.locator("td").allTextContents()).map((s) => s.trim());
    expect(cells, `字段数应为 3，实际单元格：${JSON.stringify(cells)}`).toContain("3");
    await shot(page, "MDM-02-实体落库", info);
  });

  test("MDM-03 展开实体详情展示质量规则数", async ({ page }, info) => {
    await openAs(page, ADMIN, "主数据");
    const row = tableRows(page).filter({ hasText: ENTITY }).first();
    await row.locator('button[aria-label="展开详情"]').click();
    const detail = page.locator(".mac-detail-row");
    await expect(detail).toContainText("实体标识");
    await expect(detail).toContainText(ENTITY);
    await expect(detail).toContainText("质量规则");
    await expect(detail).toContainText("3 条");
    await shot(page, "MDM-03-实体详情", info);
  });

  test("MDM-04 记录级 CRUD 与同步具备 UI 入口", async ({ page, request }, info) => {
    await openAs(page, ADMIN, "主数据");
    const row = tableRows(page).filter({ hasText: ENTITY }).first();
    await row.locator('button[aria-label="展开详情"]').click();
    const detail = page.locator(".mac-detail-row");

    await expect(detail.getByRole("button", { name: "新增记录" })).toBeVisible();
    await expect(detail.getByRole("button", { name: "同步分发" })).toBeVisible();

    // 新增记录（字段按实体定义渲染：id / email / name）
    await detail.getByRole("button", { name: "新增记录" }).click();
    const dlg = dialog(page);
    const inputs = dlg.locator("input");
    await inputs.nth(0).fill("7");
    await inputs.nth(1).fill("ui@e2e.com");
    await inputs.nth(2).fill("UI张三");
    await shot(page, "MDM-04-新增记录表单", info);
    await dlg.getByRole("button", { name: "保存" }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(detail).toContainText("ui@e2e.com");

    // 同步分发给出可读结果
    await detail.getByRole("button", { name: "同步分发" }).click();
    await expect(page.locator(".mac-callout--success")).toContainText("同步完成");

    // 删除记录
    const recRow = detail.locator("tbody tr").filter({ hasText: "ui@e2e.com" });
    await recRow.getByRole("button", { name: "删除" }).click();
    await page.locator('[role="dialog"]').getByRole("button", { name: "删除" }).click();
    await expect(detail).not.toContainText("ui@e2e.com");

    // 后端唯一约束仍生效（UI 之外的防线）
    const h = await authHeaders(request, DEV);
    const base = { id: 21, email: "dup@e2e.com", name: "张三" };
    expect((await request.post(`/api/mdm/models/${ENTITY}/records`, { headers: h, data: { record: base } })).status()).toBe(201);
    const dup = await request.post(`/api/mdm/models/${ENTITY}/records`, {
      headers: h, data: { record: { id: 22, email: "dup@e2e.com", name: "重复邮箱" } },
    });
    expect(dup.status()).toBe(422);
    await shot(page, "MDM-04-记录维护", info);
  });
});
