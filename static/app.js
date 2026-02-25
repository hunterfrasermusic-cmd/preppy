const LIBRARY_STORAGE_KEY = "preppy.songLibrary.v3";
const SETLISTS_STORAGE_KEY = "preppy.savedSetlists.v1";
const LEGACY_KEYS = ["preppy.songLibrary.v2", "preppy.songLibrary.v1"];

const SECTION_TEMPLATES = [
  "Intro",
  "V1",
  "V2",
  "V3",
  "Pre 1",
  "Pre 2",
  "C1",
  "C2",
  "B1",
  "B2",
  "Tag",
  "Turn",
  "Inst",
  "Outro",
  "END"
];

const DYNAMICS = [
  { value: "down", symbol: "↓" },
  { value: "steady", symbol: "→" },
  { value: "build", symbol: "↗" },
  { value: "up", symbol: "↑" }
];

const DEFAULT_EXPORT_HEADER_LINES = [
  "Shorthand Key",
  "Dynamics: ↓=soft, →=medium, ↗=build, ↑ big/loud, PNO=piano, EG 1=lead electric, EG 2=rhythm electric, AG=acoustic,",
  "8va=octave (assumed up), vmp=vamp, <>s=diamonds or whole notes or changes. ¼=quarter note, ⅛=eighth note,"
];
const DRAG_START_THRESHOLD_PX = 48;

let songLibrary = loadSongLibrary();
let savedSetlists = loadSavedSetlists();
let parsedSongState = null;
let setlistState = {
  id: createId("set"),
  date: todayDateString(),
  name: "",
  items: []
};
let pcoPlans = [];
let sectionRefs = [];
let activeSectionIndex = 0;
let autoServiceName = true;
const expandedSongIds = new Set();

wireTabs();
wireLibraryEditor();
wireSetlistBuilder();
wireIntegrations();
renderSectionPalette();
renderLibraryList();
renderSetlistUI();
renderSetlistHistory();

function wireTabs() {
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach((tab) => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));
}

function activateTab(target) {
  const tabs = document.querySelectorAll(".tab");
  const panels = document.querySelectorAll(".panel");
  tabs.forEach((tab) => {
    const active = tab.dataset.tab === target;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });

  panels.forEach((panel) => {
    const active = panel.id === target;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });

  if (target === "library") renderLibraryList();
  if (target === "setlists") renderSetlistUI();
}

function wireLibraryEditor() {
  const form = document.getElementById("chart-upload-form");
  const fileInput = document.getElementById("chart-file");
  const parseButton = document.getElementById("chart-submit");
  const status = document.getElementById("chart-status");
  const newSongPanel = document.getElementById("new-song");

  const newBlankButton = document.getElementById("new-blank-song");
  const saveSongButton = document.getElementById("save-song");
  const addSectionButton = document.getElementById("add-section");

  const titleInput = document.getElementById("stub-title");
  const artistInput = document.getElementById("stub-artist");
  const arrangementInput = document.getElementById("stub-arrangement");
  const keyInput = document.getElementById("stub-key");
  const bpmInput = document.getElementById("stub-bpm");

  const uploadChart = async (file) => {
    if (!file || !String(file.name || "").toLowerCase().endsWith(".pdf")) {
      status.textContent = "Choose a PDF first.";
      status.classList.remove("ok");
      return;
    }
    parseButton.disabled = true;
    status.textContent = "Uploading chart...";
    status.classList.remove("ok");

    try {
      const body = new FormData();
      body.append("chart", file);

      const response = await fetch("/api/parse-chart", { method: "POST", body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Parse failed.");

      parsedSongState = {
        song: {
          id: "",
          title: payload.song?.title || "",
          artist: payload.song?.artist || ""
        },
        arrangement: {
          id: "",
          name: payload.song?.arrangement || "Main",
          key: payload.song?.key || "",
          bpm: String(payload.song?.bpm || "")
        },
        sections: normalizeSections(payload.sections)
      };

      syncEditorInputs();
      renderSections(0);
      status.textContent = `Parsed ${file.name}`;
      status.classList.add("ok");
      fileInput.value = "";
    } catch (error) {
      status.textContent = error.message;
      status.classList.remove("ok");
    } finally {
      parseButton.disabled = false;
      fileInput.value = "";
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await uploadChart(fileInput.files?.[0]);
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    newSongPanel.addEventListener(eventName, (event) => {
      const hasFiles = Array.from(event.dataTransfer?.types || []).includes("Files");
      if (!hasFiles) return;
      event.preventDefault();
      newSongPanel.classList.add("file-drag-active");
    });
  });

  ["dragleave", "dragend", "drop"].forEach((eventName) => {
    newSongPanel.addEventListener(eventName, (event) => {
      if (eventName !== "drop") return newSongPanel.classList.remove("file-drag-active");
      event.preventDefault();
      newSongPanel.classList.remove("file-drag-active");
      const file = Array.from(event.dataTransfer?.files || []).find((item) =>
        String(item.type || "").includes("pdf") || String(item.name || "").toLowerCase().endsWith(".pdf")
      );
      if (!file) {
        status.textContent = "Dropped file is not a PDF.";
        status.classList.remove("ok");
        return;
      }
      uploadChart(file);
    });
  });

  newBlankButton.addEventListener("click", () => {
    parsedSongState = {
      song: { id: "", title: "", artist: "" },
      arrangement: { id: "", name: "Main", key: "", bpm: "" },
      sections: defaultSectionsStub()
    };
    syncEditorInputs();
    renderSections(0);
    status.textContent = "Started blank song.";
    status.classList.add("ok");
  });

  saveSongButton.addEventListener("click", () => {
    if (!parsedSongState) return;

    const title = (parsedSongState.song.title || "").trim();
    const arrangementName = (parsedSongState.arrangement.name || "").trim();
    if (!title) {
      status.textContent = "Song title is required.";
      status.classList.remove("ok");
      return;
    }
    if (!arrangementName) {
      status.textContent = "Arrangement name is required.";
      status.classList.remove("ok");
      return;
    }

    persistParsedSong();
    saveSongLibrary();
    renderLibraryList();
    renderSetlistUI();

    status.textContent = `Saved ${title} (${arrangementName})`;
    status.classList.add("ok");
  });

  addSectionButton.addEventListener("click", () => {
    if (!parsedSongState) return;
    parsedSongState.sections.push({ label: "", energy: "", notes: "" });
    renderSections(parsedSongState.sections.length - 1);
    focusNotes(parsedSongState.sections.length - 1);
  });

  titleInput.addEventListener("input", () => {
    if (!parsedSongState) return;
    parsedSongState.song.title = titleInput.value;
  });

  artistInput.addEventListener("input", () => {
    if (!parsedSongState) return;
    parsedSongState.song.artist = artistInput.value;
  });

  arrangementInput.addEventListener("input", () => {
    if (!parsedSongState) return;
    parsedSongState.arrangement.name = arrangementInput.value;
  });

  keyInput.addEventListener("input", () => {
    if (!parsedSongState) return;
    parsedSongState.arrangement.key = keyInput.value;
  });

  bpmInput.addEventListener("input", () => {
    if (!parsedSongState) return;
    parsedSongState.arrangement.bpm = bpmInput.value;
  });

  ["search-title", "search-artist", "search-arrangement", "search-key", "search-bpm"].forEach((id) => {
    document.getElementById(id).addEventListener("input", () => renderLibraryList());
  });

  document.addEventListener("keydown", (event) => {
    if (!isNewSongActive() || !parsedSongState) return;

    const target = event.target;
    const inInput = target && ["INPUT", "TEXTAREA"].includes(target.tagName);

    if (event.key === "ArrowUp" || event.key === "ArrowDown" || event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const energy = arrowToEnergy(event.key);
      if (!energy) return;
      event.preventDefault();
      setSectionEnergy(activeSectionIndex, energy);
      focusNotes(activeSectionIndex);
      return;
    }

    if (event.key.toLowerCase() === "a" && !inInput) {
      event.preventDefault();
      focusNotes(activeSectionIndex);
    }
  });
}

function wireSetlistBuilder() {
  const dateInput = document.getElementById("setlist-date");
  const nameInput = document.getElementById("setlist-name");
  const display = document.getElementById("service-date-display");
  const searchInput = document.getElementById("setlist-search");
  const historySearch = document.getElementById("setlist-history-search");
  const historyMonth = document.getElementById("setlist-history-month");
  const historyYear = document.getElementById("setlist-history-year");
  const saveCurrent = document.getElementById("save-current-setlist");
  const recallSelect = document.getElementById("setlist-recall-select");
  const recallLoad = document.getElementById("setlist-recall-load");
  const recallDelete = document.getElementById("setlist-recall-delete");
  const output = document.getElementById("setlist-output");

  dateInput.value = setlistState.date;
  setlistState.name = formatLongDate(setlistState.date);
  nameInput.value = setlistState.name;
  display.textContent = `Service: ${formatLongDate(setlistState.date)}`;

  dateInput.addEventListener("input", () => {
    setlistState.date = dateInput.value || todayDateString();
    if (autoServiceName || !nameInput.value.trim()) {
      setlistState.name = formatLongDate(setlistState.date);
      nameInput.value = setlistState.name;
      autoServiceName = true;
    }
    display.textContent = `Service: ${formatLongDate(setlistState.date)}`;
    renderSetlistHistory();
  });

  nameInput.addEventListener("input", () => {
    setlistState.name = nameInput.value;
    autoServiceName = normalizeText(setlistState.name) === normalizeText(formatLongDate(setlistState.date));
    renderSetlistHistory();
  });

  searchInput.addEventListener("input", () => renderSetlistUI());
  historySearch.addEventListener("input", () => renderSetlistHistory());
  historyMonth.addEventListener("input", () => renderSetlistHistory());
  historyYear.addEventListener("input", () => renderSetlistHistory());

  saveCurrent.addEventListener("click", () => {
    const saved = saveCurrentSetlistSnapshot({ forceNew: true });
    if (!saved) {
      const status = document.getElementById("setlist-status");
      status.textContent = "Could not save setlist.";
      status.classList.remove("ok");
      return;
    }
    renderSetlistQuickPick();
    renderSetlistHistory();
    const status = document.getElementById("setlist-status");
    status.textContent = "Setlist saved.";
    status.classList.add("ok");
  });

  recallLoad.addEventListener("click", () => {
    const id = recallSelect.value;
    if (!id) return;
    const loaded = loadSetlistById(id);
    const status = document.getElementById("setlist-status");
    if (!loaded) {
      status.textContent = "Could not load selected setlist.";
      status.classList.remove("ok");
      return;
    }
    status.textContent = "Setlist loaded.";
    status.classList.add("ok");
  });

  recallDelete.addEventListener("click", () => {
    const id = recallSelect.value;
    if (!id) return;
    const deleted = deleteSetlistById(id);
    const status = document.getElementById("setlist-status");
    if (!deleted) {
      status.textContent = "Could not delete selected setlist.";
      status.classList.remove("ok");
      return;
    }
    status.textContent = "Setlist deleted.";
    status.classList.add("ok");
  });

  document.getElementById("generate-prep-doc").addEventListener("click", () => {
    const model = generatePrepSheetModel();
    setPrepOutputText(model.text);
    const status = document.getElementById("setlist-status");
    status.textContent = model.lines.length ? "Prep sheet generated." : "Add songs first.";
    status.classList.toggle("ok", Boolean(model.lines.length));
  });

  document.getElementById("download-prep-docx").addEventListener("click", async () => {
    const model = generatePrepSheetModel();
    const status = document.getElementById("setlist-status");
    const editedLines = getPrepOutputLines();
    const linesForExport = editedLines.length ? editedLines : model.lines;
    const hasManualEdits = editedLines.length > 0;
    if (!hasManualEdits) setPrepOutputText(model.text);

    if (!linesForExport.length) {
      status.textContent = "Add songs first.";
      status.classList.remove("ok");
      return;
    }

    try {
      const response = await fetch("/api/export-docx", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lines: linesForExport,
          filename: model.filename,
          header_lines: DEFAULT_EXPORT_HEADER_LINES
        })
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || "Export failed.");
      }

      const blob = await response.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = model.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);

      status.textContent = `Downloaded ${model.filename}`;
      status.classList.add("ok");
    } catch (error) {
      status.textContent = error.message;
      status.classList.remove("ok");
    }
  });

  output.addEventListener("paste", (event) => {
    event.preventDefault();
    const plain = event.clipboardData?.getData("text/plain") || "";
    document.execCommand("insertText", false, plain);
  });

  renderSetlistQuickPick();
}

function setPrepOutputText(value) {
  const output = document.getElementById("setlist-output");
  output.textContent = value || "";
}

function getPrepOutputText() {
  const output = document.getElementById("setlist-output");
  return (output.textContent || "").replace(/\u00a0/g, " ");
}

function getPrepOutputLines() {
  return trimTrailingBlank(
    getPrepOutputText()
      .split(/\r?\n/)
      .map((line) => line.trimEnd())
  );
}

function wireIntegrations() {
  const appIdInput = document.getElementById("pco-app-id");
  const secretInput = document.getElementById("pco-secret");
  const saveButton = document.getElementById("pco-save");
  const loadPlansButton = document.getElementById("pco-load-plans");
  const importPlanButton = document.getElementById("pco-import-plan");
  const planSelect = document.getElementById("pco-plan-select");
  const status = document.getElementById("pco-status");

  if (!appIdInput || !secretInput || !saveButton || !loadPlansButton || !importPlanButton || !planSelect || !status) {
    return;
  }

  const setStatus = (text, ok = false) => {
    status.textContent = text;
    status.classList.toggle("ok", ok);
  };

  const renderPlanOptions = () => {
    planSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = pcoPlans.length ? "Select a plan..." : "Load plans first...";
    planSelect.appendChild(placeholder);

    pcoPlans.forEach((plan) => {
      const option = document.createElement("option");
      option.value = `${plan.service_type_id}::${plan.plan_id}`;
      option.textContent = `${plan.sort_date || "No date"} | ${plan.service_type_name || "Service Type"} | ${plan.title || "Plan"}`;
      planSelect.appendChild(option);
    });
  };

  const refreshPcoStatus = async () => {
    try {
      const response = await fetch("/api/pco/status");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not load PCO status.");

      if (payload.configured) {
        setStatus(`PCO connected (${payload.source}).`, true);
      } else {
        setStatus("PCO not configured yet.");
      }
    } catch (error) {
      setStatus(error.message);
    }
  };

  saveButton.addEventListener("click", async () => {
    const appId = appIdInput.value.trim();
    const secret = secretInput.value.trim();
    if (!appId || !secret) {
      setStatus("Enter app ID and secret first.");
      return;
    }

    saveButton.disabled = true;
    setStatus("Saving credentials...");
    try {
      const response = await fetch("/api/pco/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: appId, secret })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not save credentials.");
      setStatus("PCO credentials saved.", true);
      secretInput.value = "";
      await refreshPcoStatus();
    } catch (error) {
      setStatus(error.message);
    } finally {
      saveButton.disabled = false;
    }
  });

  loadPlansButton.addEventListener("click", async () => {
    loadPlansButton.disabled = true;
    setStatus("Loading upcoming plans...");
    try {
      const response = await fetch("/api/pco/upcoming-plans");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not load plans.");
      pcoPlans = Array.isArray(payload.plans) ? payload.plans : [];
      renderPlanOptions();
      setStatus(`Loaded ${pcoPlans.length} upcoming plans.`, true);
    } catch (error) {
      setStatus(error.message);
    } finally {
      loadPlansButton.disabled = false;
    }
  });

  importPlanButton.addEventListener("click", async () => {
    const selected = planSelect.value;
    if (!selected) {
      setStatus("Choose a plan first.");
      return;
    }
    const [serviceTypeId, planId] = selected.split("::");
    if (!serviceTypeId || !planId) {
      setStatus("Invalid plan selection.");
      return;
    }

    importPlanButton.disabled = true;
    setStatus("Importing plan songs...");
    try {
      const params = new URLSearchParams({ service_type_id: serviceTypeId, plan_id: planId });
      const response = await fetch(`/api/pco/import-plan?${params.toString()}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Plan import failed.");

      const imported = importPcoPlanIntoLibrary(payload.plan, payload.songs);
      setStatus(`Imported ${imported} songs from selected plan.`, true);
      activateTab("setlists");
    } catch (error) {
      setStatus(error.message);
    } finally {
      importPlanButton.disabled = false;
    }
  });

  renderPlanOptions();
  refreshPcoStatus();
}

function importPcoPlanIntoLibrary(plan, songs) {
  const list = Array.isArray(songs) ? songs : [];
  const nextItems = [];
  let importedCount = 0;

  list.forEach((entry) => {
    const title = String(entry?.title || "").trim();
    if (!title) return;

    const artist = String(entry?.artist || "").trim();
    const arrangementName = String(entry?.arrangement || "Main").trim() || "Main";
    const key = String(entry?.key || "").trim();
    const bpm = String(entry?.bpm || "").trim();

    let song = songLibrary.find(
      (item) =>
        normalizeText(item.title) === normalizeText(title) &&
        normalizeText(item.artist || "") === normalizeText(artist)
    );

    if (!song) {
      song = {
        id: createId("song"),
        title,
        artist,
        arrangements: []
      };
      songLibrary.push(song);
    }

    let arrangement = (song.arrangements || []).find((arr) => {
      const sameName = normalizeText(arr.name || "") === normalizeText(arrangementName);
      const sameKey = normalizeText(arr.key || "") === normalizeText(key);
      const sameBpm = normalizeText(String(arr.bpm || "")) === normalizeText(bpm);
      return sameName && sameKey && sameBpm;
    });

    if (!arrangement) {
      arrangement = {
        id: createId("arr"),
        name: arrangementName,
        key,
        bpm,
        sections: defaultSectionsStub()
      };
      song.arrangements.push(arrangement);
    }

    nextItems.push({ songId: song.id, arrangementId: arrangement.id });
    importedCount += 1;
  });

  if (plan && typeof plan === "object") {
    if (plan.sort_date) {
      setlistState.date = plan.sort_date;
      const dateInput = document.getElementById("setlist-date");
      if (dateInput) dateInput.value = setlistState.date;
    }
    setlistState.name = String(plan.title || formatLongDate(setlistState.date || todayDateString())).trim();
    const nameInput = document.getElementById("setlist-name");
    if (nameInput) nameInput.value = setlistState.name;
    const display = document.getElementById("service-date-display");
    if (display) display.textContent = `Service: ${formatLongDate(setlistState.date)}`;
    autoServiceName = normalizeText(setlistState.name) === normalizeText(formatLongDate(setlistState.date));
  }

  setlistState.items = nextItems;
  saveSongLibrary();
  renderLibraryList();
  renderSetlistUI();
  renderSetlistHistory();
  return importedCount;
}

function saveCurrentSetlistSnapshot(options = {}) {
  const forceNew = Boolean(options.forceNew);
  const snapshotId = forceNew ? createId("set") : (setlistState.id || createId("set"));
  const snapshot = {
    id: snapshotId,
    date: setlistState.date || todayDateString(),
    name: (setlistState.name || formatLongDate(setlistState.date || todayDateString())).trim(),
    items: [...setlistState.items]
  };

  if (forceNew) {
    savedSetlists.push(snapshot);
    setlistState.id = snapshot.id;
  } else {
    setlistState.id = snapshot.id;
    const existingIndex = savedSetlists.findIndex((entry) => entry.id === snapshot.id);
    if (existingIndex >= 0) {
      savedSetlists[existingIndex] = snapshot;
    } else {
      savedSetlists.push(snapshot);
    }
  }

  savedSetlists.sort((a, b) => String(b.date).localeCompare(String(a.date)));
  saveSavedSetlists();
  renderSetlistQuickPick();
  return true;
}

function renderSetlistHistory() {
  const list = document.getElementById("setlist-history-list");
  if (!list) return;

  const query = normalizeText(document.getElementById("setlist-history-search").value);
  const monthFilter = Number.parseInt(document.getElementById("setlist-history-month").value, 10);
  const yearFilter = Number.parseInt(document.getElementById("setlist-history-year").value, 10);

  const filtered = savedSetlists.filter((entry) => {
    const date = toDate(entry.date);
    if (!date) return false;
    const byMonth = Number.isNaN(monthFilter) ? true : date.getMonth() + 1 === monthFilter;
    const byYear = Number.isNaN(yearFilter) ? true : date.getFullYear() === yearFilter;
    const searchable = normalizeText(`${formatLongDate(entry.date)} ${entry.name || ""}`);
    const byQuery = !query || searchable.includes(query);
    return byMonth && byYear && byQuery;
  });

  list.innerHTML = "";
  if (!filtered.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No setlists match filters.";
    list.appendChild(empty);
    return;
  }

  filtered.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "saved-song-row";

    const info = document.createElement("div");
    info.className = "saved-song-info";
    info.innerHTML = `<strong>${escapeHtml(entry.name || formatLongDate(entry.date))}</strong><span>${escapeHtml(formatLongDate(entry.date))} - ${entry.items.length} songs</span>`;

    const actions = document.createElement("div");
    actions.className = "section-actions";

    const load = document.createElement("button");
    load.type = "button";
    load.textContent = "Load";
    load.addEventListener("click", () => {
      loadSetlistById(entry.id);
    });

    const del = document.createElement("button");
    del.type = "button";
    del.textContent = "Delete";
    del.addEventListener("click", () => {
      deleteSetlistById(entry.id);
    });

    actions.append(load, del);
    row.append(info, actions);
    list.appendChild(row);
  });
}

function renderSetlistQuickPick() {
  const select = document.getElementById("setlist-recall-select");
  if (!select) return;
  const current = select.value;

  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Select saved setlist...";
  select.appendChild(placeholder);

  savedSetlists.forEach((entry) => {
    const option = document.createElement("option");
    option.value = entry.id;
    option.textContent = `${formatLongDate(entry.date)} - ${entry.name || formatLongDate(entry.date)} (${entry.items.length})`;
    select.appendChild(option);
  });

  if (current && savedSetlists.some((entry) => entry.id === current)) {
    select.value = current;
  } else if (setlistState.id && savedSetlists.some((entry) => entry.id === setlistState.id)) {
    select.value = setlistState.id;
  } else {
    select.value = "";
  }
}

function loadSetlistById(id) {
  const entry = savedSetlists.find((item) => item.id === id);
  if (!entry) return false;

  setlistState = {
    id: entry.id,
    date: entry.date,
    name: entry.name || formatLongDate(entry.date),
    items: Array.isArray(entry.items) ? [...entry.items] : []
  };
  autoServiceName = normalizeText(setlistState.name) === normalizeText(formatLongDate(setlistState.date));
  document.getElementById("setlist-date").value = setlistState.date;
  document.getElementById("setlist-name").value = setlistState.name;
  document.getElementById("service-date-display").textContent = `Service: ${formatLongDate(setlistState.date)}`;
  renderSetlistUI();
  renderSetlistQuickPick();
  renderSetlistHistory();
  return true;
}

function deleteSetlistById(id) {
  const before = savedSetlists.length;
  savedSetlists = savedSetlists.filter((item) => item.id !== id);
  if (savedSetlists.length === before) return false;
  saveSavedSetlists();

  if (setlistState.id === id) {
    setlistState.id = createId("set");
  }

  renderSetlistQuickPick();
  renderSetlistHistory();
  return true;
}

function renderSectionPalette() {
  const palette = document.getElementById("section-palette");
  palette.innerHTML = "";

  SECTION_TEMPLATES.forEach((label) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "palette-btn";
    button.textContent = label;
    button.addEventListener("click", () => {
      if (!parsedSongState) return;
      parsedSongState.sections.push({ label, energy: "", notes: "" });
      renderSections(parsedSongState.sections.length - 1);
      focusNotes(parsedSongState.sections.length - 1);
    });
    palette.appendChild(button);
  });
}

function renderSections(selectedIndex = 0) {
  const container = document.getElementById("stub-sections");
  container.innerHTML = "";
  sectionRefs = [];

  if (!parsedSongState || !parsedSongState.sections.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No sections yet. Use quick-add buttons.";
    container.appendChild(empty);
    activeSectionIndex = 0;
    return;
  }

  parsedSongState.sections.forEach((section, index) => {
    const row = document.createElement("div");
    row.className = "section-row";

    const grabber = document.createElement("button");
    grabber.type = "button";
    grabber.className = "grabber";
    grabber.textContent = "⋮⋮";
    grabber.title = "Drag to reorder";
    grabber.addEventListener("mousedown", (event) => startSectionPointerDrag(event, index, row, container));

    const dynamicWrap = document.createElement("div");
    dynamicWrap.className = "dynamic-buttons";
    DYNAMICS.forEach((dynamic) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "dynamic-btn";
      button.textContent = dynamic.symbol;
      if (section.energy === dynamic.value) button.classList.add("selected");
      button.addEventListener("click", () => {
        setSectionEnergy(index, dynamic.value);
        focusNotes(index);
      });
      dynamicWrap.appendChild(button);
    });

    const label = document.createElement("input");
    label.type = "text";
    label.value = section.label || "";
    label.placeholder = "Section";
    label.addEventListener("input", () => {
      parsedSongState.sections[index].label = label.value;
    });
    label.addEventListener("focus", () => setActiveSection(index));

    const notes = document.createElement("input");
    notes.type = "text";
    notes.value = section.notes || "";
    notes.placeholder = "Song tags / notes";
    notes.addEventListener("input", () => {
      parsedSongState.sections[index].notes = notes.value;
    });
    notes.addEventListener("focus", () => setActiveSection(index));
    notes.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      moveToNextSection();
    });

    const actions = document.createElement("div");
    actions.className = "section-actions hover-actions";

    const duplicate = document.createElement("button");
    duplicate.type = "button";
    duplicate.className = "icon-action";
    duplicate.textContent = "+";
    duplicate.title = "Duplicate section";
    duplicate.addEventListener("click", () => {
      const copy = {
        label: section.label || "",
        energy: section.energy || "",
        notes: section.notes || ""
      };
      parsedSongState.sections.splice(index + 1, 0, copy);
      renderSections(index + 1);
      focusNotes(index + 1);
    });

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-action";
    remove.textContent = "-";
    remove.title = "Remove section";
    remove.addEventListener("click", () => {
      parsedSongState.sections.splice(index, 1);
      const next = Math.max(0, Math.min(index, parsedSongState.sections.length - 1));
      renderSections(next);
      focusNotes(next);
    });

    row.addEventListener("click", () => {
      setActiveSection(index);
    });

    actions.append(duplicate, remove);
    row.append(grabber, dynamicWrap, label, notes, actions);
    container.appendChild(row);

    sectionRefs.push({ row, notes, dynamicWrap });
  });

  setActiveSection(selectedIndex);
}

function setSectionEnergy(index, energy) {
  if (!parsedSongState || !parsedSongState.sections[index]) return;
  parsedSongState.sections[index].energy = energy;
  renderSections(index);
}

function setActiveSection(index) {
  if (!parsedSongState || !parsedSongState.sections.length) {
    activeSectionIndex = 0;
    return;
  }
  activeSectionIndex = Math.max(0, Math.min(index, parsedSongState.sections.length - 1));
  sectionRefs.forEach((ref, idx) => {
    ref.row.classList.toggle("active", idx === activeSectionIndex);
  });
}

function focusNotes(index) {
  setActiveSection(index);
  const ref = sectionRefs[activeSectionIndex];
  if (!ref || !ref.notes) return;
  ref.notes.focus();
  const len = ref.notes.value.length;
  ref.notes.setSelectionRange(len, len);
}

function moveToNextSection() {
  if (!parsedSongState || !parsedSongState.sections.length) return;
  const next = Math.min(activeSectionIndex + 1, parsedSongState.sections.length - 1);
  focusNotes(next);
}

function moveSection(fromIndex, toIndex) {
  if (!parsedSongState) return;
  if (fromIndex === toIndex) return;
  const [moved] = parsedSongState.sections.splice(fromIndex, 1);
  parsedSongState.sections.splice(toIndex, 0, moved);

  if (activeSectionIndex === fromIndex) {
    activeSectionIndex = toIndex;
  } else if (fromIndex < activeSectionIndex && toIndex >= activeSectionIndex) {
    activeSectionIndex -= 1;
  } else if (fromIndex > activeSectionIndex && toIndex <= activeSectionIndex) {
    activeSectionIndex += 1;
  }

  renderSections(activeSectionIndex);
  focusNotes(activeSectionIndex);
}

function startSectionPointerDrag(event, index, row, container) {
  if (event.button !== 0 || !parsedSongState) return;
  event.preventDefault();
  const rowRectAtStart = row.getBoundingClientRect();
  const startY = event.clientY;
  let reorderArmed = false;
  const offsetY = event.clientY - rowRectAtStart.top;

  const ghost = row.cloneNode(true);
  ghost.classList.remove("drag-source");
  ghost.classList.add("section-drag-ghost");
  ghost.style.width = `${rowRectAtStart.width}px`;
  document.body.appendChild(ghost);
  row.classList.add("drag-source");
  moveSectionGhost(ghost, event.clientX, event.clientY, offsetY);

  const onMouseMove = (moveEvent) => {
    moveSectionGhost(ghost, moveEvent.clientX, moveEvent.clientY, offsetY);

    if (!reorderArmed) {
      const dy = moveEvent.clientY - startY;
      if (Math.abs(dy) < DRAG_START_THRESHOLD_PX) return;
      reorderArmed = true;
    }

    const rows = Array.from(container.querySelectorAll(".section-row:not(.drag-source)"));
    const target = rows.find((entry) => {
      const rect = entry.getBoundingClientRect();
      return moveEvent.clientY < rect.top + rect.height / 2;
    });
    if (target) {
      container.insertBefore(row, target);
    } else {
      container.appendChild(row);
    }
  };

  const onMouseUp = () => {
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
    ghost.remove();

    row.classList.remove("drag-source");
    const rows = Array.from(container.querySelectorAll(".section-row"));
    const dropIndex = rows.indexOf(row);

    if (reorderArmed && dropIndex >= 0) {
      moveSection(index, dropIndex);
    } else {
      renderSections(activeSectionIndex);
    }
  };

  document.addEventListener("mousemove", onMouseMove);
  document.addEventListener("mouseup", onMouseUp);
}

function moveSectionGhost(ghost, clientX, clientY, offsetY) {
  ghost.style.left = `${clientX}px`;
  ghost.style.top = `${clientY - offsetY}px`;
}

function persistParsedSong() {
  const title = (parsedSongState.song.title || "").trim();
  const artist = (parsedSongState.song.artist || "").trim();
  const arrangementName = (parsedSongState.arrangement.name || "").trim();

  let song = parsedSongState.song.id
    ? songLibrary.find((entry) => entry.id === parsedSongState.song.id)
    : null;

  if (!song) {
    song = songLibrary.find(
      (entry) => normalizeText(entry.title) === normalizeText(title) && normalizeText(entry.artist || "") === normalizeText(artist)
    );
  }

  if (!song) {
    song = {
      id: createId("song"),
      title,
      artist,
      arrangements: []
    };
    songLibrary.push(song);
  }

  song.title = title;
  song.artist = artist;

  let arrangement = parsedSongState.arrangement.id
    ? song.arrangements.find((entry) => entry.id === parsedSongState.arrangement.id)
    : null;

  if (!arrangement) {
    arrangement = {
      id: createId("arr"),
      name: arrangementName,
      key: "",
      bpm: "",
      sections: []
    };
    song.arrangements.push(arrangement);
  }

  arrangement.name = arrangementName;
  arrangement.key = (parsedSongState.arrangement.key || "").trim();
  arrangement.bpm = String(parsedSongState.arrangement.bpm || "").trim();
  arrangement.sections = normalizeSections(parsedSongState.sections);

  parsedSongState.song.id = song.id;
  parsedSongState.arrangement.id = arrangement.id;
}

function renderLibraryList() {
  const list = document.getElementById("library-list");
  const count = document.getElementById("library-count");
  list.innerHTML = "";

  const filters = {
    title: normalizeText(document.getElementById("search-title").value),
    artist: normalizeText(document.getElementById("search-artist").value),
    arrangement: normalizeText(document.getElementById("search-arrangement").value),
    key: normalizeText(document.getElementById("search-key").value),
    bpm: normalizeText(document.getElementById("search-bpm").value)
  };

  const results = songLibrary
    .map((song) => {
      const titleMatch = !filters.title || normalizeText(song.title).includes(filters.title);
      const artistMatch = !filters.artist || normalizeText(song.artist || "").includes(filters.artist);
      if (!titleMatch || !artistMatch) return null;

      const arrangements = (song.arrangements || []).filter((arr) => {
        const a = !filters.arrangement || normalizeText(arr.name || "").includes(filters.arrangement);
        const k = !filters.key || normalizeText(arr.key || "").includes(filters.key);
        const b = !filters.bpm || normalizeText(String(arr.bpm || "")).includes(filters.bpm);
        return a && k && b;
      });

      if (!arrangements.length) return null;
      return { ...song, arrangements };
    })
    .filter(Boolean);

  count.textContent = `${results.length} song${results.length === 1 ? "" : "s"}`;

  if (!results.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No songs match filters.";
    list.appendChild(empty);
    return;
  }

  results.forEach((song) => {
    const stack = document.createElement("div");
    stack.className = "saved-song-stack";

    const top = document.createElement("div");
    top.className = "saved-song-row song-toggle-row";
    top.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      if (expandedSongIds.has(song.id)) {
        expandedSongIds.delete(song.id);
      } else {
        expandedSongIds.add(song.id);
      }
      renderLibraryList();
    });

    const info = document.createElement("div");
    info.className = "saved-song-info";
    const chevron = expandedSongIds.has(song.id) ? "▾" : "▸";
    info.innerHTML = `<strong>${chevron} ${escapeHtml(song.title)}</strong><span>${escapeHtml(song.artist || "No artist")}</span>`;

    const topActions = document.createElement("div");
    topActions.className = "section-actions";

    const newArr = document.createElement("button");
    newArr.type = "button";
    newArr.textContent = "New Arrangement";
    newArr.addEventListener("click", () => {
      parsedSongState = {
        song: { id: song.id, title: song.title || "", artist: song.artist || "" },
        arrangement: { id: "", name: suggestArrangementName(song), key: "", bpm: "" },
        sections: defaultSectionsStub()
      };
      syncEditorInputs();
      renderSections(0);
      activateTab("new-song");
    });

    const deleteSong = document.createElement("button");
    deleteSong.type = "button";
    deleteSong.textContent = "Delete Song";
    deleteSong.addEventListener("click", () => {
      songLibrary = songLibrary.filter((entry) => entry.id !== song.id);
      setlistState.items = setlistState.items.filter((item) => item.songId !== song.id);
      saveSongLibrary();
      renderLibraryList();
      renderSetlistUI();
    });

    topActions.append(newArr, deleteSong);
    top.append(info, topActions);
    stack.appendChild(top);

    const arrangementsWrap = document.createElement("div");
    arrangementsWrap.className = "arrangements-wrap";
    arrangementsWrap.hidden = !expandedSongIds.has(song.id);

    song.arrangements.forEach((arr) => {
      const row = document.createElement("div");
      row.className = "arrangement-row";

      const aInfo = document.createElement("div");
      aInfo.className = "saved-song-info";
      aInfo.innerHTML = `<strong>${escapeHtml(arr.name || "Arrangement")}</strong><span>${escapeHtml(arr.key || "")}${arr.bpm ? ` - ${escapeHtml(arr.bpm)} BPM` : ""}</span>`;

      const actions = document.createElement("div");
      actions.className = "section-actions";

      const edit = document.createElement("button");
      edit.type = "button";
      edit.textContent = "Edit";
      edit.addEventListener("click", () => {
        loadArrangement(song.id, arr.id);
        activateTab("new-song");
      });

      const duplicate = document.createElement("button");
      duplicate.type = "button";
      duplicate.textContent = "Duplicate";
      duplicate.addEventListener("click", () => {
        const clone = {
          id: createId("arr"),
          name: `${arr.name || "Arrangement"} Copy`,
          key: arr.key || "",
          bpm: String(arr.bpm || ""),
          sections: normalizeSections(arr.sections)
        };
        const targetSong = songLibrary.find((entry) => entry.id === song.id);
        if (!targetSong) return;
        targetSong.arrangements.push(clone);
        saveSongLibrary();
        renderLibraryList();
        renderSetlistUI();
      });

      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "Delete";
      del.addEventListener("click", () => {
        deleteArrangement(song.id, arr.id);
      });

      actions.append(edit, duplicate, del);
      row.append(aInfo, actions);
      arrangementsWrap.appendChild(row);
    });

    stack.appendChild(arrangementsWrap);
    list.appendChild(stack);
  });
}

function loadArrangement(songId, arrangementId) {
  const song = songLibrary.find((entry) => entry.id === songId);
  if (!song) return;
  const arrangement = (song.arrangements || []).find((entry) => entry.id === arrangementId);
  if (!arrangement) return;

  parsedSongState = {
    song: { id: song.id, title: song.title || "", artist: song.artist || "" },
    arrangement: {
      id: arrangement.id,
      name: arrangement.name || "",
      key: arrangement.key || "",
      bpm: String(arrangement.bpm || "")
    },
    sections: normalizeSections(arrangement.sections)
  };

  syncEditorInputs();
  renderSections(0);
}

function deleteArrangement(songId, arrangementId) {
  const song = songLibrary.find((entry) => entry.id === songId);
  if (!song) return;

  song.arrangements = (song.arrangements || []).filter((entry) => entry.id !== arrangementId);
  setlistState.items = setlistState.items.filter((item) => !(item.songId === songId && item.arrangementId === arrangementId));

  if (!song.arrangements.length) {
    songLibrary = songLibrary.filter((entry) => entry.id !== songId);
  }

  saveSongLibrary();
  renderLibraryList();
  renderSetlistUI();
}

function renderSetlistUI() {
  const picker = document.getElementById("setlist-song-picker");
  const items = document.getElementById("setlist-items");
  const search = normalizeText(document.getElementById("setlist-search").value);

  picker.innerHTML = "";
  items.innerHTML = "";

  const candidateArrangements = [];
  songLibrary.forEach((song) => {
    (song.arrangements || []).forEach((arr) => {
      const searchable = normalizeText(`${song.title} ${song.artist || ""} ${arr.name || ""} ${arr.key || ""} ${arr.bpm || ""}`);
      if (search && !searchable.includes(search)) return;
      candidateArrangements.push({ song, arrangement: arr });
    });
  });

  if (!candidateArrangements.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No arrangements match search.";
    picker.appendChild(empty);
  } else {
    candidateArrangements.forEach(({ song, arrangement }) => {
      const row = document.createElement("div");
      row.className = "saved-song-row";

      const info = document.createElement("div");
      info.className = "saved-song-info";
      const subtitle = `${song.artist || ""} | ${arrangement.name || "Arrangement"}${arrangement.key ? ` - ${arrangement.key}` : ""}${arrangement.bpm ? ` - ${arrangement.bpm} BPM` : ""}`;
      info.innerHTML = `<strong>${escapeHtml(song.title || "Untitled")}</strong><span>${escapeHtml(subtitle)}</span>`;

      const add = document.createElement("button");
      add.type = "button";
      add.textContent = "Add";
      add.addEventListener("click", () => {
        setlistState.items.push({ songId: song.id, arrangementId: arrangement.id });
        renderSetlistUI();
      });

      row.append(info, add);
      picker.appendChild(row);
    });
  }

  if (!setlistState.items.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No songs in setlist yet.";
    items.appendChild(empty);
    return;
  }

  setlistState.items.forEach((item, index) => {
    const resolved = resolveSetlistItem(item);
    if (!resolved) return;

    const row = document.createElement("div");
    row.className = "setlist-edit-row";

    const grabber = document.createElement("button");
    grabber.type = "button";
    grabber.className = "grabber";
    grabber.textContent = "⋮⋮";
    grabber.title = "Drag to reorder";
    grabber.addEventListener("mousedown", (event) => startSetlistPointerDrag(event, index, row, items));

    const info = document.createElement("div");
    info.className = "saved-song-info";
    const subtitle = `${resolved.arrangement.name || "Arrangement"}${resolved.arrangement.key ? ` - ${resolved.arrangement.key}` : ""}${resolved.arrangement.bpm ? ` - ${resolved.arrangement.bpm} BPM` : ""}`;
    info.innerHTML = `<strong>${index + 1}. ${escapeHtml(resolved.song.title || "Untitled")}</strong><span>${escapeHtml(subtitle)}</span>`;

    const actions = document.createElement("div");
    actions.className = "section-actions hover-actions";

    const duplicate = document.createElement("button");
    duplicate.type = "button";
    duplicate.className = "icon-action";
    duplicate.textContent = "+";
    duplicate.title = "Duplicate setlist item";
    duplicate.addEventListener("click", () => {
      const copy = { ...item };
      setlistState.items.splice(index + 1, 0, copy);
      renderSetlistUI();
    });

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-action";
    remove.textContent = "-";
    remove.title = "Remove setlist item";
    remove.addEventListener("click", () => {
      setlistState.items.splice(index, 1);
      renderSetlistUI();
    });

    actions.append(duplicate, remove);
    row.append(grabber, info, actions);
    items.appendChild(row);
  });
}

function resolveSetlistItem(item) {
  const song = songLibrary.find((entry) => entry.id === item.songId);
  if (!song) return null;
  const arrangement = (song.arrangements || []).find((entry) => entry.id === item.arrangementId);
  if (!arrangement) return null;
  return { song, arrangement };
}

function startSetlistPointerDrag(event, index, row, container) {
  if (event.button !== 0) return;
  event.preventDefault();
  const rowRectAtStart = row.getBoundingClientRect();
  const startY = event.clientY;
  let reorderArmed = false;
  const offsetY = event.clientY - rowRectAtStart.top;

  const ghost = row.cloneNode(true);
  ghost.classList.remove("drag-source");
  ghost.classList.add("setlist-drag-ghost");
  ghost.style.width = `${rowRectAtStart.width}px`;
  document.body.appendChild(ghost);
  row.classList.add("drag-source");
  moveSetlistGhost(ghost, event.clientX, event.clientY, offsetY);

  const onMouseMove = (moveEvent) => {
    moveSetlistGhost(ghost, moveEvent.clientX, moveEvent.clientY, offsetY);

    if (!reorderArmed) {
      const dy = moveEvent.clientY - startY;
      if (Math.abs(dy) < DRAG_START_THRESHOLD_PX) return;
      reorderArmed = true;
    }

    const rows = Array.from(container.querySelectorAll(".setlist-edit-row:not(.drag-source)"));
    const target = rows.find((entry) => {
      const rect = entry.getBoundingClientRect();
      return moveEvent.clientY < rect.top + rect.height / 2;
    });
    if (target) {
      container.insertBefore(row, target);
    } else {
      container.appendChild(row);
    }
  };

  const onMouseUp = () => {
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
    ghost.remove();

    row.classList.remove("drag-source");
    const rows = Array.from(container.querySelectorAll(".setlist-edit-row"));
    const dropIndex = rows.indexOf(row);

    if (!reorderArmed || dropIndex < 0 || dropIndex === index) {
      renderSetlistUI();
      return;
    }

    const copy = [...setlistState.items];
    const [moved] = copy.splice(index, 1);
    copy.splice(dropIndex, 0, moved);
    setlistState.items = copy;
    renderSetlistUI();
  };

  document.addEventListener("mousemove", onMouseMove);
  document.addEventListener("mouseup", onMouseUp);
}

function moveSetlistGhost(ghost, clientX, clientY, offsetY) {
  ghost.style.left = `${clientX}px`;
  ghost.style.top = `${clientY - offsetY}px`;
}

function generatePrepSheetModel() {
  const resolved = setlistState.items.map(resolveSetlistItem).filter(Boolean);
  if (!resolved.length) {
    const filename = buildExportFilename([]);
    return { lines: [], text: "", filename };
  }

  const lines = [`Prep Sheet ${formatLongDate(setlistState.date)}`];
  const titles = [];

  resolved.forEach(({ song, arrangement }) => {
    titles.push(song.title || "Untitled");
    const key = arrangement.key ? ` [${arrangement.key}]` : "";
    const bpm = arrangement.bpm ? ` - ${arrangement.bpm}BPM` : "";
    lines.push(`${song.title}${key}${bpm}`);

    (arrangement.sections || []).forEach((section) => {
      const symbol = energyToSymbol(section.energy);
      const label = section.label || "Section";
      const notes = section.notes ? ` - ${section.notes}` : " - ";
      lines.push(`${symbol}${label}${notes}`);
    });

    lines.push("");
  });

  const cleaned = trimTrailingBlank(lines);
  const filename = buildExportFilename(titles);
  return {
    lines: cleaned,
    text: cleaned.join("\n"),
    filename
  };
}

function buildExportFilename(songTitles) {
  const stamp = formatFilenameDate(setlistState.date);
  const chunks = songTitles.map(sanitizeFilenameChunk).filter(Boolean);
  return `${stamp} Prep Sheet${chunks.length ? `_${chunks.join("_")}` : ""}.docx`;
}

function formatFilenameDate(dateValue) {
  const [year, month, day] = (dateValue || todayDateString()).split("-");
  return `${month || "01"}${day || "01"}${year || "2000"}`;
}

function energyToSymbol(value) {
  if (value === "up") return "↑";
  if (value === "build") return "↗";
  if (value === "steady") return "→";
  if (value === "down") return "↓";
  return "";
}

function arrowToEnergy(key) {
  if (key === "ArrowUp") return "up";
  if (key === "ArrowLeft") return "build";
  if (key === "ArrowRight") return "steady";
  if (key === "ArrowDown") return "down";
  return "";
}

function formatLongDate(dateValue) {
  const date = toDate(dateValue || todayDateString());
  if (!date) return "";
  return date.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

function toDate(raw) {
  const [year, month, day] = String(raw || "").split("-").map((v) => Number(v));
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
}

function defaultSectionsStub() {
  return ["Intro", "V1", "Pre 1", "C1", "V2", "Pre 2", "C2", "Bridge", "C3", "Tag", "END"].map((label) => ({
    label,
    energy: "",
    notes: ""
  }));
}

function suggestArrangementName(song) {
  const count = (song.arrangements || []).length;
  if (count === 0) return "Main";
  return `Arrangement ${count + 1}`;
}

function syncEditorInputs() {
  if (!parsedSongState) return;
  document.getElementById("song-stub").hidden = false;
  document.getElementById("stub-title").value = parsedSongState.song.title || "";
  document.getElementById("stub-artist").value = parsedSongState.song.artist || "";
  document.getElementById("stub-arrangement").value = parsedSongState.arrangement.name || "";
  document.getElementById("stub-key").value = parsedSongState.arrangement.key || "";
  document.getElementById("stub-bpm").value = parsedSongState.arrangement.bpm || "";
}

function normalizeSections(sections) {
  if (!Array.isArray(sections)) return [];
  return sections.map((section) => ({
    label: String(section?.label || ""),
    energy: section?.energy || "",
    notes: String(section?.notes || "")
  }));
}

function loadSongLibrary() {
  const existing = parseJsonArray(localStorage.getItem(LIBRARY_STORAGE_KEY));
  if (existing) return existing;

  for (const key of LEGACY_KEYS) {
    const legacy = parseJsonArray(localStorage.getItem(key));
    if (!legacy) continue;
    const migrated = migrateLegacyLibrary(legacy);
    localStorage.setItem(LIBRARY_STORAGE_KEY, JSON.stringify(migrated));
    return migrated;
  }

  return [];
}

function saveSongLibrary() {
  localStorage.setItem(LIBRARY_STORAGE_KEY, JSON.stringify(songLibrary));
}

function loadSavedSetlists() {
  const parsed = parseJsonArray(localStorage.getItem(SETLISTS_STORAGE_KEY));
  if (!parsed) return [];

  return parsed
    .map((item) => ({
      id: item.id || createId("set"),
      date: item.date || todayDateString(),
      name: item.name || formatLongDate(item.date || todayDateString()),
      items: Array.isArray(item.items) ? item.items : []
    }))
    .sort((a, b) => String(b.date).localeCompare(String(a.date)));
}

function saveSavedSetlists() {
  localStorage.setItem(SETLISTS_STORAGE_KEY, JSON.stringify(savedSetlists));
}

function migrateLegacyLibrary(legacy) {
  return legacy
    .map((song) => {
      if (Array.isArray(song.arrangements)) {
        return {
          id: song.id || createId("song"),
          title: song.title || "Untitled Song",
          artist: song.artist || "",
          arrangements: song.arrangements.map((arr) => ({
            id: arr.id || createId("arr"),
            name: arr.name || "Main",
            key: arr.key || "",
            bpm: String(arr.bpm || ""),
            sections: normalizeSections(arr.sections)
          }))
        };
      }

      return {
        id: song.id || createId("song"),
        title: song.title || "Untitled Song",
        artist: song.artist || "",
        arrangements: [
          {
            id: createId("arr"),
            name: "Main",
            key: song.key || "",
            bpm: String(song.bpm || ""),
            sections: normalizeSections(song.sections)
          }
        ]
      };
    })
    .filter(Boolean);
}

function parseJsonArray(raw) {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function trimTrailingBlank(lines) {
  const copy = [...lines];
  while (copy.length && !copy[copy.length - 1].trim()) copy.pop();
  return copy;
}

function createId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

function todayDateString() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function sanitizeFilenameChunk(value) {
  return String(value || "")
    .replaceAll("&", "and")
    .replace(/[^A-Za-z0-9 ]+/g, "")
    .trim()
    .replace(/\s+/g, " ")
    .slice(0, 40)
    .replaceAll(" ", "_");
}

function normalizeText(value) {
  return String(value || "").trim().toLowerCase();
}

function isNewSongActive() {
  const panel = document.getElementById("new-song");
  return !!panel && !panel.hidden;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
