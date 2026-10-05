import { expect, test } from "@playwright/test";

import { mockApi, REPO } from "./fixtures";

test("home lists repositories and opens one", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Read a codebase from its evidence" })).toBeVisible();
  await page.getByRole("link", { name: /widget/ }).first().click();
  await expect(page).toHaveURL(new RegExp(`/r/${REPO}`));
});

test("genome graph shows nodes, filters, and evidence for a selected node", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/r/${REPO}/genome`);
  await expect(page.getByRole("heading", { name: "Software genome" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Files \(2\)/ })).toBeVisible();
  await page.getByRole("button", { name: "Files: api.ts" }).click();
  await expect(page.getByRole("button", { name: "Focus on this file" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "calls (1)" })).toBeVisible();
  await expect(page.getByText(/inferred 80%/)).toBeVisible();
});

test("bug history labels heuristic links and lists the likely origin", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/r/${REPO}/bugs`);
  await expect(page.getByText("Candidates, not proof")).toBeVisible();
  await expect(page.getByText("fix retry crash")).toBeVisible();
  await expect(page.getByText("70% confidence")).toBeVisible();
});

test("an API failure shows an error state instead of a broken page", async ({ page }) => {
  await mockApi(page, {
    [`/repositories/${REPO}/bugs`]: (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ title: "Server error", status: 500, detail: "Bug history is unavailable." }) }),
  });
  await page.goto(`/r/${REPO}/bugs`);
  // Next's route announcer is also an alert, so pick ours by its text.
  await expect(page.getByRole("alert").filter({ hasText: "Bug history could not be loaded" })).toBeVisible();
});

test("theme toggle switches between light and dark", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  const toggle = page.getByRole("button", { name: /Use (dark|light) theme/ });
  const before = await toggle.getAttribute("aria-label");
  await toggle.click();
  const expected = before === "Use dark theme" ? "dark" : "light";
  await expect(page.locator("html")).toHaveAttribute("data-theme", expected);
});
