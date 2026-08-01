"""Extraction stage.

Any input format (.epub/.mobi/.azw3/.pdf) is first normalized to a clean EPUB by
Calibre's ``ebook-convert``; we then parse that single, predictable EPUB for
metadata, cover, and per-chapter text. Chaptering follows the EPUB spine (one
spine document = one chapter), with titles from the table of contents or the
document's first heading. This is intentionally simple; it handles the common
"one file per chapter" layout well and degrades gracefully otherwise.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
import sys
import urllib.parse
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from .. import tools

try:  # XHTML is valid XML but we intentionally parse it with the HTML parser.
    from bs4 import XMLParsedAsHTMLWarning

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except Exception:  # pragma: no cover - older bs4 without this warning class
    pass

SUPPORTED_INPUT = {".epub", ".mobi", ".azw3", ".azw", ".fb2", ".pdf"}


class ExtractionError(RuntimeError):
    pass


@dataclass
class RawChapter:
    title: str
    text: str


@dataclass
class RawBook:
    title: str
    author: str
    cover_bytes: bytes | None
    cover_ext: str | None
    chapters: list[RawChapter]
    year: str | None = None
    description: str | None = None
    isbn: str | None = None
    series: str | None = None
    series_index: str | None = None


# --- repairing the input before Calibre sees it ------------------------------

# An EPUB's spine is its reading order, so every entry has to be a document.
# Some commercial EPUBs list the cover *image* there instead. Calibre walks the
# spine assuming each item parsed into a tree, calls .find() on raw JPEG bytes,
# and dies inside its CSS flattener with a bare TypeError — no mention of the
# spine, the cover, or the book. Dropping the entry costs nothing (the cover is
# still in the manifest and guide) and makes such books convert normally.
#
# Deliberately a deny-list of things we are certain are *not* documents, rather
# than an allow-list of the ones we think are: an unknown or absent media type
# is left alone, because dropping a real chapter would silently truncate a book,
# which is far worse than the crash this avoids.
_NOT_A_DOCUMENT_PREFIXES = ("image/", "audio/", "video/", "font/")
_NOT_A_DOCUMENT_TYPES = frozenset({
    "text/css",
    "application/x-dtbncx+xml",
    "application/font-woff",
    "application/font-sfnt",
    "application/vnd.ms-opentype",
    "application/x-font-ttf",
    "application/x-font-otf",
})


def _is_not_a_document(media_type: str) -> bool:
    mt = (media_type or "").strip().lower()
    if not mt:
        return False  # unknown: assume a document and leave it alone
    return mt.startswith(_NOT_A_DOCUMENT_PREFIXES) or mt in _NOT_A_DOCUMENT_TYPES


def repair_epub_spine(src: Path, dest: Path) -> tuple[Path, list[str]] | None:
    """Copy ``src`` to ``dest`` without its non-document spine entries.

    Returns ``(dest, dropped_hrefs)``, or None when the book needs no repair.
    Also returns None when the file can't be read as an EPUB at all: that is
    Calibre's complaint to make, with its much better diagnostics.
    """
    from lxml import etree

    try:
        with zipfile.ZipFile(src) as zf:
            opf_name = _opf_path(zf)
            opf_bytes = zf.read(opf_name)
    except (zipfile.BadZipFile, KeyError, ExtractionError, OSError):
        return None

    try:
        root = etree.fromstring(opf_bytes)
    except etree.XMLSyntaxError:
        return None

    # {*} matches any namespace, so this works whether or not the OPF declares
    # the usual one.
    media_types = {
        item.get("id"): item.get("media-type", "")
        for item in root.iterfind(".//{*}manifest/{*}item")
        if item.get("id")
    }

    dropped: list[str] = []
    for itemref in list(root.iterfind(".//{*}spine/{*}itemref")):
        idref = itemref.get("idref")
        if idref and _is_not_a_document(media_types.get(idref, "")):
            dropped.append(idref)
            itemref.getparent().remove(itemref)

    if not dropped:
        return None

    new_opf = etree.tostring(root.getroottree(), xml_declaration=True, encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        # mimetype has to stay the first entry and stored uncompressed, or the
        # result is no longer a valid EPUB.
        names = zin.namelist()
        if "mimetype" in names:
            zout.writestr(zipfile.ZipInfo("mimetype"), zin.read("mimetype"),
                          compress_type=zipfile.ZIP_STORED)
        for info in zin.infolist():
            if info.filename == "mimetype":
                continue
            data = new_opf if info.filename == opf_name else zin.read(info.filename)
            zout.writestr(info, data)
    return dest, dropped


def run_ebook_convert(src: Path, out_epub: Path, timeout: int = 1800) -> Path:
    try:
        exe = tools.require_ebook_convert()
    except tools.MissingToolError as e:
        raise ExtractionError(str(e)) from e
    out_epub.parent.mkdir(parents=True, exist_ok=True)

    # Errors are always reported against the file the user actually gave us.
    source = src
    if src.suffix.lower() == ".epub":
        repaired = repair_epub_spine(src, out_epub.parent / "repaired-input.epub")
        if repaired:
            source, dropped = repaired
            plural = "entry" if len(dropped) == 1 else "entries"
            print(f"repaired “{src.name}”: dropped {len(dropped)} non-document spine "
                  f"{plural} ({', '.join(dropped)}) that Calibre cannot process",
                  file=sys.stderr, flush=True)

    try:
        proc = tools.run(
            [exe, source, out_epub, "--enable-heuristics"],
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ExtractionError(
            f"Converting “{src.name}” to EPUB timed out. It may be unusually large or "
            "malformed; try converting it to EPUB in Calibre first."
        )
    if proc.returncode != 0 or not out_epub.exists():
        raise ExtractionError(_convert_error_message(src, proc))
    return out_epub


def _convert_error_message(src: Path, proc: subprocess.CompletedProcess) -> str:
    """Turn a raw ebook-convert failure into a human, actionable message."""
    out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    low = out.lower()
    if "drm" in low:
        return (
            f"“{src.name}” appears to be DRM-protected, so Calibre can't convert it. "
            "Purchased Kindle and Adobe books are usually locked this way. Use a "
            "DRM-free copy, or remove the DRM from a book you own, then re-import."
        )
    if "failed to detect the input file type" in low or "not a valid" in low:
        return (
            f"Calibre couldn't read “{src.name}” — the file may be corrupt or not the "
            "format its extension claims. Try re-downloading it, or convert it to EPUB "
            "in Calibre first."
        )
    if "stylize_spine" in low or "flatcss" in low:
        return (
            f"“{src.name}” lists a file in its reading order that isn't a document — "
            "usually the cover image — which Calibre can't process. We repair that "
            "automatically before converting, so reaching this message means the book "
            "is malformed in some further way. Try a different copy."
        )
    tail = (proc.stderr or proc.stdout or "").strip()[-600:]
    # Telling someone to convert to EPUB in Calibre is useless when the file is
    # already an EPUB and Calibre is what just fell over on it.
    if src.suffix.lower() == ".epub":
        advice = (
            "It's already an EPUB, so converting it again in Calibre will hit the same "
            "fault — the file itself is likely malformed. Try a different copy of the "
            "book, or open it in Calibre's Edit Book to see what's wrong."
        )
    else:
        advice = "If this persists, convert it to EPUB in Calibre first."
    return (
        f"Couldn't convert “{src.name}” to EPUB (Calibre exited {proc.returncode}). "
        f"{advice}\n\n" + tail
    )


# --- EPUB parsing ------------------------------------------------------------

def _clean_meta(value: str) -> str:
    """Collapse whitespace and trim stray trailing separator punctuation that
    some EPUBs leave on title/author fields (e.g. ``"Title;"``)."""
    return re.sub(r"\s+", " ", value or "").strip().strip(",;")


def _clean_creator(raw: str) -> str:
    """Tidy a dc:creator value for tags and foldering: EPUBs commonly store
    multiple authors separated by ``;`` (sometimes with a trailing one, giving
    ``"Andy Weir;"``). Split on ``;``, drop the blanks, and re-join with commas."""
    parts = [_clean_meta(p) for p in (raw or "").split(";")]
    return ", ".join(p for p in parts if p)


def _opf_path(zf: zipfile.ZipFile) -> str:
    container = zf.read("META-INF/container.xml")
    soup = BeautifulSoup(container, "xml")
    rootfile = soup.find("rootfile")
    if not rootfile or not rootfile.get("full-path"):
        raise ExtractionError("EPUB container.xml has no rootfile")
    return rootfile["full-path"]


def _resolve(base_dir: str, href: str) -> str:
    href = urllib.parse.unquote(href.split("#")[0])
    return posixpath.normpath(posixpath.join(base_dir, href))


def _extract_chapter(html: bytes) -> tuple[str | None, str]:
    """Return ``(heading, body_text)`` for one spine document.

    The first heading is pulled out and removed from the body so the chapter
    title isn't spoken twice (it's re-emitted as a dedicated title segment). A
    horizontal rule (``<hr>``) — a common scene divider — is converted to a
    textual scene-break marker so the chunker can pause on it.
    """
    soup = BeautifulSoup(html, "lxml")
    for junk in soup(["script", "style", "nav"]):
        junk.decompose()

    heading = None
    for level in ("h1", "h2", "h3"):
        h = soup.find(level)
        if h and h.get_text(strip=True):
            heading = h.get_text(" ", strip=True)
            h.decompose()  # drop from body to avoid a duplicate read
            break

    for hr in soup.find_all("hr"):
        hr.replace_with("\n\n* * *\n\n")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "tr"]):
        tag.append("\n\n")
    body = soup.body or soup
    return heading, body.get_text()


def _toc_titles(zf: zipfile.ZipFile, opf_dir: str, manifest: dict, spine_ids: list[str]) -> dict[str, str]:
    """Map spine document path -> title from nav.xhtml (EPUB3) or toc.ncx (EPUB2)."""
    titles: dict[str, str] = {}
    # EPUB3 nav
    nav_item = next((m for m in manifest.values() if "nav" in (m.get("properties") or "")), None)
    ncx_item = next((m for m in manifest.values() if m.get("media_type") == "application/x-dtbncx+xml"), None)
    try:
        if nav_item:
            path = _resolve(opf_dir, nav_item["href"])
            soup = BeautifulSoup(zf.read(path), "lxml")
            for a in soup.select("nav a[href]"):
                target = _resolve(posixpath.dirname(path), a["href"])
                text = a.get_text(" ", strip=True)
                if text and target not in titles:
                    titles[target] = text
        elif ncx_item:
            path = _resolve(opf_dir, ncx_item["href"])
            soup = BeautifulSoup(zf.read(path), "xml")
            for nav_point in soup.find_all("navPoint"):
                label = nav_point.find("navLabel")
                content = nav_point.find("content")
                if label and content and content.get("src"):
                    target = _resolve(posixpath.dirname(path), content["src"])
                    text = label.get_text(" ", strip=True)
                    if text and target not in titles:
                        titles[target] = text
    except (KeyError, zipfile.BadZipFile, ExtractionError):
        pass
    return titles


def parse_epub(epub_path: Path) -> RawBook:
    with zipfile.ZipFile(epub_path) as zf:
        opf = _opf_path(zf)
        opf_dir = posixpath.dirname(opf)
        soup = BeautifulSoup(zf.read(opf), "xml")

        title_tag = soup.find("title")  # dc:title
        title = _clean_meta(title_tag.get_text(strip=True)) if title_tag else "Unknown Title"
        # dc:creator may repeat (one per author); join them cleanly.
        creators = [c.get_text(strip=True) for c in soup.find_all("creator")]
        author = _clean_creator("; ".join(creators)) or "Unknown Author"

        meta = _metadata(soup)

        # Manifest: id -> {href, media_type, properties}
        manifest: dict[str, dict] = {}
        href_to_id: dict[str, str] = {}
        for item in soup.find_all("item"):
            mid = item.get("id")
            href = item.get("href")
            if not mid or not href:
                continue
            manifest[mid] = {
                "href": href,
                "media_type": item.get("media-type", ""),
                "properties": item.get("properties", ""),
            }
            href_to_id[_resolve(opf_dir, href)] = mid

        spine_ids = [ir.get("idref") for ir in soup.select("spine itemref") if ir.get("idref")]

        # Cover image.
        cover_bytes, cover_ext = _find_cover(zf, soup, manifest, opf_dir)

        toc = _toc_titles(zf, opf_dir, manifest, spine_ids)

        chapters: list[RawChapter] = []
        for idx, sid in enumerate(spine_ids, start=1):
            item = manifest.get(sid)
            if not item or "html" not in item["media_type"]:
                continue
            path = _resolve(opf_dir, item["href"])
            try:
                html = zf.read(path)
            except KeyError:
                continue
            heading, text = _extract_chapter(html)
            if len(text.strip()) < 20:  # skip covers/nav/blank documents
                continue
            title_c = toc.get(path) or heading or f"Chapter {len(chapters) + 1}"
            chapters.append(RawChapter(title=title_c, text=text))

    if not chapters:
        raise ExtractionError("No readable chapters found in the EPUB")
    return RawBook(
        title=title, author=author, cover_bytes=cover_bytes, cover_ext=cover_ext,
        chapters=chapters, **meta,
    )


def _metadata(soup) -> dict:
    """Best-effort bibliographic metadata from the OPF ``<metadata>`` block.

    Everything here is optional — used to enrich Plex tags and library
    foldering — so anything unparseable is simply left as None."""
    import re as _re

    out: dict[str, str | None] = {
        "year": None, "description": None, "isbn": None,
        "series": None, "series_index": None,
    }

    date_tag = soup.find("date")  # dc:date
    if date_tag:
        m = _re.search(r"\d{4}", date_tag.get_text())
        if m:
            out["year"] = m.group(0)

    desc_tag = soup.find("description")  # dc:description (may contain markup)
    if desc_tag:
        text = BeautifulSoup(desc_tag.get_text(" ", strip=True), "lxml").get_text(" ", strip=True)
        out["description"] = text or None

    # ISBN: a dc:identifier flagged as ISBN, or any identifier that looks like one.
    for ident in soup.find_all("identifier"):
        raw = ident.get_text(strip=True)
        scheme = (ident.get("opf:scheme") or ident.get("scheme") or "").lower()
        digits = _re.sub(r"[^0-9Xx]", "", raw.replace("urn:isbn:", ""))
        if scheme == "isbn" or "isbn" in raw.lower() or len(digits) in (10, 13):
            if len(digits) in (10, 13):
                out["isbn"] = digits.upper()
                break

    # Series: Calibre EPUB2 metas, else EPUB3 belongs-to-collection.
    s_name = soup.find("meta", attrs={"name": "calibre:series"})
    s_idx = soup.find("meta", attrs={"name": "calibre:series_index"})
    if s_name and s_name.get("content"):
        out["series"] = s_name["content"].strip()
        if s_idx and s_idx.get("content"):
            out["series_index"] = s_idx["content"].strip()
    else:
        coll = soup.find("meta", attrs={"property": "belongs-to-collection"})
        if coll and coll.get_text(strip=True):
            out["series"] = coll.get_text(strip=True)
    return out


def _find_cover(zf, soup, manifest, opf_dir) -> tuple[bytes | None, str | None]:
    cover_id = None
    # EPUB3: manifest item with properties="cover-image".
    for mid, m in manifest.items():
        if "cover-image" in (m.get("properties") or ""):
            cover_id = mid
            break
    # EPUB2: <meta name="cover" content="itemid"/>.
    if not cover_id:
        meta = soup.find("meta", attrs={"name": "cover"})
        if meta and meta.get("content") in manifest:
            cover_id = meta["content"]
    # Fallback: any manifest image whose id/href mentions "cover".
    if not cover_id:
        for mid, m in manifest.items():
            if m["media_type"].startswith("image/") and "cover" in (mid + m["href"]).lower():
                cover_id = mid
                break
    if not cover_id:
        return None, None
    try:
        href = manifest[cover_id]["href"]
        data = zf.read(_resolve(opf_dir, href))
        ext = posixpath.splitext(href)[1].lower() or ".jpg"
        return data, ext
    except (KeyError, zipfile.BadZipFile):
        return None, None
