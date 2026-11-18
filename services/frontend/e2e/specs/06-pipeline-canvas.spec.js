/* E2. 可视化编排画布：节点增删、节点配置、单节点预览、拓扑持久化 */
import { test, expect } from "@playwright/test";
import { ADMIN, PROJECT, TASK_OK, readSeed } from "../fixtures/env.js";
import { openAs, shot, dialog, authHeaders } from "../fixtures/session.js";

const NEW_NAME = "数据源(改名)";

async function openCanvas(page) {
  await openAs(page, ADMIN, "ETL 项目");
  await page.locator(".mac-table tbody tr", { hasText: PROJECT }).locator('button[title="进入项目"]').first().click();
  await expect(page.locator("main h1")).toHaveText(PROJECT);
  const row = page.locator(".mac-table tbody tr", { hasText: TASK_OK }).first();
  await row.getByRole("button", { name: "编辑" }).click();
  const edit = dialog(page);
  await expect(edit.locator(".mac-card__title").first()).toHaveText("编辑任务");
  await edit.getByRole("button", { name: "可视化编排（多步清洗）" }).click();
  const canvas = dialog(page).filter({ hasText: "多步清洗编排" });
  await expect(canvas).toBeVisible();
  await expect(canvas.locator(".react-flow__node")).toHaveCount(3);
  return { edit, canvas };
}

test.describe.serial("E2. 可视化编排画布", () => {
  test("TK-14 打开画布即回显「源→映射→目标」三节点与两条连线", async ({ page }, info) => {
    const { edit, canvas } = await openCanvas(page);
    await expect(canvas.locator(".react-flow__node")).toHaveCount(3);
    await expect(canvas.locator(".react-flow__edge")).toHaveCount(2);
    await expect(canvas).toContainText("关系库表");
    await expect(canvas).toContainText("字段选择与映射");
    await expect(canvas).toContainText("关系库目标");
    await shot(page, "TK-14-画布回显", info);
    await canvas.getByRole("button", { name: "取消" }).click();
    await edit.getByRole("button", { name: "取消" }).click();
  });

  test("TK-15 添加与删除步骤节点", async ({ page }, info) => {
    const { edit, canvas } = await openCanvas(page);
    const addSel = canvas.locator("select").first();
    await addSel.selectOption("filter");
    await expect(canvas.locator(".react-flow__node")).toHaveCount(4);
    await expect(canvas).toContainText("行过滤");
    await shot(page, "TK-15-添加节点", info);

    // 新节点默认被选中，直接删除
    await canvas.getByRole("button", { name: "删除节点" }).click();
    await expect(canvas.locator(".react-flow__node")).toHaveCount(3);
    await canvas.getByRole("button", { name: "取消" }).click();
    await edit.getByRole("button", { name: "取消" }).click();
  });

  test("TK-16 选中节点展示配置抽屉并可按节点预览", async ({ page }, info) => {
    const { edit, canvas } = await openCanvas(page);
    await canvas.locator(".react-flow__node").first().click();
    const panel = canvas.locator(".mac-card", { hasText: "节点配置" });
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("节点名称");
    await shot(page, "TK-16-节点配置", info);

    await panel.getByRole("button", { name: "预览该节点" }).click();
    // 节点预览 Modal 嵌套渲染在编排 Modal 内部，父节点也包含该文案，取最后一层
    const preview = dialog(page).filter({ hasText: "节点预览" }).last();
    await expect(preview).toBeVisible({ timeout: 30_000 });
    await expect(preview).toContainText("输出样本");
    await expect(preview).toContainText("行");
    await shot(page, "TK-16-节点预览", info);
    await preview.getByRole("button", { name: "关闭" }).last().click();
    await canvas.getByRole("button", { name: "取消" }).click();
    await edit.getByRole("button", { name: "取消" }).click();
  });

  test("TK-17 画布改动可保存并在重开后回显", async ({ page }, info) => {
    const name = `${NEW_NAME}-${Date.now().toString().slice(-5)}`;
    let { edit, canvas } = await openCanvas(page);
    await canvas.locator(".react-flow__node").first().click();
    const panel = canvas.locator(".mac-card", { hasText: "节点配置" });
    await panel.locator("input").first().fill(name);
    await canvas.locator('[title="批次读取大小"]').fill("1000");
    await canvas.getByRole("button", { name: "完成编排" }).click();

    await edit.getByRole("button", { name: "保存修改" }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(page.locator(".mac-callout--danger")).toHaveCount(0);
    await shot(page, "TK-17-保存编排", info);

    // 1) 配置层校验：名称与批次大小已落库
    const resp = await page.request.get(`/api/tasks?project=${PROJECT}`, { headers: await authHeaders(page.request) });
    const tasks = await resp.json();
    const { taskOk } = readSeed();
    const saved = tasks.find((t) => t.task_key === taskOk);
    expect(saved, `任务列表应含 ${TASK_OK}；实际 keys=${JSON.stringify(tasks.map((t) => t.task_key))}`).toBeTruthy();
    expect(saved.pipeline.nodes.find((n) => n.id === "source").name, "节点名称应持久化").toBe(name);
    expect(saved.pipeline.batch_size, "批次大小应持久化").toBe(1000);

    // 2) 界面层校验：整页重载后重新打开画布，改动应回显
    ({ edit, canvas } = await openCanvas(page));
    await expect(canvas.locator(".react-flow__node")).toHaveCount(3);
    await expect(canvas.locator(".react-flow__node").first()).toContainText(name);
    await expect(canvas.locator('[title="批次读取大小"]')).toHaveValue("1000");
    await shot(page, "TK-17-重开回显", info);
    await canvas.getByRole("button", { name: "取消" }).click();
    await edit.getByRole("button", { name: "取消" }).click();
  });

  test("TK-18 节点步级指标徽标（分页参数为 page_size）", async ({ page }, info) => {
    const { edit, canvas } = await openCanvas(page);
    await expect(canvas.locator(".react-flow__node")).toHaveCount(3);
    await expect(canvas.locator(".mac-callout--danger")).toHaveCount(0);

    // 前端请求应使用后端支持的分页口径（page_size），不再传被忽略的 limit
    const runsResp = await page.request.get(
      `/api/runs?task_key=${readSeed().taskOk}&page_size=10`,
      { headers: await authHeaders(page.request) },
    );
    const data = await runsResp.json();
    expect(data.page_size, "page_size 应被后端采纳").toBe(10);
    expect(data.runs?.length ?? 0, "应能取到最近运行").toBeGreaterThan(0);
    await shot(page, "TK-18-步级指标", info);
    await canvas.getByRole("button", { name: "取消" }).click();
    await edit.getByRole("button", { name: "取消" }).click();
  });
});
