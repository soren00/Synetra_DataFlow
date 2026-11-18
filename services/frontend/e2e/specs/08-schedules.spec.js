/* G. 定时调度：列表、启停、cron 修改与校验、新建入口现状 */
import { test, expect } from "@playwright/test";
import { ADMIN, PROJECT, TASK_SCHED, TASK_OK, readSeed } from "../fixtures/env.js";
import { openAs, shot, dialog, tableRows } from "../fixtures/session.js";

const rowOf = (page) => tableRows(page).filter({ hasText: TASK_SCHED }).first();

test.describe.serial("G. 定时调度", () => {
  test("SC-01 调度列表展示 cron / 启用徽标 / 上次与下次运行", async ({ page }, info) => {
    await openAs(page, ADMIN, "定时任务");
    await expect(page.locator("main h1")).toHaveText("定时任务");
    const row = rowOf(page);
    await expect(row).toContainText(TASK_SCHED);
    await expect(row.locator("code")).toContainText("*/15 * * * *");
    await expect(row.locator(".mac-badge")).toContainText("已启用");
    // 列头完整
    await expect(page.locator(".mac-table thead")).toContainText("上次运行");
    await expect(page.locator(".mac-table thead")).toContainText("下次运行");
    await shot(page, "SC-01-调度列表", info);
  });

  test("SC-02 停用与再启用调度", async ({ page }, info) => {
    await openAs(page, ADMIN, "定时任务");
    let row = rowOf(page);
    await row.getByRole("button", { name: "停用" }).click();
    await expect(page.locator(".mac-callout--success")).toContainText("已停用");
    row = rowOf(page);
    await expect(row.locator(".mac-badge")).toContainText("已停用");

    await row.getByRole("button", { name: "启用" }).click();
    await expect(page.locator(".mac-callout--success")).toContainText("已启用");
    await expect(rowOf(page).locator(".mac-badge")).toContainText("已启用");
    await shot(page, "SC-02-启停", info);
  });

  test("SC-03 通过预设修改 cron", async ({ page }, info) => {
    await openAs(page, ADMIN, "定时任务");
    await rowOf(page).getByRole("button", { name: "改cron" }).click();
    const dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toContainText("修改 cron");
    await dlg.locator("select").selectOption({ label: "每小时" });
    await dlg.getByRole("button", { name: "确定" }).click();
    await expect(page.locator(".mac-callout--success")).toContainText("已更新");
    await expect(rowOf(page).locator("code")).toContainText("0 * * * *");
    await shot(page, "SC-03-改cron", info);
  });

  test("SC-04 自定义 cron 段数不合法时被拦截", async ({ page }, info) => {
    await openAs(page, ADMIN, "定时任务");
    await rowOf(page).getByRole("button", { name: "改cron" }).click();
    const dlg = dialog(page);
    await dlg.locator("select").selectOption("custom");
    await dlg.getByPlaceholder("0 2 * * *").fill("0 2 *");
    await dlg.getByRole("button", { name: "确定" }).click();
    await expect(page.locator(".mac-callout--danger")).toContainText("5 字段");
    // 弹窗保持打开，避免静默丢弃用户输入
    await expect(dlg).toBeVisible();
    await shot(page, "SC-04-cron校验", info);
    await dlg.getByRole("button", { name: "取消" }).click();
  });

  test("SC-05 可直接在定时任务页新建调度", async ({ page }, info) => {
    await openAs(page, ADMIN, "定时任务");
    await page.getByRole("button", { name: "新建" }).click();
    const dlg = dialog(page);
    await expect(dlg.locator(".mac-card__title").first()).toHaveText("新建调度");

    // 选项目后再选任务（下拉第 1 个是项目、第 2 个是任务）
    await dlg.locator("select").nth(0).selectOption(PROJECT);
    const taskSelect = dlg.locator("select").nth(1);
    await expect(taskSelect.locator("option").nth(1)).toBeAttached();
    await taskSelect.selectOption(readSeed().taskOk);
    await expect(dlg.getByPlaceholder("0 2 * * *")).toHaveValue("0 2 * * *");
    await shot(page, "SC-05-新建调度表单", info);

    await dlg.getByRole("button", { name: "创建调度" }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(tableRows(page).filter({ hasText: TASK_OK }).first()).toBeVisible();
    await shot(page, "SC-05-新建调度结果", info);
  });

  test("SC-05b 新建调度时非法 cron 被拦截", async ({ page }, info) => {
    await openAs(page, ADMIN, "定时任务");
    await page.getByRole("button", { name: "新建" }).click();
    const dlg = dialog(page);
    const taskSelect = dlg.locator("select").nth(1);
    await expect(taskSelect.locator("option").nth(1)).toBeAttached();
    await taskSelect.selectOption({ index: 1 });
    await dlg.getByPlaceholder("0 2 * * *").fill("bad cron");
    await dlg.getByRole("button", { name: "创建调度" }).click();
    await expect(page.locator(".mac-callout--danger")).toContainText("cron 需为 5 字段");
    await expect(dialog(page)).toHaveCount(1);
    await shot(page, "SC-05b-cron校验", info);
  });
});
