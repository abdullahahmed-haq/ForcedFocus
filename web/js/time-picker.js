const TIME_PICKER_SELECTOR = "[data-time-picker]";

let pickerDialog = null;
let activeInput = null;
let draftHour = 12;
let draftMinute = 0;

function pad(value) {
  return String(value).padStart(2, "0");
}

function parseMachineTime(value) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || "").trim());
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return { hour, minute };
}

function toMachineTime(hour, minute) {
  return `${pad(hour)}:${pad(minute)}`;
}

export function formatTimePickerValue(value) {
  const parsed = parseMachineTime(value);
  if (!parsed) return "";
  const period = parsed.hour >= 12 ? "PM" : "AM";
  const displayHour = parsed.hour % 12 || 12;
  return `${displayHour}:${pad(parsed.minute)} ${period}`;
}

export function getTimePickerValue(input) {
  if (!input) return "";
  const storedValue = input.dataset.value || "";
  if (parseMachineTime(storedValue)) return storedValue;
  const directValue = parseMachineTime(input.value);
  return directValue ? toMachineTime(directValue.hour, directValue.minute) : "";
}

export function setTimePickerValue(input, value, { notify = false } = {}) {
  if (!input) return;
  const parsed = parseMachineTime(value);
  if (!parsed) {
    input.value = "";
    delete input.dataset.value;
    input.dataset.hasValue = "false";
  } else {
    const machineValue = toMachineTime(parsed.hour, parsed.minute);
    input.dataset.value = machineValue;
    input.dataset.hasValue = "true";
    input.value = formatTimePickerValue(machineValue);
    input.setAttribute("aria-valuetext", input.value);
  }

  if (notify) {
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

function fieldLabel(input) {
  const explicitLabel = input.dataset.timePickerLabel || input.getAttribute("aria-label");
  if (explicitLabel) return explicitLabel;
  const label = input.id ? document.querySelector(`label[for="${input.id}"]`) : null;
  if (label) return label.childNodes[0]?.textContent?.trim() || label.textContent.trim();
  return "time";
}

function pickerMarkup() {
  const hourButtons = Array.from({ length: 12 }, (_, index) => {
    const hour = index + 1;
    return `<button type="button" class="ff-time-choice" data-picker-hour="${hour}" aria-label="${hour} o'clock">${pad(hour)}</button>`;
  }).join("");
  const minuteButtons = Array.from({ length: 12 }, (_, index) => {
    const minute = index * 5;
    return `<button type="button" class="ff-time-choice" data-picker-minute="${minute}" aria-label="${pad(minute)} minutes">${pad(minute)}</button>`;
  }).join("");

  return `
    <form method="dialog" class="ff-time-picker-panel">
      <header class="ff-time-picker-header">
        <div>
          <p class="ff-time-picker-context">Choose a time</p>
          <h2 id="ffTimePickerTitle">Select time</h2>
        </div>
        <button type="button" class="ff-time-picker-close" data-picker-action="cancel" aria-label="Close time picker">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" /></svg>
        </button>
      </header>

      <output class="ff-time-picker-preview" id="ffTimePickerPreview" aria-live="polite">
        <span class="ff-time-picker-clock" aria-hidden="true">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5l3.25 2"/></svg>
        </span>
        <span class="ff-time-picker-preview-value" id="ffTimePickerPreviewValue">12:00</span>
        <span class="ff-time-picker-preview-period" id="ffTimePickerPreviewPeriod">PM</span>
      </output>

      <section class="ff-time-picker-section" aria-labelledby="ffTimePickerHourLabel">
        <div class="ff-time-picker-section-heading">
          <span id="ffTimePickerHourLabel">Hour</span>
          <span>1–12</span>
        </div>
        <div class="ff-time-picker-grid" role="group" aria-label="Hour">
          ${hourButtons}
        </div>
      </section>

      <section class="ff-time-picker-section" aria-labelledby="ffTimePickerMinuteLabel">
        <div class="ff-time-picker-section-heading">
          <span id="ffTimePickerMinuteLabel">Minute</span>
          <span>5 min steps</span>
        </div>
        <div class="ff-time-picker-grid" role="group" aria-label="Minute">
          ${minuteButtons}
        </div>
      </section>

      <div class="ff-time-picker-period" role="group" aria-label="Period">
        <button type="button" data-picker-period="AM">AM</button>
        <button type="button" data-picker-period="PM">PM</button>
      </div>

      <footer class="ff-time-picker-actions">
        <button type="button" class="ff-time-picker-now" data-picker-action="now">Use current time</button>
        <div>
          <button type="button" class="ff-time-picker-cancel" data-picker-action="cancel">Cancel</button>
          <button type="button" class="ff-time-picker-confirm" data-picker-action="confirm">Set time</button>
        </div>
      </footer>
    </form>
  `;
}

function ensureDialog() {
  if (pickerDialog) return pickerDialog;
  pickerDialog = document.createElement("dialog");
  pickerDialog.className = "ff-time-picker";
  pickerDialog.setAttribute("aria-labelledby", "ffTimePickerTitle");
  pickerDialog.innerHTML = pickerMarkup();
  document.body.appendChild(pickerDialog);

  pickerDialog.addEventListener("click", (event) => {
    const hourButton = event.target.closest("[data-picker-hour]");
    const minuteButton = event.target.closest("[data-picker-minute]");
    const periodButton = event.target.closest("[data-picker-period]");
    const actionButton = event.target.closest("[data-picker-action]");

    if (hourButton) {
      const displayHour = Number(hourButton.dataset.pickerHour);
      const isPM = draftHour >= 12;
      draftHour = (displayHour % 12) + (isPM ? 12 : 0);
      updateDialog();
      return;
    }
    if (minuteButton) {
      draftMinute = Number(minuteButton.dataset.pickerMinute);
      updateDialog();
      return;
    }
    if (periodButton) {
      const displayHour = draftHour % 12;
      draftHour = displayHour + (periodButton.dataset.pickerPeriod === "PM" ? 12 : 0);
      updateDialog();
      return;
    }
    if (actionButton?.dataset.pickerAction === "now") {
      const now = new Date();
      draftHour = now.getHours();
      draftMinute = Math.round(now.getMinutes() / 5) * 5;
      if (draftMinute === 60) {
        draftMinute = 0;
        draftHour = (draftHour + 1) % 24;
      }
      updateDialog();
      return;
    }
    if (actionButton?.dataset.pickerAction === "confirm") {
      setTimePickerValue(activeInput, toMachineTime(draftHour, draftMinute), { notify: true });
      pickerDialog.close("confirm");
      return;
    }
    if (actionButton?.dataset.pickerAction === "cancel") {
      pickerDialog.close("cancel");
      return;
    }

    if (event.target === pickerDialog) pickerDialog.close("cancel");
  });

  pickerDialog.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    if (event.target.matches("[data-picker-hour], [data-picker-minute], [data-picker-period]")) return;
    event.preventDefault();
    const direction = event.key === "ArrowUp" ? 5 : -5;
    const totalMinutes = (draftHour * 60 + draftMinute + direction + 1440) % 1440;
    draftHour = Math.floor(totalMinutes / 60);
    draftMinute = totalMinutes % 60;
    updateDialog();
  });

  pickerDialog.addEventListener("close", () => {
    const inputToRestore = activeInput;
    activeInput = null;
    inputToRestore?.focus({ preventScroll: true });
  });

  return pickerDialog;
}

function updateDialog() {
  if (!pickerDialog) return;
  const displayHour = draftHour % 12 || 12;
  const period = draftHour >= 12 ? "PM" : "AM";
  pickerDialog.querySelector("#ffTimePickerPreviewValue").textContent = `${displayHour}:${pad(draftMinute)}`;
  pickerDialog.querySelector("#ffTimePickerPreviewPeriod").textContent = period;

  pickerDialog.querySelectorAll("[data-picker-hour]").forEach((button) => {
    const selected = Number(button.dataset.pickerHour) === displayHour;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  pickerDialog.querySelectorAll("[data-picker-minute]").forEach((button) => {
    const selected = Number(button.dataset.pickerMinute) === draftMinute;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  pickerDialog.querySelectorAll("[data-picker-period]").forEach((button) => {
    const selected = button.dataset.pickerPeriod === period;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function openTimePicker(input) {
  if (!input || input.disabled) return;
  const dialog = ensureDialog();
  const current = parseMachineTime(getTimePickerValue(input));
  const now = new Date();
  activeInput = input;
  draftHour = current?.hour ?? now.getHours();
  draftMinute = current?.minute ?? Math.floor(now.getMinutes() / 5) * 5;
  dialog.querySelector("#ffTimePickerTitle").textContent = `Select ${fieldLabel(input)}`;
  updateDialog();
  dialog.showModal();
  dialog.querySelector(`[data-picker-hour="${draftHour % 12 || 12}"]`)?.focus();
}

function enhanceTimeInput(input) {
  if (input.dataset.timePickerReady === "true") return;
  input.dataset.timePickerReady = "true";
  const initialValue = input.dataset.value || input.value;
  input.type = "text";
  input.readOnly = true;
  input.autocomplete = "off";
  input.inputMode = "none";
  input.classList.add("ff-time-input");
  input.setAttribute("aria-haspopup", "dialog");
  setTimePickerValue(input, initialValue);

  input.addEventListener("click", () => openTimePicker(input));
  input.addEventListener("keydown", (event) => {
    if (!["Enter", " ", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    openTimePicker(input);
  });
}

export function initTimePickers(root = document) {
  root.querySelectorAll(TIME_PICKER_SELECTOR).forEach(enhanceTimeInput);
}

