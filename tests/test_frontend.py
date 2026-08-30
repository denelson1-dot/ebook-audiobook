"""Checks on the browser half of the app, which the route tests cannot see.

Every test here exists because a real bug shipped past a green suite. The route
tests assert that a page returns 200 and contains some markup; none of them
execute a line of JavaScript or resolve a single CSS selector, so an interface
can be comprehensively broken while 556 tests pass.

These are cheap structural checks, not a substitute for driving a browser. They
catch the specific class of mistake that has actually bitten:

  * a stylesheet rule that matches nothing the code generates
  * a conditional inline style silently outranked by a static one
  * the same top-level `const` declared twice on one page
  * an SVG inside a button defeating a delegated click handler
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from ebook_audiobook.web import create_app

WEB = Path(__file__).resolve().parent.parent / "ebook_audiobook" / "web"
TEMPLATES = WEB / "templates"
APP_JS = (WEB / "static" / "app.js").read_text("utf-8")
APP_CSS = (WEB / "static" / "app.css").read_text("utf-8")

INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def strip_jinja(text: str) -> str:
    """Turn template expressions into harmless literals so a JS parser can read it."""
    return JINJA.sub("0", text)


# --- the stylesheet has to match what the code actually builds ---------------

@pytest.mark.parametrize("selector, built_by", [
    (r"\.toast\b", "toast() — every confirmation and error message in the app"),
    (r"\.toast\.show\b", "toast() — the visible state"),
    (r"\.drawer\b", "openDrawer() — the chapter text peek"),
    (r"\.drawer-backdrop\b", "openDrawer()"),
    (r"\.modal-backdrop\b", "confirmDialog() and openFsBrowser()"),
    (r"\.confirm-body\b", "confirmDialog()"),
    (r"\.fs-item\b", "openFsBrowser() — each row"),
    (r"\.fs-list\b", "openFsBrowser()"),
    (r"\.spinner\b", "withBusy() and the loading states"),
    (r"\.plan-row\b", "the render plan dialog"),
    (r"\.power-mode\b", "the render plan's intensity picker"),
])
def test_stylesheet_has_a_rule_for(selector, built_by):
    """A class the JavaScript creates but the stylesheet never mentions.

    This is exactly how every toast in the app became invisible: the rule was
    written as `#toast` while `toast()` builds `<div class="toast">`, so the
    selector matched nothing and 28 call sites went out silently.
    """
    assert re.search(selector, APP_CSS), (
        f"nothing in app.css matches {selector} — needed by {built_by}")


def test_no_orphaned_id_rules_for_classes_the_js_builds():
    """The specific slip above, caught by shape rather than by name."""
    for name in ("toast", "drawer", "modal", "spinner"):
        assert f"#{name} " not in APP_CSS and f"#{name}{{" not in APP_CSS, (
            f"app.css styles #{name}, but the JavaScript builds a *class* named {name}")


# --- inline styles ----------------------------------------------------------

def test_no_conditional_inline_style_is_shadowed_by_a_static_one():
    """`style="{{ '' if x else 'display:none' }};display:flex"` shows both panes.

    Declarations are applied in order, so the static one wins and the condition
    does nothing. Layout belongs in the stylesheet; the inline attribute should
    carry only the state.
    """
    problems = []
    for f in sorted(TEMPLATES.glob("*.html")):
        for m in re.finditer(r'style="([^"]*)"', f.read_text("utf-8")):
            raw = m.group(1)
            static = JINJA.sub("", raw)
            static_props = {d.split(":", 1)[0].strip().lower()
                            for d in static.split(";") if ":" in d}
            for expr in JINJA.findall(raw):
                for prop in re.findall(r"([a-z-]+)\s*:", expr):
                    if prop in static_props:
                        problems.append(f"{f.name}: '{prop}' set both conditionally "
                                        f"and statically in style=\"{raw[:80]}\"")
    assert not problems, "\n".join(problems)


# --- delegated click handlers ----------------------------------------------

def test_delegated_clicks_match_the_button_not_what_was_clicked_inside_it():
    """`e.target.hasAttribute("data-close")` is false when the button holds an icon.

    Swapping a ✕ glyph for an inline <svg> makes e.target the <svg>, which
    carries no data attribute — silently disabling every icon-only close button.
    `closest()` is correct for both text and icons.
    """
    offenders = re.findall(r"e\.target\.hasAttribute\([^)]*\)", APP_JS)
    assert not offenders, (
        "use e.target.closest('[data-…]') instead, or an icon inside the button "
        f"breaks the handler: {offenders}")


# --- one page, one global scope --------------------------------------------

PAGES = ["/", "/new", "/voices", "/settings", "/storage"]


def _declarations(html: str) -> list[str]:
    """Top-level `const`/`let` names across every inline script on a page.

    Inline scripts share one global scope, so the same name declared in two of
    them is a SyntaxError that kills whichever block runs second — taking the
    whole page's behaviour with it, with nothing but a console message.
    """
    names = []
    for block in INLINE_SCRIPT.findall(html):
        for line in strip_jinja(block).splitlines():
            m = re.match(r"(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=", line)
            if m:
                names.append(m.group(1))
    return names


@pytest.mark.parametrize("path", PAGES)
def test_no_name_is_declared_twice_in_a_pages_global_scope(path):
    html = create_app().test_client().get(path).data.decode()
    names = _declarations(html)
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, (
        f"{path} declares {dupes} more than once at the top level of its inline "
        "scripts; the second block throws and never runs")


# --- the JavaScript parses at all ------------------------------------------

HAVE_NODE = shutil.which("node") is not None


@pytest.mark.skipif(not HAVE_NODE, reason="node is not installed")
def test_app_js_parses():
    _node_check(APP_JS, "app.js")


@pytest.mark.skipif(not HAVE_NODE, reason="node is not installed")
@pytest.mark.parametrize("template", sorted(p.name for p in TEMPLATES.glob("*.html")))
def test_inline_scripts_parse(template):
    """An unbalanced brace in a template is invisible to a 200-response test."""
    for i, block in enumerate(INLINE_SCRIPT.findall((TEMPLATES / template).read_text("utf-8"))):
        _node_check(strip_jinja(block), f"{template} block {i}")


def _node_check(source: str, label: str) -> None:
    # encoding is not optional here. Without it Python writes in the platform's
    # default encoding, which is cp1252 on Windows — and the interface contains
    # characters like U+2212 MINUS SIGN that cp1252 cannot represent at all. The
    # test then dies encoding the file rather than checking anything, on one
    # platform only. Node reads UTF-8 regardless of locale.
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as tmp:
        tmp.write(source)
        path = tmp.name
    try:
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        assert r.returncode == 0, f"{label} does not parse:\n{r.stderr.strip()}"
    finally:
        Path(path).unlink(missing_ok=True)
