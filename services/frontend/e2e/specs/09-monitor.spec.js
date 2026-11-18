/* H. 监控与告警：资源降级、指标卡、运行详情、实时日志、对账与告警空态 */
import { test, expect } from "@playwright/test";
import { ADMIN, readSeed } from "../fixtures/env.js";
import { openAs, shot, dialog, authHeaders } from "../fixtures/session.js";

test.describe("H. 监控与告警", () => {
  test.beforeEach(async ({ page }) => {
    await openAs(page, ADMIN, "监控告警");
  });

  test("MO-01 非 Linux 主机资源降级为可读文案", async ({ page }, info) => {
    await expect(page.locator("main h1")).toHaveText("监控与运行历史");
    await expect(page.locator(".mac-card", { hasText: "系统资源守护" })).toContainText("当前平台不支持主机资源采集");
    // 接口本身正常返回（不再 500），并以 supported=false 标记
    const resp = await page.request.get("/api/monitor/system", {
      headers: await authHeaders(page.request),
    });
    expect(resp.status(), "系统资源接口不应因缺少 /proc 而报错").toBe(200);
    expect((await resp.json()).supported).toBe(false);
    // 降级不应连带其它区域报错
    await expect(page.locator(".mac-callout--warning")).toHaveCount(0);
    await shot(page, "MO-01-资源降级", info);
  });

  test("MO-02 六张指标卡数值有效", async ({ page }, info) => {
    const labels = page.locator(".mac-stat__label");
    await expect(labels).toHaveCount(6);
    const values = await page.locator(".mac-stat__value").allTextContents();
    expect(values.every((v) => v.trim() !== "" && v.trim() !== "NaN"), `指标卡异常：${values}`).toBeTruthy();
    await expect(labels.nth(0)).toHaveText("运行批次");
    await expect(labels.nth(4)).toHaveText("平均延迟");
    await expect(labels.nth(5)).toHaveText("启用的告警");
    await shot(page, "MO-02-指标卡", info);
  });

  test("MO-03 最近运行可打开详情", async ({ page }, info) => {
    const card = page.locator(".mac-card", { hasText: "最近运行" }).first();
    const firstRow = card.locator(`.mac-table tbody tr[title="点击查看运行详情"]`).first();
    await expect(firstRow).toBeVisible();
    await firstRow.locator("td").first().click();
    const dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toHaveText("运行详情");
    await expect(dlg).toContainText("运行实例");
    await expect(dlg).toContainText("运行日志");
    await shot(page, "MO-03-运行详情", info);
    await dlg.getByRole("button", { name: "关闭" }).last().click();
  });

  test("MO-04 实时日志区展示最近一次运行日志", async ({ page }, info) => {
    const card = page.locator(".mac-card", { hasText: "实时日志" });
    const pre = card.locator("pre");
    await expect(pre).toBeVisible();
    // 日志取自「最近一次运行」，需等 runs 与 logs 两跳请求返回
    await expect.poll(async () => ((await pre.textContent()) || "").trim(), { timeout: 20_000 })
      .not.toBe("(无日志)");
    const text = (await pre.textContent()) || "";
    expect(text.length, "实时日志应有内容").toBeGreaterThan(10);
    await shot(page, "MO-04-实时日志", info);
  });

  test("MO-05 数据对账表在无记录时给出空态", async ({ page }, info) => {
    const card = page.locator(".mac-card", { hasText: "数据对账" });
    await expect(card.locator(".mac-badge").first()).toContainText(/一致|存在差异/);
    await expect(card.locator(".mac-table tbody")).toContainText(/暂无对账记录|一致|差异/);
    await shot(page, "MO-05-对账", info);
  });

  test("MO-06 告警事件表空态与刷新可用", async ({ page }, info) => {
    const card = page.locator(".mac-card", { hasText: "告警事件" });
    await expect(card.locator(".mac-table tbody")).toContainText(/暂无告警事件|触发/);
    await page.getByRole("button", { name: "刷新" }).first().click();
    await expect(page.locator("main h1")).toHaveText("监控与运行历史");
    await shot(page, "MO-06-告警事件", info);
  });

  test("MO-07 运行详情中步骤指标与任务上下文可用", async ({ page }, info) => {
    const { taskOk } = readSeed();
    const card = page.locator(".mac-card", { hasText: "最近运行" }).first();
    // 取基线任务（e2e-task-orders）最近的一次运行：其它用例可能删任务，导致「任务上下文」无从加载
    const cell = card.locator(`td[title="${taskOk}"]`).first();
    await expect(cell).toBeVisible();
    await cell.locator("xpath=ancestor::tr[1]").locator("td").first().click();
    const dlg = dialog(page);
    await expect(dlg).toContainText("任务上下文");
    await expect(dlg).toContainText("任务名称");
    await expect(dlg).toContainText("源对象");
    // 日志条数提示（N 条）
    await expect(dlg).toContainText(/\d+ 条/);
    await shot(page, "MO-07-任务上下文", info);
    await dlg.getByRole("button", { name: "关闭" }).last().click();
  });
});
