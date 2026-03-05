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

let songLibrary = [];
let savedSetlists = [];
let parsedSongState = null;
let setlistState = {
  id: null,
  date: todayDateString(),
  name: "",
  items: []
};
let sectionRefs = [];
let activeSectionIndex = 0;
let autoServiceName = true;
const expandedSongIds = new Set();

async function init() {
  const cfg = window.PREPPY_CONFIG || {};
  if (cfg.dbEnabled) {
    try {
      [songLibrary, savedSetlists] = await Promise.all([
        apiFetch("/api/songs").then((r) => r.json()),
        apiFetch("/api/setlists").then((r) => r.json()),
      ]);
    } catch (e) {
      console.error("Failed to load data from server:", e);
      songLibrary = [];
      savedSetlists = [];
    }
  } else {
    songLibrary = loadSongLibrary();
    savedSetlists = loadSavedSetlists();
  }

  wireTabs();
  wireLibraryEditor();
  wireSetlistBuilder();
  initPcoSongSearch();
  renderSectionPalette();
  renderLibraryList();
  renderSetlistUI();
  renderSetlistHistory();

  // Show migration card if server library is empty but localStorage has data
  if (cfg.dbEnabled && songLibrary.length === 0) {
    const lsData = parseJsonArray(localStorage.getItem(LIBRARY_STORAGE_KEY));
    if (lsData && lsData.length > 0) {
      const card = document.getElementById("migrate-card");
      if (card) card.style.display = "";
    }
  }
}

init();

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function apiFetch(url, options = {}) {
  const resp = await fetch(url, options);
  if (resp.status === 401) {
    window.location.href = "/auth/pco";
    throw new Error("Not authenticated");
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${resp.status}`);
  }
  return resp;
}

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

  saveSongButton.addEventListener("click", async () => {
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

    saveSongButton.disabled = true;
    status.textContent = "Saving...";
    status.classList.remove("ok");
    try {
      await persistParsedSong();
      renderLibraryList();
      renderSetlistUI();
      status.textContent = `Saved ${title} (${arrangementName})`;
      status.classList.add("ok");
    } catch (e) {
      status.textContent = `Save failed: ${e.message}`;
      status.classList.remove("ok");
    } finally {
      saveSongButton.disabled = false;
    }
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

  saveCurrent.addEventListener("click", async () => {
    const status = document.getElementById("setlist-status");
    saveCurrent.disabled = true;
    status.textContent = "Saving...";
    status.classList.remove("ok");
    try {
      await saveCurrentSetlistSnapshot({ forceNew: true });
      renderSetlistQuickPick();
      renderSetlistHistory();
      status.textContent = "Setlist saved.";
      status.classList.add("ok");
    } catch (e) {
      status.textContent = `Save failed: ${e.message}`;
    } finally {
      saveCurrent.disabled = false;
    }
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

  recallDelete.addEventListener("click", async () => {
    const id = recallSelect.value;
    if (!id) return;
    const status = document.getElementById("setlist-status");
    recallDelete.disabled = true;
    try {
      const deleted = await deleteSetlistById(id);
      if (!deleted) {
        status.textContent = "Could not delete selected setlist.";
        status.classList.remove("ok");
        return;
      }
      status.textContent = "Setlist deleted.";
      status.classList.add("ok");
    } catch (e) {
      status.textContent = `Delete failed: ${e.message}`;
    } finally {
      recallDelete.disabled = false;
    }
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

  // PCO import button
  const pcoImportBtn = document.getElementById("pco-import-btn");
  if (pcoImportBtn) {
    pcoImportBtn.addEventListener("click", () => openPcoImportModal());
  }

  // localStorage migration button
  const migrateBtn = document.getElementById("migrate-btn");
  if (migrateBtn) {
    migrateBtn.addEventListener("click", async () => {
      const migrateStatus = document.getElementById("migrate-status");
      const songLibraryData = parseJsonArray(localStorage.getItem(LIBRARY_STORAGE_KEY));
      const setlistsData = parseJsonArray(localStorage.getItem(SETLISTS_STORAGE_KEY));
      if (!songLibraryData && !setlistsData) {
        migrateStatus.textContent = "No localStorage data found.";
        return;
      }
      migrateBtn.disabled = true;
      migrateStatus.textContent = "Importing...";
      try {
        const resp = await apiFetch("/api/migrate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ songLibrary: songLibraryData || [], savedSetlists: setlistsData || [] }),
        });
        const result = await resp.json();
        migrateStatus.textContent = `Imported ${result.importedSongs} songs and ${result.importedSetlists} setlists.`;
        migrateStatus.classList.add("ok");
        // Reload data from server
        [songLibrary, savedSetlists] = await Promise.all([
          apiFetch("/api/songs").then((r) => r.json()),
          apiFetch("/api/setlists").then((r) => r.json()),
        ]);
        renderLibraryList();
        renderSetlistUI();
        renderSetlistHistory();
        renderSetlistQuickPick();
        document.getElementById("migrate-card").style.display = "none";
      } catch (e) {
        migrateStatus.textContent = `Import failed: ${e.message}`;
      } finally {
        migrateBtn.disabled = false;
      }
    });
  }

  // PCO modal close button
  const pcoModalClose = document.getElementById("pco-modal-close");
  if (pcoModalClose) {
    pcoModalClose.addEventListener("click", closePcoImportModal);
  }
  const pcoModalBackdrop = document.querySelector("#pco-modal .modal-backdrop");
  if (pcoModalBackdrop) {
    pcoModalBackdrop.addEventListener("click", closePcoImportModal);
  }
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

async function saveCurrentSetlistSnapshot(options = {}) {
  const cfg = window.PREPPY_CONFIG || {};
  const forceNew = Boolean(options.forceNew);
  const snapshot = {
    date: setlistState.date || todayDateString(),
    name: (setlistState.name || formatLongDate(setlistState.date || todayDateString())).trim(),
    items: setlistState.items.map((item) => {
      if (item.itemType === "header" || item.itemType === "item") {
        return { itemType: item.itemType, label: item.label || "" };
      }
      return { arrangementId: item.arrangementId };
    }),
  };

  if (cfg.dbEnabled) {
    try {
      if (!forceNew && setlistState.id) {
        await apiFetch(`/api/setlists/${setlistState.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(snapshot),
        });
        // Update in-memory — preserve PCO fields from existing entry
        const idx = savedSetlists.findIndex((e) => e.id === setlistState.id);
        const existing = idx >= 0 ? savedSetlists[idx] : {};
        const updated = {
          ...existing,
          id: setlistState.id,
          ...snapshot,
          items: [...setlistState.items],
        };
        if (idx >= 0) savedSetlists[idx] = updated;
        else savedSetlists.push(updated);
      } else {
        const resp = await apiFetch("/api/setlists", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(snapshot),
        });
        const { id: newId } = await resp.json();
        setlistState.id = newId;
        savedSetlists.push({ id: newId, ...snapshot, items: [...setlistState.items] });
      }
    } catch (e) {
      throw e;
    }
  } else {
    const snapshotId = forceNew ? createId("set") : (setlistState.id || createId("set"));
    const localSnapshot = { id: snapshotId, date: snapshot.date, name: snapshot.name, items: [...setlistState.items] };
    if (forceNew) {
      savedSetlists.push(localSnapshot);
      setlistState.id = localSnapshot.id;
    } else {
      setlistState.id = localSnapshot.id;
      const existingIndex = savedSetlists.findIndex((entry) => entry.id === localSnapshot.id);
      if (existingIndex >= 0) savedSetlists[existingIndex] = localSnapshot;
      else savedSetlists.push(localSnapshot);
    }
    saveSavedSetlists();
  }

  savedSetlists.sort((a, b) => String(b.date).localeCompare(String(a.date)));
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
    const songCount = entry.items.filter((i) => !i.itemType || i.itemType === "song").length;
    info.innerHTML = `<strong>${escapeHtml(entry.name || formatLongDate(entry.date))}</strong><span>${escapeHtml(formatLongDate(entry.date))} - ${songCount} song${songCount !== 1 ? "s" : ""}</span>`;

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
    del.addEventListener("click", async () => {
      await deleteSetlistById(entry.id);
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
    const qpSongCount = entry.items.filter((i) => !i.itemType || i.itemType === "song").length;
    option.textContent = `${formatLongDate(entry.date)} - ${entry.name || formatLongDate(entry.date)} (${qpSongCount})`;
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
  const entry = savedSetlists.find((item) => item.id == id);
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
  renderPcoSetlistActions();
  return true;
}

async function deleteSetlistById(id) {
  const cfg = window.PREPPY_CONFIG || {};
  const before = savedSetlists.length;
  savedSetlists = savedSetlists.filter((item) => item.id !== id);
  if (savedSetlists.length === before) return false;

  if (cfg.dbEnabled) {
    await apiFetch(`/api/setlists/${id}`, { method: "DELETE" });
  } else {
    saveSavedSetlists();
  }

  if (setlistState.id === id) {
    setlistState.id = null;
  }

  renderSetlistQuickPick();
  renderSetlistHistory();
  return true;
}

// ---------------------------------------------------------------------------
// PCO Import modal
// ---------------------------------------------------------------------------

function openPcoImportModal() {
  const modal = document.getElementById("pco-modal");
  if (!modal) return;
  modal.hidden = false;
  loadPcoPlans();
}

function closePcoImportModal() {
  const modal = document.getElementById("pco-modal");
  if (modal) modal.hidden = true;
}

async function loadPcoPlans() {
  const body = document.getElementById("pco-modal-body");
  if (!body) return;
  body.innerHTML = "<p class='hint'>Loading upcoming plans...</p>";
  try {
    const resp = await apiFetch("/api/pco/plans");
    const plans = await resp.json();
    if (!plans.length) {
      body.innerHTML = "<p class='hint'>No upcoming plans found in Planning Center.</p>";
      return;
    }
    body.innerHTML = "";
    const list = document.createElement("div");
    list.className = "saved-song-list";
    plans.forEach((plan) => {
      const row = document.createElement("div");
      row.className = "saved-song-row";
      const info = document.createElement("div");
      info.className = "saved-song-info";
      info.innerHTML = `<strong>${escapeHtml(plan.date || "")}: ${escapeHtml(plan.title || plan.serviceTypeName)}</strong><span>${escapeHtml(plan.serviceTypeName)} &mdash; ${plan.itemCount} items</span>`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Import";
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "Importing...";
        try {
          const importResp = await apiFetch(`/api/pco/import/${plan.id}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ serviceTypeId: plan.serviceTypeId, date: plan.date, title: plan.title }),
          });
          const { setlistId } = await importResp.json();
          // Reload data from server
          [songLibrary, savedSetlists] = await Promise.all([
            apiFetch("/api/songs").then((r) => r.json()),
            apiFetch("/api/setlists").then((r) => r.json()),
          ]);
          closePcoImportModal();
          // Load the newly imported setlist
          loadSetlistById(setlistId);
          activateTab("setlists");
          renderLibraryList();
          renderSetlistUI();
          renderSetlistHistory();
          renderSetlistQuickPick();
        } catch (e) {
          btn.textContent = "Import";
          btn.disabled = false;
          alert(`Import failed: ${e.message}`);
        }
      });
      row.append(info, btn);
      list.appendChild(row);
    });
    body.appendChild(list);
  } catch (e) {
    body.innerHTML = `<p class='hint'>Failed to load plans: ${escapeHtml(e.message)}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Phase 4: PCO Song Library Search
// ---------------------------------------------------------------------------

let pcoSongSearchTimer = null;

function openPcoSongSearchModal() {
  const modal = document.getElementById("pco-song-search-modal");
  if (!modal) return;
  modal.hidden = false;
  const input = document.getElementById("pco-song-search-input");
  if (input) { input.value = ""; input.focus(); }
  document.getElementById("pco-song-search-results").innerHTML =
    "<p class='hint'>Type to search Planning Center songs...</p>";
}

function closePcoSongSearchModal() {
  const modal = document.getElementById("pco-song-search-modal");
  if (modal) modal.hidden = true;
}

function initPcoSongSearch() {
  const input = document.getElementById("pco-song-search-input");
  const closeBtn = document.getElementById("pco-song-search-close");
  const searchBtn = document.getElementById("pco-search-btn");
  if (searchBtn) searchBtn.addEventListener("click", () => openPcoSongSearchModal());
  if (closeBtn) closeBtn.addEventListener("click", () => closePcoSongSearchModal());

  const backdrop = document.querySelector("#pco-song-search-modal .modal-backdrop");
  if (backdrop) backdrop.addEventListener("click", () => closePcoSongSearchModal());

  if (input) {
    input.addEventListener("input", () => {
      clearTimeout(pcoSongSearchTimer);
      const q = input.value.trim();
      if (q.length < 2) {
        document.getElementById("pco-song-search-results").innerHTML =
          "<p class='hint'>Type at least 2 characters...</p>";
        return;
      }
      pcoSongSearchTimer = setTimeout(() => searchPcoSongs(q), 350);
    });
  }
}

async function searchPcoSongs(query) {
  const results = document.getElementById("pco-song-search-results");
  results.innerHTML = "<p class='hint'>Searching...</p>";
  try {
    const resp = await apiFetch(`/api/pco/songs?q=${encodeURIComponent(query)}`);
    const songs = await resp.json();
    if (!songs.length) {
      results.innerHTML = "<p class='hint'>No songs found.</p>";
      return;
    }
    results.innerHTML = "";
    const list = document.createElement("div");
    list.className = "saved-song-list";
    songs.forEach((song) => {
      const row = document.createElement("div");
      row.className = "saved-song-row pco-song-result";

      const info = document.createElement("div");
      info.className = "saved-song-info";
      info.innerHTML = `<strong>${escapeHtml(song.title)}</strong><span>${escapeHtml(song.author || "")} &mdash; ${song.arrangementCount} arrangement(s)</span>`;
      info.style.cursor = "pointer";

      const importBtn = document.createElement("button");
      importBtn.type = "button";
      importBtn.textContent = "Import All";
      importBtn.addEventListener("click", async () => {
        importBtn.disabled = true;
        importBtn.textContent = "Importing...";
        try {
          await apiFetch(`/api/pco/songs/${song.pcoSongId}/import`, { method: "POST" });
          songLibrary = await apiFetch("/api/songs").then((r) => r.json());
          renderLibraryList();
          importBtn.textContent = "Imported";
        } catch (e) {
          importBtn.textContent = "Import All";
          importBtn.disabled = false;
          alert(`Import failed: ${e.message}`);
        }
      });

      const detailDiv = document.createElement("div");
      detailDiv.className = "pco-song-arrangements";
      detailDiv.hidden = true;

      info.addEventListener("click", async () => {
        if (!detailDiv.hidden) { detailDiv.hidden = true; return; }
        detailDiv.innerHTML = "<p class='hint'>Loading arrangements...</p>";
        detailDiv.hidden = false;
        try {
          const arrResp = await apiFetch(`/api/pco/songs/${song.pcoSongId}/arrangements`);
          const arrangements = await arrResp.json();
          detailDiv.innerHTML = "";
          arrangements.forEach((arr) => {
            const arrRow = document.createElement("div");
            arrRow.className = "saved-song-row";
            arrRow.style.paddingLeft = "1.5rem";
            const arrInfo = document.createElement("div");
            arrInfo.className = "saved-song-info";
            arrInfo.innerHTML = `<strong>${escapeHtml(arr.name)}</strong><span>Key: ${escapeHtml(arr.key || "—")} | BPM: ${escapeHtml(arr.bpm || "—")}</span>`;
            const arrBtn = document.createElement("button");
            arrBtn.type = "button";
            arrBtn.textContent = "Import";
            arrBtn.addEventListener("click", async () => {
              arrBtn.disabled = true;
              arrBtn.textContent = "Importing...";
              try {
                await apiFetch(`/api/pco/songs/${song.pcoSongId}/import`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ pcoArrangementId: arr.pcoArrangementId }),
                });
                songLibrary = await apiFetch("/api/songs").then((r) => r.json());
                renderLibraryList();
                arrBtn.textContent = "Imported";
              } catch (e) {
                arrBtn.textContent = "Import";
                arrBtn.disabled = false;
                alert(`Import failed: ${e.message}`);
              }
            });
            arrRow.append(arrInfo, arrBtn);
            detailDiv.appendChild(arrRow);
          });
        } catch (e) {
          detailDiv.innerHTML = `<p class='hint'>Failed: ${escapeHtml(e.message)}</p>`;
        }
      });

      row.append(info, importBtn);
      list.appendChild(row);
      list.appendChild(detailDiv);
    });
    results.appendChild(list);
  } catch (e) {
    results.innerHTML = `<p class='hint'>Search failed: ${escapeHtml(e.message)}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Phase 6: Upload Prep Sheet to PCO
// ---------------------------------------------------------------------------

async function uploadPrepSheetToPco() {
  const cfg = window.PREPPY_CONFIG || {};
  if (!cfg.dbEnabled) return;

  const currentSetlist = savedSetlists.find((s) => s.id == setlistState.id);
  if (!currentSetlist || !currentSetlist.pco_plan_id) {
    alert("This setlist was not imported from Planning Center.");
    return;
  }

  const btn = document.getElementById("pco-upload-btn");
  if (btn) { btn.disabled = true; btn.textContent = "Uploading..."; }

  try {
    // Generate the docx blob using the existing export logic
    const model = generatePrepSheetModel();
    const editedLines = getPrepOutputLines();
    const linesForExport = editedLines.length ? editedLines : model.lines;
    if (!linesForExport.length) throw new Error("No content to export. Add songs first.");

    const resp = await fetch("/api/export-docx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lines: linesForExport,
        filename: model.filename,
        header_lines: DEFAULT_EXPORT_HEADER_LINES,
      }),
    });
    if (!resp.ok) throw new Error("Failed to generate prep sheet");
    const blob = await resp.blob();

    const filename = `${setlistState.name || "Prep Sheet"}.docx`;
    const formData = new FormData();
    formData.append("file", blob, filename);
    formData.append("serviceTypeId", currentSetlist.pco_service_type_id || "");
    formData.append("filename", filename);

    const uploadResp = await apiFetch(
      `/api/pco/plans/${currentSetlist.pco_plan_id}/upload-prep-sheet`,
      { method: "POST", body: formData }
    );
    const result = await uploadResp.json();
    if (btn) { btn.textContent = "Uploaded!"; }
    setTimeout(() => { if (btn) { btn.textContent = "Upload to Planning Center"; btn.disabled = false; } }, 3000);
  } catch (e) {
    alert(`Upload failed: ${e.message}`);
    if (btn) { btn.textContent = "Upload to Planning Center"; btn.disabled = false; }
  }
}

// ---------------------------------------------------------------------------
// Phase 7: Sync with PCO
// ---------------------------------------------------------------------------

async function syncWithPco() {
  const cfg = window.PREPPY_CONFIG || {};
  if (!cfg.dbEnabled) return;

  const currentSetlist = savedSetlists.find((s) => s.id == setlistState.id);
  if (!currentSetlist || !currentSetlist.pco_plan_id) {
    alert("This setlist was not imported from Planning Center.");
    return;
  }

  const btn = document.getElementById("pco-sync-btn");
  if (btn) { btn.disabled = true; btn.textContent = "Syncing..."; }

  try {
    const resp = await apiFetch(`/api/pco/plans/${currentSetlist.pco_plan_id}/sync`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        serviceTypeId: currentSetlist.pco_service_type_id || "",
        setlistId: currentSetlist.id,
      }),
    });
    const result = await resp.json();

    // Reload data
    [songLibrary, savedSetlists] = await Promise.all([
      apiFetch("/api/songs").then((r) => r.json()),
      apiFetch("/api/setlists").then((r) => r.json()),
    ]);
    loadSetlistById(currentSetlist.id);
    renderLibraryList();

    const changes = result.changes || {};
    const summary = `Sync complete: ${changes.added || 0} added, ${changes.removed || 0} removed${changes.reordered ? ", order updated" : ""}.`;
    if (btn) { btn.textContent = summary; }
    setTimeout(() => { if (btn) { btn.textContent = "Sync with PCO"; btn.disabled = false; } }, 4000);
  } catch (e) {
    alert(`Sync failed: ${e.message}`);
    if (btn) { btn.textContent = "Sync with PCO"; btn.disabled = false; }
  }
}

function renderPcoSetlistActions() {
  const container = document.getElementById("pco-setlist-actions");
  if (!container) return;
  container.innerHTML = "";

  const cfg = window.PREPPY_CONFIG || {};
  if (!cfg.dbEnabled) return;

  const currentSetlist = savedSetlists.find((s) => s.id == setlistState.id);
  if (!currentSetlist || !currentSetlist.pco_plan_id) return;

  const uploadBtn = document.createElement("button");
  uploadBtn.type = "button";
  uploadBtn.id = "pco-upload-btn";
  uploadBtn.textContent = "Upload to Planning Center";
  uploadBtn.addEventListener("click", uploadPrepSheetToPco);

  const syncBtn = document.createElement("button");
  syncBtn.type = "button";
  syncBtn.id = "pco-sync-btn";
  syncBtn.textContent = "Sync with PCO";
  syncBtn.addEventListener("click", syncWithPco);

  container.append(syncBtn, uploadBtn);
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

async function persistParsedSong() {
  const cfg = window.PREPPY_CONFIG || {};
  const title = (parsedSongState.song.title || "").trim();
  const artist = (parsedSongState.song.artist || "").trim();
  const arrangementName = (parsedSongState.arrangement.name || "").trim();
  const sections = normalizeSections(parsedSongState.sections);
  const arrPayload = {
    name: arrangementName,
    key: (parsedSongState.arrangement.key || "").trim(),
    bpm: String(parsedSongState.arrangement.bpm || "").trim(),
    sections,
  };

  if (cfg.dbEnabled) {
    const songId = parsedSongState.song.id;
    const arrId = parsedSongState.arrangement.id;

    if (songId && arrId) {
      // Update existing song + arrangement
      await apiFetch(`/api/songs/${songId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, artist }),
      });
      await apiFetch(`/api/arrangements/${arrId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(arrPayload),
      });
      const song = songLibrary.find((s) => s.id === songId);
      if (song) {
        song.title = title;
        song.artist = artist;
        const arr = (song.arrangements || []).find((a) => a.id === arrId);
        if (arr) Object.assign(arr, arrPayload);
      }
    } else if (songId && !arrId) {
      // Existing song, new arrangement
      await apiFetch(`/api/songs/${songId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, artist }),
      });
      const resp = await apiFetch(`/api/songs/${songId}/arrangements`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(arrPayload),
      });
      const { id: newArrId } = await resp.json();
      parsedSongState.arrangement.id = newArrId;
      const song = songLibrary.find((s) => s.id === songId);
      if (song) {
        song.title = title;
        song.artist = artist;
        song.arrangements.push({ ...arrPayload, id: newArrId });
      }
    } else {
      // New song — check if it already exists by title+artist
      let existingSong = songLibrary.find(
        (s) => normalizeText(s.title) === normalizeText(title) && normalizeText(s.artist || "") === normalizeText(artist)
      );
      if (existingSong) {
        parsedSongState.song.id = existingSong.id;
        const resp = await apiFetch(`/api/songs/${existingSong.id}/arrangements`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(arrPayload),
        });
        const { id: newArrId } = await resp.json();
        parsedSongState.arrangement.id = newArrId;
        existingSong.arrangements.push({ ...arrPayload, id: newArrId });
      } else {
        const resp = await apiFetch("/api/songs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, artist, arrangements: [arrPayload] }),
        });
        const { id: newSongId } = await resp.json();
        // Reload to get proper IDs assigned by server
        const songsResp = await apiFetch("/api/songs");
        songLibrary = await songsResp.json();
        const newSong = songLibrary.find((s) => s.id === newSongId);
        if (newSong) {
          parsedSongState.song.id = newSong.id;
          parsedSongState.arrangement.id = newSong.arrangements[0]?.id;
        }
      }
    }
    return;
  }

  // localStorage fallback
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
  saveSongLibrary();
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
    deleteSong.addEventListener("click", async () => {
      const cfg = window.PREPPY_CONFIG || {};
      if (cfg.dbEnabled) {
        try {
          await apiFetch(`/api/songs/${song.id}`, { method: "DELETE" });
        } catch (e) {
          alert(`Delete failed: ${e.message}`);
          return;
        }
      }
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
      duplicate.addEventListener("click", async () => {
        const cfg = window.PREPPY_CONFIG || {};
        const clonePayload = {
          name: `${arr.name || "Arrangement"} Copy`,
          key: arr.key || "",
          bpm: String(arr.bpm || ""),
          sections: normalizeSections(arr.sections)
        };
        const targetSong = songLibrary.find((entry) => entry.id === song.id);
        if (!targetSong) return;

        if (cfg.dbEnabled) {
          try {
            const resp = await apiFetch(`/api/songs/${song.id}/arrangements`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(clonePayload),
            });
            const { id: newArrId } = await resp.json();
            targetSong.arrangements.push({ ...clonePayload, id: newArrId });
          } catch (e) {
            alert(`Duplicate failed: ${e.message}`);
            return;
          }
        } else {
          const clone = { id: createId("arr"), ...clonePayload };
          targetSong.arrangements.push(clone);
          saveSongLibrary();
        }
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

async function deleteArrangement(songId, arrangementId) {
  const cfg = window.PREPPY_CONFIG || {};
  if (cfg.dbEnabled) {
    try {
      await apiFetch(`/api/arrangements/${arrangementId}`, { method: "DELETE" });
    } catch (e) {
      alert(`Delete failed: ${e.message}`);
      return;
    }
  }

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

  let songNumber = 0;
  setlistState.items.forEach((item, index) => {
    // Header / non-song items
    if (item.itemType === "header" || item.itemType === "item") {
      const row = document.createElement("div");
      row.className = "setlist-edit-row setlist-header-row";

      const info = document.createElement("div");
      info.className = "saved-song-info";
      info.innerHTML = `<strong class="setlist-header-label">${escapeHtml(item.label || "—")}</strong>`;

      const actions = document.createElement("div");
      actions.className = "section-actions hover-actions";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-action";
      remove.textContent = "-";
      remove.title = "Remove header";
      remove.addEventListener("click", () => {
        setlistState.items.splice(index, 1);
        renderSetlistUI();
      });
      actions.append(remove);

      row.append(info, actions);
      items.appendChild(row);
      return;
    }

    const resolved = resolveSetlistItem(item);
    if (!resolved) return;
    songNumber++;

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
    info.innerHTML = `<strong>${songNumber}. ${escapeHtml(resolved.song.title || "Untitled")}</strong><span>${escapeHtml(subtitle)}</span>`;

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
  const hasContent = setlistState.items.some(
    (item) => (item.itemType === "header" || item.itemType === "item") || resolveSetlistItem(item)
  );
  if (!hasContent) {
    const filename = buildExportFilename([]);
    return { lines: [], text: "", filename };
  }

  const lines = [`Prep Sheet ${formatLongDate(setlistState.date)}`];
  const titles = [];

  setlistState.items.forEach((item) => {
    if (item.itemType === "header" || item.itemType === "item") {
      lines.push(`--- ${item.label || ""} ---`);
      return;
    }
    const resolved = resolveSetlistItem(item);
    if (!resolved) return;
    const { song, arrangement } = resolved;
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
