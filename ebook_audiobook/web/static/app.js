const _SVG = (d, extra = "") =>
  `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const ICON = {
  folder: _SVG('<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h3.6l2 2.5h9.4A1.5 1.5 0 0 1 21 10v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18z"/>'),
  file: _SVG('<path d="M13.5 3H7a1.5 1.5 0 0 0-1.5 1.5v15A1.5 1.5 0 0 0 7 21h10a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M13.5 3v5H18.5"/>'),
  up: _SVG('<path d="M12 19V6m0 0-5.5 5.5M12 6l5.5 5.5"/>'),
  blocked: _SVG('<circle cx="12" cy="12" r="8.5"/><path d="m6.5 6.5 11 11"/>'),
  close: _SVG('<path d="M7 7l10 10M17 7 7 17"/>'),
  check: _SVG('<path d="m5 12.5 4.5 4.5L19 7" stroke-width="2.4"/>'),
};

function baseName(p) {
  const parts = String(p || "").split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : p;
}

// Shared helpers for the ebook-audiobook UI. No framework — plain fetch + DOM.

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function postForm(url, data) {
  const body = new URLSearchParams(data || {});
  const r = await fetch(url, { method: "POST", body });
  if (!r.ok) throw new Error(await r.text());
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}
async function postJSON(url, obj) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(obj || {}),
  });
  if (!r.ok) throw new Error(await r.text());
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

// Open one of the app's folders in the system file manager.
// Takes the same names the /reveal route understands — never a path, so there is
// nothing here that could be pointed somewhere it shouldn't go.
async function revealFolder(params) {
  try {
    const r = await fetch("/reveal", { method: "POST", body: new URLSearchParams(params) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) { toast(d.error || "Couldn't open that folder"); return; }
  } catch (e) { toast("Couldn't open that folder"); }
}

// Run an async action with the button showing that it is working.
//
// A POST to localhost is usually quick enough that only the toast is needed, but
// "usually" is doing a lot of work there: writing settings touches the disk, and
// on a machine mid-render it can stall long enough that a button which does
// nothing visible reads as a button that did nothing at all.
async function withBusy(btn, label, fn) {
  if (!btn || btn.classList.contains("busy")) return;
  const original = btn.innerHTML;
  const width = btn.getBoundingClientRect().width;
  btn.style.minWidth = Math.ceil(width) + "px";   // don't let the row jump
  btn.classList.add("busy");
  btn.setAttribute("aria-busy", "true");
  btn.innerHTML = `<span class="spinner"></span> ${label}`;
  try {
    return await fn();
  } finally {
    btn.classList.remove("busy");
    btn.removeAttribute("aria-busy");
    btn.innerHTML = original;
    btn.style.minWidth = "";
  }
}

let _toastTimer = null;
function toast(msg) {
  let el = document.querySelector(".toast");
  if (!el) { el = document.createElement("div"); el.className = "toast"; document.body.appendChild(el); }
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

function fmtHMS(secs) {
  secs = Math.max(0, Math.round(secs));
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
  return (h ? h + "h" : "") + String(m).padStart(h ? 2 : 1, "0") + "m" + String(s).padStart(2, "0") + "s";
}

// A friendly, rounded duration for humans ("2h 10m", "8 min", "45 sec"). Used
// for estimates where second-precision would be false precision.
function fmtDuration(secs) {
  secs = Math.max(0, Math.round(secs));
  if (secs < 60) return secs + " sec";
  const h = Math.floor(secs / 3600), m = Math.round((secs % 3600) / 60);
  if (h) return m ? `${h}h ${m}m` : `${h}h`;
  return m + " min";
}

function humanBytes(n) {
  if (n === null || n === undefined) return "—";
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0, f = n;
  while (f >= 1024 && i < u.length - 1) { f /= 1024; i++; }
  return (i === 0 ? f.toFixed(0) : f.toFixed(1)) + " " + u[i];
}

// ---- Slide-over drawer (chapter text peek) -----------------------------------
// openDrawer({ title, subtitle, loading }) returns a handle with setBody(html),
// setFooter(node|html) and close(). One shared drawer element is reused.
function openDrawer(opts) {
  let backdrop = document.getElementById("drawer");
  if (!backdrop) {
    backdrop = document.createElement("div");
    backdrop.id = "drawer";
    backdrop.className = "drawer-backdrop";
    backdrop.innerHTML = `
      <aside class="drawer" role="dialog" aria-modal="true">
        <header>
          <div class="drawer-head"><h3 data-title></h3><div class="meta" data-sub></div></div>
        </header>
        <div class="drawer-body" data-body></div>
        <footer class="drawer-foot" data-foot></footer>
      </aside>`;
    document.body.appendChild(backdrop);
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop || e.target.closest("[data-close]")) closeDrawer();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && backdrop.classList.contains("open")) closeDrawer();
    });
  }
  backdrop.querySelector("[data-title]").textContent = opts.title || "";
  backdrop.querySelector("[data-sub]").textContent = opts.subtitle || "";
  const body = backdrop.querySelector("[data-body]");
  body.innerHTML = opts.loading ? `<div class="empty"><span class="spinner"></span></div>` : "";
  backdrop.querySelector("[data-foot]").innerHTML = "";
  backdrop.classList.add("open");
  return {
    el: backdrop,
    setTitle: (t, sub) => {
      backdrop.querySelector("[data-title]").textContent = t || "";
      if (sub !== undefined) backdrop.querySelector("[data-sub]").textContent = sub || "";
    },
    setBody: (html) => { body.innerHTML = html; },
    setFooter: (node) => {
      const f = backdrop.querySelector("[data-foot]");
      f.innerHTML = "";
      if (typeof node === "string") f.innerHTML = node; else if (node) f.appendChild(node);
    },
    close: closeDrawer,
  };
}
function closeDrawer() {
  const d = document.getElementById("drawer");
  if (d) d.classList.remove("open");
}

// ---- Centered confirm dialog -------------------------------------------------
// confirmDialog({ title, bodyHtml, confirmLabel, danger }) -> Promise<bool>
function confirmDialog(opts) {
  return new Promise((resolve) => {
    const back = document.createElement("div");
    back.className = "modal-backdrop open";
    back.innerHTML = `
      <div class="modal confirm" role="dialog" aria-modal="true">
        <header><h3>${opts.title || "Confirm"}</h3></header>
        <div class="confirm-body">${opts.bodyHtml || ""}</div>
        <footer>
          <button class="btn ghost" data-cancel>Cancel</button>
          <button class="btn ${opts.danger ? "danger" : "primary"}" data-ok>${opts.confirmLabel || "Continue"}</button>
        </footer>
      </div>`;
    document.body.appendChild(back);
    const done = (v) => { back.remove(); resolve(v); };
    back.addEventListener("click", (e) => { if (e.target === back || e.target.closest("[data-cancel]")) done(false); });
    back.querySelector("[data-ok]").addEventListener("click", () => done(true));
    back.querySelector("[data-ok]").focus();
  });
}

// ---- Filesystem browser modal ------------------------------------------------
// openFsBrowser({ start, accept, onPick }) — accept: "ebook" | "audio" | "dir"
// "dir" is a folder picker: navigate into folders, then "Use this folder" picks
// the current directory (there are no files to select).
function openFsBrowser(opts) {
  const accept = opts.accept || "ebook";
  const isDir = accept === "dir";
  const kind = isDir ? "dir" : accept === "audio" ? "audio" : "ebook";
  const heading = isDir ? "Choose a folder" : `Choose a ${accept === "audio" ? "voice clip" : "book"}`;

  let backdrop = document.getElementById("fsModal");
  if (!backdrop) {
    backdrop = document.createElement("div");
    backdrop.id = "fsModal";
    backdrop.className = "modal-backdrop";
    backdrop.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true">
        <header><h3></h3>
          <button class="btn ghost sm" data-close aria-label="Close">${ICON.close}</button></header>
        <div class="crumbs" data-crumbs></div>
        <div class="fs-list" data-list></div>
        <footer>
          <button class="btn ghost" data-close>Cancel</button>
          <button class="btn primary" data-use style="display:none">Use this folder</button>
        </footer>
      </div>`;
    document.body.appendChild(backdrop);
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop || e.target.closest("[data-close]")) close();
    });
  }
  const listEl = backdrop.querySelector("[data-list]");
  const crumbEl = backdrop.querySelector("[data-crumbs]");
  const useBtn = backdrop.querySelector("[data-use]");
  backdrop.querySelector("h3").textContent = heading;

  let current = "";
  function close() { backdrop.classList.remove("open"); }

  useBtn.style.display = isDir ? "" : "none";
  useBtn.onclick = isDir ? (() => { opts.onPick(current); close(); }) : null;

  async function load(path) {
    listEl.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    let data;
    try {
      data = await getJSON("/api/fs?kind=" + kind + "&path=" + encodeURIComponent(path || ""));
    } catch (e) { listEl.innerHTML = `<div class="empty">Could not read folder</div>`; return; }
    current = data.cwd;
    crumbEl.textContent = data.cwd + (data.error ? "  (" + data.error + ")" : "");
    const rows = [];
    if (data.parent) rows.push(row("dir", ICON.up, "Back to " + baseName(data.parent), data.parent, true));
    for (const d of data.dirs) rows.push(row("dir", ICON.folder, d.name, d.path, true));
    for (const fl of data.files) rows.push(row("file", ICON.file, fl.name, fl.path, false, fl.disabled, fl.reason));
    listEl.innerHTML = "";
    if (!rows.length) {
      listEl.innerHTML = `<div class="empty">${isDir ? "No subfolders here" : "No matching files in this folder"}</div>`;
    }
    rows.forEach((r) => listEl.appendChild(r));
  }

  function row(kind, icon, name, path, isDirRow, disabled, reason) {
    const el = document.createElement("div");
    el.className = "fs-item " + kind + (disabled ? " disabled" : "");
    if (disabled) {
      el.innerHTML = `${ICON.blocked}<span>${name}<span class="fs-reason">${reason || "unsupported"}</span></span>`;
      el.title = reason || "unsupported";
      return el;  // not selectable
    }
    el.innerHTML = `${icon}<span>${name}</span>`;
    el.addEventListener("click", () => { isDirRow ? load(path) : (opts.onPick(path), close()); });
    return el;
  }

  backdrop.classList.add("open");
  load(opts.start || "");
}

// ---- The sidebar: what the machine is doing, and what it is hoarding ---------
// Both figures are wanted on every page, and both are expensive to compute
// (surveying storage walks every job's tree), so neither is rendered server-side
// — the page paints first and these fill themselves in, exactly as the
// prerequisite banner does.
function startSidebar() {
  const dock = document.getElementById("renderDock");
  const idle = document.getElementById("dockIdle");
  const strip = document.getElementById("diskStrip");
  const line = document.getElementById("diskLine");
  if (!dock) return;

  let rendering = false;

  function paintStatus(s) {
    const job = s && s.job;
    rendering = !!(s && s.busy && job);
    dock.hidden = !rendering;
    idle.hidden = rendering;
    if (!rendering) return;

    dock.href = "/job/" + job.job_id;
    document.getElementById("dockStage").textContent = job.stage_label || "Working";
    document.getElementById("dockTitle").textContent = job.title || "";

    // A preview reports its own progress; a full render is measured in segments.
    const previewing = job.stage === "previewing";
    const frac = previewing
      ? (job.preview_progress || 0)
      : (job.total_segments ? job.rendered_segments / job.total_segments : 0);
    document.getElementById("dockBar").style.width = Math.round(frac * 100) + "%";
    document.getElementById("dockDetail").textContent = previewing
      ? "a short excerpt"
      : (job.total_segments ? `section ${job.rendered_segments.toLocaleString()} of ${job.total_segments.toLocaleString()}` : "");
    document.getElementById("dockLeft").textContent = remainingText(job, frac);
  }

  // Honest only once the render has actually produced something: before that
  // there is no rate to extrapolate from, so it says nothing rather than lying.
  function remainingText(job, frac) {
    if (!job.render_started_at || frac <= 0.01) return "estimating…";
    const started = Date.parse(job.render_started_at);
    if (!started) return "";
    const elapsed = (Date.now() - started) / 1000;
    if (elapsed <= 0) return "";
    const left = elapsed / frac - elapsed;
    return left > 30 ? fmtDuration(left) + " left" : "almost done";
  }

  function paintStorage(d) {
    if (!d) return;
    const count = (d.books || []).length;
    const nav = document.getElementById("navBookCount");
    if (nav) nav.textContent = count ? count : "";

    // Below a gigabyte this is not worth anyone's attention; nagging about
    // 40 MB is how a standing reminder becomes wallpaper.
    const worth = d.safe_bytes >= 1e9;
    if (!worth) { strip.hidden = true; line.hidden = true; return; }

    if (rendering) {
      strip.hidden = true;
      line.hidden = false;
      document.getElementById("diskLineText").textContent = humanBytes(d.safe_bytes) + " of working files";
      return;
    }
    line.hidden = true;
    strip.hidden = false;
    document.getElementById("diskBig").textContent = humanBytes(d.safe_bytes);
    const share = d.total_bytes ? d.safe_bytes / d.total_bytes : 0;
    document.getElementById("diskBar").style.width = Math.round(share * 100) + "%";
    const n = d.safe_count;
    document.getElementById("diskWhy").textContent =
      `Left behind by ${n} finished book${n === 1 ? "" : "s"}. Deleting them changes nothing you can hear.`;
  }

  async function tick() {
    try { paintStatus(await getJSON("/api/status")); } catch (e) { /* shutting down */ }
    try { paintStorage(await getJSON("/api/storage")); } catch (e) { /* ditto */ }
  }

  const stop = document.getElementById("dockStop");
  if (stop) {
    stop.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const id = (dock.href || "").split("/job/")[1];
      if (!id) return;
      await postForm(`/job/${id}/cancel`, {});
      toast("Stopping…");
      tick();
    });
  }

  // Remember where this window is, so relaunching reopens it here.
  //
  // Browsers fire `resize` but there is no "moved" event, so position has to be
  // sampled. It rides the tick that is already running rather than starting a
  // timer of its own, and only posts when something actually changed.
  let lastGeom = "";
  function reportGeometry() {
    // A minimised or hidden window reports zeroes; remembering those would
    // reopen something invisible.
    if (!window.outerWidth || !window.outerHeight) return;
    const g = {
      x: window.screenX, y: window.screenY,
      width: window.outerWidth, height: window.outerHeight,
    };
    const key = [g.x, g.y, g.width, g.height].join(",");
    if (key === lastGeom) return;
    lastGeom = key;
    fetch("/api/window", { method: "POST", body: new URLSearchParams(g) }).catch(() => {});
  }

  tick();
  reportGeometry();
  // Slow on purpose: this is a background readout, not the job page's own poll.
  setInterval(() => { tick(); reportGeometry(); }, 4000);
  // Catch a drag that ends just before the window closes.
  window.addEventListener("pagehide", reportGeometry);
}
