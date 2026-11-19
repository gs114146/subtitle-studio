const audio = document.getElementById("audio");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const editor = document.getElementById("editor");
const wordLayer = document.getElementById("words");
const rendered = document.getElementById("rendered");
const scrubber = document.getElementById("scrubber");
const durationEl = document.getElementById("duration");
const cueTicks = document.getElementById("cue-ticks");
const modeBadge = document.getElementById("preview-mode-badge");
const previewEmpty = document.getElementById("preview-empty");
const stageFrame = document.querySelector(".stage-frame");

const PORTRAIT_SIZE = { width: 1080, height: 1920 };
const LANDSCAPE_SIZE = { width: 1920, height: 1080 };

const STYLE_SECTIONS = [
  {
    title: "Colours",
    fields: [
      { key: "color_normal", label: "Normal text", type: "color" },
      { key: "color_highlight", label: "Highlight", type: "color" },
    ],
  },
  {
    title: "Frame & placement",
    fields: [
      {
        key: "orientation",
        label: "Orientation",
        type: "select",
        options: [
          ["portrait", "Portrait (9:16)"],
          ["landscape", "Landscape (16:9)"],
        ],
      },
      {
        key: "position",
        label: "Subtitle position",
        type: "select",
        options: [
          ["top", "Top"],
          ["middle", "Middle"],
          ["bottom", "Bottom"],
        ],
      },
      { key: "margin_bottom", label: "Edge margin", type: "range", min: 40, max: 420, step: 4, suffix: "px" },
      { key: "width", label: "Width", type: "number", min: 720, max: 3840, step: 2 },
      { key: "height", label: "Height", type: "number", min: 720, max: 3840, step: 2 },
    ],
  },
  {
    title: "Typography",
    fields: [
      { key: "font_size", label: "Font size", type: "range", min: 36, max: 120, step: 1, suffix: "px" },
      { key: "shadow_opacity", label: "Shadow strength", type: "range", min: 0, max: 1, step: 0.05 },
      { key: "font_path", label: "Font file", type: "text", placeholder: "assets/fonts/Montserrat-Bold.otf" },
    ],
  },
  {
    title: "Motion",
    fields: [
      { key: "animation_ms", label: "Pop duration", type: "range", min: 0, max: 400, step: 10, suffix: "ms" },
      { key: "pop_scale", label: "Pop scale", type: "range", min: 1, max: 1.2, step: 0.01 },
      { key: "pause_hold_ms", label: "Pause hold", type: "range", min: 0, max: 800, step: 10, suffix: "ms" },
    ],
  },
  {
    title: "Export",
    fields: [
      {
        key: "codec",
        label: "Codec",
        type: "select",
        options: [
          ["prores4444", "ProRes 4444 (alpha)"],
          ["qtrle", "QuickTime Animation (alpha)"],
        ],
      },
      { key: "fps", label: "Frame rate", type: "range", min: 24, max: 60, step: 1, suffix: " fps" },
      { key: "language", label: "Language", type: "text" },
    ],
  },
];

let meta = null;
let state = { cues: [], audio_duration: 0 };
let savedSettings = null;
let settings = null; // draft used for live preview
let layouts = {};
let layoutTimer = null;
let pollTimer = null;
let playing = false;
let exactMode = false;
let scrubbing = false;
let currentStep = 1;
let inspectorPrimaryAction = "open";
let volumeBeforeMute = 1;

function formatClock(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const mins = Math.floor(value / 60);
  const secs = value - mins * 60;
  return `${mins}:${secs.toFixed(2).padStart(5, "0")}`;
}

function audioDuration() {
  if (Number.isFinite(audio.duration) && audio.duration > 0) return audio.duration;
  return Number(meta?.audio_duration || state.audio_duration || 0);
}

function updatePreviewEmpty() {
  const ready = Boolean(meta?.audio_path && (state.cues || []).length);
  stageFrame?.classList.toggle("is-ready", ready);
  if (previewEmpty) previewEmpty.hidden = ready;
}

function setExactMode(on, { announce = false } = {}) {
  exactMode = Boolean(on);
  if (!exactMode) {
    rendered.hidden = true;
  }
  modeBadge?.classList.toggle("is-live", !exactMode);
  modeBadge?.classList.toggle("is-exact", exactMode);
  if (modeBadge) modeBadge.textContent = exactMode ? "Exact" : "Live";
  document.getElementById("exact")?.classList.toggle("is-active", exactMode);
  if (announce) {
    setStatus(exactMode
      ? "Exact frame mode — this is what Resolve will get."
      : "Live preview mode.");
  }
}

function setPlayButtonState(isPlaying) {
  const button = document.getElementById("play");
  if (!button) return;
  button.classList.toggle("is-playing", Boolean(isPlaying));
  button.setAttribute("aria-label", isPlaying ? "Pause" : "Play");
}

function syncVolumeUI() {
  const muteButton = document.getElementById("mute");
  const volumeInput = document.getElementById("volume");
  const muted = Boolean(audio.muted || audio.volume === 0);
  const level = muted ? 0 : audio.volume;
  if (volumeInput) {
    volumeInput.value = String(level);
    volumeInput.style.setProperty("--volume", `${level * 100}%`);
  }
  if (muteButton) {
    muteButton.classList.toggle("is-muted", muted);
    muteButton.setAttribute("aria-pressed", muted ? "true" : "false");
    muteButton.setAttribute("aria-label", muted ? "Unmute" : "Mute");
  }
}

function setVolume(level, { persist = true } = {}) {
  const next = Math.min(Math.max(Number(level) || 0, 0), 1);
  audio.volume = next;
  audio.muted = next === 0;
  if (next > 0) volumeBeforeMute = next;
  syncVolumeUI();
  if (persist) {
    try {
      localStorage.setItem("subtitlePreviewVolume", String(next));
      localStorage.setItem("subtitlePreviewMuted", audio.muted ? "1" : "0");
    } catch (_) {
      /* ignore */
    }
  }
}

function toggleMute() {
  if (audio.muted || audio.volume === 0) {
    setVolume(volumeBeforeMute > 0 ? volumeBeforeMute : 1);
    return;
  }
  volumeBeforeMute = audio.volume || volumeBeforeMute || 1;
  audio.muted = true;
  syncVolumeUI();
  try {
    localStorage.setItem("subtitlePreviewMuted", "1");
    localStorage.setItem("subtitlePreviewVolume", String(volumeBeforeMute));
  } catch (_) {
    /* ignore */
  }
}

function syncTransport(time = audio.currentTime || 0) {
  const total = audioDuration();
  const clock = document.getElementById("clock");
  if (clock) clock.textContent = formatClock(time);
  if (durationEl) durationEl.textContent = formatClock(total);
  if (scrubber && !scrubbing) {
    scrubber.max = String(Math.max(total, 0));
    scrubber.value = String(Math.min(Math.max(time, 0), total || 0));
  }
  if (scrubber) {
    const max = Number(scrubber.max) || 0;
    const value = Number(scrubber.value) || 0;
    const progress = max > 0 ? (value / max) * 100 : 0;
    scrubber.style.setProperty("--progress", `${progress}%`);
  }
  document.getElementById("exact")?.classList.toggle("is-active", exactMode);
}

function seekBy(delta) {
  const total = audioDuration();
  const next = Math.min(Math.max((audio.currentTime || 0) + delta, 0), total || 0);
  audio.currentTime = next;
  setExactMode(false);
  draw(next);
  syncTransport(next);
}

function renderCueTicks() {
  if (!cueTicks) return;
  cueTicks.replaceChildren();
  const total = audioDuration();
  if (!total) return;
  for (const cue of state.cues || []) {
    if (cue.start == null) continue;
    const tick = document.createElement("button");
    tick.type = "button";
    tick.className = "cue-tick";
    tick.title = `Cue ${cue.index} · ${formatClock(cue.start)}`;
    tick.style.left = `${(cue.start / total) * 100}%`;
    tick.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      audio.currentTime = cue.start;
      setExactMode(false);
      draw(cue.start);
      syncTransport(cue.start);
    });
    cueTicks.append(tick);
  }
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
  statusEl.classList.toggle("ok", !isError && /ready|saved|Exported|loaded|restored/i.test(message));
}

function setStep(step) {
  currentStep = step;
  document.querySelectorAll(".step").forEach((item) => {
    const value = Number(item.dataset.step);
    item.classList.toggle("is-active", value === step);
    item.classList.toggle("is-done", value < step);
  });
  updateInspectorPrimary();
}

function switchInspectorTab(tab) {
  const next = ["media", "style", "edit", "export"].includes(tab) ? tab : "media";
  document.querySelectorAll(".inspector-tab").forEach((button) => {
    const active = button.dataset.tab === next;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".inspector-pane").forEach((pane) => {
    const active = pane.dataset.tabPanel === next;
    pane.classList.toggle("is-active", active);
    pane.hidden = !active;
  });
  updateInspectorPrimary();
}

function hasWordTimings() {
  return (state.cues || []).some((cue) =>
    (cue.words || []).some((word) => word.start != null && word.end != null)
  );
}

function updateInspectorPrimary() {
  const primary = document.getElementById("inspector-primary");
  const hint = document.getElementById("inspector-hint");
  if (!primary) return;

  const audioPath = document.getElementById("audio-path")?.value.trim() || "";
  const srtPath = document.getElementById("srt-path")?.value.trim() || "";
  const activeTab = document.querySelector(".inspector-tab.is-active")?.dataset.tab || "media";
  const timed = hasWordTimings();

  if (activeTab === "export") {
    inspectorPrimaryAction = "export";
    primary.textContent = "Export transparent video";
    primary.className = "btn btn-accent inspector-primary";
    if (hint) hint.textContent = "Output a transparent overlay for Resolve.";
    return;
  }
  if (activeTab === "style") {
    inspectorPrimaryAction = "save-settings";
    primary.textContent = "Save style";
    primary.className = "btn btn-primary inspector-primary";
    if (hint) hint.textContent = "Tune look, then save for future Shorts.";
    return;
  }
  if (activeTab === "edit") {
    inspectorPrimaryAction = "save";
    primary.textContent = "Save corrections";
    primary.className = "btn btn-primary inspector-primary";
    if (hint) hint.textContent = "Fix flagged words, then save.";
    return;
  }

  if (!audioPath || !srtPath) {
    inspectorPrimaryAction = "open";
    primary.textContent = "Load files";
    primary.className = "btn btn-primary inspector-primary";
    if (hint) hint.textContent = "Choose audio and SRT to begin.";
    return;
  }
  if (!timed) {
    inspectorPrimaryAction = "align";
    primary.textContent = "Generate word timings";
    primary.className = "btn btn-accent inspector-primary";
    if (hint) hint.textContent = "Align the SRT words to the voiceover.";
    return;
  }
  inspectorPrimaryAction = "save";
  primary.textContent = "Save corrections";
  primary.className = "btn btn-primary inspector-primary";
  if (hint) hint.textContent = "Review the preview, then save or export.";
}

function showJobLog(show = true) {
  const panel = document.getElementById("progress-panel");
  if (panel) panel.hidden = !show;
}

function normalizeHex(value) {
  const text = String(value || "").trim();
  if (/^#[0-9a-fA-F]{6}$/.test(text)) return text.toUpperCase();
  if (/^[0-9a-fA-F]{6}$/.test(text)) return `#${text.toUpperCase()}`;
  return "#FFFFFF";
}

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.error) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function numberOrNull(value) {
  if (value === "" || value == null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function activeWordIndex(words, time, hold) {
  const usable = words
    .map((word, index) => ({ word, index }))
    .filter(({ word }) => word.start != null && word.end != null && word.end >= word.start);
  if (!usable.length) return null;
  usable.sort((a, b) => a.word.start - b.word.start || a.index - b.index);
  for (let position = 0; position < usable.length; position += 1) {
    const { word, index } = usable[position];
    const nextStart = position + 1 < usable.length ? usable[position + 1].word.start : null;
    if (word.start <= time && time < word.end) return index;
    if (time === word.end && (nextStart == null || nextStart > time)) return index;
  }
  for (let position = 0; position < usable.length; position += 1) {
    const { word, index } = usable[position];
    if (time < word.end) continue;
    const nextStart = position + 1 < usable.length ? usable[position + 1].word.start : null;
    if (nextStart == null) return time - word.end <= hold ? index : null;
    if (time < nextStart && nextStart - word.end <= hold) return index;
    if (time < nextStart) return null;
  }
  return null;
}

function visibleCue(cues, time) {
  const matches = cues.filter((cue) => cue.start <= time && time < cue.end);
  if (matches.length) return matches[matches.length - 1];
  const ending = cues.filter((cue) => Math.abs(cue.end - time) < 1e-6 && cue.start <= time);
  return ending.length ? ending[ending.length - 1] : null;
}

function popScale(time, wordStart, animationSeconds, amount) {
  if (!animationSeconds || amount <= 1 || wordStart == null) return 1;
  const age = time - wordStart;
  if (age < 0 || age > animationSeconds) return 1;
  return 1 + (amount - 1) * Math.sin(Math.PI * age / animationSeconds);
}

function collect() {
  const cues = [...editor.querySelectorAll(".cue")].map((cue) => {
    const words = [...cue.querySelectorAll(".word-row")].map((row) => ({
      text: row.querySelector(".word-text").value.trim(),
      start: numberOrNull(row.querySelector(".word-start").value),
      end: numberOrNull(row.querySelector(".word-end").value),
      probability: numberOrNull(row.dataset.probability),
      flagged: row.querySelector(".word-flag").checked,
      note: row.querySelector(".word-note").value,
    })).filter((word) => word.text);
    return {
      index: Number(cue.dataset.index),
      start: Number(cue.querySelector(".cue-start").value),
      end: Number(cue.querySelector(".cue-end").value),
      text: words.map((word) => word.text).join(" "),
      words,
    };
  });
  return {
    version: 1,
    audio_path: meta?.audio_path || "",
    srt_path: meta?.srt_path || "",
    audio_duration: meta?.audio_duration || 0,
    cues,
  };
}

function syncFromEditor() {
  if (!editor.querySelector(".cue")) return;
  state = collect();
  draw(audio.currentTime || 0);
}

function wordRow(word) {
  const row = document.createElement("div");
  row.className = "word-row" + (word.flagged ? " flagged" : "");
  if (word.probability != null) row.dataset.probability = String(word.probability);

  const text = document.createElement("input");
  text.className = "word-text";
  text.value = word.text || "";
  const start = document.createElement("input");
  start.className = "word-start";
  start.type = "number";
  start.step = "0.01";
  start.value = word.start ?? "";
  start.title = "Start seconds";
  const end = document.createElement("input");
  end.className = "word-end";
  end.type = "number";
  end.step = "0.01";
  end.value = word.end ?? "";
  end.title = "End seconds";
  const check = document.createElement("label");
  check.className = "check";
  const flag = document.createElement("input");
  flag.className = "word-flag";
  flag.type = "checkbox";
  flag.checked = Boolean(word.flagged);
  check.append(flag, document.createTextNode("Check"));
  const note = document.createElement("input");
  note.className = "word-note";
  note.placeholder = "Note";
  note.value = word.note || "";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn btn-ghost";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => {
    row.remove();
    syncFromEditor();
    scheduleLayout();
  });
  row.append(text, start, end, check, remove, note);
  return row;
}

function renderEditor() {
  editor.replaceChildren();
  for (const cue of state.cues || []) {
    const article = document.createElement("details");
    article.className = "cue";
    article.dataset.index = String(cue.index);
    // Keep cues closed by default so the page stays short.
    article.open = false;

    const summary = document.createElement("summary");
    summary.className = "cue-summary";
    const badge = document.createElement("span");
    badge.className = "cue-index";
    badge.textContent = `Cue ${cue.index}`;
    const flaggedCount = cue.words.filter((word) => word.flagged).length;
    if (flaggedCount) badge.classList.add("has-flags");

    const preview = document.createElement("div");
    preview.className = "cue-preview";
    const heading = document.createElement("strong");
    heading.textContent = cue.text || cue.words.map((word) => word.text).join(" ");
    const metaLine = document.createElement("span");
    metaLine.textContent = `${cue.start.toFixed(2)}s – ${cue.end.toFixed(2)}s · ${cue.words.length} words${flaggedCount ? ` · ${flaggedCount} to check` : ""}`;
    preview.append(heading, metaLine);

    const chevron = document.createElement("span");
    chevron.className = "chevron";
    chevron.setAttribute("aria-hidden", "true");
    summary.append(badge, preview, chevron);

    const body = document.createElement("div");
    body.className = "cue-body";
    const bounds = document.createElement("div");
    bounds.className = "grid";
    const startLabel = document.createElement("label");
    startLabel.textContent = "Sentence appears";
    const start = document.createElement("input");
    start.className = "cue-start";
    start.type = "number";
    start.step = "0.01";
    start.value = cue.start;
    startLabel.append(start);
    const endLabel = document.createElement("label");
    endLabel.textContent = "Sentence leaves";
    const end = document.createElement("input");
    end.className = "cue-end";
    end.type = "number";
    end.step = "0.01";
    end.value = cue.end;
    endLabel.append(end);
    bounds.append(startLabel, endLabel);

    const sentenceLabel = document.createElement("label");
    sentenceLabel.textContent = "Sentence";
    const sentence = document.createElement("textarea");
    sentence.className = "sentence";
    sentence.value = cue.text || cue.words.map((word) => word.text).join(" ");
    sentenceLabel.append(sentence);
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "btn btn-secondary";
    apply.textContent = "Update sentence";
    apply.addEventListener("click", () => updateSentence(cue.index, sentence.value));

    const words = document.createElement("div");
    words.className = "words";
    for (const word of cue.words) words.append(wordRow(word));
    const add = document.createElement("button");
    add.type = "button";
    add.className = "btn btn-ghost";
    add.textContent = "Add word";
    add.addEventListener("click", () => {
      words.append(wordRow({ text: "word", flagged: true, note: "Added manually" }));
      syncFromEditor();
      scheduleLayout();
    });
    body.append(bounds, sentenceLabel, apply, words, add);
    article.append(summary, body);
    editor.append(article);
  }
  const flagged = (state.cues || []).reduce((sum, cue) => sum + cue.words.filter((word) => word.flagged).length, 0);
  const total = (state.cues || []).reduce((sum, cue) => sum + cue.words.length, 0);
  const cueCount = (state.cues || []).length;
  const hint = document.getElementById("words-summary-hint");
  if (hint) {
    hint.textContent = cueCount
      ? `${cueCount} cues · ${total} words${flagged ? ` · ${flagged} to check` : ""}. Closed by default.`
      : "Open only when you need to correct timing or text.";
  }
  updateNextCheckButton();
  if (total) setStatus(`${total} words loaded. ${flagged} marked for a check.`);
}

function openCue(index, wordIndex = null) {
  switchInspectorTab("edit");
  const cueEl = editor.querySelector(`.cue[data-index="${index}"]`);
  if (!cueEl) return;
  editor.querySelectorAll(".cue").forEach((item) => {
    item.open = item === cueEl;
  });
  cueEl.open = true;
  cueEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (wordIndex != null) {
    const row = cueEl.querySelectorAll(".word-row")[wordIndex];
    row?.querySelector(".word-text")?.focus();
  }
}

function flaggedWordList() {
  const items = [];
  for (const cue of state.cues || []) {
    (cue.words || []).forEach((word, wordIndex) => {
      if (word.flagged) items.push({ cue, word, wordIndex });
    });
  }
  return items;
}

function updateNextCheckButton() {
  const button = document.getElementById("next-check");
  if (!button) return;
  const count = flaggedWordList().length;
  button.textContent = count ? `Next Check (${count})` : "Next Check";
  button.disabled = count === 0;
}

function jumpToNextCheck() {
  const items = flaggedWordList();
  if (!items.length) {
    setStatus("No flagged words to check.");
    updateNextCheckButton();
    return;
  }
  const time = audio.currentTime || 0;
  const chosen = items.find((item) => (item.word.start ?? item.cue.start ?? 0) > time + 0.05) || items[0];
  const seek = chosen.word.start ?? chosen.cue.start ?? 0;
  audio.pause();
  audio.currentTime = seek;
  setExactMode(false);
  draw(seek);
  syncTransport(seek);
  openCue(chosen.cue.index, chosen.wordIndex);
  setStatus(`Check “${chosen.word.text}” at ${formatClock(seek)}.`);
}

async function updateSentence(index, sentence) {
  const data = await api("/api/sentence", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: collect(), index, sentence }),
  });
  applyProject(data.project);
}

function fileNameFromPath(path) {
  const text = String(path || "").trim();
  if (!text) return "";
  return text.split(/[/\\]/).pop() || text;
}

function updateFileCards() {
  const audioPath = document.getElementById("audio-path")?.value.trim() || "";
  const srtPath = document.getElementById("srt-path")?.value.trim() || "";
  const audioLabel = document.getElementById("audio-file-label");
  const srtLabel = document.getElementById("srt-file-label");
  const audioDrop = document.getElementById("audio-drop");
  const srtDrop = document.getElementById("srt-drop");
  const readyPill = document.getElementById("files-ready");

  if (audioLabel) audioLabel.textContent = fileNameFromPath(audioPath) || "No file chosen";
  if (srtLabel) srtLabel.textContent = fileNameFromPath(srtPath) || "No file chosen";
  audioDrop?.classList.toggle("is-filled", Boolean(audioPath));
  srtDrop?.classList.toggle("is-filled", Boolean(srtPath));

  if (readyPill) {
    const ready = Boolean(audioPath && srtPath);
    readyPill.textContent = ready ? "Ready" : audioPath || srtPath ? "Almost" : "Waiting";
    readyPill.classList.toggle("is-ready", ready);
  }
  updateInspectorPrimary();
}

function applyProject(project) {
  meta = project;
  state = project;
  document.getElementById("audio-path").value = project.audio_path || "";
  document.getElementById("srt-path").value = project.srt_path || "";
  const stem = (project.srt_path || "subtitles").split(/[/\\]/).pop().replace(/\.srt$/i, "");
  document.getElementById("output-path").value = `output/${stem}-subs.mov`;
  setExactMode(false);
  updatePreviewEmpty();
  updateFileCards();
  renderEditor();
  attachAudio(project);
  renderCueTicks();
  syncTransport(0);
  refreshLayout();
}

function attachAudio(project) {
  audio.pause();
  playing = false;
  audio.removeAttribute("src");
  audio.load();
  const token = encodeURIComponent(project.audio_path || "");
  audio.src = `/api/audio?file=${token}&t=${Date.now()}`;
  syncTransport(0);
}

async function refreshLayout() {
  if (!state.cues?.length) return;
  const draft = currentDraft();
  const data = await api("/api/layout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: collect(), settings: draft }),
  });
  layouts = {};
  for (const cue of data.cues) layouts[cue.index] = cue;
  settings = { ...draft, width: data.width, height: data.height };
  draw(audio.currentTime || 0);
  updateStyleDirtyState();
}

function scheduleLayout() {
  clearTimeout(layoutTimer);
  layoutTimer = setTimeout(() => {
    refreshLayout().catch((error) => setStatus(error.message, true));
  }, 250);
}

function draw(time) {
  syncTransport(time || 0);
  updatePreviewEmpty();
  if (exactMode) return;
  const cue = visibleCue(state.cues || [], time || 0);
  if (!cue || !layouts[cue.index]) {
    wordLayer.replaceChildren();
    wordLayer.dataset.cue = "";
    return;
  }
  const layout = layouts[cue.index];
  const stageWidth = document.querySelector(".stage-frame")?.clientWidth || 320;
  const scale = stageWidth / (settings?.width || 1080);
  const hold = (settings?.pause_hold_ms || 0) / 1000;
  const active = activeWordIndex(cue.words, time || 0, hold);
  if (wordLayer.dataset.cue !== String(cue.index) || wordLayer.children.length !== layout.words.length) {
    wordLayer.dataset.cue = String(cue.index);
    wordLayer.replaceChildren();
    for (const placed of layout.words) {
      const span = document.createElement("span");
      span.className = "subtitle-word";
      span.dataset.index = String(placed.index);
      span.textContent = placed.text;
      span.addEventListener("click", () => {
        openCue(cue.index);
        const cueEl = editor.querySelector(`.cue[data-index="${cue.index}"]`);
        const row = cueEl?.querySelectorAll(".word-row")[placed.index];
        row?.querySelector(".word-text")?.focus();
      });
      wordLayer.append(span);
    }
  }
  [...wordLayer.children].forEach((span, position) => {
    const placed = layout.words[position];
    const word = cue.words[placed.index];
    const on = placed.index === active;
    span.textContent = word?.text || placed.text;
    span.style.left = `${placed.x * scale}px`;
    span.style.top = `${placed.y * scale}px`;
    span.style.fontSize = `${layout.font_size * scale}px`;
    span.style.color = on ? (settings?.color_highlight || "#FFD43B") : (settings?.color_normal || "#FFFFFF");
    const amount = on ? popScale(time, word?.start, (settings?.animation_ms || 0) / 1000, settings?.pop_scale || 1) : 1;
    span.style.transform = `scale(${amount})`;
  });
}

function showLog(snapshot) {
  logEl.textContent = (snapshot.messages || []).join("\n");
  if (snapshot.error) setStatus(snapshot.error, true);
}

function watchJob(kind) {
  clearInterval(pollTimer);
  setBusy(true);
  showJobLog(true);
  pollTimer = setInterval(async () => {
    try {
      const snapshot = await api("/api/status");
      showLog(snapshot);
      if (snapshot.running) return;
      clearInterval(pollTimer);
      setBusy(false);
      if (snapshot.error) return;
      if (snapshot.done === "align") {
        const data = await api("/api/project");
        applyProject(data.project);
        setStatus("Word timings are ready. Play the voiceover, then correct any flagged words.");
        setStep(3);
        const flagged = (data.project.cues || []).reduce(
          (sum, cue) => sum + cue.words.filter((word) => word.flagged).length,
          0
        );
        updateNextCheckButton();
        if (flagged) switchInspectorTab("edit");
      } else if (snapshot.done === "export") {
        setStatus(`Exported ${snapshot.export_path}`);
        setStep(4);
        switchInspectorTab("export");
      }
    } catch (error) {
      clearInterval(pollTimer);
      setBusy(false);
      setStatus(error.message, true);
    }
  }, 500);
  if (kind) setStatus(kind === "align" ? "Aligning words to the voice…" : "Exporting the overlay…");
}

function setBusy(busy) {
  for (const id of ["open", "align", "save", "export", "save-settings", "inspector-primary"]) {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  }
  document.querySelectorAll(".inspector-tab").forEach((tab) => {
    tab.disabled = busy;
  });
}

async function uploadFile(file, kind) {
  const data = await api(`/api/upload?kind=${kind}&name=${encodeURIComponent(file.name)}`, {
    method: "POST",
    body: file,
  });
  return data.path;
}

async function openFiles() {
  const audioPath = document.getElementById("audio-path").value.trim();
  const srtPath = document.getElementById("srt-path").value.trim();
  if (!audioPath || !srtPath) {
    setStatus("Choose a voiceover and an SRT file.", true);
    return;
  }
  const data = await api("/api/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ audio_path: audioPath, srt_path: srtPath }),
  });
  applyProject(data.project);
  setStatus("Files loaded. Generate word timings next.");
  setStep(2);
  rendered.hidden = true;
}

async function saveProject(quiet = false) {
  const data = await api("/api/project", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: collect() }),
  });
  state = collect();
  if (!quiet) setStatus(`Corrections saved. ${data.flagged} words still flagged.`);
  return data;
}

function formatSettingValue(field, value) {
  if (field.type === "range" || field.type === "number") {
    const number = Number(value);
    const shown = field.step && String(field.step).includes(".") ? number.toFixed(2).replace(/\.?0+$/, "") : String(number);
    return `${shown}${field.suffix || ""}`;
  }
  return String(value ?? "");
}

function createColorControl(field, value) {
  const wrap = document.createElement("div");
  wrap.className = "color-field";
  const label = document.createElement("span");
  label.className = "control-label";
  label.textContent = field.label;
  const swatch = document.createElement("div");
  swatch.className = "swatch";
  const picker = document.createElement("input");
  picker.type = "color";
  picker.dataset.key = field.key;
  picker.value = normalizeHex(value);
  const hex = document.createElement("input");
  hex.type = "text";
  hex.className = "hex";
  hex.dataset.key = field.key;
  hex.value = normalizeHex(value);
  hex.maxLength = 7;
  picker.addEventListener("input", () => {
    hex.value = picker.value.toUpperCase();
    liveStylePreview({ relayout: false });
  });
  hex.addEventListener("input", () => {
    const next = normalizeHex(hex.value);
    if (/^#[0-9A-F]{6}$/.test(next)) {
      picker.value = next;
      hex.value = next;
      liveStylePreview({ relayout: false });
    }
  });
  swatch.append(picker, hex);
  wrap.append(label, swatch);
  return wrap;
}

function createRangeControl(field, value) {
  const wrap = document.createElement("label");
  wrap.className = "control";
  const top = document.createElement("div");
  top.className = "control-top";
  const name = document.createElement("span");
  name.className = "control-label";
  name.textContent = field.label;
  const readout = document.createElement("span");
  readout.className = "control-value";
  readout.dataset.readout = field.key;
  readout.textContent = formatSettingValue(field, value);
  top.append(name, readout);
  const input = document.createElement("input");
  input.type = "range";
  input.dataset.key = field.key;
  input.min = field.min;
  input.max = field.max;
  input.step = field.step;
  input.value = value ?? field.min;
  input.addEventListener("input", () => {
    readout.textContent = formatSettingValue(field, input.value);
    const needsLayout = ["font_size", "margin_bottom", "width", "height"].includes(field.key);
    liveStylePreview({ relayout: needsLayout });
  });
  wrap.append(top, input);
  return wrap;
}

function syncOrientChips(orientation) {
  const value = orientation === "landscape" ? "landscape" : "portrait";
  document.querySelectorAll(".orient-chip").forEach((chip) => {
    chip.classList.toggle("is-active", chip.dataset.orientation === value);
  });
}

function applyFrameChrome(draft) {
  const orientation = draft?.orientation === "landscape" ? "landscape" : "portrait";
  const position = ["top", "middle", "bottom"].includes(draft?.position) ? draft.position : "bottom";
  const device = document.querySelector(".device");
  const frame = document.querySelector(".stage-frame");
  const guide = document.querySelector(".guide");
  if (device) device.dataset.orientation = orientation;
  if (frame) frame.dataset.orientation = orientation;
  if (guide) guide.dataset.position = position;
  syncOrientChips(orientation);
}

function setOrientationFromChip(orientation) {
  const next = orientation === "landscape" ? "landscape" : "portrait";
  const select = document.querySelector('#settings [data-key="orientation"]');
  if (select) select.value = next;
  const draft = currentDraft();
  draft.orientation = next;
  applyOrientationPreset(draft, { force: true });
  liveStylePreview({ relayout: true });
  syncOrientChips(next);
}

function applyOrientationPreset(draft, { force = false } = {}) {
  const orientation = draft.orientation === "landscape" ? "landscape" : "portrait";
  const preset = orientation === "landscape" ? LANDSCAPE_SIZE : PORTRAIT_SIZE;
  const mismatched = orientation === "landscape" ? draft.width < draft.height : draft.width > draft.height;
  if (!force && !mismatched) return draft;
  draft.width = preset.width;
  draft.height = preset.height;
  const widthInput = document.querySelector('#settings [data-key="width"]');
  const heightInput = document.querySelector('#settings [data-key="height"]');
  if (widthInput) widthInput.value = String(preset.width);
  if (heightInput) heightInput.value = String(preset.height);
  return draft;
}

function createTextControl(field, value) {
  const wrap = document.createElement("label");
  wrap.className = "control";
  const name = document.createElement("span");
  name.className = "control-label";
  name.textContent = field.label;
  const input = document.createElement("input");
  input.type = field.type === "number" ? "number" : "text";
  input.dataset.key = field.key;
  if (field.type === "number") {
    input.min = field.min;
    input.max = field.max;
    input.step = field.step;
  }
  if (field.placeholder) input.placeholder = field.placeholder;
  input.value = value ?? "";
  input.addEventListener("input", () => {
    const needsLayout = field.key === "font_path" || field.type === "number" || ["width", "height"].includes(field.key);
    liveStylePreview({ relayout: needsLayout });
  });
  wrap.append(name, input);
  return wrap;
}

function createSelectControl(field, value) {
  const wrap = document.createElement("label");
  wrap.className = "control select-wrap";
  const name = document.createElement("span");
  name.className = "control-label";
  name.textContent = field.label;
  const select = document.createElement("select");
  select.dataset.key = field.key;
  for (const [optionValue, optionLabel] of field.options) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionLabel;
    if (optionValue === value) option.selected = true;
    select.append(option);
  }
  select.addEventListener("change", () => {
    const needsLayout = ["orientation", "position"].includes(field.key);
    if (field.key === "orientation") {
      const draft = currentDraft();
      draft.orientation = select.value;
      applyOrientationPreset(draft, { force: true });
      liveStylePreview({ relayout: true });
      syncOrientChips(select.value);
      return;
    }
    liveStylePreview({ relayout: needsLayout });
  });
  wrap.append(name, select);
  return wrap;
}

function fillSettings(values, { markSaved = true } = {}) {
  settings = { ...values };
  if (markSaved) savedSettings = { ...values };
  const box = document.getElementById("settings");
  box.replaceChildren();

  const sample = document.createElement("div");
  sample.className = "style-section";
  sample.innerHTML = `
    <h3>Sample</h3>
    <div class="preview-chip" id="style-sample">
      <span class="muted">Keep the</span>
      <span class="hot">highlight</span>
      <span class="muted">clear</span>
    </div>
  `;
  box.append(sample);

  for (const section of STYLE_SECTIONS) {
    const block = document.createElement("div");
    block.className = "style-section";
    const title = document.createElement("h3");
    title.textContent = section.title;
    block.append(title);

    if (section.title === "Colours") {
      const row = document.createElement("div");
      row.className = "color-row";
      for (const field of section.fields) row.append(createColorControl(field, values[field.key]));
      block.append(row);
    } else {
      const grid = document.createElement("div");
      grid.className = "style-grid";
      for (const field of section.fields) {
        if (field.type === "range") grid.append(createRangeControl(field, values[field.key]));
        else if (field.type === "select") grid.append(createSelectControl(field, values[field.key]));
        else grid.append(createTextControl(field, values[field.key]));
      }
      block.append(grid);
    }
    box.append(block);
  }
  applyFrameChrome(values);
  liveStylePreview({ relayout: false });
  updateStyleDirtyState();
}

function readSettings() {
  const data = { ...(settings || {}) };
  const numeric = new Set([
    "font_size", "margin_bottom", "animation_ms", "pop_scale", "pause_hold_ms",
    "width", "height", "fps", "shadow_opacity",
  ]);
  for (const input of document.querySelectorAll("#settings [data-key]")) {
    const key = input.dataset.key;
    if (input.classList.contains("hex")) continue;
    if (input.type === "color") {
      data[key] = normalizeHex(input.value);
      continue;
    }
    if (input.tagName === "SELECT") {
      data[key] = input.value;
      continue;
    }
    data[key] = numeric.has(key) ? Number(input.value) : String(input.value || "").trim();
  }
  return data;
}

function currentDraft() {
  return { ...(settings || {}), ...readSettings() };
}

function styleFingerprint(values) {
  if (!values) return "";
  const keys = [
    "color_normal", "color_highlight", "font_size", "font_path", "margin_bottom",
    "shadow_opacity", "animation_ms", "pop_scale", "pause_hold_ms", "codec",
    "fps", "width", "height", "language", "orientation", "position",
  ];
  return keys.map((key) => `${key}:${values[key]}`).join("|");
}

function updateStyleDirtyState() {
  const dirty = styleFingerprint(currentDraft()) !== styleFingerprint(savedSettings);
  const note = document.getElementById("style-dirty");
  const hint = document.getElementById("style-hint");
  if (note) note.hidden = !dirty;
  if (hint) {
    hint.textContent = dirty
      ? "Previewing unsaved changes on the left. Click Save style to keep them."
      : "Changes preview live. Save when you like the look.";
  }
  return dirty;
}

function liveStylePreview({ relayout = true } = {}) {
  const draft = currentDraft();
  settings = draft;
  applyFrameChrome(draft);
  // Only the subtitle preview uses these colours — never the page chrome.
  const sample = document.getElementById("style-sample");
  if (sample) {
    sample.querySelectorAll(".muted").forEach((node) => {
      node.style.color = normalizeHex(draft.color_normal || "#FFFFFF");
    });
    const hot = sample.querySelector(".hot");
    if (hot) hot.style.color = normalizeHex(draft.color_highlight || "#FFD43B");
  }
  draw(audio.currentTime || 0);
  updateStyleDirtyState();
  if (relayout) scheduleLayout();
}

function tick() {
  draw(audio.currentTime || 0);
  if (playing) requestAnimationFrame(tick);
}

document.getElementById("audio-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    document.getElementById("audio-path").value = await uploadFile(file, "audio");
    updateFileCards();
    await useSelectedFiles("Voiceover ready. Choose the SRT as well.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("srt-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    document.getElementById("srt-path").value = await uploadFile(file, "srt");
    updateFileCards();
    await useSelectedFiles("SRT ready. Choose the voiceover as well.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

async function useSelectedFiles(waitingMessage) {
  const audioPath = document.getElementById("audio-path").value.trim();
  const srtPath = document.getElementById("srt-path").value.trim();
  if (!audioPath || !srtPath) {
    setStatus(waitingMessage);
    return;
  }
  if (audioPath === meta?.audio_path && srtPath === meta?.srt_path) {
    attachAudio(meta);
    setStatus("These files are already loaded.");
    return;
  }
  await openFiles();
}

for (const id of ["audio-path", "srt-path"]) {
  document.getElementById(id).addEventListener("input", () => updateFileCards());
  document.getElementById(id).addEventListener("change", () => {
    updateFileCards();
    useSelectedFiles("Choose both the voiceover and the SRT.").catch((error) => setStatus(error.message, true));
  });
}

document.querySelectorAll(".inspector-tab").forEach((button) => {
  button.addEventListener("click", () => switchInspectorTab(button.dataset.tab));
});

document.getElementById("expand-cues")?.addEventListener("click", () => {
  switchInspectorTab("edit");
  editor.querySelectorAll(".cue").forEach((item) => {
    item.open = true;
  });
});
document.getElementById("collapse-cues")?.addEventListener("click", () => {
  editor.querySelectorAll(".cue").forEach((item) => {
    item.open = false;
  });
});

async function runAlign() {
  if (!meta) throw new Error("Load an audio file and an SRT file first");
  await saveProject(true);
  await api("/api/align", { method: "POST" });
  watchJob("align");
}

async function runExport() {
  if (!meta) throw new Error("Load a project before exporting");
  if (updateStyleDirtyState()) {
    throw new Error("Save style first. Export uses the last saved look, not the unsaved preview.");
  }
  await saveProject(true);
  setStep(4);
  await api("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ output: document.getElementById("output-path").value.trim() }),
  });
  watchJob("export");
}

async function runSaveSettings() {
  const draft = currentDraft();
  settings = await api("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
  fillSettings(settings, { markSaved: true });
  await refreshLayout();
  setStatus("Style saved. Future Shorts will use it.");
}

document.getElementById("open").addEventListener("click", () => openFiles().catch((error) => setStatus(error.message, true)));
document.getElementById("save").addEventListener("click", () => saveProject().catch((error) => setStatus(error.message, true)));
document.getElementById("align").addEventListener("click", () => runAlign().catch((error) => setStatus(error.message, true)));
document.getElementById("export").addEventListener("click", () => runExport().catch((error) => setStatus(error.message, true)));
document.getElementById("save-settings").addEventListener("click", () => runSaveSettings().catch((error) => setStatus(error.message, true)));
document.getElementById("reset-settings").addEventListener("click", async () => {
  if (!savedSettings) return;
  fillSettings(savedSettings, { markSaved: true });
  try {
    await refreshLayout();
    setStatus("Style reset to the last saved look.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("inspector-primary")?.addEventListener("click", () => {
  const actions = {
    open: () => openFiles(),
    align: () => runAlign(),
    save: () => saveProject(),
    export: () => runExport(),
    "save-settings": () => runSaveSettings(),
  };
  const run = actions[inspectorPrimaryAction] || actions.open;
  Promise.resolve(run()).catch((error) => setStatus(error.message, true));
});

document.getElementById("close-log")?.addEventListener("click", () => showJobLog(false));
const playButton = document.getElementById("play");

async function showExactFrame() {
  if (!meta) throw new Error("Load a project first");
  await saveProject(true);
  const response = await fetch("/api/frame", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project: collect(),
      settings: currentDraft(),
      t: audio.currentTime || 0,
    }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || response.statusText);
  }
  const blob = await response.blob();
  if (rendered.dataset.objectUrl) URL.revokeObjectURL(rendered.dataset.objectUrl);
  const url = URL.createObjectURL(blob);
  rendered.dataset.objectUrl = url;
  rendered.src = url;
  rendered.hidden = false;
  setExactMode(true, { announce: true });
  setStatus(updateStyleDirtyState()
    ? "Exact frame uses your unsaved preview style."
    : "Exact frame matches the saved style.");
}

playButton.addEventListener("click", async () => {
  try {
    const audioPath = document.getElementById("audio-path").value.trim();
    const srtPath = document.getElementById("srt-path").value.trim();
    if (audioPath && srtPath && (audioPath !== meta?.audio_path || srtPath !== meta?.srt_path)) {
      await openFiles();
    }
    if (!audio.src) return;
    setExactMode(false);
    if (audio.paused) await audio.play();
    else audio.pause();
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("seek-back").addEventListener("click", () => seekBy(-1));
document.getElementById("seek-forward").addEventListener("click", () => seekBy(1));
document.getElementById("mute")?.addEventListener("click", () => toggleMute());
document.getElementById("volume")?.addEventListener("input", (event) => {
  setVolume(event.target.value);
});

document.getElementById("exact").addEventListener("click", () => {
  showExactFrame().catch((error) => setStatus(error.message, true));
});

modeBadge?.addEventListener("click", () => {
  if (exactMode) {
    setExactMode(false, { announce: true });
    draw(audio.currentTime || 0);
    return;
  }
  showExactFrame().catch((error) => setStatus(error.message, true));
});

rendered.addEventListener("click", () => {
  setExactMode(false, { announce: true });
  draw(audio.currentTime || 0);
});

scrubber?.addEventListener("pointerdown", () => {
  scrubbing = true;
});
scrubber?.addEventListener("pointerup", () => {
  scrubbing = false;
});
scrubber?.addEventListener("input", () => {
  const time = Number(scrubber.value) || 0;
  const max = Number(scrubber.max) || 0;
  scrubber.style.setProperty("--progress", `${max > 0 ? (time / max) * 100 : 0}%`);
  audio.currentTime = time;
  setExactMode(false);
  draw(time);
  syncTransport(time);
});

editor.addEventListener("input", (event) => {
  syncFromEditor();
  if (event.target.classList.contains("word-text")) scheduleLayout();
});

audio.addEventListener("loadedmetadata", () => {
  syncTransport(audio.currentTime || 0);
  renderCueTicks();
});
audio.addEventListener("durationchange", () => {
  syncTransport(audio.currentTime || 0);
  renderCueTicks();
});
audio.addEventListener("play", () => {
  playing = true;
  setPlayButtonState(true);
  setExactMode(false);
  if (meta) setStep(3);
  requestAnimationFrame(tick);
});
audio.addEventListener("pause", () => {
  playing = false;
  setPlayButtonState(false);
  draw(audio.currentTime || 0);
});
audio.addEventListener("seeked", () => {
  draw(audio.currentTime || 0);
  syncTransport(audio.currentTime || 0);
});
audio.addEventListener("timeupdate", () => {
  syncTransport(audio.currentTime || 0);
  if (audio.paused) draw(audio.currentTime || 0);
});

window.addEventListener("resize", () => {
  if (!exactMode) draw(audio.currentTime || 0);
});

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, textarea, select")) return;
  if (event.code === "Space") {
    event.preventDefault();
    playButton.click();
    return;
  }
  if (event.code === "ArrowLeft") {
    event.preventDefault();
    seekBy(event.shiftKey ? -5 : -1);
    return;
  }
  if (event.code === "ArrowRight") {
    event.preventDefault();
    seekBy(event.shiftKey ? 5 : 1);
    return;
  }
  if (event.code === "KeyM") {
    event.preventDefault();
    toggleMute();
  }
});

function setPreviewBackground(mode) {
  const next = mode === "solid" ? "solid" : "transparent";
  const frame = document.querySelector(".stage-frame");
  if (frame) frame.dataset.previewBg = next;
  document.querySelectorAll(".preview-bg-option").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.previewBg === next);
  });
  try {
    localStorage.setItem("subtitlePreviewBg", next);
  } catch (_) {
    /* ignore */
  }
}

document.querySelectorAll(".preview-bg-option").forEach((button) => {
  button.addEventListener("click", () => setPreviewBackground(button.dataset.previewBg));
});

function setGuideVisible(visible) {
  const on = Boolean(visible);
  const guide = document.querySelector(".guide");
  const button = document.getElementById("toggle-guide");
  if (guide) guide.hidden = !on;
  if (button) {
    button.classList.toggle("is-on", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.textContent = on ? "Guide" : "Guide off";
  }
  try {
    localStorage.setItem("subtitlePreviewGuide", on ? "1" : "0");
  } catch (_) {
    /* ignore */
  }
}

document.getElementById("toggle-guide")?.addEventListener("click", () => {
  const button = document.getElementById("toggle-guide");
  setGuideVisible(!(button?.classList.contains("is-on")));
});

document.querySelectorAll(".orient-chip").forEach((chip) => {
  chip.addEventListener("click", () => setOrientationFromChip(chip.dataset.orientation));
});

document.getElementById("next-check")?.addEventListener("click", () => jumpToNextCheck());

editor.addEventListener("change", (event) => {
  if (event.target.classList.contains("word-flag")) {
    syncFromEditor();
    updateNextCheckButton();
  }
});

async function boot() {
  try {
    let savedPreviewBg = "transparent";
    let savedGuide = true;
    try {
      savedPreviewBg = localStorage.getItem("subtitlePreviewBg") || "transparent";
      savedGuide = localStorage.getItem("subtitlePreviewGuide") !== "0";
    } catch (_) {
      /* ignore */
    }
    setPreviewBackground(savedPreviewBg);
    setGuideVisible(savedGuide);
    setExactMode(false);
    updatePreviewEmpty();
    updateFileCards();
    updateNextCheckButton();
    syncTransport(0);
    switchInspectorTab("media");

    let savedVolume = 1;
    let savedMuted = false;
    try {
      const storedVolume = Number(localStorage.getItem("subtitlePreviewVolume"));
      if (Number.isFinite(storedVolume)) savedVolume = Math.min(Math.max(storedVolume, 0), 1);
      savedMuted = localStorage.getItem("subtitlePreviewMuted") === "1";
    } catch (_) {
      /* ignore */
    }
    volumeBeforeMute = savedVolume > 0 ? savedVolume : 1;
    setVolume(savedMuted ? 0 : savedVolume, { persist: false });
    if (savedMuted) audio.muted = true;
    syncVolumeUI();

    fillSettings(await api("/api/config"));
    const data = await api("/api/project");
    if (data.project) {
      applyProject(data.project);
      const hasTimes = (data.project.cues || []).some((cue) =>
        (cue.words || []).some((word) => word.start != null && word.end != null)
      );
      setStatus("Previous timings restored. Play the voiceover to check the highlight.");
      setStep(hasTimes ? 3 : 2);
      switchInspectorTab(hasTimes ? "edit" : "media");
    } else {
      setStep(1);
      updatePreviewEmpty();
      updateInspectorPrimary();
    }
  } catch (error) {
    setStatus(error.message, true);
  }
}

boot();
