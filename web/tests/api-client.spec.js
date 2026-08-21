const { test, expect } = require("@playwright/test");

async function importApi(page) {
  return page.evaluate(async () => {
    const module = await import(`/shared/api.js?test=${crypto.randomUUID()}`);
    window.testApi = module.api;
  });
}

test("shared API refreshes a stale daemon token once after 401", async ({ page }) => {
  const authorizationHeaders = [];
  let rootRequests = 0;

  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/") {
      rootRequests += 1;
      return route.fulfill({
        contentType: "text/html",
        body: '<script>window.apiToken = "fresh-token";</script>',
      });
    }
    if (url.pathname === "/api/protected") {
      const token = route.request().headers()["x-api-token"] || "";
      authorizationHeaders.push(token);
      return route.fulfill({
        status: token === "fresh-token" ? 200 : 401,
        contentType: "application/json",
        body: JSON.stringify(token === "fresh-token"
          ? { status: "ok", value: 42 }
          : { status: "error", message: "Unauthorized" }),
      });
    }
    return route.continue();
  });

  await page.goto("/");
  await page.evaluate(() => { window.apiToken = "stale-token"; });
  await importApi(page);
  const response = await page.evaluate(() => window.testApi("GET", "/api/protected"));

  expect(response).toMatchObject({ status: "ok", value: 42 });
  expect(authorizationHeaders).toEqual(["stale-token", "fresh-token"]);
  expect(rootRequests).toBe(2); // initial navigation plus the forced token bootstrap
});

test("shared API coalesces concurrent reads without aborting a consumer", async ({ page }) => {
  let requestCount = 0;
  await page.route("**/api/status", async (route) => {
    requestCount += 1;
    await new Promise((resolve) => setTimeout(resolve, 50));
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", active: false }),
    });
  });

  await page.goto("/");
  await page.evaluate(() => { window.apiToken = "test-token"; });
  await importApi(page);
  const responses = await page.evaluate(() => Promise.all([
    window.testApi("GET", "/api/status"),
    window.testApi("GET", "/api/status"),
  ]));

  expect(responses).toEqual([
    { status: "ok", active: false },
    { status: "ok", active: false },
  ]);
  expect(requestCount).toBe(1);
});

test("shared API normalizes a non-JSON server failure", async ({ page }) => {
  await page.route("**/api/status", (route) => route.fulfill({
    status: 502,
    contentType: "text/html",
    body: "Bad gateway",
  }));

  await page.goto("/");
  await page.evaluate(() => { window.apiToken = "test-token"; });
  await importApi(page);
  const response = await page.evaluate(() => window.testApi("GET", "/api/status"));

  expect(response).toMatchObject({ status: "error", http_status: 502 });
  expect(response.message).toContain("HTTP 502");
});

test("shared task renderer rolls back an optimistic change rejected by the daemon", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { renderIntentTasks } = await import(`/shared/intent-tasks.js?test=${crypto.randomUUID()}`);
    const container = document.createElement("div");
    document.body.appendChild(container);
    const errors = [];
    container.addEventListener("forcedfocus:intent-error", (event) => errors.push(event.detail.message));
    const tasks = [{ text: "Write release notes", completed: false }];
    renderIntentTasks(container, tasks, async () => ({ status: "error", message: "Daemon offline" }), "Ship");
    const checkbox = container.querySelector("input");
    checkbox.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    return {
      checked: checkbox.checked,
      disabled: checkbox.disabled,
      completed: tasks[0].completed,
      errors,
    };
  });

  expect(result).toEqual({
    checked: false,
    disabled: false,
    completed: false,
    errors: ["Daemon offline"],
  });
});

test("shared formatting clamps invalid timers and a newer toast survives an older hide timer", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { formatTime, showToast } = await import(`/shared/utils.js?test=${crypto.randomUUID()}`);
    const toast = document.createElement("div");
    toast.className = "hidden";
    document.body.appendChild(toast);
    showToast(toast, "first", 10);
    await new Promise((resolve) => setTimeout(resolve, 20));
    showToast(toast, "second", 500);
    await new Promise((resolve) => setTimeout(resolve, 320));
    return {
      invalid: formatTime(Number.NaN),
      negative: formatTime(-12),
      text: toast.textContent,
      visible: !toast.classList.contains("hidden") && toast.classList.contains("show"),
    };
  });

  expect(result).toEqual({
    invalid: "00:00:00",
    negative: "00:00:00",
    text: "second",
    visible: true,
  });
});
