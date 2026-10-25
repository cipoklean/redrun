import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const shots = path.resolve(here, "../../docs/screenshots");

/** Console and page errors, which a 200 does not rule out. */
function watchConsole(page: Page): string[] {
  const problems: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  return problems;
}

async function documentOverflow(page: Page): Promise<boolean> {
  return page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  );
}

test("the eval screen reads the latest results, and says so when there are none", async ({
  page,
}) => {
  const problems = watchConsole(page);
  await page.goto("/eval");

  // Either state is a real page: results, or an honest empty state. Anything else
  // (a blank body, an error shell) fails here rather than passing quietly.
  const heading = page.getByRole("heading", { name: "Evaluation" });
  const empty = page.getByRole("heading", { name: /No evaluation has been run yet/i });
  await expect(heading.or(empty).first()).toBeVisible({ timeout: 20_000 });

  if (await heading.isVisible()) {
    await expect(page.getByText("Totals")).toBeVisible();
    await expect(page.getByText("Per case")).toBeVisible();
    await expect(page.getByText("dev", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("holdout", { exact: true }).first()).toBeVisible();
    // The baseline is named, so the reader knows what the comparison is against.
    await expect(page.getByText(/the baseline is one plain/i)).toBeVisible();
  } else {
    await expect(page.getByText(/python -m redrun eval/)).toBeVisible();
  }

  expect(problems).toEqual([]);
  expect(await documentOverflow(page)).toBe(true);

  await mkdir(shots, { recursive: true });
  await page.screenshot({ path: path.join(shots, "05-eval.png"), fullPage: true });
});

test("the eval screen holds a phone width and has no console errors at 390px", async ({
  page,
}) => {
  const problems = watchConsole(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/eval");
  await expect(
    page.getByRole("heading", { name: /Evaluation|No evaluation has been run yet/i }).first(),
  ).toBeVisible({ timeout: 20_000 });

  expect(await documentOverflow(page)).toBe(true);
  expect(problems).toEqual([]);
});

test("the header links to the eval screen", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Eval" }).click();
  await expect(page).toHaveURL(/\/eval$/);
  await expect(
    page.getByRole("heading", { name: /Evaluation|No evaluation has been run yet/i }).first(),
  ).toBeVisible({ timeout: 20_000 });
});
