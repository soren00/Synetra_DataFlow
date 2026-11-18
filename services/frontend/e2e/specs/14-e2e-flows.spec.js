/* N. 端到端主链路：连接→查询→任务→运行→监控→血缘，跨页面串联 */
import { test, expect } from "@playwright/test";
import { ADMIN, CONN_DST, SOURCE_TABLE, TARGET_TABLE, TASK_OK, TASK_SCHED } from "../fixtures/env.js";
import { openAs, gotoPage, shot, dialog, tableRows, authHeaders } from "../fixtures/session.js";
import { readSeed } from "../fixtures/env.js";

async function scalar(request, connKey, sql) {
  const res = await request.post(`/api/connections/${connKey}/query`, {
    headers: await authHeaders(request, ADMIN),
    data: { project: "E2E-Demo", sql, limit: 10 },
  });
  expect(res.ok(), `query failed: ${res.status()} ${await res.text()}`).toBeTruthy();
  const data = await res.json();
  const row = data.rows?.[0];
  return Array.isArray(row) ? Number(Object.values(row)[0]) : Number(Object.values(row || {})[0]);
}

test.describe.serial("N. 端到端主链路", () => {
  test("FLOW-01 连接测试 → 库表枚举 → 只读查询看到样例数据", async ({ page, request }, info) => {
    const { connSrc } = readSeed();
    await openAs(page, ADMIN, "连接管理");
    const row = tableRows(page).filter({ hasText: "e2e-mysql-src" }).first();
    await row.getByRole("button", { name: "测试" }).click();
    await expect(row.locator(".mac-badge", { hasText: "接通" })).toBeVisible();

    await gotoPage(page, "只读查询");
    const sel = page.locator("aside.mac-workbench__side select").first();
    const readOpts = async () => sel.locator("option").evaluateAll(
      (os) => os.map((o) => ({ v: o.value, t: (o.textContent || "").trim() })));
    await expect.poll(async () => (await readOpts()).some((o) => o.t.startsWith("e2e-mysql-src")), { timeout: 20_000 })
      .toBeTruthy();
    const hit = (await readOpts()).find((o) => o.t.startsWith("e2e-mysql-src"));
    await sel.selectOption(hit.v);
    await expect(page.locator(".mac-tree__item", { hasText: SOURCE_TABLE })).toHaveCount(1);

    await page.locator("textarea.mac-textarea").fill(`SELECT COUNT(*) AS cnt FROM ${SOURCE_TABLE};`);
    await page.getByRole("button", { name: "执行" }).click();
    const resultCard = page.locator(".mac-card", { hasText: "查询结果" }).last();
    await expect(resultCard).toContainText("1 行");
    await expect(resultCard.locator("td.num")).toContainText("25");

    // 与数据库直查一致
    expect(await scalar(request, connSrc, `SELECT COUNT(*) FROM ${SOURCE_TABLE}`)).toBe(25);
    await shot(page, "FLOW-01-连接查询链路", info);
  });

  test("FLOW-02 运行任务后目标表按预期增量写入", async ({ page, request }, info) => {
    const { connDst } = readSeed();
    const before = await scalar(request, connDst, `SELECT COUNT(*) FROM ${TARGET_TABLE}`);

    await openAs(page, ADMIN, "ETL 项目");
    await page.locator(".mac-table tbody tr", { hasText: "E2E-Demo" }).locator('button[title="进入项目"]').first().click();
    await expect(page.locator("main h1")).toHaveText("E2E-Demo");
    const taskRow = tableRows(page).filter({ hasText: TASK_OK }).first();
    await taskRow.getByRole("button", { name: "运行" }).click();
    await expect(page.locator(".mac-callout", { hasText: "运行状态" })).toContainText("成功", { timeout: 40_000 });

    const after = await scalar(request, connDst, `SELECT COUNT(*) FROM ${TARGET_TABLE}`);
    expect(after - before, "追加模式应写入与源表相同的 25 行").toBe(25);
    await shot(page, "FLOW-02-任务运行", info);
  });

  test("FLOW-03 运行结果在监控页可见并可查看详情", async ({ page }, info) => {
    await openAs(page, ADMIN, "监控告警");
    const card = page.locator(".mac-card", { hasText: "最近运行" }).first();
    const firstRow = card.locator('.mac-table tbody tr[title="点击查看运行详情"]').first();
    await expect(firstRow).toBeVisible();
    await firstRow.locator("td").first().click();
    const dlg = dialog(page);
    await expect(dlg).toContainText("写入行数");
    await expect(dlg).toContainText("成功");
    await expect(dlg).toContainText("e2e-task");
    // 行数正确性已在 FLOW-02 用目标库直查断言（25 行），此处只验证详情可读
    await shot(page, "FLOW-03-监控可见", info);
    await dlg.getByRole("button", { name: "关闭" }).last().click();

    await gotoPage(page, "血缘图谱");
    const lineage = page.locator(".mac-card", { hasText: "字段映射明细" });
    await expect(lineage).toContainText("字段级");
    await shot(page, "FLOW-03-血缘可见", info);
  });

  test("FLOW-04 调度启用后手动运行，调度页上次运行时间更新", async ({ page }, info) => {
    await openAs(page, ADMIN, "定时任务");
    const row = tableRows(page).filter({ hasText: TASK_SCHED }).first();
    await expect(row.locator(".mac-badge")).toContainText("已启用");
    const first = await row.locator("td.num").first().textContent();

    // 手动跑一次（调度器在 E2E 环境关闭，用真实执行通道验证）
    const runResp = await page.request.post(`/api/tasks/${readSeed().taskSched}/run`, {
      headers: await authHeaders(page.request, ADMIN),
    });
    expect(runResp.ok()).toBeTruthy();
    expect((await runResp.json()).status).toBe("success");

    await page.getByRole("button", { name: "刷新" }).first().click();
    const readLast = async () => (await tableRows(page).filter({ hasText: TASK_SCHED }).first()
      .locator("td.num").first().textContent())?.trim();
    // 刷新是异步请求，需轮询到值变化，避免读到旧渲染
    await expect.poll(readLast, { timeout: 15_000 }).not.toBe(first);
    const after = await readLast();
    expect(after, `上次运行应更新（原值 ${first}）`).not.toBe("-");
    expect(after).not.toBe(first);
    await shot(page, "FLOW-04-调度更新", info);
  });
});
