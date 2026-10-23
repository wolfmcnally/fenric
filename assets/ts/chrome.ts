// The trusted chrome's script. Python renders the markup; this collects the
// inputs, holds the primary button until required inputs are answered,
// cancels on Escape, runs the countdown ring, and measures the content.
//
// Nothing here calls into Python. The page keeps its state in one object on
// its own window, and the backend reads that object from outside. Generated
// content lives in a cross-origin frame and cannot touch it.

type Value = string | number | boolean | null | number[];

interface Payload {
  action: string;
  values: Record<string, Value>;
  destination_confirmed?: boolean;
}

interface State {
  touched: boolean;
  height: number;
  done: null | { kind: "submit"; payload: Payload } | { kind: "cancel" } | { kind: "expire" };
}

const state: State = { touched: false, height: 0, done: null };
(window as unknown as { fenric: State }).fenric = state;
const OTHER = "other";
let keyboardEngaged = false;
let tabStarted = false;
const responseKeysDown = new Set<string>();

function disengageKeyboard(): void {
  keyboardEngaged = false;
  tabStarted = false;
  responseKeysDown.clear();
}

function finish(done: NonNullable<State["done"]>): void {
  if (document.body.dataset.reviewOnly === "true" && done.kind !== "cancel") return;
  const confirmation = document.querySelector<HTMLInputElement>("[data-destination-confirmation]");
  if (done.kind === "submit" && confirmation) {
    if (!confirmation.checked) return;
    done.payload.destination_confirmed = true;
  }
  if (state.done === null && !remotelySettled) state.done = done;
}

function inputs(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>("[data-key]"))
    .filter((element) => element.closest<HTMLElement>("[data-question]")?.dataset.active !== "false");
}

function checkedIn(group: HTMLElement): HTMLInputElement[] {
  return Array.from(group.querySelectorAll<HTMLInputElement>("input:checked"));
}

function read(element: HTMLElement): Value {
  switch (element.dataset.kind) {
    case "radio": {
      const chosen = checkedIn(element)[0];
      if (!chosen) return null;
      return chosen.value === OTHER ? OTHER : Number(chosen.value);
    }
    case "checks":
      return checkedIn(element).map((box) => Number(box.value));
    case "check":
      return (element as HTMLInputElement).checked;
    case "select": {
      const value = (element as HTMLSelectElement).value;
      return value === "" ? null : Number(value);
    }
    default:
      return (element as HTMLInputElement | HTMLTextAreaElement).value;
  }
}

function otherText(key: string): string {
  const box = document.querySelector<HTMLInputElement>(`[data-key="${key}.other"]`);
  return box ? box.value.trim() : "";
}

function answered(element: HTMLElement): boolean {
  const key = element.dataset.key ?? "";
  const value = read(element);
  switch (element.dataset.kind) {
    case "radio":
      return value === OTHER ? otherText(key) !== "" : value !== null;
    case "checks":
      return (value as number[]).length > 0 || otherText(key) !== "";
    case "select":
      return value !== null;
    case "check":
      return true;
    default:
      return (value as string).trim() !== "";
  }
}

function validInput(element: HTMLElement): boolean {
  if (element.hasAttribute("data-required") && !answered(element)) return false;
  if ((element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) && element.maxLength >= 0) {
    return element.value.length <= element.maxLength;
  }
  return true;
}

function imagesReady(root: ParentNode = document): boolean {
  return Array.from(root.querySelectorAll<HTMLImageElement>(".question-image"))
    .filter((image) => image.closest<HTMLElement>("[data-question]")?.dataset.active !== "false")
    .every((image) => image.complete && image.naturalWidth > 0);
}

function complete(): boolean {
  const confirmation = document.querySelector<HTMLInputElement>("[data-destination-confirmation]");
  return inputs().every(validInput) && imagesReady() && (!confirmation || confirmation.checked);
}

function collect(): Record<string, Value> {
  const values: Record<string, Value> = {};
  for (const element of inputs()) {
    values[element.dataset.key ?? ""] = read(element);
  }
  return values;
}

function primary(): HTMLButtonElement | null {
  return document.querySelector<HTMLButtonElement>("button[data-primary]:not([hidden])");
}

// The primary button is marked unavailable rather than disabled, so it can
// still take focus; act() refuses it until every required answer is given.
function refreshGuard(): void {
  if (wizard) { updateBranches(); showStep(); return; }
  const button = primary();
  if (button) button.setAttribute("aria-disabled", String(!complete()));
}

function act(action: string): void {
  if (state.done !== null || remotelySettled) return;
  if (action === "cancel") {
    finish({ kind: "cancel" });
    return;
  }
  if (wizard) { wizardAction(action); return; }
  if (!complete()) return;
  finish({ kind: "submit", payload: { action, values: collect() } });
}

// Wizard pages retain their own controls; only inactive branches are cleared.
const wizard = document.querySelector<HTMLElement>("#wizard");
const questions = Array.from(document.querySelectorAll<HTMLElement>("[data-question]"));
let currentQuestion = 0;
let reviewing = false;
let remotelySettled = false;

function activeQuestions(): HTMLElement[] {
  return questions.filter((question) => question.dataset.active === "true");
}

function questionInputs(question: HTMLElement): HTMLElement[] {
  return Array.from(question.querySelectorAll<HTMLElement>("[data-key]"));
}

function clearQuestion(question: HTMLElement): void {
  for (const control of Array.from(question.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input, textarea"))) {
    if (control instanceof HTMLInputElement && (control.type === "radio" || control.type === "checkbox")) {
      control.checked = false;
    } else {
      control.value = "";
    }
  }
}

function updateBranches(): void {
  for (const question of questions) {
    let active = true;
    if (question.dataset.depQuestion !== undefined) {
      const source = questions[Number(question.dataset.depQuestion)];
      const input = source?.querySelector<HTMLElement>("[data-key]");
      const value = input ? read(input) : null;
      const option = Number(question.dataset.depOption);
      active = source?.dataset.active === "true" && (
        question.dataset.depOperator === "includes"
          ? Array.isArray(value) && value.includes(option)
          : typeof value === "number" && value === option
      );
    }
    if (!active) clearQuestion(question);
    question.dataset.active = String(active);
  }
}

function stepComplete(question: HTMLElement): boolean {
  return questionInputs(question).every(validInput) && imagesReady(question);
}

function answerSummary(question: HTMLElement): string {
  const input = question.querySelector<HTMLElement>("[data-key]");
  if (!input || !answered(input)) return "Not answered (optional)";
  const value = read(input);
  if (input.dataset.kind === "text") return String(value);
  const labels = checkedIn(input).filter((chosen) => chosen.value !== OTHER).map((chosen) =>
    chosen.closest("label")?.querySelector("span")?.firstChild?.textContent ?? "",
  );
  const other = otherText(input.dataset.key ?? "");
  if (other && (Array.isArray(value) || value === OTHER)) labels.push(other);
  return labels.join("; ");
}

function renderReview(): void {
  const container = document.querySelector<HTMLElement>("#wizard-answers");
  if (!container) return;
  container.replaceChildren();
  for (const question of activeQuestions()) {
    const row = document.createElement("section");
    row.className = "review-answer";
    const title = document.createElement("h3");
    title.textContent = question.querySelector(".step-heading")?.textContent ?? "";
    const answer = document.createElement("p");
    answer.textContent = answerSummary(question);
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "Edit";
    edit.setAttribute("aria-label", `Edit: ${title.textContent}`);
    edit.addEventListener("click", (event) => {
      if (state.done !== null || remotelySettled || event.detail > 1) return;
      currentQuestion = Number(question.dataset.question);
      reviewing = false;
      showStep(true);
    });
    row.append(title);
    const original = question.querySelector<HTMLImageElement>(".question-image");
    if (original) {
      const thumbnail = original.cloneNode(false) as HTMLImageElement;
      thumbnail.className = "review-image";
      const figure = document.createElement("figure");
      figure.className = "review-figure generated";
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "Caller image";
      figure.append(badge, thumbnail);
      row.append(figure);
    }
    row.append(answer, edit);
    container.append(row);
  }
}

function showStep(focus = false): void {
  if (!wizard) return;
  const active = activeQuestions();
  if (questions[currentQuestion]?.dataset.active !== "true") {
    currentQuestion = Number(active[0]?.dataset.question ?? 0);
  }
  for (const question of questions) {
    question.hidden = reviewing || Number(question.dataset.question) !== currentQuestion || question.dataset.active !== "true";
  }
  const review = document.querySelector<HTMLElement>("#wizard-review");
  if (review) review.hidden = !reviewing;
  const index = active.indexOf(questions[currentQuestion]);
  const progress = document.querySelector<HTMLElement>("#wizard-progress");
  if (progress) progress.textContent = reviewing ? `Review · ${active.length} questions` : `Question ${index + 1} of ${active.length}`;
  const back = document.querySelector<HTMLButtonElement>('[data-action="back"]');
  const next = document.querySelector<HTMLButtonElement>('[data-action="next"]');
  const submit = document.querySelector<HTMLButtonElement>('[data-action="ok"]');
  if (back) back.hidden = !reviewing && index === 0;
  if (next) {
    next.hidden = reviewing;
    const label = index === active.length - 1 ? "Review" : "Next";
    if (next.textContent !== label) next.textContent = label;
  }
  if (submit) submit.hidden = !reviewing;
  next?.toggleAttribute("data-primary", !reviewing);
  submit?.toggleAttribute("data-primary", reviewing);
  const error = document.querySelector<HTMLElement>("#wizard-error");
  if (error) error.textContent = "";
  if (reviewing) renderReview();
  const button = primary();
  if (button) button.setAttribute("aria-disabled", String(reviewing ? !complete() : !stepComplete(questions[currentQuestion])));
  if (focus) {
    const target = reviewing ? review?.querySelector<HTMLElement>("h2") : questions[currentQuestion]?.querySelector<HTMLElement>(".step-heading");
    target?.focus();
  }
  measure();
}

function wizardAction(action: string): void {
  updateBranches();
  const active = activeQuestions();
  const index = active.indexOf(questions[currentQuestion]);
  if (action === "back") {
    currentQuestion = Number(active[reviewing ? active.length - 1 : Math.max(0, index - 1)].dataset.question);
    reviewing = false;
    showStep(true);
    return;
  }
  if (action === "next" && !reviewing) {
    if (!stepComplete(questions[currentQuestion])) {
      const error = document.querySelector<HTMLElement>("#wizard-error");
      if (error) error.textContent = imagesReady(questions[currentQuestion]) ? "Answer this required question before continuing." : "The image must finish loading before continuing. If it failed, cancel and ask for a new gallery.";
      questions[currentQuestion].querySelector<HTMLElement>(".step-heading")?.focus();
      return;
    }
    if (index === active.length - 1) reviewing = true;
    else currentQuestion = Number(active[index + 1].dataset.question);
    showStep(true);
    return;
  }
  if (action !== "ok" || !reviewing) return;
  const invalid = active.find((question) => !stepComplete(question));
  if (invalid) {
    currentQuestion = Number(invalid.dataset.question);
    reviewing = false;
    showStep(true);
    const error = document.querySelector<HTMLElement>("#wizard-error");
    if (error) error.textContent = imagesReady(invalid) ? "Answer this required question before submitting." : "The image must finish loading before submitting. If it failed, cancel and ask for a new gallery.";
    return;
  }
  finish({ kind: "submit", payload: { action: "ok", values: collect() } });
}

function fitTextareas(): void {
  const pageX = window.scrollX;
  const pageY = window.scrollY;
  for (const field of Array.from(document.querySelectorAll<HTMLTextAreaElement>("textarea"))) {
    if (!field.getClientRects().length) continue;
    const style = getComputedStyle(field);
    const line = parseFloat(style.lineHeight);
    const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const top = field.scrollTop;
    const minimum = Math.ceil(line + padding + border);
    const maximum = Math.ceil(10 * line + padding + border);
    field.style.overflowY = "hidden";
    field.style.height = "0px";
    const wanted = field.scrollHeight + border;
    field.style.height = `${Math.min(maximum, Math.max(minimum, wanted))}px`;
    field.style.overflowY = wanted > maximum + 1 ? "auto" : "hidden";
    field.scrollTop = top;
  }
  if (window.scrollX !== pageX || window.scrollY !== pageY) window.scrollTo(pageX, pageY);
}

function measure(): void {
  fitTextareas();
  const frame = document.body.hasAttribute("data-html")
    ? document.querySelector<HTMLIFrameElement>("#fenric-html") : null;
  if (frame) {
    // Fit once from natural chrome plus requested frame height, independently
    // of the viewport-filling layout. Later native sizing belongs to the person.
    const main = document.querySelector("main")!;
    state.height = Math.ceil(Math.max(main.scrollHeight, main.getBoundingClientRect().height)
      - frame.getBoundingClientRect().height + Number(frame.getAttribute("height")));
  } else {
    state.height = Math.ceil(document.documentElement.getBoundingClientRect().height);
  }
}

let ringDeadline: number | null = null;
let ringStarted = false;
function startRing(seconds: number): void {
  ringDeadline = performance.now() + seconds * 1000;
  if (ringStarted) return;
  ringStarted = true;
  const ring = document.querySelector<SVGCircleElement>(".ring-fill");
  const total = Number(document.body.dataset.timeout ?? seconds);
  const circumference = ring ? 2 * Math.PI * ring.r.baseVal.value : 0;
  if (ring) ring.style.strokeDasharray = String(circumference);
  const tick = (): void => {
    if (state.touched || state.done !== null || ringDeadline === null) return;
    const left = Math.max(0, (ringDeadline - performance.now()) / (total * 1000));
    if (ring) ring.style.strokeDashoffset = String(circumference * (1 - left));
    if (left <= 0) { finish({ kind: "expire" }); return; }
    window.setTimeout(tick, 100);
  };
  tick();
}

interface SharedState { touched: boolean; remaining: number | null; status: string | null; settled_elsewhere: boolean; rejected?: boolean }
(window as unknown as { fenricSync: (shared: SharedState) => void }).fenricSync = (shared) => {
  if (shared.rejected && shared.status === null) {
    state.done = null;
    if (!document.getElementById("submission-refusal")) {
      const message = document.createElement("p");
      message.id = "submission-refusal";
      message.setAttribute("role", "status");
      message.textContent = "Answer not accepted. Review and try again.";
      document.querySelector("main")?.append(message);
    }
  }
  if (shared.touched) showTimeoutCanceled();
  if (shared.remaining !== null && !state.touched) startRing(shared.remaining);
  if (shared.status !== null) {
    remotelySettled = true;
    for (const element of inputs()) {
      if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) element.value = "";
    }
    state.done = null;
    const main = document.querySelector("main");
    if (main) {
      const heading = document.createElement("h1");
      const sent = shared.status === "ok" && !shared.settled_elsewhere;
      const integrated = document.body.dataset.integrated === "true";
      heading.textContent = sent ? (integrated ? "Collected" : "Sent!") : shared.status === "ok" ? "Completed on another surface" : shared.status;
      main.replaceChildren(heading);
      if (sent) {
        const message = document.createElement("p");
        message.textContent = integrated ? "Effect and delivery are reported separately by the requesting host." : "You may close this tab now.";
        main.append(message);
      }
      main.classList.add("terminal");
      main.setAttribute("role", "status");
      main.tabIndex = -1;
      main.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    }
  }
};

function showTimeoutCanceled(): void {
  if (state.touched) return;
  state.touched = true;
  document.body.classList.add("touched");
  const ring = document.querySelector<SVGElement>(".ring");
  if (ring) {
    const label = document.body.dataset.integrated === "true" ? "Response deadline still applies" : "Timeout canceled";
    ring.setAttribute("aria-label", label);
    const title = ring.querySelector("title");
    if (title) title.textContent = label;
  }
  const fill = document.querySelector<SVGCircleElement>(".ring-fill");
  if (fill) {
    fill.style.strokeDasharray = "none";
    fill.style.strokeDashoffset = "0";
  }
}

function touch(): void {
  if (state.done !== null || remotelySettled) return;
  showTimeoutCanceled();
}

function controlInteraction(event: Event): void {
  if (!(event.target instanceof Element)) return;
  const control = event.target.closest("input, textarea, select, button, summary");
  if (!control || !control.closest("main") || control.matches(":disabled, [aria-disabled=true]")) return;
  if ((control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement) && control.readOnly) return;
  touch();
}

function startCopyView(): void {
  const view = document.querySelector<HTMLElement>("#copy-text");
  const button = document.querySelector<HTMLButtonElement>("#copy-button");
  const status = document.querySelector<HTMLElement>("#copy-status");
  if (!view || !button || !status) return;
  const text = new TextDecoder("utf-8", { ignoreBOM: true }).decode(Uint8Array.from(atob(view.dataset.copyBase64 ?? ""), char => char.charCodeAt(0)));
  view.textContent = text;
  let copying = false;
  button.addEventListener("click", async (event) => {
    if (!event.isTrusted || copying || state.done !== null || remotelySettled) return;
    copying = true;
    button.setAttribute("aria-disabled", "true");
    status.textContent = "Copying…";
    try {
      if (navigator.clipboard?.writeText) {
        // Keep this call inside the original gesture for Safari/iPhone.
        await navigator.clipboard.writeText(text);
      } else {
        const selection = window.getSelection();
        const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
        let written = false;
        const copy = (copyEvent: ClipboardEvent): void => {
          if (!copyEvent.clipboardData) return;
          copyEvent.clipboardData.setData("text/plain", text);
          copyEvent.preventDefault();
          written = true;
        };
        document.addEventListener("copy", copy);
        try {
          const range = document.createRange();
          range.selectNodeContents(view);
          selection?.removeAllRanges();
          selection?.addRange(range);
          if (!document.execCommand("copy") || !written) throw new Error("copy unavailable");
        } finally {
          document.removeEventListener("copy", copy);
          selection?.removeAllRanges();
          for (const range of ranges) selection?.addRange(range);
        }
      }
      status.textContent = "Copied";
    } catch {
      status.textContent = "Copy unavailable. Select the text and copy it manually.";
    } finally {
      copying = false;
      button.removeAttribute("aria-disabled");
    }
  });
}

function start(): void {
  startCopyView();
  for (const image of Array.from(document.querySelectorAll<HTMLImageElement>(".question-image"))) {
    const figure = image.closest<HTMLElement>(".caller-image");
    const status = figure?.querySelector<HTMLElement>(".image-error");
    const update = (): void => {
      image.dataset.imageState = image.complete ? (image.naturalWidth > 0 ? "ready" : "error") : "loading";
      if (status) {
        status.hidden = image.dataset.imageState === "ready";
        status.textContent = image.dataset.imageState === "error" ? "This image could not be displayed. Cancel and ask for a new gallery." : "Loading image…";
      }
      refreshGuard();
    };
    image.addEventListener("load", update);
    image.addEventListener("error", update);
    figure?.querySelector<HTMLButtonElement>("[data-image-zoom]")?.addEventListener("click", (event) => {
      if (state.done !== null || remotelySettled || event.detail > 1) return;
      const button = event.currentTarget as HTMLButtonElement;
      const natural = button.getAttribute("aria-pressed") !== "true";
      button.setAttribute("aria-pressed", String(natural));
      figure.querySelector<HTMLElement>(".image-viewport")?.setAttribute("data-natural", String(natural));
      measure();
    });
    update();
  }
  for (const button of Array.from(document.querySelectorAll<HTMLButtonElement>("[data-action]"))) {
    button.addEventListener("click", (event) => {
      if (button.hidden || (wizard && event.detail > 1)) return;
      act(button.dataset.action ?? "");
    });
  }
  // Typing in a "Something else" box selects its choice.
  for (const box of Array.from(document.querySelectorAll<HTMLInputElement>("[data-other-for]"))) {
    box.addEventListener("input", () => {
      const radio = document.querySelector<HTMLInputElement>(
        `[data-key="${box.dataset.otherFor}"] input[type="radio"][value="${OTHER}"]`,
      );
      if (radio && box.value.trim() !== "") radio.checked = true;
    });
  }
  document.addEventListener("input", refreshGuard);
  document.addEventListener("input", measure);
  document.addEventListener("change", refreshGuard);
  // A new or revisited page must not interpret keys from another task as an
  // answer. Focus/load alone are not engagement. A completed click or a
  // completed Tab navigation is deliberate; neither changes the timer here.
  document.addEventListener("click", (event) => {
    if (event.isTrusted) keyboardEngaged = true;
  }, { capture: true });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Tab" && !keyboardEngaged) {
      event.preventDefault();
      if (!event.repeat) {
        tabStarted = true;
        // WebKit can skip buttons under the system's text-only Tab setting.
        // Make the explicit first Tab enter a visible control on every dialog.
        const controls = Array.from(document.querySelectorAll<HTMLElement>(
          'input, textarea, select, button, summary, [tabindex="0"]',
        )).filter((control) => !control.matches(":disabled") && control.getClientRects().length > 0);
        (event.shiftKey ? controls[controls.length - 1] : controls[0])?.focus();
      }
    }
    if (event.key !== "Enter" && event.key !== " ") return;
    const editing = event.target instanceof HTMLTextAreaElement ||
      (event.target instanceof HTMLInputElement && ["text", "password", "number"].includes(event.target.type));
    const repeatedAction = event.repeat && (!editing || (event.key === "Enter" && !(event.target instanceof HTMLTextAreaElement)));
    if (!keyboardEngaged || repeatedAction) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    responseKeysDown.add(event.key);
  }, { capture: true });
  document.addEventListener("keyup", (event) => {
    if (event.key === "Tab") {
      if (tabStarted) keyboardEngaged = true;
      tabStarted = false;
    }
    if (event.key !== "Enter" && event.key !== " ") return;
    if (!keyboardEngaged || !responseKeysDown.delete(event.key)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, { capture: true });
  window.addEventListener("blur", disengageKeyboard);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) disengageKeyboard();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      act("cancel");
    } else if (event.key === "Enter" && wizard && event.repeat) {
      event.preventDefault();
    } else if (event.key === "Enter" && !(event.target instanceof Element && event.target.closest("summary")) && !(event.target instanceof HTMLTextAreaElement)) {
      if (event.target instanceof Element && event.target.closest("button")) return;
      if (wizard && reviewing) return;
      const button = primary();
      if (button && (wizard || complete())) {
        event.preventDefault();
        button.click();
      }
    }
  });
  refreshGuard();

  const seconds = Number(document.body.dataset.timeout ?? "");
  if (seconds > 0 && !document.body.dataset.remote) startRing(seconds);
  // A completed control activation or answer edit is intentional interaction.
  // Focus, scrolling and interrupted gestures must leave the timer running.
  for (const name of ["click", "input", "change"]) {
    document.addEventListener(name, controlInteraction, { capture: true });
  }

  window.addEventListener("message", (event) => {
    const chart = document.querySelector<HTMLIFrameElement>("#fenric-chart");
    if (!chart || event.source !== chart.contentWindow) return;
    if (event.data === "fenric-chart-touch") touch();
    if (event.data === "fenric-chart-cancel") { touch(); act("cancel"); }
  });
  measure();
  window.addEventListener("load", measure);
  window.addEventListener("resize", measure);
  void document.fonts.ready.then(measure);
  new ResizeObserver(measure).observe(document.documentElement);
  // Layout without moving focus into a control that could accept a stray key.
  if (wizard) showStep();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start);
} else {
  start();
}
