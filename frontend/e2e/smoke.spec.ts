import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const shots = path.resolve(here, "../../docs/screenshots");

async function startSample(page: Page, sample: string, title: string) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /why did the run fail/i })).toBeVisible();
  await page.getByRole("button", { name: `Run sample ${title}` }).click();
  await page.waitForURL(/\/run\/[0-9a-f]{12}/, { timeout: 30_000 });
  return page.url();
}

test("smoke: running a sample ends on a verdict with evidence", async ({ page }) => {
  await startSample(page, "dependency_bump", "Dependency bump");

  // The cause badge, in both its human and raw forms.
  await expect(page.getByText("Dependency bump").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("dependency_bump").first()).toBeVisible();

  // At least one evidence card, and the citation chips point at real ids.
  const cards = page.locator("[data-evidence-id]");
  await expect(cards.first()).toBeVisible();
  expect(await cards.count()).toBeGreaterThanOrEqual(1);

  await expect(page.getByText(/computed from signals, not model-reported/i).first()).toBeVisible();
  await expect(page.getByText("next step", { exact: false }).first()).toBeVisible();

  // A cited chip scrolls to its card and flashes it.
  const chip = page.getByRole("button", { name: /^Jump to evidence ev_/ }).first();
  await expect(chip).toBeVisible();
  const label = await chip.getAttribute("aria-label");
  const evidenceId = (label ?? "").replace("Jump to evidence ", "");
  await chip.click();
  await expect(page.locator(`[data-evidence-id="${evidenceId}"]`)).toHaveClass(/card-flash/);
});

test("smoke: the flaky sample ends as flaky_test after one revision", async ({ page }) => {
  await startSample(page, "flaky_test", "Flaky test");
  await expect(page.getByText("Flaky test").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/skeptic revised, 1 revision|skeptic revised/i).first()).toBeVisible();
  await expect(
    page.getByText(/critique sent back to the investigator, revision 1/i).first(),
  ).toBeVisible();
});

test("screenshots: investigate, live, verdict, timeline", async ({ page }) => {
  await mkdir(shots, { recursive: true });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /why did the run fail/i })).toBeVisible();
  await expect(page.locator("text=sample mode").first()).toBeVisible();
  await page.screenshot({ path: path.join(shots, "01-investigate.png"), fullPage: false });

  // Hold the stored result back so the live layout is on screen long enough to capture.
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = () => resolve();
  });
  await page.route(/\/run\/[0-9a-f]{12}$/, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await held;
    return route.continue();
  });

  const url = await startSample(page, "dependency_bump", "Dependency bump");
  await expect(page.getByRole("heading", { name: "Investigator steps" })).toBeVisible({
    timeout: 30_000,
  });
  // The heading shows immediately; wait for the stream to actually fill the panels.
  await expect(page.getByText("step 1", { exact: true }).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("same_sha_elsewhere").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/cross-source query/i).first()).toBeVisible();
  await page.screenshot({ path: path.join(shots, "02-live.png"), fullPage: true });
  release();
  await expect(page.getByText("Dependency bump").first()).toBeVisible({ timeout: 60_000 });
  await page.screenshot({ path: path.join(shots, "03-verdict.png"), fullPage: true });

  await page.getByRole("tab", { name: "Change timeline" }).click();
  await expect(page.getByRole("heading", { name: "Change timeline" })).toBeVisible();
  await page.screenshot({ path: path.join(shots, "04-timeline.png"), fullPage: true });
});
