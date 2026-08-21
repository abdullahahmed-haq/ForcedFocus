const { test, expect } = require("@playwright/test");

async function mockSettingsApi(page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/stream") return route.abort();
    const payloads = {
      "/api/settings": { status: "ok", settings: {} },
      "/api/sounds": { status: "ok", sounds: [] },
      "/api/groups": { status: "ok", groups: {} },
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(payloads[path] || { status: "ok" }) });
  });
}

async function mockHomeApi(page) {
  let hasQueuedSleepChanges = false;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/stream") return route.abort();
    if (path === "/api/status") {
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          status: "ok",
          active: false,
          sleep_schedule: {
            enabled: false,
            active: false,
            mode: "blacklist",
            days_of_week: [],
            sleep_time: "22:00",
            wake_time: "07:00",
            wake_at: null,
            next_start_at: null,
            remaining_seconds: 0,
            has_pending_changes: hasQueuedSleepChanges,
            pending_apply_at: hasQueuedSleepChanges ? "2030-01-02T07:00:00" : null,
          },
        }),
      });
    }
    if (path === "/api/sleep-schedule" && route.request().method() === "POST") {
      const schedule = route.request().postDataJSON();
      hasQueuedSleepChanges = true;
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          status: "ok",
          queued: true,
          sleep_schedule: {
            enabled: false,
            days_of_week: [],
            sleep_time: "22:00",
            wake_time: "07:00",
            mode: "blacklist",
            blacklist: [],
            whitelist: [],
          },
          pending_sleep_schedule: schedule,
          apply_at: "2030-01-02T07:00:00",
        }),
      });
    }
    const payloads = {
      "/api/version": { status: "ok", product_version: "1.0.0", api_version: 1, state_schema_version: 1 },
      "/api/health": { status: "ok", recovery_required: false, migration_in_progress: false },
      "/api/status": {
        status: "ok",
        active: false,
        sleep_schedule: {
          enabled: false,
          active: false,
          mode: "blacklist",
          days_of_week: [],
          sleep_time: "22:00",
          wake_time: "07:00",
          wake_at: null,
          next_start_at: null,
          remaining_seconds: 0,
          has_pending_changes: false,
          pending_apply_at: null,
        },
      },
      "/api/sleep-schedule": {
        status: "ok",
        sleep_schedule: {
          enabled: false,
          days_of_week: [],
          sleep_time: "22:00",
          wake_time: "07:00",
          mode: "blacklist",
          blacklist: [],
          whitelist: [],
        },
        summary: {
          enabled: false,
          active: false,
          mode: "blacklist",
          days_of_week: [],
          sleep_time: "22:00",
          wake_time: "07:00",
          wake_at: null,
          next_start_at: null,
          remaining_seconds: 0,
          has_pending_changes: false,
          pending_apply_at: null,
        },
      },
      "/api/lists": { status: "ok", blacklist: [], whitelist: [] },
      "/api/perma-blocklist": { status: "ok", domains: [] },
      "/api/groups": { status: "ok", groups: {} },
      "/api/templates": { status: "ok", templates: [] },
      "/api/settings": { status: "ok", settings: {} },
      "/api/sounds": { status: "ok", sounds: [] },
      "/api/prayer": { status: "ok", all_prayers: [] },
      "/api/history": { status: "ok", history: [] },
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(payloads[path] || { status: "ok" }) });
  });
}

async function mockSettingsApiWithRetry(page) {
  let shouldFail = true;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/stream") return route.abort();
    if (shouldFail && ["/api/settings", "/api/sounds", "/api/groups"].includes(path)) {
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ status: "error" }) });
    }
    const payloads = {
      "/api/settings": { status: "ok", settings: {} },
      "/api/sounds": { status: "ok", sounds: [] },
      "/api/groups": { status: "ok", groups: {} },
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(payloads[path] || { status: "ok" }) });
  });
  return () => { shouldFail = false; };
}

async function shellMetrics(page, url) {
  await page.goto(url);
  return page.evaluate(() => {
    const body = getComputedStyle(document.body);
    const before = getComputedStyle(document.body, "::before");
    const after = getComputedStyle(document.body, "::after");
    return {
      backgroundColor: body.backgroundColor,
      backgroundImage: body.backgroundImage,
      before: [before.top, before.left, before.width, before.height, before.backgroundColor, before.filter, before.opacity],
      after: [after.right, after.bottom, after.width, after.height, after.backgroundColor, after.filter, after.opacity],
    };
  });
}

test("Home and Settings share the same application background", async ({ browser }) => {
  const home = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const settings = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await mockSettingsApi(settings);
  const homeMetrics = await shellMetrics(home, "/html/index.html");
  const settingsMetrics = await shellMetrics(settings, "/html/settings.html");
  expect(homeMetrics).toEqual(settingsMetrics);
  expect(homeMetrics.backgroundColor).toBe("rgb(9, 9, 11)");
});

test("Home remains within a 390px viewport and tabs are keyboard accessible", async ({ page }) => {
  await mockHomeApi(page);
  await page.goto("/html/index.html");
  for (const width of [1280, 768, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  }
  const dashboardTab = page.getByRole("tab", { name: "Dashboard" });
  await dashboardTab.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("tab", { name: "Rules" })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/#rules$/);
});

test("Home exposes the daemon migration reliability state", async ({ page }) => {
  await mockHomeApi(page);
  await page.route("**/api/health", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ status: "ok", recovery_required: false, migration_in_progress: true }),
  }));
  await page.goto("/html/index.html");
  await expect(page.locator("#daemonHealthBanner")).toBeVisible();
  await expect(page.locator("#daemonHealthTitle")).toHaveText("State migration in progress");
  await expect(page.getByRole("button", { name: "Check again" })).toBeVisible();
  await expect(page.locator("#btnStart")).toBeDisabled();
});

test("Sleep Schedule editing moves from the dashboard to its Settings tab", async ({ page }) => {
  await mockHomeApi(page);
  const sleepScheduleLoaded = page.waitForResponse((response) => (
    new URL(response.url()).pathname === "/api/sleep-schedule" && response.request().method() === "GET"
  ));
  await page.goto("/html/index.html");
  await sleepScheduleLoaded;
  await expect(page.locator("#sleepScheduleState")).toHaveText("Disabled");
  await page.getByRole("tab", { name: "Schedules" }).click();

  await expect(page.getByRole("heading", { name: "Sleep Schedule" })).toBeVisible();
  await expect(page.locator("#sleepScheduleCard input")).toHaveCount(0);
  await expect(page.locator("#sleepOverviewTime")).toHaveText("10:00 PM – 7:00 AM");
  await expect(page.locator("#sleepScheduleModal")).toHaveCount(0);
  const editSleepSchedule = page.getByRole("link", { name: "Edit" });
  await expect(editSleepSchedule).toHaveAttribute("href", "/html/settings.html#sleep");
  await editSleepSchedule.click();
  await expect(page).toHaveURL(/\/html\/settings\.html#sleep$/);
  await expect(page.getByRole("tab", { name: "Sleep Schedule" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#sleepScheduleForm")).toBeVisible();
  await page.getByText("Enable Sleep Schedule", { exact: true }).click();
  await expect(page.locator("#sleepScheduleEnabled")).toBeChecked();
  await page.locator("#sleepScheduleDays input[value='0']").check();
  await page.locator("#sleepScheduleSleepTime").click();
  const timePicker = page.getByRole("dialog", { name: "Select sleep time" });
  await expect(timePicker).toBeVisible();
  await timePicker.getByRole("button", { name: "11 o'clock" }).click();
  await timePicker.getByRole("button", { name: "30 minutes" }).click();
  await timePicker.getByRole("button", { name: "PM" }).click();
  await timePicker.getByRole("button", { name: "Set time" }).click();
  await expect(page.locator("#sleepScheduleSleepTime")).toHaveValue("11:30 PM");
  await page.getByLabel("Block selected websites").check();
  await page.getByRole("button", { name: "Save Sleep Schedule" }).click();
  await expect(page.getByRole("alert")).toHaveText("Add at least one website for the selected restriction mode.");

  await page.locator("#sleepDomainInput").fill("example.com");
  await page.getByRole("button", { name: "Add website" }).click();
  await expect(page.locator("#sleepDomainList")).toContainText("example.com");
  const saveRequest = page.waitForRequest((request) => (
    new URL(request.url()).pathname === "/api/sleep-schedule" && request.method() === "POST"
  ));
  await page.getByRole("button", { name: "Save Sleep Schedule" }).click();
  expect((await saveRequest).postDataJSON()).toMatchObject({
    enabled: true,
    days_of_week: [0],
    sleep_time: "23:30",
    wake_time: "07:00",
    mode: "blacklist",
    blacklist: ["example.com"],
    whitelist: [],
  });
  await expect(page.locator("#sleepSettingsStatus")).toHaveText("Enabled");
  await expect(page.locator("#sleepScheduleModal")).toHaveCount(0);
});

test("Menu bar does not expose or load Sleep Schedule settings", async ({ page }) => {
  let sleepScheduleRequests = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/sleep-schedule") sleepScheduleRequests += 1;
    if (path === "/api/stream") return route.abort();
    const payloads = {
      "/api/settings": { status: "ok", settings: {} },
      "/api/groups": { status: "ok", groups: {} },
      "/api/templates": { status: "ok", templates: [] },
      "/api/status": { status: "ok", active: false },
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(payloads[path] || { status: "ok" }) });
  });

  await page.goto("/html/menubar.html");
  await page.waitForLoadState("networkidle");
  await expect(page.locator("#mbSleepSchedule")).toHaveCount(0);
  await expect(page.getByText("Sleep Schedule", { exact: true })).toHaveCount(0);
  expect(sleepScheduleRequests).toBe(0);
});

test("Settings uses a mobile horizontal tab rail and supports dialog keyboard close", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockSettingsApi(page);
  await page.goto("/html/settings.html");
  await expect(page.getByRole("tab", { name: "Sounds & Audio" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".settings-tabs")).toHaveCSS("flex-direction", "row");
  await page.getByRole("tab", { name: "Prayer Times" }).click();
  await expect(page).toHaveURL(/#prayer$/);
  await page.getByRole("tab", { name: "Domain Groups" }).click();
  await page.getByRole("button", { name: "Add new domain group" }).click();
  await expect(page.getByRole("dialog", { name: /New Group/ })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect.poll(() => page.evaluate(() => document.querySelector("#groupModal").contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: /New Group/ })).toBeHidden();
});

test("Settings enables Save only for dirty settings and resets after a confirmed save", async ({ page }) => {
  await mockSettingsApi(page);
  await page.goto("/html/settings.html");
  const save = page.getByRole("button", { name: "Save Preferences" });
  await expect(save).toBeDisabled();
  await page.getByRole("tab", { name: "Prayer Times" }).click();
  await page.locator("#prayerLatitude").fill("30.0444");
  await expect(save).toBeEnabled();
  await save.click();
  await expect(save).toBeDisabled();
});

test("Settings exposes an actionable retry state when its data cannot load", async ({ page }) => {
  const allowRetry = await mockSettingsApiWithRetry(page);
  await page.goto("/html/settings.html");
  await expect(page.getByRole("alert")).toBeVisible();
  allowRetry();
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("alert")).toBeHidden();
});

test("Settings does not overwrite unsaved fields after an external revision", async ({ page }) => {
  let settingsRequests = 0;
  await page.addInitScript(() => {
    window.testEventSources = [];
    window.EventSource = class MockEventSource {
      constructor() {
        this.readyState = 1;
        window.testEventSources.push(this);
      }
      close() { this.readyState = 2; }
      emit(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/settings") settingsRequests += 1;
    const payloads = {
      "/api/settings": { status: "ok", settings: { prayer_latitude: 30 } },
      "/api/sounds": { status: "ok", sounds: [] },
      "/api/groups": { status: "ok", groups: {} },
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(payloads[path] || { status: "ok" }) });
  });

  await page.goto("/html/settings.html");
  await page.getByRole("tab", { name: "Prayer Times" }).click();
  await page.locator("#prayerLatitude").fill("31.25");
  await page.evaluate(() => {
    const source = window.testEventSources[0];
    source.emit({ state_revision: 1 });
    source.emit({ state_revision: 2 });
  });

  await expect(page.locator("#prayerLatitude")).toHaveValue("31.25");
  await expect(page.getByRole("button", { name: "Save Preferences" })).toBeEnabled();
  expect(settingsRequests).toBe(1);
});
