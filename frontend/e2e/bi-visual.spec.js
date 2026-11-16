import { test, expect } from "@playwright/test";
import { openBiPage, contractDrift, VOLATILE } from "./support/bi-stubs";

// Visual regression for the BI dashboard.
//
// Every assertion targets one panel rather than the whole page: a full-page
// capture fails for reasons that have nothing to do with the panel under review
// (a badge two rows down, a scrollbar, a reordered grid) and the diff image then
// tells you nothing. Panel-level captures keep the failure local and the diff
// readable.
//
// Determinism comes from playwright.config.js (one browser build, one viewport,
// one device pixel ratio, one locale, one timezone, motion disabled) plus the
// frozen clock and stubbed payloads in support/bi-stubs.js.

const masks = (page) => VOLATILE.map((id) => page.getByTestId(id));

const settle = async (page) => {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForLoadState("networkidle");
};

// Panels lower down the page start below the fold. Scrolling first keeps the
// capture deterministic: otherwise Playwright's own scroll-into-view races the
// stability check and the assertion times out without ever comparing pixels.
const shot = async (page, testId, name, options = {}) => {
  const locator = page.getByTestId(testId);
  await locator.scrollIntoViewIfNeeded();
  await expect(locator).toHaveScreenshot(name, { timeout: 15_000, ...options });
};

test.describe("BI dashboard visual regression", () => {
  test("executive persona panels", async ({ page }) => {
    await openBiPage(page, { role: "planner" });
    await page.getByTestId("bi-persona-executive").click();
    await settle(page);

    await shot(page, "bi-kpis", "kpis.png");
    await shot(page, "bi-kpi-tree-panel", "kpi-tree.png");
    await shot(page, "bi-triage-mix", "triage-mix.png");
    await shot(page, "bi-plan-panel", "plan-vs-baseline.png");
    await shot(page, "bi-fva-panel", "fva-by-planner.png");
  });

  test("operations persona panels", async ({ page }) => {
    await openBiPage(page, { role: "planner" });
    await page.getByTestId("bi-persona-operations").click();
    await settle(page);

    await shot(page, "bi-diagnostics-panel", "diagnostics.png");
    await shot(page, "bi-actions-panel", "proposed-actions.png");
    await shot(page, "bi-annotate-panel", "annotate.png");
    await shot(page, "bi-channel-panel", "exceptions-by-channel.png");
    await shot(page, "bi-risk-panel", "value-at-risk.png");
  });

  test("analyst persona panels", async ({ page }) => {
    await openBiPage(page, { role: "planner" });
    await settle(page);

    await shot(page, "bi-metrics-panel", "metric-catalog.png");
    await shot(page, "bi-ask-panel", "ask-panel.png");
    await shot(page, "bi-serving-panel", "serving-panel.png");
    await shot(page, "bi-mart-panel", "data-mart.png", { mask: masks(page) });
    await shot(page, "bi-top-panel", "top-exceptions.png");
  });

  test("status bar badges", async ({ page }) => {
    await openBiPage(page, { role: "planner" });
    await settle(page);

    // The clock is frozen, so the freshness label and the latency badge are
    // stable text rather than a moving target.
    await expect(page.getByTestId("bi-freshness-badge")).toHaveText("Mart built 2 h ago");
    await expect(page.getByTestId("bi-latency-badge")).toHaveText("42 ms");
    await shot(page, "bi-status-bar", "status-bar.png");
  });

  test("contract drift is visible in the mart panel", async ({ page }) => {
    await openBiPage(page, {
      role: "planner",
      overrides: { "/api/v1/bi/contracts": contractDrift },
    });
    await settle(page);

    const badge = page.getByTestId("bi-contract-badge");
    await expect(badge).toContainText("Contract drift: 1 error(s)");
    await expect(badge).toContainText("triage_items is missing column var_gbp");
    await shot(page, "bi-mart-panel", "data-mart-drift.png", { mask: masks(page) });
  });

  test("conforming contract badge", async ({ page }) => {
    await openBiPage(page, { role: "planner" });
    await settle(page);

    await expect(page.getByTestId("bi-contract-badge")).toHaveText("Contract: 12/12 tables conform");
  });

  test("auditor sees a read-only action queue", async ({ page }) => {
    await openBiPage(page, { role: "auditor" });
    await page.getByTestId("bi-persona-operations").click();
    await settle(page);

    await shot(page, "bi-actions-panel", "proposed-actions-readonly.png");
  });

  test("grounded ask result", async ({ page }) => {
    await openBiPage(page, { role: "planner" });
    await settle(page);

    await page.getByTestId("bi-ask-input").fill("How much value is exposed to out-of-stock risk?");
    await page.getByTestId("bi-ask-submit").click();
    await expect(page.getByTestId("bi-ask-result")).toBeVisible();
    await settle(page);

    await shot(page, "bi-ask-result", "ask-result.png");
  });
});
