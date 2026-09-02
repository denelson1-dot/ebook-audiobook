#!/usr/bin/env python3
"""Maintain the interface translations.

    python tools/i18n.py extract            # every translatable string -> a .pot (scratch)
    python tools/i18n.py update             # merge new strings into each locale's .po
    python tools/i18n.py compile            # .po -> .mo, the file the app reads
    python tools/i18n.py check              # stale .mo? bad placeholders? untranslated?
    python tools/i18n.py report             # what still needs translating, per language
    python tools/i18n.py add fr             # start a new language

The ``.po`` under ``ebook_audiobook/locale/<lang>/LC_MESSAGES/`` is the source
of truth and is what a translator edits (Poedit opens it). The ``.mo`` beside
it is compiled from it and committed too, because the app reads only the
``.mo`` and must not need Babel at runtime. ``check`` — also run by
``tests/test_i18n.py`` and CI — is what stops the two drifting apart.

Strings are found in three kinds of file: Python (``_()``, ``ngettext()``,
``N_()``), the templates' Jinja expressions, and JavaScript — both ``app.js``
and the inline ``<script>`` blocks inside templates, which Babel's Jinja
extractor cannot see into. :func:`extract_template` handles a template whole:
the Jinja part through Jinja's own extractor, then each script block through
Babel's JavaScript one, with line numbers put back so a ``.po`` reference
points at the real line of the template.

Babel is a development dependency (``pip install -e '.[dev]'``).
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "ebook_audiobook"
LOCALE_DIR = PACKAGE / "locale"
DOMAIN = "messages"
PROJECT = "ebook-audiobook"
ISSUES = "https://github.com/denelson1-dot/ebook-audiobook/issues"

# Babel's defaults plus the no-op marker used on the shared label tables.
KEYWORDS = {
    "_": None, "gettext": None, "ngettext": (1, 2), "N_": None,
    "pgettext": ((1, "c"), 2), "npgettext": ((1, "c"), 2, 3),
}
COMMENT_TAGS = ("NOTE:",)

# The same two regexes tests/test_frontend.py uses to find and neutralise
# inline scripts, so the extractor and the parse test agree on what a script
# block is.
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)

PLACEHOLDER = re.compile(r"%\((\w+)\)[sd]")
HTML_TAG = re.compile(r"</?[a-zA-Z][\w-]*")


def is_translated(message) -> bool:
    """A message with every form filled in — a plural's empty pair is a tuple,
    and a tuple of empty strings is truthy, which is the trap this avoids."""
    if isinstance(message.string, tuple):
        return all(message.string)
    return bool(message.string)


def _need_babel():
    try:
        import babel  # noqa: F401
    except ImportError:
        sys.exit("Babel is not installed: pip install -e '.[dev]'")


# --- extraction ---------------------------------------------------------------

# A _("…") or ngettext("…", "…", n) call whose arguments are plain string
# literals. Babel's JavaScript lexer treats a template literal as one opaque
# token, so a call inside `${…}` — which is how most sentences are built into
# markup here — would never be seen. This second pass catches those; the
# catalog merges the duplicates it produces for calls the lexer did see.
_JS_STRING = r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\''
JS_CALL = re.compile(
    r"(?<![\w$.])(_|ngettext)\(\s*(" + _JS_STRING + r")(?:\s*,\s*(" + _JS_STRING + r"))?")


def _js_unquote(literal: str) -> str:
    body = literal[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


def js_calls(source: str):
    """Yield (lineno, funcname, message) for every literal _()/ngettext() call."""
    for m in JS_CALL.finditer(source):
        lineno = source.count("\n", 0, m.start()) + 1
        func, first, second = m.group(1), m.group(2), m.group(3)
        if func == "ngettext":
            if not second:
                continue
            yield lineno, func, (_js_unquote(first), _js_unquote(second)), []
        else:
            yield lineno, func, _js_unquote(first), []


def extract_script(fileobj, keywords, comment_tags, options):
    """A JavaScript file: Babel's extractor, plus the template-literal pass."""
    from babel.messages.extract import extract_javascript

    data = fileobj.read()
    yield from extract_javascript(io.BytesIO(data), keywords, comment_tags, options)
    yield from js_calls(data.decode("utf-8"))


def extract_template(fileobj, keywords, comment_tags, options):
    """Extract from a template: its Jinja expressions, then its inline scripts."""
    from babel.messages.extract import extract_javascript
    from jinja2.ext import babel_extract

    data = fileobj.read()
    text = data.decode("utf-8")

    yield from babel_extract(io.BytesIO(data), keywords, comment_tags, options)

    for m in INLINE_SCRIPT.finditer(text):
        block_start = text.count("\n", 0, m.start(1)) + 1
        block_text = JINJA.sub("0", m.group(1))
        block = block_text.encode("utf-8")
        for lineno, funcname, message, comments in extract_javascript(
                io.BytesIO(block), keywords, comment_tags, options):
            yield block_start + lineno - 1, funcname, message, comments
        for lineno, funcname, message, comments in js_calls(block_text):
            yield block_start + lineno - 1, funcname, message, comments


METHOD_MAP = [
    ("**.py", "python"),
    ("web/static/**.js", extract_script),
    ("web/templates/**.html", extract_template),
]
OPTIONS_MAP = {
    "web/templates/**.html": {"extensions": "jinja2.ext.i18n", "trimmed": "true"},
}


def extract_catalog():
    """Every translatable string in the package, as a Babel Catalog."""
    from babel.messages.catalog import Catalog
    from babel.messages.extract import extract_from_dir

    catalog = Catalog(project=PROJECT, version=_version(), charset="utf-8",
                      copyright_holder="denelson1", msgid_bugs_address=ISSUES,
                      header_comment=(
                          "# Interface strings for ebook-audiobook.\n"
                          "# Edit the per-language .po files, not this template."))
    for filename, lineno, message, comments, context in extract_from_dir(
            str(PACKAGE), method_map=METHOD_MAP, options_map=OPTIONS_MAP,
            keywords=KEYWORDS, comment_tags=COMMENT_TAGS):
        catalog.add(message, None, [(f"ebook_audiobook/{filename}", lineno)],
                    auto_comments=comments, context=context)
    return catalog


def _version() -> str:
    text = (ROOT / "pyproject.toml").read_text("utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else "0"


def po_path(lang: str) -> Path:
    return LOCALE_DIR / lang / "LC_MESSAGES" / f"{DOMAIN}.po"


def mo_path(lang: str) -> Path:
    return LOCALE_DIR / lang / "LC_MESSAGES" / f"{DOMAIN}.mo"


def languages() -> list[str]:
    if not LOCALE_DIR.is_dir():
        return []
    return sorted(p.name for p in LOCALE_DIR.iterdir() if po_path(p.name).is_file())


def read_catalog(lang: str):
    from babel.messages.pofile import read_po

    with po_path(lang).open("rb") as f:
        return read_po(f, locale=lang, domain=DOMAIN)


def compile_bytes(catalog) -> bytes:
    from babel.messages.mofile import write_mo

    buf = io.BytesIO()
    # use_fuzzy=False: an entry marked "needs work" falls back to English rather
    # than shipping a sentence the translator was not sure of.
    write_mo(buf, catalog, use_fuzzy=False)
    return buf.getvalue()


# --- commands -----------------------------------------------------------------

def cmd_extract(args) -> int:
    from babel.messages.pofile import write_po

    catalog = extract_catalog()
    out = Path(args.output) if args.output else None
    if out is None:
        print(f"{len(catalog)} strings")
        return 0
    with out.open("wb") as f:
        write_po(f, catalog, width=88)
    print(f"{len(catalog)} strings -> {out}")
    return 0


def cmd_update(args) -> int:
    from babel.messages.pofile import write_po

    template = extract_catalog()
    langs = args.languages or languages()
    if not langs:
        print("no languages yet — start one with: python tools/i18n.py add <code>")
        return 1
    for lang in langs:
        path = po_path(lang)
        if path.is_file():
            catalog = read_catalog(lang)
        else:
            from babel.messages.catalog import Catalog

            catalog = Catalog(locale=lang, project=PROJECT, version=_version(),
                              charset="utf-8", copyright_holder="denelson1",
                              msgid_bugs_address=ISSUES, last_translator=PROJECT,
                              fuzzy=False, header_comment=_language_header(lang))
        # The template's creation date must not churn every .po on every run.
        try:
            catalog.update(template, no_fuzzy_matching=False, update_creation_date=False)
        except TypeError:  # older Babel
            catalog.update(template, no_fuzzy_matching=False)
        catalog.project = PROJECT
        catalog.version = _version()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            # Obsolete entries stay (as #~) for one cycle so a translator sees
            # what disappeared; the next update drops them for good.
            write_po(f, catalog, width=88, ignore_obsolete=False)
        print(f"{lang}: {_summary(catalog)} -> {path.relative_to(ROOT)}")
    return 0


def _language_header(lang: str) -> str:
    return (f"# {lang} translation of the ebook-audiobook interface.\n"
            f"# Open this file in Poedit. Keep %(name)s placeholders and any <code>…</code>\n"
            f"# exactly as they are in the English; translate everything else.")


def cmd_compile(args) -> int:
    langs = args.languages or languages()
    for lang in langs:
        catalog = read_catalog(lang)
        data = compile_bytes(catalog)
        mo_path(lang).write_bytes(data)
        print(f"{lang}: {_summary(catalog)} -> {mo_path(lang).relative_to(ROOT)} ({len(data)} bytes)")
    return 0


def problems(lang: str, allow_missing: bool = False) -> list[str]:
    """Everything wrong with one language's catalog, as sentences. Empty is good.

    Shared with ``tests/test_i18n.py`` so pytest and CI cannot disagree.
    """
    out: list[str] = []
    catalog = read_catalog(lang)

    # 1. The committed .mo is what the app reads; it must be the .po compiled.
    committed = mo_path(lang).read_bytes() if mo_path(lang).is_file() else b""
    if committed != compile_bytes(catalog):
        out.append(f"{lang}: messages.mo is stale — run: python tools/i18n.py compile")

    # 2. Placeholders, %-format validity, HTML tags.
    for msg in catalog:
        if not msg.id:
            continue  # the header
        ids = [msg.id] if isinstance(msg.id, str) else list(msg.id)
        strings = [msg.string] if isinstance(msg.string, str) else list(msg.string)
        allowed = set().union(*(set(PLACEHOLDER.findall(i)) for i in ids))
        for index, translated in enumerate(strings):
            if not translated:
                continue
            source = ids[min(index, len(ids) - 1)]
            where = f"{lang}: {msg.id!r}"
            have = set(PLACEHOLDER.findall(translated))
            if not have <= allowed:
                out.append(f"{where}: translation uses placeholders the English does not have: "
                           f"{sorted(have - allowed)}")
            missing = set(PLACEHOLDER.findall(source)) - have
            if missing:
                out.append(f"{where}: translation is missing placeholders {sorted(missing)}")
            try:
                translated % _SafeDict()
            except (ValueError, TypeError) as e:
                out.append(f"{where}: not a valid format string ({e}); a literal % must be %%")
            if sorted(HTML_TAG.findall(source)) != sorted(HTML_TAG.findall(translated)):
                out.append(f"{where}: HTML tags differ from the English")

    # 3. Completeness.
    if not allow_missing:
        untranslated = [m.id for m in catalog if m.id and not is_translated(m)]
        fuzzy = [m.id for m in catalog if m.id and m.fuzzy]
        if untranslated:
            out.append(f"{lang}: {len(untranslated)} untranslated "
                       f"(python tools/i18n.py report shows them)")
        if fuzzy:
            out.append(f"{lang}: {len(fuzzy)} marked 'needs work' — confirm them in Poedit")
    return out


class _SafeDict(dict):
    """Stands in for any placeholder, whether it is formatted with %s or %d."""

    def __missing__(self, key):
        return 0


def cmd_check(args) -> int:
    langs = args.languages or languages()
    # The extracted strings must all be in every .po, or the .po is behind.
    template = extract_catalog()
    found: list[str] = []
    for lang in langs:
        catalog = read_catalog(lang)
        missing = [m.id for m in template if m.id and m.id not in catalog]
        if missing:
            found.append(f"{lang}: {len(missing)} string(s) in the source but not in the .po "
                         f"— run: python tools/i18n.py update")
        found.extend(problems(lang, allow_missing=args.allow_missing))
    for line in found:
        print(line)
    if not found:
        print(f"ok: {', '.join(langs) or 'no languages'}")
    return 1 if found else 0


def cmd_report(args) -> int:
    for lang in args.languages or languages():
        catalog = read_catalog(lang)
        print(f"{lang}: {_summary(catalog)}")
        for m in catalog:
            if not m.id:
                continue
            if not is_translated(m) or m.fuzzy:
                refs = ", ".join(f"{f}:{n}" for f, n in m.locations[:2])
                tag = "needs work" if m.fuzzy else "untranslated"
                print(f"  [{tag}] {m.id!r}  ({refs})")
    return 0


def cmd_add(args) -> int:
    lang = args.language
    if po_path(lang).is_file():
        print(f"{lang} already exists")
        return 1
    args.languages = [lang]
    cmd_update(args)
    print(f"\nnow add {lang!r} to i18n.SUPPORTED, PLURAL_RULES in app.js, "
          f"and the installer message tables.")
    return 0


def _summary(catalog) -> str:
    total = sum(1 for m in catalog if m.id)
    done = sum(1 for m in catalog if m.id and is_translated(m) and not m.fuzzy)
    fuzzy = sum(1 for m in catalog if m.id and m.fuzzy)
    return f"{done}/{total} translated" + (f", {fuzzy} need work" if fuzzy else "")


def main(argv=None) -> int:
    _need_babel()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="list every translatable string")
    p.add_argument("-o", "--output", help="write a .pot here (not committed)")
    p.set_defaults(func=cmd_extract)

    for name, func, doc in (("update", cmd_update, "merge new strings into the .po files"),
                            ("compile", cmd_compile, "compile .po -> .mo"),
                            ("report", cmd_report, "list what still needs translating")):
        p = sub.add_parser(name, help=doc)
        p.add_argument("languages", nargs="*")
        p.set_defaults(func=func)

    p = sub.add_parser("check", help="fail if anything is stale, wrong or missing")
    p.add_argument("languages", nargs="*")
    p.add_argument("--allow-missing", action="store_true",
                   help="don't fail on untranslated entries")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("add", help="start a new language")
    p.add_argument("language")
    p.set_defaults(func=cmd_add)

    args = parser.parse_args(argv)
    if not hasattr(args, "languages"):
        args.languages = []
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
