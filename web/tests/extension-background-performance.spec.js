const { test, expect } = require("@playwright/test");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function chromeEvent() {
  return { addListener() {} };
}

async function bootBackground() {
  const requests = [];
  const eventSources = [];
  const status = {
    status: "ok",
    active: false,
    state: "idle",
    state_revision: 7,
    sleep_schedule: { enabled: false, active: false },
  };
  class FakeEventSource {
    constructor() {
      this.readyState = 1;
      eventSources.push(this);
    }
    close() {
      this.readyState = 2;
    }
  }
  const chrome = {
    declarativeNetRequest: {
      MAX_NUMBER_OF_DYNAMIC_RULES: 5000,
      getDynamicRules: async () => [],
      updateDynamicRules: async () => {},
    },
    storage: {
      session: { get: async () => ({}), set: async () => {} },
      local: { get: (_keys, callback) => callback({}), set: async () => {} },
    },
    alarms: { create() {}, clear() {}, onAlarm: chromeEvent() },
    runtime: {
      onStartup: chromeEvent(),
      onInstalled: chromeEvent(),
      onConnect: chromeEvent(),
      onMessage: chromeEvent(),
      sendMessage: async () => {},
      getURL: (value) => `chrome-extension://test/${value}`,
    },
    contextMenus: { removeAll(callback) { callback(); }, create() {}, onClicked: chromeEvent() },
    webNavigation: { onBeforeNavigate: chromeEvent(), onErrorOccurred: chromeEvent() },
    action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
    notifications: { create() {} },
    scripting: { executeScript: async () => {} },
    tabs: { update: async () => {} },
  };
  const fetch = async (url) => {
    const pathname = new URL(url).pathname;
    requests.push(pathname);
    const payloads = {
      "/api/status": status,
      "/api/perma-blocklist": { status: "ok", domains: [], pending_unlocks: {} },
      "/api/settings": { status: "ok", settings: {} },
    };
    if (pathname === "/") {
      return new Response('<script>window.apiToken = "test-token";</script>');
    }
    return new Response(JSON.stringify(payloads[pathname] || { status: "ok" }), {
      headers: { "Content-Type": "application/json" },
    });
  };
  const sandbox = {
    AbortSignal,
    Date,
    EventSource: FakeEventSource,
    Promise,
    Response,
    URL,
    chrome,
    clearTimeout,
    console: { log() {}, warn() {}, error() {} },
    fetch,
    setTimeout,
  };
  vm.createContext(sandbox);
  const source = fs.readFileSync(
    path.resolve(__dirname, "../../chrome-extension/background.js"),
    "utf8",
  );
  vm.runInContext(source, sandbox, { filename: "background.js" });
  await new Promise((resolve) => setTimeout(resolve, 20));
  requests.length = 0;
  return { eventSource: eventSources[0], requests, status };
}

async function emitStatus(harness, revision) {
  harness.eventSource.onmessage({
    data: JSON.stringify({ ...harness.status, state_revision: revision }),
  });
  await new Promise((resolve) => setTimeout(resolve, 10));
}

test("extension skips config requests for unchanged SSE revisions", async () => {
  const harness = await bootBackground();

  for (let index = 0; index < 5; index += 1) {
    await emitStatus(harness, 7);
  }

  expect(harness.requests.filter((path) => path === "/api/perma-blocklist")).toHaveLength(0);
  expect(harness.requests.filter((path) => path === "/api/settings")).toHaveLength(0);
});

test("extension refreshes config once when the SSE revision changes", async () => {
  const harness = await bootBackground();

  await emitStatus(harness, 8);
  await emitStatus(harness, 8);

  expect(harness.requests.filter((path) => path === "/api/perma-blocklist")).toHaveLength(1);
  expect(harness.requests.filter((path) => path === "/api/settings")).toHaveLength(1);
});
