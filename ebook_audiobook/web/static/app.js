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

// --- interface language -------------------------------------------------------
// The server puts this page's catalog in window.EBAB_I18N (empty for English)
// and its language code in window.EBAB_LANG. Strings are looked up by their
// English text, exactly as in Python and the templates, so one .po file holds
// all three. Placeholders are %(name)s everywhere for the same reason.
//
// Plural rules are gettext *indexes*, mirroring each language's Plural-Forms
// header — not Intl.PluralRules, whose CLDR categories ("many" for a million
// in French) would index past a two-form entry.
const PLURAL_RULES = {
  en: (n) => (n !== 1 ? 1 : 0),
  fr: (n) => (n > 1 ? 1 : 0),
};
function _fmt(s, params) {
  // Always a format string, as in the templates: %% is a literal percent.
  const filled = params ? s.replace(/%\((\w+)\)[sd]/g, (m, k) => (k in params ? params[k] : m)) : s;
  return filled.replace(/%%/g, "%");
}
function _(msgid, params) {
  const t = (window.EBAB_I18N || {})[msgid];
  return _fmt(typeof t === "string" ? t : msgid, params);
}
function ngettext(singular, plural, n, params) {
  const rule = PLURAL_RULES[window.EBAB_LANG] || PLURAL_RULES.en;
  const t = (window.EBAB_I18N || {})[singular];
  const forms = Array.isArray(t) ? t : [singular, plural];
  const form = forms[rule(n)] ?? forms[forms.length - 1];
  return _fmt(form, Object.assign({ n }, params));
}

// Shared helpers for the ebook-audiobook UI. No framework — plain fetch + DOM.

// For anything that goes into innerHTML and did not originate in this code:
// book and chapter titles come from the ebook, file names from the disk,
// error text from whatever failed. All of them can contain "<".
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

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
// Like postForm, but a refusal is an answer, not an exception: the routes that
// validate (a library folder that isn't writable, say) reply 400 with
// {ok:false, error} and the caller wants that error, not a thrown one.
async function postFormResult(url, data) {
  let r;
  try {
    r = await fetch(url, { method: "POST", body: new URLSearchParams(data || {}) });
  } catch (e) {
    return { ok: false, status: 0, error: _("Couldn't reach the app") };
  }
  let body = {};
  try { body = await r.json(); } catch (e) { body = { error: r.statusText }; }
  return { status: r.status, ...body, ok: r.ok && body.ok !== false };
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

// Forms that delete ask first. The question lives in data-confirm rather than
// in an inline onsubmit="confirm('…{{ title }}…')": a title with an apostrophe
// — The Hitchhiker's Guide — ends the JS string early there, the handler fails
// to compile, and the form submits with no question at all. An attribute is
// escaped as an attribute, and read as text.
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement) || !form.dataset.confirm) return;
  e.preventDefault();
  const detail = form.dataset.confirmDetail;
  confirmDialog({
    title: form.dataset.confirm,
    bodyHtml: detail ? `<p>${escapeHtml(detail)}</p>` : "",
    confirmLabel: form.dataset.confirmLabel || _("Delete"),
    danger: true,
  }).then((ok) => { if (ok) form.submit(); });  // submit() bypasses this listener
});

// Open one of the app's folders in the system file manager.
// Takes the same names the /reveal route understands — never a path, so there is
// nothing here that could be pointed somewhere it shouldn't go.
async function revealFolder(params) {
  try {
    const r = await fetch("/reveal", { method: "POST", body: new URLSearchParams(params) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) { toast(d.error || _("Couldn't open that folder")); return; }
  } catch (e) { toast(_("Couldn't open that folder")); }
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
  const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(s).padStart(2, "0");
  // NOTE: elapsed time — "1h02m03s" / "2m03s"
  return h ? _("%(h)sh%(m)sm%(s)ss", { h, m: mm, s: ss }) : _("%(m)sm%(s)ss", { m: mm, s: ss });
}

// A friendly, rounded duration for humans ("2h 10m", "8 min", "45 sec"). Used
// for estimates where second-precision would be false precision.
function fmtDuration(secs) {
  secs = Math.max(0, Math.round(secs));
  if (secs < 60) return _("%(n)s sec", { n: secs });
  const h = Math.floor(secs / 3600), m = Math.round((secs % 3600) / 60);
  // NOTE: rounded durations — "2h 10m", "2h", "8 min"
  if (h) return m ? _("%(h)sh %(m)sm", { h, m }) : _("%(h)sh", { h });
  return _("%(n)s min", { n: m });
}

function humanBytes(n) {
  if (n === null || n === undefined) return "—";
  // The units are msgids ("Mo" in French); the decimal separator follows the
  // interface language too, so this agrees with the Python side's "1,5 Mo".
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0, f = n;
  if (!n) return "0 " + _(u[0]);
  while (f >= 1024 && i < u.length - 1) { f /= 1024; i++; }
  const lang = window.EBAB_LANG || "en";
  const num = i === 0 ? f.toFixed(0)
    : f.toLocaleString(lang, { minimumFractionDigits: 1, maximumFractionDigits: 1, useGrouping: false });
  return num + " " + _(u[i]);
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
        <header><h3>${escapeHtml(opts.title || _("Confirm"))}</h3></header>
        <div class="confirm-body">${opts.bodyHtml || ""}</div>
        <footer>
          <button class="btn ghost" data-cancel>${_("Cancel")}</button>
          <button class="btn ${opts.danger ? "danger" : "primary"}" data-ok>${opts.confirmLabel || _("Continue")}</button>
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
  const heading = isDir ? _("Choose a folder") : accept === "audio" ? _("Choose a voice clip") : _("Choose a book");

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
          <button class="btn ghost" data-close>${_("Cancel")}</button>
          <button class="btn primary" data-use style="display:none">${_("Use this folder")}</button>
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
    } catch (e) { listEl.innerHTML = `<div class="empty">${_("Could not read folder")}</div>`; return; }
    current = data.cwd;
    crumbEl.textContent = data.cwd + (data.error ? "  (" + data.error + ")" : "");
    const rows = [];
    if (data.parent) rows.push(row("dir", ICON.up, _("Back to %(name)s", { name: baseName(data.parent) }), data.parent, true));
    // Names are real file names, escaped in row(): a file called
    // <img src=x onerror=…>.epub is a valid name.
    for (const d of data.dirs) rows.push(row("dir", ICON.folder, d.name, d.path, true));
    for (const fl of data.files) rows.push(row("file", ICON.file, fl.name, fl.path, false, fl.disabled, fl.reason));
    listEl.innerHTML = "";
    if (!rows.length) {
      listEl.innerHTML = `<div class="empty">${isDir ? _("No subfolders here") : _("No matching files in this folder")}</div>`;
    }
    rows.forEach((r) => listEl.appendChild(r));
  }

  function row(kind, icon, name, path, isDirRow, disabled, reason) {
    const el = document.createElement("div");
    el.className = "fs-item " + kind + (disabled ? " disabled" : "");
    if (disabled) {
      el.innerHTML = `${ICON.blocked}<span>${escapeHtml(name)}<span class="fs-reason">${escapeHtml(reason || _("unsupported"))}</span></span>`;
      el.title = reason || _("unsupported");
      return el;  // not selectable
    }
    el.innerHTML = `${icon}<span>${escapeHtml(name)}</span>`;
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
  const idleTitle = idle ? idle.querySelector("b") : null;
  const idleText = idleTitle ? idleTitle.textContent : "";

  function paintStatus(s) {
    const job = s && s.job;
    rendering = !!(s && s.busy && job);
    dock.hidden = !rendering;
    idle.hidden = rendering;
    // Busy with no book: a voice audition. Still worth a word, or the sidebar
    // says nothing is happening while the machine is plainly working.
    if (idleTitle) {
      idleTitle.textContent = (s && s.busy && s.kind === "voice_test")
        ? _("Rendering a voice sample") : idleText;
    }
    if (!rendering) return;

    dock.href = "/job/" + job.job_id;
    document.getElementById("dockStage").textContent = job.stage_label || _("Working");
    document.getElementById("dockTitle").textContent = job.title || "";

    // A preview reports its own progress; a full render is measured in segments.
    const previewing = job.stage === "previewing";
    const frac = previewing
      ? (job.preview_progress || 0)
      : (job.total_segments ? job.rendered_segments / job.total_segments : 0);
    document.getElementById("dockBar").style.width = Math.round(frac * 100) + "%";
    document.getElementById("dockDetail").textContent = previewing
      ? _("a short excerpt")
      : (job.total_segments ? _("section %(done)s of %(total)s", {
          done: job.rendered_segments.toLocaleString(window.EBAB_LANG || "en"),
          total: job.total_segments.toLocaleString(window.EBAB_LANG || "en") }) : "");
    document.getElementById("dockLeft").textContent = remainingText(job, frac);
  }

  // Honest only once the render has actually produced something: before that
  // there is no rate to extrapolate from, so it says nothing rather than lying.
  function remainingText(job, frac) {
    if (!job.render_started_at || frac <= 0.01) return _("estimating…");
    const started = Date.parse(job.render_started_at);
    if (!started) return "";
    const elapsed = (Date.now() - started) / 1000;
    if (elapsed <= 0) return "";
    const left = elapsed / frac - elapsed;
    return left > 30 ? _("%(time)s left", { time: fmtDuration(left) }) : _("almost done");
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
      document.getElementById("diskLineText").textContent = _("%(size)s of working files", { size: humanBytes(d.safe_bytes) });
      return;
    }
    line.hidden = true;
    strip.hidden = false;
    document.getElementById("diskBig").textContent = humanBytes(d.safe_bytes);
    const share = d.total_bytes ? d.safe_bytes / d.total_bytes : 0;
    document.getElementById("diskBar").style.width = Math.round(share * 100) + "%";
    const n = d.safe_count;
    document.getElementById("diskWhy").textContent =
      ngettext("Left behind by %(n)s book that is finished, or only ever previewed. Deleting them changes nothing you can hear.",
               "Left behind by %(n)s books that are finished, or only ever previewed. Deleting them changes nothing you can hear.", n);
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
      try {
        await postForm(`/job/${id}/cancel`, {});
        toast(_("Stopping…"));
      } catch (e) {
        toast(_("Couldn't reach the app to stop it"));
      }
      tick();
    });
  }

  // Remember where this window is, so relaunching reopens it here.
  //
  // Only when this *is* the app window. With no Chromium installed the UI is a
  // tab in the user's own browser — Safari, on a Mac — and what this would
  // record then is the position of their personal browser window, to be handed
  // to --window-position the day they install Chrome. Chromium's --app windows
  // report display-mode: standalone; ordinary tabs report "browser".
  //
  // Browsers fire `resize` but there is no "moved" event, so position has to be
  // sampled. It rides the tick that is already running rather than starting a
  // timer of its own, and only posts when something actually changed.
  let lastGeom = "";
  const isAppWindow = !!(window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
  function reportGeometry() {
    if (!isAppWindow) return;
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
    // keepalive: the last call comes from pagehide, and a plain fetch started
    // while the page is being torn down is cancelled with it.
    fetch("/api/window", { method: "POST", body: new URLSearchParams(g), keepalive: true })
      .catch(() => {});
  }

  tick();
  reportGeometry();
  // Slow on purpose: this is a background readout, not the job page's own poll.
  setInterval(() => { tick(); reportGeometry(); }, 4000);
  // Catch a drag that ends just before the window closes.
  window.addEventListener("pagehide", reportGeometry);
}
