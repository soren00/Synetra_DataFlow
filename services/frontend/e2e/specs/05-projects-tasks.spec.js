/* E. 项目与任务编排：项目 CRUD、任务向导、字段规则、增量、cron 校验、运行与回显 */
import { test, expect } from "@playwright/test";
import { ADMIN, PROJECT, TASK_OK } from "../fixtures/env.js";
import { openAs, shot, dialog, tableRows } from "../fixtures/session.js";

const uniq = (p) => `${p}-${Date.now().toString().slice(-6)}`;
const TMP_TASK = uniq("e2e-ui-task");

async function enterProject(page) {
  await openAs(page, ADMIN, "ETL 项目");
  await expect(page.locator("main h1")).toHaveText("项目");
  await page.locator(".mac-table tbody tr", { hasText: PROJECT }).locator('button[title="进入项目"]').first().click();
  await expect(page.locator("main h1")).toHaveText(PROJECT);
}

async function openCreateTask(page) {
  await page.getByRole("button", { name: "新建任务" }).click();
  const dlg = dialog(page);
  await expect(dlg.locator(".mac-card__title").first()).toHaveText("新建任务");
  return dlg;
}

async function fillBasics(dlg, { name, source, target }) {
  await dlg.getByPlaceholder("如 订单增量同步").fill(name);
  await dlg.locator(".mac-field", { hasText: "数据源" }).locator("select").selectOption({ label: "e2e-mysql-src" });
  await dlg.getByPlaceholder("orders", { exact: true }).fill(source);
  await dlg.locator(".mac-field", { hasText: "数据目标" }).locator("select").selectOption({ label: "e2e-mysql-dst" });
  await dlg.getByPlaceholder("orders_dw").fill(target);
}

test.describe.serial("E. 项目与任务编排", () => {
  test("TK-01 项目列表与新建/删除项目", async ({ page }, info) => {
    const name = uniq("E2E-UI");
    await openAs(page, ADMIN, "ETL 项目");
    await expect(page.locator("main h1")).toHaveText("项目");
    await expect(page.locator(".mac-muted", { hasText: /共 \d+ 个项目/ }).first()).toBeVisible();
    await expect(page.locator(".mac-table tbody tr", { hasText: PROJECT })).toHaveCount(1);

    await page.getByRole("button", { name: "新建项目" }).click();
    let dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toHaveText("新建项目");
    await dlg.getByPlaceholder("如 订单域").fill(name);
    await dlg.getByRole("button", { name: "创建" }).click();
    await expect(page.locator(".mac-table tbody tr", { hasText: name })).toHaveCount(1);
    await shot(page, "TK-01-新建项目", info);

    // 重名应被拒绝（409）
    await page.getByRole("button", { name: "新建项目" }).click();
    dlg = dialog(page);
    await dlg.getByPlaceholder("如 订单域").fill(name);
    await dlg.getByRole("button", { name: "创建" }).click();
    await expect(dlg.locator(".mac-callout--danger")).toContainText("新建失败");
    await dlg.getByRole("button", { name: "取消" }).click();

    // 清理
    const row = page.locator(".mac-table tbody tr", { hasText: name });
    await row.locator('button[title="删除项目"]').click();
    const confirm = dialog(page).filter({ hasText: "删除项目" });
    await confirm.getByRole("button", { name: "删除" }).click();
    await expect(page.locator(".mac-table tbody tr", { hasText: name })).toHaveCount(0);
  });

  test("TK-02 进入项目可看到项目内任务，并可返回", async ({ page }, info) => {
    await enterProject(page);
    await expect(page.locator(".mac-muted", { hasText: /共 \d+ 个任务/ }).first()).toBeVisible();
    await expect(page.locator(".mac-table tbody tr", { hasText: TASK_OK })).toHaveCount(1);
    await shot(page, "TK-02-项目内任务", info);
    await page.getByRole("button", { name: "返回项目" }).click();
    await expect(page.locator("main h1")).toHaveText("项目");
  });

  test("TK-03 查询预览 10 条并自动生成字段映射", async ({ page }, info) => {
    await enterProject(page);
    const dlg = await openCreateTask(page);
    await fillBasics(dlg, { name: TMP_TASK, source: "e2e_orders", target: "e2e_orders_out" });
    await dlg.getByRole("button", { name: "查询预览 10 条" }).click();

    const preview = dialog(page).filter({ hasText: "数据预览" });
    await expect(preview).toBeVisible();
    const rows = preview.locator(".mac-table tbody tr");
    await expect(rows.first()).toBeVisible();
    const n = await rows.count();
    expect(n, "预览行数应 ≤10").toBeLessThanOrEqual(10);
    await expect(preview.locator(".mac-table thead")).toContainText("order_no");
    await shot(page, "TK-03-查询预览", info);
    await preview.getByRole("button", { name: "关闭" }).last().click();

    // 预览后按源列自动生成直连映射
    const srcInputs = dlg.getByPlaceholder("源字段");
    expect(await srcInputs.count(), "应自动生成字段映射行").toBeGreaterThanOrEqual(5);
    await dlg.getByRole("button", { name: "取消" }).click();
  });

  test("TK-04 字段规则类型切换显示对应输入项", async ({ page }, info) => {
    await enterProject(page);
    const dlg = await openCreateTask(page);
    await dlg.getByRole("button", { name: "添加规则" }).click();
    const lastRow = dlg.locator('input[placeholder="源字段"]').last().locator("xpath=ancestor::div[1]");
    await lastRow.locator("select").selectOption("cast");
    await expect(dlg.getByPlaceholder("类型")).toBeVisible();
    await lastRow.locator("select").selectOption("constant");
    await expect(dlg.getByPlaceholder("常量值")).toBeVisible();
    await lastRow.locator("select").selectOption("null_fill");
    await expect(dlg.getByPlaceholder("填充值")).toBeVisible();
    await shot(page, "TK-04-规则类型", info);
    await dlg.getByRole("button", { name: "取消" }).click();
  });

  test("TK-05 AI 生成表达式在未配置模型时降级且保留输入", async ({ page }, info) => {
    await enterProject(page);
    const dlg = await openCreateTask(page);
    await dlg.getByRole("button", { name: "添加规则" }).click();
    const row = dlg.locator('input[placeholder="源字段"]').last().locator("xpath=ancestor::div[1]");
    await row.locator('input[placeholder="源字段"]').fill("customer");
    await row.locator("select").selectOption("expression");
    const expr = dlg.getByPlaceholder("表达式").first();
    await expr.fill("concat(customer,'-x')");
    await dlg.getByRole("button", { name: "AI 生成" }).first().click();

    const aiModal = dialog(page).filter({ hasText: "AI 生成表达式" });
    await expect(aiModal).toBeVisible();
    await aiModal.getByPlaceholder("如：手机号中间四位打码").fill("把客户名转成大写");
    await aiModal.getByRole("button", { name: "生成" }).click();
    await expect(aiModal.locator(".mac-callout--danger")).toContainText("生成失败");
    await shot(page, "TK-05-AI表达式降级", info);
    await aiModal.getByRole("button", { name: "关闭" }).last().click();
    await expect(expr).toHaveValue("concat(customer,'-x')");
    await dlg.getByRole("button", { name: "取消" }).click();
  });

  test("TK-06 抽取模式切到增量显示水位相关字段", async ({ page }, info) => {
    await enterProject(page);
    const dlg = await openCreateTask(page);
    const modeSel = dlg.locator(".mac-field", { hasText: "抽取模式" }).locator("select");
    await modeSel.selectOption("incremental");
    await expect(dlg.getByPlaceholder("updated_at").first()).toBeVisible();
    await expect(dlg.getByPlaceholder("updated_at")).toHaveCount(2);
    await expect(dlg.locator(".mac-field", { hasText: "首跑策略" })).toBeVisible();
    await modeSel.selectOption("upsert");
    await expect(dlg.locator(".mac-field", { hasText: "主键字段" })).toBeVisible();
    await expect(dlg.getByPlaceholder("updated_at")).toHaveCount(0);
    await shot(page, "TK-06-抽取模式", info);
    await dlg.getByRole("button", { name: "取消" }).click();
  });

  test("TK-07 非法自定义 cron 被前端拦截", async ({ page }, info) => {
    await enterProject(page);
    const dlg = await openCreateTask(page);
    await fillBasics(dlg, { name: uniq("e2e-cron"), source: "e2e_orders", target: "e2e_orders_out" });
    await dlg.locator(".mac-field", { hasText: "定时频率" }).locator("select").selectOption("custom");
    await dlg.getByPlaceholder("0 2 * * *").fill("0 2 *");
    await dlg.getByRole("button", { name: "创建任务" }).click();
    await expect(dlg.locator(".mac-callout--danger")).toContainText("5 段");
    await shot(page, "TK-07-cron校验", info);
    await dlg.getByRole("button", { name: "取消" }).click();
  });

  test("TK-08 向导创建任务成功", async ({ page }, info) => {
    await enterProject(page);
    const dlg = await openCreateTask(page);
    await fillBasics(dlg, { name: TMP_TASK, source: "e2e_orders", target: "e2e_orders_out" });
    await dlg.getByRole("button", { name: "查询预览 10 条" }).click();
    await dialog(page).filter({ hasText: "数据预览" }).getByRole("button", { name: "关闭" }).last().click();
    await dlg.getByRole("button", { name: "创建任务" }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(page.locator(".mac-table tbody tr", { hasText: TMP_TASK })).toHaveCount(1);
    await shot(page, "TK-08-创建任务", info);
  });

  test("TK-09 运行任务后状态与行数更新", async ({ page }, info) => {
    await enterProject(page);
    const row = page.locator(".mac-table tbody tr", { hasText: TMP_TASK }).first();
    await row.getByRole("button", { name: "运行" }).click();
    const msg = page.locator(".mac-callout", { hasText: "运行状态" });
    await expect(msg).toBeVisible({ timeout: 30_000 });
    await expect(msg).toContainText("成功");
    const refreshed = page.locator(".mac-table tbody tr", { hasText: TMP_TASK }).first();
    await expect(refreshed.locator(".mac-badge")).toContainText("成功");
    await expect(refreshed.locator("td.num")).not.toHaveText("-");
    await shot(page, "TK-09-运行任务", info);
  });

  test("TK-10 编辑任务回显已保存配置", async ({ page }, info) => {
    await enterProject(page);
    const row = page.locator(".mac-table tbody tr", { hasText: TMP_TASK }).first();
    await row.getByRole("button", { name: "编辑" }).click();
    const dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toHaveText("编辑任务");
    await expect(dlg.getByPlaceholder("如 订单增量同步")).toHaveValue(TMP_TASK);
    await expect(dlg.getByPlaceholder("orders", { exact: true })).toHaveValue("e2e_orders");
    await expect(dlg.getByPlaceholder("orders_dw")).toHaveValue("e2e_orders_out");
    await expect(dlg.locator(".mac-field", { hasText: "数据源" }).locator("select")).toHaveValue(/.+/);
    expect(await dlg.getByPlaceholder("源字段").count()).toBeGreaterThanOrEqual(5);
    await shot(page, "TK-10-编辑回显", info);
    await dlg.getByRole("button", { name: "取消" }).click();
  });

  test("TK-11 明细展示最近一次运行", async ({ page }, info) => {
    await enterProject(page);
    const row = page.locator(".mac-table tbody tr", { hasText: TMP_TASK }).first();
    await row.getByRole("button", { name: "明细" }).click();
    await expect(page.locator(".mac-callout", { hasText: "最近运行明细" })).toBeVisible();
    await expect(page.locator(".mac-callout", { hasText: "最近运行明细" })).toContainText("成功");
    await shot(page, "TK-11-运行明细", info);
  });

  test("TK-12 删除任务", async ({ page }, info) => {
    await enterProject(page);
    const row = page.locator(".mac-table tbody tr", { hasText: TMP_TASK }).first();
    await row.getByRole("button", { name: "删除" }).click();
    const confirm = dialog(page).filter({ hasText: "删除任务" });
    await expect(confirm).toContainText(TMP_TASK);
    await confirm.getByRole("button", { name: "删除" }).click();
    await expect(page.locator(".mac-table tbody tr", { hasText: TMP_TASK })).toHaveCount(0);
    await shot(page, "TK-12-删除任务", info);
  });

  test("TK-13 搜索与状态筛选", async ({ page }, info) => {
    await enterProject(page);
    await page.getByPlaceholder("搜索任务…").fill("e2e-task-orders");
    await expect(tableRows(page)).toHaveCount(1);
    await page.getByPlaceholder("搜索任务…").fill("");

    await page.locator(".mac-segmented").getByRole("tab", { name: "失败" }).click();
    await expect(page.locator(".mac-table tbody")).toContainText("e2e-task-broken");
    const badges = await page.locator(".mac-table tbody .mac-badge").allTextContents();
    expect(badges.every((b) => b.includes("失败")), `筛选失败后应只有失败状态：${badges}`).toBeTruthy();
    await shot(page, "TK-13-任务筛选", info);
  });
});
