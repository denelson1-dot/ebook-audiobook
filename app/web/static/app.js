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
          <button class="btn ghost sm" data-close>✕</button></header>
        <div class="crumbs" data-crumbs></div>
        <div class="fs-list" data-list></div>
        <footer>
          <button class="btn ghost" data-close>Cancel</button>
          <button class="btn primary" data-use style="display:none">Use this folder</button>
        </footer>
      </div>`;
    document.body.appendChild(backdrop);
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop || e.target.hasAttribute("data-close")) close();
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
    if (data.parent) rows.push(row("dir", "⬆", "..", data.parent, true));
    for (const d of data.dirs) rows.push(row("dir", "📁", d.name, d.path, true));
    for (const fl of data.files) rows.push(row("file", "📄", fl.name, fl.path, false, fl.disabled, fl.reason));
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
      el.innerHTML = `<span class="ic">🚫</span><span>${name}<span class="fs-reason">${reason || "unsupported"}</span></span>`;
      el.title = reason || "unsupported";
      return el;  // not selectable
    }
    el.innerHTML = `<span class="ic">${icon}</span><span>${name}</span>`;
    el.addEventListener("click", () => { isDirRow ? load(path) : (opts.onPick(path), close()); });
    return el;
  }

  backdrop.classList.add("open");
  load(opts.start || "");
}
