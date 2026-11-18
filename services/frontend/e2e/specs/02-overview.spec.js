/* B. 总览：健康徽标、统计卡、状态筛选、分页、运行详情 */
import { test, expect } from "@playwright/test";
import { ADMIN } from "../fixtures/env.js";
import { openAs, shot, dialog, tableRows } from "../fixtures/session.js";

test.describe("B. 总览", () => {
  test.beforeEach(async ({ page }) => {
    await openAs(page, ADMIN, "总览");
  });

  test("OV-01 服务健康徽标为 ok", async ({ page }, info) => {
    await expect(page.locator(".mac-badge", { hasText: "服务 ok" })).toBeVisible();
    await expect(page.locator(".mac-callout--danger")).toHaveCount(0);
    await expect(page.getByText("一切正常")).toBeVisible();
    await shot(page, "OV-01-健康状态", info);
  });

  test("OV-02 四张统计卡数值有效且与运行记录一致", async ({ page }, info) => {
    const labels = page.locator(".mac-stat__label");
    await expect(labels).toHaveCount(4);
    await expect(labels.nth(0)).toHaveText("运行批次");
    await expect(labels.nth(1)).toHaveText("成功");
    await expect(labels.nth(2)).toHaveText("失败");
    await expect(labels.nth(3)).toHaveText("同步总行数");

    const readValues = async () => page.locator(".mac-stat__value").allTextContents();
    // 统计卡初始为 0，需等 /runs 返回后再断言（避免读到首帧）
    await expect.poll(async () => Number((await readValues())[0]), { timeout: 15_000 }).toBeGreaterThanOrEqual(2);
    const values = await readValues();
    expect(values.every((v) => v.trim() !== "" && v.trim() !== "NaN"), `统计卡不应为空/NaN：${values}`).toBeTruthy();
    expect(Number(values[1])).toBeGreaterThanOrEqual(1);
    expect(Number(values[2])).toBeGreaterThanOrEqual(1);
    await shot(page, "OV-02-统计卡", info);
  });

  test("OV-03 状态筛选：仅显示失败运行", async ({ page }, info) => {
    await page.locator(".mac-segmented").getByRole("tab", { name: /失败/ }).click();
    await expect(page.locator(".mac-segmented__item--active")).toContainText("失败");
    await expect(page.locator(".mac-table tbody")).toContainText("失败");
    const badges = await page.locator(".mac-table tbody .mac-badge").allTextContents();
    expect(badges.length).toBeGreaterThan(0);
    expect(badges.every((b) => b.includes("失败")), `筛选后应只有失败：${badges}`).toBeTruthy();
    await shot(page, "OV-03-筛选失败", info);
  });

  test("OV-04 分页信息与翻页控件", async ({ page }, info) => {
    const pager = page.locator(".mac-pager");
    await expect(pager).toBeVisible();
    await expect(pager.locator(".mac-pager__info")).toContainText("共");
    await expect(pager.locator(".mac-pager__page")).toHaveText(/1 \/ \d+/);
    // 种子数据量小于一页，下一页应禁用
    await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "上一页" })).toBeDisabled();
    // 切换每页条数
    await page.locator(".mac-pager__size").selectOption("20");
    await expect(page.locator(".mac-pager__page")).toHaveText(/1 \/ \d+/);
    await shot(page, "OV-04-分页", info);
  });

  test("OV-05 点击运行行打开详情，含运行信息与日志", async ({ page }, info) => {
    // 只认真实运行行（占位行「暂无运行记录」不可点）
    const firstRow = page.locator('.mac-table tbody tr[title="点击查看运行详情"]').first();
    await expect(firstRow).toBeVisible();
    // 点第一列单元格：行的「操作」列 td 有 stopPropagation，点行中心可能落在该列而不触发行点击
    await firstRow.locator("td").first().click();
    const dlg = dialog(page);
    await expect(dlg).toBeVisible();
    await expect(dlg.locator(".mac-card__title").first()).toHaveText("运行详情");
    await expect(dlg).toContainText("运行信息");
    await expect(dlg).toContainText("任务上下文");
    await expect(dlg).toContainText("运行日志");
    await expect(dlg).toContainText("运行实例");
    await shot(page, "OV-05-运行详情", info);
    await dlg.getByRole("button", { name: "关闭" }).last().click();
    await expect(dlg).toHaveCount(0);
  });

  test("OV-06 刷新按钮可用且不产生错误提示", async ({ page }, info) => {
    await page.getByRole("button", { name: "刷新" }).click();
    await expect(page.locator(".mac-badge", { hasText: "服务 ok" })).toBeVisible();
    await expect(page.locator(".mac-callout--danger")).toHaveCount(0);
    await shot(page, "OV-06-刷新", info);
  });
});
