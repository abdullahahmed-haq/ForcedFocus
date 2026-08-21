let apiToken = "";
let tokenRequest = null;
const activeRequests = new Map();
const REQUEST_TIMEOUT_MS = 5000;

function apiUrl(baseUrl, endpoint) {
  return `${String(baseUrl || "").replace(/\/$/, "")}${endpoint}`;
}

async function fetchWithTimeout(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

async function loadApiToken(baseUrl = "", forceRefresh = false) {
  if (!forceRefresh && window.apiToken) {
    apiToken = window.apiToken;
    return apiToken;
  }
  if (tokenRequest) return tokenRequest;

  tokenRequest = (async () => {
    try {
      const response = await fetchWithTimeout(apiUrl(baseUrl, "/"), {
        cache: "no-store",
      });
      if (!response.ok) return "";
      const html = await response.text();
      const match = html.match(/window\.apiToken\s*=\s*["']([^"']+)["']/);
      apiToken = match?.[1] || "";
      if (apiToken) window.apiToken = apiToken;
    } catch {
      apiToken = "";
    }
    return apiToken;
  })();
  try {
    return await tokenRequest;
  } finally {
    tokenRequest = null;
  }
}

async function responsePayload(response) {
  try {
    const payload = await response.json();
    if (payload && typeof payload === "object") {
      if (!response.ok && payload.status !== "error") payload.status = "error";
      if (!response.ok) payload.http_status = response.status;
      return payload;
    }
  } catch {
    // Convert proxy/server HTML and empty responses into the same contract the
    // clients already use for daemon errors.
  }
  return {
    status: "error",
    http_status: response.status,
    message: response.ok
      ? "The local service returned an invalid response."
      : `The local service returned HTTP ${response.status}.`,
  };
}

async function performRequest(method, url, body, baseUrl) {
  if (!apiToken) {
    await loadApiToken(baseUrl);
  }

  const headers = {};
  if (body !== null && body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (apiToken) headers["X-API-Token"] = apiToken;

  const options = { method, headers, cache: "no-store" };
  if (body !== null && body !== undefined) options.body = JSON.stringify(body);

  let response = await fetchWithTimeout(url, options);
  if (response.status === 401) {
    // The daemon rotates this per-launch token. Fetch a newly injected copy of
    // the root document instead of rereading the stale value in the live page.
    await loadApiToken(baseUrl, true);
    if (apiToken) headers["X-API-Token"] = apiToken;
    else delete headers["X-API-Token"];
    response = await fetchWithTimeout(url, options);
  }
  return responsePayload(response);
}

export async function api(method, endpoint, body = null, baseUrl = "") {
  const normalizedMethod = String(method || "GET").toUpperCase();
  const url = apiUrl(baseUrl, endpoint);
  const requestKey = `${normalizedMethod}:${url}`;

  // Status/config renders often ask for the same resource concurrently. Share
  // the request instead of aborting another consumer and making it look offline.
  if (normalizedMethod === "GET" && activeRequests.has(requestKey)) {
    return activeRequests.get(requestKey);
  }

  const request = performRequest(normalizedMethod, url, body, baseUrl).catch((error) => {
    if (error.name !== "AbortError") console.error("API Error:", error);
    return {
      status: "error",
      message: error.name === "AbortError"
        ? "The local service timed out."
        : "Communication failed.",
    };
  });

  if (normalizedMethod === "GET") {
    activeRequests.set(requestKey, request);
    request.finally(() => {
      if (activeRequests.get(requestKey) === request) activeRequests.delete(requestKey);
    });
  }

  return request;
}
