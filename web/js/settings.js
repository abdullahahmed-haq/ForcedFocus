import { escapeHtml, extractDomain, showToast as sharedShowToast } from "../shared/utils.js";
import { api } from "../shared/api.js";
import { getTimePickerValue, initTimePickers, setTimePickerValue } from "./time-picker.js";

/**
 * ForcedFocus — Settings Client
 */

const $ = (sel) => document.querySelector(sel);

const normalizeNumerals = (str) => {
  if (typeof str !== 'string') return str;
  return str.replace(/[٠-٩]/g, d => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace(/[٫]/g, '.');
};

const formatCoordinate = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(1) : "0.0";
};

const setCoordinateValue = (input, value) => {
  if (!input) return;
  const raw = value ?? 0;
  input.dataset.preciseValue = String(raw);
  input.dataset.coordinateDirty = "false";
  input.value = formatCoordinate(raw);
};

const coordinateValueForSave = (input) => {
  if (!input) return 0;
  const raw = input.dataset.coordinateDirty === "true" ? input.value : input.dataset.preciseValue;
  return parseFloat(normalizeNumerals(raw)) || 0;
};

const els = {
  settingsGrid: $("#settingsGrid"),
  soundLibrary: $("#soundLibrary"),
  btnSaveSettings: $("#btnSaveSettings"),
  settingsCancel: $("#settingsCancel"),
  toast: $("#toast"),
  fileInput: $("#fileInput"),
  btnTriggerUpload: $("#btnTriggerUpload"),
  btnToggleLibrary: $("#btnToggleLibrary"),
  libraryContent: $("#libraryContent"),
  uploadStatus: $("#uploadStatus"),
  groupList: $("#groupList"),
  btnNewGroup: $("#btnNewGroup"),
  groupModal: $("#groupModal"),
  groupNameInput: $("#groupNameInput"),
  groupDomainsInput: $("#groupDomainsInput"),
  btnSaveGroup: $("#btnSaveGroup"),
  btnCancelGroup: $("#btnCancelGroup"),
  groupModalTitle: $("#groupModalTitle"),
  navTabs: document.querySelectorAll(".nav-tab"),
  tabContents: document.querySelectorAll(".tab-content"),
  dropZone: $("#btnTriggerUpload"),
  domainError: $("#domainError"),
  btnToggleSoundMapping: $("#btnToggleSoundMapping"),
  iconToggleSoundMapping: $("#iconToggleSoundMapping"),
  soundMappingContent: $("#soundMappingContent"),
  settingsHealthBanner: $("#settingsHealthBanner"),
  settingsHealthTitle: $("#settingsHealthTitle"),
  settingsHealthMessage: $("#settingsHealthMessage"),
  settingsHealthAction: $("#settingsHealthAction"),
  groupModalBackdrop: $("#groupModalBackdrop"),
  settingsFooter: $("#settingsFooter"),
  sleepScheduleForm: $("#sleepScheduleForm"),
  sleepScheduleEnabled: $("#sleepScheduleEnabled"),
  sleepScheduleDays: $("#sleepScheduleDays"),
  sleepScheduleSleepTime: $("#sleepScheduleSleepTime"),
  sleepScheduleWakeTime: $("#sleepScheduleWakeTime"),
  sleepDomainEditor: $("#sleepDomainEditor"),
  sleepDomainTitle: $("#sleepDomainTitle"),
  sleepDomainDescription: $("#sleepDomainDescription"),
  sleepDomainInput: $("#sleepDomainInput"),
  btnAddSleepDomain: $("#btnAddSleepDomain"),
  sleepDomainList: $("#sleepDomainList"),
  sleepDomainCount: $("#sleepDomainCount"),
  sleepScheduleError: $("#sleepScheduleError"),
  sleepSettingsStatus: $("#sleepSettingsStatus"),
  btnSaveSleepSchedule: $("#btnSaveSleepSchedule"),
  btnRetrySleepSchedule: $("#btnRetrySleepSchedule"),
};

let settings = {};
let availableSounds = [];
let availableGroups = {};
let previewAudio = null;
let currentPreviewBtn = null;

let eventSource = null;
let sseReconnectTimer = null;
let _lastRevision = undefined;
let _lastNextPrayerSeconds = null;
let settingsReady = false;
let settingsDirty = false;
let groupModalTrigger = null;
let sleepSchedule = {
  enabled: false,
  days_of_week: [],
  sleep_time: "22:00",
  wake_time: "07:00",
  mode: "blacklist",
  blacklist: [],
  whitelist: [],
};
let sleepScheduleReady = false;
let sleepScheduleDirty = false;
let sleepScheduleSaveInFlight = false;

const showToast = (msg) => sharedShowToast(els.toast, msg);

function setSettingsHealth(kind = "healthy", message = "") {
  const unavailable = kind !== "healthy";
  els.settingsHealthBanner.classList.toggle("hidden", !unavailable);
  if (unavailable) {
    els.settingsHealthTitle.textContent = "Settings are unavailable";
    els.settingsHealthMessage.textContent = message || "The local service did not respond.";
  }
  document.querySelectorAll("#main-content input, #main-content select, #main-content button").forEach((control) => {
    if (control.classList.contains("nav-tab")) return;
    if (unavailable) {
      control.dataset.settingsDisabled = control.disabled ? "already" : "forced";
      control.disabled = true;
    } else if (control.dataset.settingsDisabled === "forced") {
      control.disabled = false;
      delete control.dataset.settingsDisabled;
    }
  });
  if (!unavailable) {
    setSettingsDirty(settingsDirty);
    setSleepScheduleDirty(sleepScheduleDirty);
  }
}

function setSettingsDirty(dirty) {
  settingsDirty = dirty;
  if (els.btnSaveSettings) els.btnSaveSettings.disabled = !settingsReady || !dirty;
}

function markSettingsDirty() {
  if (settingsReady) setSettingsDirty(true);
}

function normalizeSleepSchedule(config = {}) {
  const mode = ["ban", "blacklist", "whitelist"].includes(config.mode) ? config.mode : "blacklist";
  const days = Array.isArray(config.days_of_week)
    ? [...new Set(config.days_of_week.filter((day) => Number.isInteger(day) && day >= 0 && day <= 6))].sort((a, b) => a - b)
    : [];
  const domains = (name) => Array.isArray(config[name])
    ? [...new Set(config[name].filter((domain) => typeof domain === "string" && domain))]
    : [];
  return {
    enabled: Boolean(config.enabled),
    days_of_week: days,
    sleep_time: typeof config.sleep_time === "string" ? config.sleep_time : "22:00",
    wake_time: typeof config.wake_time === "string" ? config.wake_time : "07:00",
    mode,
    blacklist: domains("blacklist"),
    whitelist: domains("whitelist"),
  };
}

function showSleepScheduleError(message = "") {
  if (!els.sleepScheduleError) return;
  els.sleepScheduleError.textContent = message;
  els.sleepScheduleError.classList.toggle("hidden", !message);
}

function setSleepScheduleDirty(dirty) {
  sleepScheduleDirty = dirty;
  if (els.btnSaveSleepSchedule) {
    els.btnSaveSleepSchedule.disabled = !sleepScheduleReady || !dirty || sleepScheduleSaveInFlight;
  }
}

function selectedSleepDomains() {
  return sleepSchedule.mode === "ban" ? [] : sleepSchedule[sleepSchedule.mode];
}

function renderSleepDomainList() {
  if (!els.sleepDomainList) return;
  const selectedMode = sleepSchedule.mode;
  const usesDomainList = selectedMode !== "ban";
  const domains = selectedSleepDomains();
  els.sleepDomainEditor.hidden = !usesDomainList;
  if (!usesDomainList) return;

  const isWhitelist = selectedMode === "whitelist";
  els.sleepDomainTitle.textContent = isWhitelist ? "Allowed websites" : "Blocked websites";
  els.sleepDomainDescription.textContent = isWhitelist
    ? "Add the websites to allow during sleep. All other websites will be restricted."
    : "Add the websites to block during sleep.";
  els.sleepDomainCount.textContent = String(domains.length);
  els.sleepDomainList.replaceChildren();

  if (domains.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-list-item";
    empty.textContent = "No websites added yet.";
    els.sleepDomainList.appendChild(empty);
    return;
  }

  domains.forEach((domain) => {
    const item = document.createElement("li");
    const text = document.createElement("span");
    text.textContent = domain;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-btn";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${domain}`);
    remove.addEventListener("click", () => {
      sleepSchedule[selectedMode] = sleepSchedule[selectedMode].filter((itemDomain) => itemDomain !== domain);
      showSleepScheduleError("");
      setSleepScheduleDirty(true);
      renderSleepDomainList();
    });
    item.append(text, remove);
    els.sleepDomainList.appendChild(item);
  });
}

function renderSleepSchedule() {
  if (!els.sleepScheduleForm) return;
  const isLoading = els.sleepSettingsStatus.dataset.state === "loading";
  const controls = els.sleepScheduleForm.querySelectorAll("input, button");
  controls.forEach((control) => {
    if (control === els.btnRetrySleepSchedule) return;
    control.disabled = !sleepScheduleReady || sleepScheduleSaveInFlight;
  });
  els.btnRetrySleepSchedule.classList.toggle("hidden", sleepScheduleReady || isLoading);

  if (!sleepScheduleReady) {
    if (!isLoading) {
      els.sleepSettingsStatus.textContent = "Unavailable";
      els.sleepSettingsStatus.dataset.state = "error";
    }
    setSleepScheduleDirty(false);
    return;
  }

  els.sleepScheduleEnabled.checked = sleepSchedule.enabled;
  setTimePickerValue(els.sleepScheduleSleepTime, sleepSchedule.sleep_time);
  setTimePickerValue(els.sleepScheduleWakeTime, sleepSchedule.wake_time);
  els.sleepScheduleDays.querySelectorAll("input[type='checkbox']").forEach((input) => {
    input.checked = sleepSchedule.days_of_week.includes(Number(input.value));
  });
  document.querySelectorAll("input[name='sleepScheduleMode']").forEach((input) => {
    input.checked = input.value === sleepSchedule.mode;
  });
  els.sleepScheduleForm.classList.toggle("is-disabled", !sleepSchedule.enabled);
  els.sleepSettingsStatus.textContent = sleepSchedule.enabled ? "Enabled" : "Disabled";
  els.sleepSettingsStatus.dataset.state = sleepSchedule.enabled ? "enabled" : "disabled";
  renderSleepDomainList();
  setSleepScheduleDirty(sleepScheduleDirty);
}

function sleepSchedulePayload() {
  return {
    enabled: sleepSchedule.enabled,
    days_of_week: [...sleepSchedule.days_of_week],
    sleep_time: sleepSchedule.sleep_time,
    wake_time: sleepSchedule.wake_time,
    mode: sleepSchedule.mode,
    blacklist: [...sleepSchedule.blacklist],
    whitelist: [...sleepSchedule.whitelist],
  };
}

function validateSleepSchedule() {
  if (!sleepSchedule.sleep_time || !sleepSchedule.wake_time) return "Choose both sleep and wake times.";
  if (sleepSchedule.sleep_time === sleepSchedule.wake_time) return "Sleep and wake times must differ.";
  if (!sleepSchedule.enabled) return "";
  if (sleepSchedule.days_of_week.length === 0) return "Select at least one sleep night.";
  if (sleepSchedule.mode !== "ban" && sleepSchedule[sleepSchedule.mode].length === 0) {
    return "Add at least one website for the selected restriction mode.";
  }
  return "";
}

function focusFirstSleepScheduleProblem() {
  if (!sleepSchedule.sleep_time) els.sleepScheduleSleepTime.focus();
  else if (!sleepSchedule.wake_time || sleepSchedule.sleep_time === sleepSchedule.wake_time) els.sleepScheduleWakeTime.focus();
  else if (sleepSchedule.enabled && sleepSchedule.days_of_week.length === 0) els.sleepScheduleDays.querySelector("input")?.focus();
  else if (sleepSchedule.enabled && sleepSchedule.mode !== "ban" && sleepSchedule[sleepSchedule.mode].length === 0) els.sleepDomainInput.focus();
}

async function fetchSleepSchedule() {
  sleepScheduleReady = false;
  els.sleepSettingsStatus.textContent = "Loading…";
  els.sleepSettingsStatus.dataset.state = "loading";
  renderSleepSchedule();
  try {
    const data = await api("GET", "/api/sleep-schedule");
    if (data.status !== "ok") throw new Error(data.message || "Sleep Schedule is unavailable.");
    sleepSchedule = normalizeSleepSchedule(data.pending_sleep_schedule || data.pending_config || data.sleep_schedule);
    sleepScheduleReady = true;
    sleepScheduleDirty = false;
    showSleepScheduleError("");
  } catch {
    sleepScheduleReady = false;
    showSleepScheduleError("The Sleep Schedule could not be loaded. Retry after the daemon is available.");
  }
  renderSleepSchedule();
}

async function saveSleepSchedule() {
  const validationMessage = validateSleepSchedule();
  if (validationMessage) {
    showSleepScheduleError(validationMessage);
    focusFirstSleepScheduleProblem();
    return;
  }

  sleepScheduleSaveInFlight = true;
  els.btnSaveSleepSchedule.textContent = "Saving…";
  els.btnSaveSleepSchedule.setAttribute("aria-busy", "true");
  showSleepScheduleError("");
  renderSleepSchedule();
  try {
    const data = await api("POST", "/api/sleep-schedule", sleepSchedulePayload());
    if (data.status !== "ok") throw new Error(data.message || "Failed to save Sleep Schedule.");
    sleepSchedule = normalizeSleepSchedule(data.pending_sleep_schedule || data.pending_config || data.sleep_schedule || sleepSchedule);
    sleepScheduleDirty = false;
    showToast(data.message || "Sleep Schedule saved.");
  } catch (error) {
    showSleepScheduleError(error.message || "Failed to save Sleep Schedule.");
  } finally {
    sleepScheduleSaveInFlight = false;
    els.btnSaveSleepSchedule.textContent = "Save Sleep Schedule";
    els.btnSaveSleepSchedule.removeAttribute("aria-busy");
    renderSleepSchedule();
  }
}

function addSleepDomain() {
  if (sleepSchedule.mode === "ban") return;
  const domain = extractDomain(els.sleepDomainInput.value);
  if (!domain) {
    showSleepScheduleError("Enter a valid domain.");
    els.sleepDomainInput.focus();
    return;
  }
  if (!sleepSchedule[sleepSchedule.mode].includes(domain)) {
    sleepSchedule[sleepSchedule.mode].push(domain);
    setSleepScheduleDirty(true);
  }
  els.sleepDomainInput.value = "";
  showSleepScheduleError("");
  renderSleepDomainList();
}

function playPreview(filename, btnElement = null) {
  if (previewAudio) {
    previewAudio.pause();
    previewAudio = null;
  }

  if (currentPreviewBtn) {
    currentPreviewBtn.textContent = "▶";
    const wasSameBtn = (currentPreviewBtn === btnElement);
    currentPreviewBtn = null;
    if (wasSameBtn) {
      return;
    }
  }

  if (!filename) return;
  
  previewAudio = new Audio("/assets/sounds/" + encodeURIComponent(filename));
  
  if (btnElement) {
    currentPreviewBtn = btnElement;
    btnElement.textContent = "⏹";
    previewAudio.addEventListener("ended", () => {
      if (currentPreviewBtn === btnElement) {
        btnElement.textContent = "▶";
        currentPreviewBtn = null;
      }
    });
  }

  previewAudio.play().catch((e) => {
    console.error("Preview error:", e);
    if (currentPreviewBtn === btnElement && btnElement) {
      btnElement.textContent = "▶";
      currentPreviewBtn = null;
    }
  });
}

async function handleFileUpload(e) {
  const file = e.target.files[0];
  if (!file) return;

  if (!file.name.endsWith(".mp3")) {
    return showToast("Only .mp3 files are allowed.");
  }

  els.uploadStatus.textContent = "Uploading…";

  const reader = new FileReader();
  reader.onload = async () => {
    const base64 = reader.result.split(",")[1];
    try {
      const res = await api("POST", "/api/upload-sound", {
        filename: file.name,
        data: base64,
      });
      if (res.status === "ok") {
        showToast("Sound uploaded.");
        const soundsRes = await api("GET", "/api/sounds");
        if (soundsRes.sounds) {
          availableSounds = soundsRes.sounds;
          renderSettings();
          renderSoundLibrary();
        }
      } else {
        showToast("Error: " + res.message);
      }
    } catch {
      showToast("Upload failed.");
    }
    els.uploadStatus.textContent = "";
    els.fileInput.value = "";
  };
  reader.readAsDataURL(file);
}

function renderSettings() {
  if (!settings) return;
  const labels = {
    sound_start: "Session Start",
    sound_rescue: "Rescue Mode",
    sound_unlock: "Unlock Request",
    sound_break: "Break Time",
    sound_end: "Session End",
    sound_scheduled: "Scheduled Session",
    sound_blocked: "Blocked Site Access",
    sound_prayer: "Prayer Time",
  };

  // R7: Use escapeHtml on all user-controlled data
  let html = "";
  for (const [key, label] of Object.entries(labels)) {
    const current = settings[key] || "";
        html += `
            <div class="flex items-center justify-between p-5 bg-black/20 rounded-2xl border border-white/5 hover:border-white/10 transition-colors">
                <label for="setting-${escapeHtml(key)}" class="font-bold text-gray-200 text-base">${escapeHtml(label)}</label>
                <div class="relative">
                  <select id="setting-${escapeHtml(key)}" name="${escapeHtml(key)}" autocomplete="off" class="bg-black/50 border border-white/10 text-gray-200 text-sm font-bold rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 block p-3 pr-10 outline-none appearance-none cursor-pointer hover:border-white/20 transition-colors" data-key="${escapeHtml(key)}">
                      <option value="">None</option>
                      ${availableSounds.map((s) => `<option value="${escapeHtml(s)}" ${s === current ? "selected" : ""}>${escapeHtml(s)}</option>`).join("")}
                  </select>
                  <div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-3 text-gray-400">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                  </div>
                </div>
            </div>
        `;
  }
  els.settingsGrid.innerHTML = html;

  // Notifications and Goals
  const intentEnabled = document.getElementById("intentNotifEnabled");
  const intentInterval = document.getElementById("intentNotifInterval");
  const dailyFocusGoal = document.getElementById("dailyFocusGoalHours");
  if (intentEnabled)
    intentEnabled.checked = settings.intent_notification_enabled !== false;
  if (intentInterval)
    intentInterval.value = settings.intent_notification_interval || 15;
  if (dailyFocusGoal)
    dailyFocusGoal.value = settings.daily_focus_goal_hours || "";
    
  // Prayer Times
  const prayerEnabled = document.getElementById("prayerBlockEnabled");
  const prayerLat = document.getElementById("prayerLatitude");
  const prayerLon = document.getElementById("prayerLongitude");
  const prayerMethod = document.getElementById("prayerMethod");
  const prayerMinBefore = document.getElementById("prayerMinutesBefore");
  const prayerMinAfter = document.getElementById("prayerMinutesAfter");
  
  if (prayerEnabled) {
    prayerEnabled.checked = settings.prayer_block_enabled || false;
    updatePrayerCheckboxState();
  }
  setCoordinateValue(prayerLat, settings.prayer_latitude ?? 0);
  setCoordinateValue(prayerLon, settings.prayer_longitude ?? 0);
  if (prayerMethod) prayerMethod.value = settings.prayer_method ?? 5;
  if (prayerMinBefore) prayerMinBefore.value = settings.prayer_minutes_before ?? 10;
  if (prayerMinAfter) prayerMinAfter.value = settings.prayer_minutes_after ?? 30;
}

function updatePrayerCheckboxState() {
  const prayerEnabled = document.getElementById("prayerBlockEnabled");
  if (prayerEnabled) {
    // Disable checkbox if within 30 minutes of next prayer and it's currently enabled
    if (prayerEnabled.checked && _lastNextPrayerSeconds !== null && _lastNextPrayerSeconds <= 1800) {
      prayerEnabled.disabled = true;
      prayerEnabled.title = "Cannot disable prayer mode within 30 minutes of a prayer time.";
      prayerEnabled.parentElement.title = "Cannot disable prayer mode within 30 minutes of a prayer time.";
    } else {
      prayerEnabled.disabled = false;
      prayerEnabled.title = "";
      prayerEnabled.parentElement.title = "";
    }
  }
}

async function saveSettings() {
  if (!settingsReady || !settingsDirty) return;
  const btn = els.btnSaveSettings;
  if (btn) btn.disabled = true;
  const originalText = btn ? btn.textContent : "";
  if (btn) {
    btn.textContent = "Saving…";
    btn.setAttribute("aria-busy", "true");
  }

  try {
    const newSettings = {};
    els.settingsGrid.querySelectorAll("select").forEach((sel) => {
      newSettings[sel.dataset.key] = sel.value;
    });

    const intentEnabled = document.getElementById("intentNotifEnabled");
    const intentInterval = document.getElementById("intentNotifInterval");
    const dailyFocusGoal = document.getElementById("dailyFocusGoalHours");
    if (intentEnabled)
      newSettings.intent_notification_enabled = intentEnabled.checked;
    if (intentInterval) {
      const parsedInterval = parseInt(intentInterval.value, 10);
      if (!Number.isInteger(parsedInterval) || parsedInterval < 1 || parsedInterval > 1440) {
        showToast("Notification interval must be 1-1440 minutes.");
        return;
      }
      newSettings.intent_notification_interval = parsedInterval;
    }
    if (dailyFocusGoal && dailyFocusGoal.value.trim() !== "") {
      const parsedHours = parseFloat(normalizeNumerals(dailyFocusGoal.value));
      if (!isNaN(parsedHours) && parsedHours > 0) {
        newSettings.daily_focus_goal_hours = parsedHours;
      }
    } else if (dailyFocusGoal) {
      newSettings.daily_focus_goal_hours = 0;
    }

    // Prayer settings
    const prayerEnabled = document.getElementById("prayerBlockEnabled");
    const prayerLat = document.getElementById("prayerLatitude");
    const prayerLon = document.getElementById("prayerLongitude");
    const prayerMethod = document.getElementById("prayerMethod");
    const prayerMinBefore = document.getElementById("prayerMinutesBefore");
    const prayerMinAfter = document.getElementById("prayerMinutesAfter");

    if (prayerEnabled) newSettings.prayer_block_enabled = prayerEnabled.checked;
    if (prayerLat) newSettings.prayer_latitude = coordinateValueForSave(prayerLat);
    if (prayerLon) newSettings.prayer_longitude = coordinateValueForSave(prayerLon);
    if (prayerMethod) newSettings.prayer_method = parseInt(prayerMethod.value, 10) || 5;
    // Helper to safely parse and clamp minutes (0 to 120)
    const parseMinutes = (val) => {
      const parsed = parseInt(normalizeNumerals(val), 10);
      return Math.min(120, Math.max(0, isNaN(parsed) ? 0 : parsed));
    };

    if (prayerMinBefore) newSettings.prayer_minutes_before = parseMinutes(prayerMinBefore.value);
    if (prayerMinAfter) newSettings.prayer_minutes_after = parseMinutes(prayerMinAfter.value);

    const res = await api("POST", "/api/settings", { settings: newSettings });
    if (res.status === "ok") {
      showToast("Settings saved.");
      await fetchData(); // Instant update
      setSettingsDirty(false);
    } else {
      showToast("Error: " + res.message);
    }
  } catch {
    showToast("Failed to save settings.");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalText;
      btn.removeAttribute("aria-busy");
      setSettingsDirty(settingsDirty);
    }
  }
}

async function fetchData() {
  try {
    const [settingsRes, soundsRes, groupsRes] = await Promise.all([
      api("GET", "/api/settings"),
      api("GET", "/api/sounds"),
      api("GET", "/api/groups"),
    ]);

    if (settingsRes.status !== "ok" || soundsRes.status !== "ok" || groupsRes.status !== "ok") {
      throw new Error("The local service did not return current settings.");
    }

    settings = settingsRes.settings || {};
    availableSounds = soundsRes.sounds || [];
    availableGroups = groupsRes.groups || {};

    renderSettings();
    renderSoundLibrary();
    renderGroups();
    settingsReady = true;
    setSettingsHealth();
    setSettingsDirty(false);
    return true;
  } catch (e) {
    console.error("Fetch error:", e);
    settingsReady = false;
    setSettingsHealth("offline", "The local service did not respond. Retry after the daemon is available.");
    els.settingsGrid.innerHTML = '<div class="text-sm text-rose-300 p-4 text-center">Audio settings could not be loaded.</div>';
    els.soundLibrary.innerHTML = '<div class="empty-state">Sound library unavailable.</div>';
    els.groupList.innerHTML = '<div class="empty-state">Domain groups unavailable.</div>';
    return false;
  }
}

function connectSSE() {
  if (sseReconnectTimer) {
    clearTimeout(sseReconnectTimer);
    sseReconnectTimer = null;
  }
  if (eventSource) eventSource.close();
  
  eventSource = new EventSource("/api/stream");
  
  eventSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      
      let prayerTimeChanged = false;
      if (data.next_prayer_seconds !== _lastNextPrayerSeconds) {
        _lastNextPrayerSeconds = data.next_prayer_seconds;
        prayerTimeChanged = true;
      }

      // Instant Config Sync
      if (typeof _lastRevision === "undefined") {
        _lastRevision = data.state_revision;
      } else if (data.state_revision !== undefined && data.state_revision > _lastRevision) {
        _lastRevision = data.state_revision;
        // Never replace fields the user is actively editing with a background
        // refresh from another ForcedFocus surface.
        if (settingsDirty || sleepScheduleDirty) {
          showToast("Settings changed elsewhere. Save or reload before reviewing those changes.");
        } else {
          Promise.all([fetchData(), fetchSleepSchedule()]);
        }
      } else if (prayerTimeChanged) {
        // Just re-render checkbox state, not the whole settings UI
        updatePrayerCheckboxState();
      }
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  };
  
  eventSource.onerror = () => {
    console.warn("SSE connection lost. Reconnecting in 3s...");
    eventSource.close();
    eventSource = null;
    sseReconnectTimer = setTimeout(connectSSE, 3000);
  };
}

async function init() {
  initTimePickers();
  await Promise.all([fetchData(), fetchSleepSchedule()]);
  connectSSE();

  // Attach event listeners
  els.btnSaveSettings.addEventListener("click", saveSettings);
  els.settingsHealthAction.addEventListener("click", fetchData);
  if(els.btnTriggerUpload) els.btnTriggerUpload.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", handleFileUpload);
  document.querySelector("#main-content").addEventListener("input", (event) => {
    if (event.target.closest("#sleepScheduleForm")) return;
    if (event.target.matches("#prayerLatitude, #prayerLongitude")) {
      event.target.dataset.coordinateDirty = "true";
      event.target.dataset.preciseValue = event.target.value;
    }
    markSettingsDirty();
  });
  document.querySelector("#main-content").addEventListener("change", (event) => {
    if (event.target.closest("#sleepScheduleForm")) return;
    markSettingsDirty();
  });
  window.addEventListener("beforeunload", (event) => {
    if (!settingsDirty && !sleepScheduleDirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
  els.settingsCancel?.addEventListener("click", (event) => {
    if (!settingsDirty && !sleepScheduleDirty) return;
    if (!window.confirm("Discard unsaved settings changes?")) event.preventDefault();
  });

  els.sleepScheduleForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    saveSleepSchedule();
  });
  els.sleepScheduleEnabled?.addEventListener("change", () => {
    sleepSchedule.enabled = els.sleepScheduleEnabled.checked;
    els.sleepScheduleForm.classList.toggle("is-disabled", !sleepSchedule.enabled);
    els.sleepSettingsStatus.textContent = sleepSchedule.enabled ? "Enabled" : "Disabled";
    els.sleepSettingsStatus.dataset.state = sleepSchedule.enabled ? "enabled" : "disabled";
    showSleepScheduleError("");
    setSleepScheduleDirty(true);
  });
  els.sleepScheduleDays?.addEventListener("change", () => {
    sleepSchedule.days_of_week = [...els.sleepScheduleDays.querySelectorAll("input:checked")]
      .map((input) => Number(input.value))
      .sort((a, b) => a - b);
    showSleepScheduleError("");
    setSleepScheduleDirty(true);
  });
  [els.sleepScheduleSleepTime, els.sleepScheduleWakeTime].forEach((input) => {
    input?.addEventListener("input", () => {
      sleepSchedule[input === els.sleepScheduleSleepTime ? "sleep_time" : "wake_time"] = getTimePickerValue(input);
      showSleepScheduleError("");
      setSleepScheduleDirty(true);
    });
  });
  document.querySelectorAll("input[name='sleepScheduleMode']").forEach((input) => {
    input.addEventListener("change", () => {
      if (!input.checked) return;
      sleepSchedule.mode = input.value;
      showSleepScheduleError("");
      setSleepScheduleDirty(true);
      renderSleepDomainList();
    });
  });
  els.btnAddSleepDomain?.addEventListener("click", addSleepDomain);
  els.sleepDomainInput?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addSleepDomain();
  });
  els.btnRetrySleepSchedule?.addEventListener("click", fetchSleepSchedule);
  
  const btnGetLocation = document.getElementById("btnGetLocation");
  if (btnGetLocation) {
    btnGetLocation.addEventListener("click", () => {
      if (!navigator.geolocation) {
        showToast("Geolocation is not supported by your browser.");
        return;
      }
      const originalText = btnGetLocation.innerHTML;
      btnGetLocation.textContent = "Fetching…";
      btnGetLocation.disabled = true;
      navigator.geolocation.getCurrentPosition(
        (position) => {
          setCoordinateValue(document.getElementById("prayerLatitude"), position.coords.latitude);
          setCoordinateValue(document.getElementById("prayerLongitude"), position.coords.longitude);
          btnGetLocation.innerHTML = originalText;
          btnGetLocation.disabled = false;
          markSettingsDirty();
          showToast("Location updated successfully.");
        },
        () => {
          showToast("Unable to retrieve your location.");
          btnGetLocation.innerHTML = originalText;
          btnGetLocation.disabled = false;
        }
      );
    });
  }
  
  // Drag and Drop
  const dz = els.dropZone;
  if (dz) {
    dz.addEventListener('dragover', (e) => {
      e.preventDefault();
      dz.classList.add('bg-indigo-500/10', 'border-indigo-400');
    });
    dz.addEventListener('dragleave', (e) => {
      e.preventDefault();
      dz.classList.remove('bg-indigo-500/10', 'border-indigo-400');
    });
    dz.addEventListener('drop', (e) => {
      e.preventDefault();
      dz.classList.remove('bg-indigo-500/10', 'border-indigo-400');
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        els.fileInput.files = e.dataTransfer.files;
        handleFileUpload({ target: els.fileInput });
      }
    });
  }

  // Tabs
  const tabHash = {
    "tab-sounds": "sounds",
    "tab-domains": "domains",
    "tab-prayer": "prayer",
    "tab-notifications": "notifications",
    "tab-sleep": "sleep",
  };
  const activateTab = (tab, { focus = false, updateHash = true } = {}) => {
    const targetId = tab?.dataset.target;
    const targetEl = document.getElementById(targetId);
    if (!targetEl) return;
    els.navTabs.forEach((candidate) => {
      const active = candidate === tab;
      candidate.classList.toggle("active", active);
      candidate.classList.toggle("bg-indigo-500/15", active);
      candidate.classList.toggle("border-indigo-500/30", active);
      candidate.classList.toggle("text-white", active);
      candidate.classList.toggle("text-gray-400", !active);
      candidate.classList.toggle("border-transparent", !active);
      candidate.setAttribute("aria-selected", String(active));
      candidate.tabIndex = active ? 0 : -1;
    });
    els.tabContents.forEach((content) => {
      const active = content === targetEl;
      content.classList.toggle("active", active);
      content.hidden = !active;
    });
    if (els.settingsFooter) els.settingsFooter.hidden = targetId === "tab-sleep";
    if (focus) tab.focus();
    tab.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (updateHash) window.history.replaceState(null, "", `#${tabHash[targetId]}`);
  };
  const tabs = [...els.navTabs];
  const initialHash = window.location.hash.slice(1);
  activateTab(tabs.find((tab) => tabHash[tab.dataset.target] === initialHash) || tabs[0], { updateHash: false });
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activateTab(tab));
    tab.addEventListener("keydown", (event) => {
      let nextIndex = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = tabs.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      activateTab(tabs[nextIndex], { focus: true });
    });
  });
  window.addEventListener("hashchange", () => {
    const hash = window.location.hash.slice(1);
    activateTab(tabs.find((tab) => tabHash[tab.dataset.target] === hash) || tabs[0], { updateHash: false });
  });

  // Sound Library Listeners
  els.soundLibrary.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-icon");
    if (!btn) return;
    const sound = btn.dataset.sound;
    if (btn.classList.contains("play-sound")) playPreview(sound, btn);
    if (btn.classList.contains("delete-sound")) deleteSound(sound, btn);
  });

  els.btnToggleLibrary.addEventListener("click", () => {
    els.btnToggleLibrary.classList.toggle("open");
    els.libraryContent.classList.toggle("hidden");
    els.btnToggleLibrary.setAttribute("aria-expanded", String(!els.libraryContent.classList.contains("hidden")));
  });

  if (els.btnToggleSoundMapping) {
    els.btnToggleSoundMapping.addEventListener("click", () => {
      els.soundMappingContent.classList.toggle("hidden");
      els.iconToggleSoundMapping.classList.toggle("rotate-180");
      els.btnToggleSoundMapping.setAttribute("aria-expanded", String(!els.soundMappingContent.classList.contains("hidden")));
    });
  }

  // Groups Listeners
  els.btnNewGroup.addEventListener("click", (event) => openGroupModal("", event.currentTarget));
  els.btnCancelGroup.addEventListener("click", closeGroupModal);
  els.groupModalBackdrop.addEventListener("click", closeGroupModal);
  els.btnSaveGroup.addEventListener("click", saveGroup);

  els.groupList.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-group-action");
    if (!btn) return;
    const action = btn.dataset.action;
    const name = btn.dataset.name;
    if (action === "edit") openGroupModal(name, btn);
    if (action === "delete") deleteGroup(name, btn);
  });
  
  if (els.groupDomainsInput) {
    const formatInput = (e) => {
      const val = e.target.value;
      if (!val.trim()) return;
      const cleanedDomains = val
        .split(/[\n, ]+/)
        .map(d => extractDomain(d))
        .filter(d => d.length > 0);
      e.target.value = cleanedDomains.join("\n");
    };
    
    els.groupDomainsInput.addEventListener("blur", formatInput);
    
    // Clear any previous error states immediately on input
    els.groupDomainsInput.addEventListener("input", () => {
      if (els.domainError) els.domainError.classList.add("hidden");
      if (els.btnSaveGroup) {
        els.btnSaveGroup.disabled = false;
        els.btnSaveGroup.classList.remove("opacity-50", "cursor-not-allowed");
      }
    });
  }

  els.settingsGrid.addEventListener("change", (e) => {
    if (e.target.tagName === "SELECT") {
      playPreview(e.target.value);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (els.groupModal.classList.contains("hidden")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeGroupModal();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...els.groupModal.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled])')];
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

function renderSoundLibrary() {
  if (availableSounds.length === 0) {
    els.soundLibrary.innerHTML =
      '<div class="empty-state">No sounds available.</div>';
    return;
  }

  let html = "";
  for (const sound of availableSounds) {
    const safeSound = escapeHtml(sound);
        html += `
            <div class="flex items-center justify-between p-3.5 border-b border-white/5 last:border-0 hover:bg-white/5 transition-colors rounded-xl group">
                <div class="flex items-center gap-4">
                    <button class="btn-icon play-sound w-10 h-10 flex items-center justify-center rounded-full bg-indigo-500/10 text-indigo-400 hover:bg-indigo-500 hover:text-white transition-colors" data-sound="${safeSound}" aria-label="Play ${safeSound}" title="Play">▶</button>
                    <div class="text-sm font-bold text-gray-200 truncate max-w-[200px]" title="${safeSound}">${safeSound}</div>
                </div>
                <button class="btn-icon delete delete-sound w-10 h-10 flex items-center justify-center rounded-full bg-red-500/10 text-red-400 hover:bg-red-500 hover:text-white transition-colors opacity-0 group-hover:opacity-100 group-focus-within:opacity-100" data-sound="${safeSound}" aria-label="Delete ${safeSound}" title="Delete">✕</button>
            </div>
        `;
  }
  els.soundLibrary.innerHTML = html;
}

async function deleteSound(filename, button = null) {
  if (!confirm(`Delete sound "${filename}"?`)) return;
  if (button) button.disabled = true;

  try {
    const res = await api("POST", "/api/delete-sound", { filename });
    if (res.status === "ok") {
      showToast(`Sound "${filename}" deleted.`);
      const soundsRes = await api("GET", "/api/sounds");
      if (soundsRes.sounds) {
        availableSounds = soundsRes.sounds;
        renderSettings();
        renderSoundLibrary();
      }
    } else {
      showToast("Error: " + res.message);
    }
  } catch {
    showToast("Failed to delete sound.");
  } finally {
    if (button) button.disabled = false;
  }
}

function renderGroups() {
  if (Object.keys(availableGroups).length === 0) {
    els.groupList.innerHTML =
      '<div class="empty-state">No groups created yet.</div>';
    return;
  }

  // R7: Use escapeHtml on all group names to prevent XSS
  let html = "";
  for (const [name, domains] of Object.entries(availableGroups)) {
    const safeName = escapeHtml(name);
        html += `
            <div class="bg-black/20 p-6 rounded-2xl border border-white/5 flex flex-col justify-between transition-[border-color,transform] hover:border-white/10 hover:-translate-y-1 group">
                <div class="mb-6">
                    <div class="font-bold text-white text-xl">${safeName}</div>
                    <div class="text-xs font-bold text-indigo-400 mt-2 tracking-wide uppercase">${domains.length} domains</div>
                </div>
                <div class="flex gap-3">
                    <button class="btn-group-action flex-1 py-3 rounded-xl bg-white/5 hover:bg-white/10 border border-transparent hover:border-white/10 text-gray-300 text-sm font-bold transition-[background-color,border-color,transform] hover:scale-105 active:scale-95" data-action="edit" data-name="${safeName}">✏️ Edit</button>
                    <button class="btn-group-action flex-1 py-3 rounded-xl bg-red-500/10 hover:bg-red-500/20 border border-transparent hover:border-red-500/20 text-red-400 text-sm font-bold transition-[background-color,border-color,transform] hover:scale-105 active:scale-95" data-action="delete" data-name="${safeName}">🗑️ Delete</button>
                </div>
            </div>
        `;
  }
  els.groupList.innerHTML = html;
}

function openGroupModal(name = "", trigger = document.activeElement) {
  groupModalTrigger = trigger;
  if (name) {
    els.groupModalTitle.textContent = "🛡️ Edit Group";
    els.groupNameInput.value = name;
    els.groupNameInput.disabled = true;
    els.groupDomainsInput.value = availableGroups[name].join("\n");
  } else {
    els.groupModalTitle.textContent = "🛡️ New Group";
    els.groupNameInput.value = "";
    els.groupNameInput.disabled = false;
    els.groupDomainsInput.value = "";
  }
  els.groupModal.classList.remove("hidden");
  requestAnimationFrame(() => els.groupNameInput.focus());
}

function closeGroupModal() {
  els.groupModal.classList.add("hidden");
  groupModalTrigger?.focus?.();
  groupModalTrigger = null;
}

async function saveGroup() {
  const name = els.groupNameInput.value.trim();
  const domainsText = els.groupDomainsInput.value.trim();
  if (!name) return showToast("Please enter a group name.");

  const domains = domainsText
    .split(/[\n, ]+/)
    .map((d) => extractDomain(d))
    .filter((d) => d.length > 0);

  if (domains.length === 0) return showToast("Please add at least one domain.");

  els.btnSaveGroup.disabled = true;
  const originalText = els.btnSaveGroup.textContent;
  els.btnSaveGroup.textContent = "Saving…";
  try {
    const res = await api("POST", "/api/groups", { name, domains });
    if (res.status === "ok") {
      closeGroupModal();
      showToast(`Group "${name}" saved.`);
      // S5: Re-fetch from server instead of optimistic update
      const groupsRes = await api("GET", "/api/groups");
      if (groupsRes.groups) {
        availableGroups = groupsRes.groups;
        renderGroups();
      }
    } else {
      showToast("Error: " + res.message);
    }
  } catch {
    showToast("Failed to save group.");
  } finally {
    els.btnSaveGroup.disabled = false;
    els.btnSaveGroup.textContent = originalText;
  }
}

async function deleteGroup(name, button = null) {
  if (!confirm(`Delete group "${name}"?`)) return;
  if (button) button.disabled = true;

  try {
    const res = await api("DELETE", `/api/groups/${encodeURIComponent(name)}`);
    if (res.status === "ok") {
      delete availableGroups[name];
      renderGroups();
      showToast(`Group "${name}" removed.`);
    } else {
      showToast("Error: " + res.message);
    }
  } catch {
    showToast("Failed to delete group.");
  } finally {
    if (button) button.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", init);
