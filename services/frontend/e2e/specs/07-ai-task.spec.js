/* F. AI ETL 任务：连接选择、无模型降级、重置、生成记录 */
import { test, expect } from "@playwright/test";
import { ADMIN, PROJECT, CONN_SRC } from "../fixtures/env.js";
import { openAs, shot } from "../fixtures/session.js";

const SRC_SELECT = () => ".mac-field:nth-child(2) select";

/** 连接/项目下拉为异步加载，按文本轮询后再选值。 */
async function pickByText(sel, prefix) {
  const read = async () => sel.locator("option").evaluateAll(
    (os) => os.map((o) => ({ value: o.value, text: (o.textContent || "").trim() })));
  await expect.poll(async () => (await read()).some((o) => o.text.startsWith(prefix)), { timeout: 20_000 })
    .toBeTruthy();
  const hit = (await read()).find((o) => o.text.startsWith(prefix));
  await sel.selectOption(hit.value);
  return hit;
}

test.describe("F. AI ETL 任务", () => {
  test.beforeEach(async ({ page }) => {
    await openAs(page, ADMIN, "AI ETL 任务");
  });

  test("AI-01 页面三个下拉加载可选连接与项目", async ({ page }, info) => {
    await expect(page.locator("main h1")).toHaveText("AI ETL 任务");
    await expect(page.locator(".mac-label", { hasText: "所属项目（可选）" })).toBeVisible();
    await expect(page.locator(".mac-label", { hasText: "数据源" }).first()).toBeVisible();
    await expect(page.locator(".mac-label", { hasText: "目标数据库" })).toBeVisible();
    expect(await page.locator(".mac-field").count()).toBeGreaterThanOrEqual(3);

    const srcSel = page.locator(".mac-field", { hasText: "数据源" }).first().locator("select");
    const srcHit = await pickByText(srcSel, CONN_SRC);
    expect(srcHit.text).toContain("mysql");

    const projSel = page.locator(".mac-field", { hasText: "所属项目" }).first().locator("select");
    const projHit = await pickByText(projSel, PROJECT);
    expect(projHit.value).toBe(PROJECT);
    await shot(page, "AI-01-三下拉", info);
  });

  test("AI-02 未配置模型时发送需求降级为可读错误，不白屏", async ({ page }, info) => {
    const srcSel = page.locator(".mac-field", { hasText: "数据源" }).first().locator("select");
    await pickByText(srcSel, CONN_SRC);
    await page.getByPlaceholder("描述抽取需求…").fill("把 e2e_orders 全量同步到 e2e_orders_out");
    await page.getByRole("button", { name: "生成", exact: true }).click();
    const err = page.locator(".mac-callout--danger");
    await expect(err).toBeVisible({ timeout: 25_000 });
    await expect(err).toContainText("生成失败");
    await expect(err).toContainText(/未配置|API Key/);
    await expect(page.locator("main h1")).toHaveText("AI ETL 任务");
    await expect(page.locator(".mac-card", { hasText: "草稿卡" })).toContainText("尚未生成草稿");
    await shot(page, "AI-02-未配置降级", info);
  });

  test("AI-03 重置清空对话与选择", async ({ page }, info) => {
    const srcSel = page.locator(".mac-field", { hasText: "数据源" }).first().locator("select");
    await pickByText(srcSel, CONN_SRC);
    await page.getByPlaceholder("描述抽取需求…").fill("临时内容");
    await page.getByRole("button", { name: "重置" }).click();
    await expect(page.getByPlaceholder("描述抽取需求…")).toHaveValue("");
    await expect(srcSel).toHaveValue("");
    await shot(page, "AI-03-重置", info);
  });

  test("AI-04 生成记录页支持筛选与返回", async ({ page }, info) => {
    await page.getByRole("button", { name: "查看生成记录" }).click();
    await expect(page.locator("main h1")).toHaveText("生成与创建记录");
    await expect(page.locator(".mac-segmented")).toBeVisible();
    await expect(page.locator("main")).toContainText(/暂无生成记录|暂无记录|暂无/);
    await page.locator(".mac-segmented").getByRole("tab", { name: "创建失败" }).click();
    await expect(page.locator("main")).toContainText(/暂无|创建失败/);
    await shot(page, "AI-04-生成记录", info);
    await page.getByRole("button", { name: "返回 AI ETL" }).click();
    await expect(page.locator("main h1")).toHaveText("AI ETL 任务");
  });

  test("AI-05 空输入时生成按钮不可用", async ({ page }, info) => {
    const genBtn = page.getByRole("button", { name: "生成", exact: true });
    await expect(genBtn).toBeDisabled();
    await page.getByPlaceholder("描述抽取需求…").fill("x");
    await expect(genBtn).toBeEnabled();
    await page.getByPlaceholder("描述抽取需求…").fill("");
    await expect(genBtn).toBeDisabled();
    await shot(page, "AI-05-按钮态", info);
  });
});
