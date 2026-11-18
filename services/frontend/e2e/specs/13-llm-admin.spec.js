/* L. AI 管理（admin）：模型配置回显/保存/测试/吊销，技能包读写，非管理员拦截 */
import { test, expect } from "@playwright/test";
import { ADMIN, DEV } from "../fixtures/env.js";
import { openAs, shot, dialog, authHeaders } from "../fixtures/session.js";

test.describe.serial("L. AI 管理", () => {
  test("LLM-01 生效配置五项回显", async ({ page }, info) => {
    await openAs(page, ADMIN, "AI 管理");
    await expect(page.locator("main h1")).toHaveText("AI 管理");
    const card = page.locator(".mac-card", { hasText: "生效配置" });
    for (const label of ["供应商", "Base URL", "模型", "API Key", "最近更新"]) {
      await expect(card).toContainText(label);
    }
    await expect(card).toContainText("deepseek");
    await shot(page, "LLM-01-生效配置", info);
  });

  test("LLM-02 保存配置并即时生效", async ({ page }, info) => {
    await openAs(page, ADMIN, "AI 管理");
    const body = page.locator(".mac-card", { hasText: "编辑配置" });
    await body.getByPlaceholder("https://api.deepseek.com/v1").fill("https://api.deepseek.com/v1");
    await body.getByPlaceholder("如 deepseek-chat（可输入，也可从建议中选择）").fill("deepseek-chat");
    await body.getByRole("button", { name: "保存" }).click();
    await expect(page.locator(".mac-callout--success")).toContainText("配置已保存并热切换生效");
    await expect(page.locator(".mac-card", { hasText: "生效配置" })).toContainText("deepseek-chat");
    await shot(page, "LLM-02-保存配置", info);
  });

  test("LLM-03 未配置 Key 时测试连通返回可读失败原因", async ({ page }, info) => {
    await openAs(page, ADMIN, "AI 管理");
    const body = page.locator(".mac-card", { hasText: "编辑配置" });
    await body.getByRole("button", { name: "测试连通" }).click();
    const err = page.locator(".mac-callout--danger");
    await expect(err).toBeVisible({ timeout: 20_000 });
    await expect(err).toContainText("连通失败");
    await expect(err).not.toContainText("500");
    await shot(page, "LLM-03-测试连通", info);
  });

  test("LLM-04 未配置 Key 时吊销按钮不可用", async ({ page }, info) => {
    await openAs(page, ADMIN, "AI 管理");
    const btn = page.getByRole("button", { name: "吊销 Key" });
    const hasKey = ((await page.locator(".mac-card", { hasText: "生效配置" }).textContent()) || "").includes("已配置");
    if (hasKey) {
      await expect(btn).toBeEnabled();
    } else {
      await expect(btn).toBeDisabled();
    }
    await shot(page, "LLM-04-吊销按钮", info);
  });

  test("LLM-05 技能包列表与内容可读", async ({ page }, info) => {
    await openAs(page, ADMIN, "AI 管理");
    await page.locator(".mac-segmented").getByRole("tab", { name: "技能包" }).click();
    const card = page.locator(".mac-card", { hasText: "技能包" }).first();
    await expect(card.locator(".mac-table thead")).toContainText("内容指纹");
    const rows = card.locator(".mac-table tbody tr");
    await expect(rows.first()).toBeVisible();
    expect(await rows.count()).toBeGreaterThan(0);
    // 默认已打开首个技能，编辑区应有内容
    const editor = page.locator(".mac-card", { hasText: "编辑 " });
    await expect(editor.locator("textarea")).toBeVisible();
    const content = await editor.locator("textarea").inputValue();
    expect(content.length, "技能包主文件不应为空").toBeGreaterThan(100);
    await expect(page.getByRole("button", { name: "放弃修改" })).toBeEnabled();
    await shot(page, "LLM-05-技能包", info);
  });

  test("LLM-06 非管理员访问 AI 配置接口被拒（403）", async ({ request }) => {
    const r1 = await request.get("/api/llm/config", { headers: await authHeaders(request, DEV) });
    expect(r1.status()).toBe(403);
    const r2 = await request.put("/api/llm/config", {
      headers: await authHeaders(request, DEV),
      data: { provider: "deepseek", base_url: "https://evil.example.com", model: "x", api_key: null },
    });
    expect(r2.status()).toBe(403);
    const r3 = await request.get("/api/llm/skills", { headers: await authHeaders(request, DEV) });
    expect(r3.status()).toBe(403);
  });
});
