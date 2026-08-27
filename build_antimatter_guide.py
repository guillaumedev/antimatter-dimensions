#!/usr/bin/env python3
"""Build a clean, single-file HTML copy of the Antimatter Dimensions guide."""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, NavigableString, Tag


SOURCE_PAGE = "https://antimatter-dimensions.fandom.com/wiki/Guide"
API_URL = (
    "https://antimatter-dimensions.fandom.com/api.php"
    "?action=parse&page=Guide&prop=text%7Cdisplaytitle&format=json&origin=*"
)
CACHE_FILE = Path("/tmp/antimatter-guide.json")
OUTPUT_FILE = Path(__file__).with_name("index.html")
USER_AGENT = "Mozilla/5.0 (compatible; offline-reading-copy/1.0)"

PROGRESS_SCRIPT = r"""  <script>
    (() => {
      "use strict";

      const COMPLETED_KEY = "antimatter-guide-completed-v1";
      const POSITION_KEY = "antimatter-guide-position-v1";
      const headings = [...document.querySelectorAll(".article h2, .article h3, .article h4, .article h5, .article h6")];
      const progressCount = document.getElementById("progress-count");
      const progressBar = document.getElementById("progress-bar");
      const storageStatus = document.getElementById("storage-status");
      const resetButton = document.getElementById("reset-progress");
      let completed = new Set();
      let saveTimer = 0;

      function readJSON(key, fallback) {
        try {
          const value = localStorage.getItem(key);
          return value ? JSON.parse(value) : fallback;
        } catch (_) {
          storageStatus.textContent = "Sauvegarde locale indisponible";
          storageStatus.classList.add("is-error");
          return fallback;
        }
      }

      function writeJSON(key, value) {
        try {
          localStorage.setItem(key, JSON.stringify(value));
          storageStatus.textContent = "Sauvegardé sur cet appareil";
          storageStatus.classList.remove("is-error");
          return true;
        } catch (_) {
          storageStatus.textContent = "Sauvegarde locale indisponible";
          storageStatus.classList.add("is-error");
          return false;
        }
      }

      function tocItemFor(sectionId) {
        for (const link of document.querySelectorAll('.toc a[href^="#"]')) {
          let target = link.getAttribute("href").slice(1);
          try { target = decodeURIComponent(target); } catch (_) {}
          if (target === sectionId) return link.closest("li");
        }
        return null;
      }

      function updateProgress() {
        const count = headings.filter((heading) => completed.has(heading.id)).length;
        const percent = headings.length ? Math.round((count / headings.length) * 100) : 0;
        progressCount.textContent = `${count} / ${headings.length} sections terminées`;
        progressBar.style.width = `${percent}%`;
        progressBar.parentElement.setAttribute("aria-valuenow", String(percent));
      }

      function setCompleted(sectionId, isCompleted, persist = true) {
        if (isCompleted) completed.add(sectionId);
        else completed.delete(sectionId);

        const heading = document.getElementById(sectionId);
        const checkbox = heading?.querySelector('.section-done-control input[type="checkbox"]');
        if (checkbox) checkbox.checked = isCompleted;
        heading?.classList.toggle("is-complete", isCompleted);
        tocItemFor(sectionId)?.classList.toggle("is-done", isCompleted);

        updateProgress();
        if (persist) writeJSON(COMPLETED_KEY, [...completed]);
      }

      function addSectionControls() {
        for (const heading of headings) {
          const control = document.createElement("label");
          control.className = "section-done-control";
          control.title = "Marquer cette section comme terminée";

          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.checked = completed.has(heading.id);
          checkbox.setAttribute("aria-label", "Section terminée");

          const label = document.createElement("span");
          label.textContent = "Fait";
          control.append(checkbox, label);
          heading.append(" ", control);

          checkbox.addEventListener("change", () => {
            if (checkbox.checked) {
              const currentIndex = headings.indexOf(heading);
              for (let index = 0; index <= currentIndex; index += 1) {
                setCompleted(headings[index].id, true, false);
              }
              writeJSON(COMPLETED_KEY, [...completed]);
            } else {
              setCompleted(heading.id, false);
            }
          });

          setCompleted(heading.id, checkbox.checked, false);
        }
      }

      function currentPosition() {
        const marker = window.scrollY + Math.min(220, window.innerHeight * 0.3);
        let active = null;
        for (const heading of headings) {
          if (heading.offsetTop <= marker) active = heading;
          else break;
        }
        return {
          scrollY: Math.round(window.scrollY),
          sectionId: active?.id || "",
          sectionOffset: active ? Math.round(window.scrollY - active.offsetTop) : 0,
          savedAt: Date.now()
        };
      }

      function savePosition() {
        writeJSON(POSITION_KEY, currentPosition());
      }

      function restorePosition() {
        const saved = readJSON(POSITION_KEY, null);
        if (!saved) return;
        let target = Number(saved.scrollY) || 0;
        const heading = saved.sectionId ? document.getElementById(saved.sectionId) : null;
        if (heading) target = heading.offsetTop + (Number(saved.sectionOffset) || 0);
        requestAnimationFrame(() => window.scrollTo({ top: Math.max(0, target), behavior: "auto" }));
      }

      completed = new Set(readJSON(COMPLETED_KEY, []));
      addSectionControls();
      updateProgress();

      if ("scrollRestoration" in history) history.scrollRestoration = "manual";
      window.addEventListener("load", () => setTimeout(restorePosition, 80), { once: true });
      window.addEventListener("scroll", () => {
        window.clearTimeout(saveTimer);
        saveTimer = window.setTimeout(savePosition, 500);
      }, { passive: true });
      window.addEventListener("pagehide", savePosition);

      resetButton.addEventListener("click", () => {
        if (!window.confirm("Effacer les sections terminées et la dernière position enregistrée ?")) return;
        completed.clear();
        try {
          localStorage.removeItem(COMPLETED_KEY);
          localStorage.removeItem(POSITION_KEY);
        } catch (_) {}
        for (const heading of headings) setCompleted(heading.id, false, false);
        updateProgress();
        window.scrollTo({ top: 0, behavior: "smooth" });
        storageStatus.textContent = "Progression réinitialisée";
      });
    })();
  </script>
"""


def fetch(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": SOURCE_PAGE},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except OSError:
        # The workspace may restrict Python's direct DNS access while allowing
        # the system downloader. Keep the builder usable in both environments.
        return subprocess.check_output(
            [
                "curl",
                "-sS",
                "-L",
                "--compressed",
                "-A",
                USER_AGENT,
                "-e",
                SOURCE_PAGE,
                url,
            ],
            timeout=45,
        )


def load_page_data() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return json.loads(fetch(API_URL))


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "section"


def unique_id(candidate: str, used: set[str]) -> str:
    candidate = candidate.strip() or "section"
    result = candidate
    suffix = 2
    while result in used:
        result = f"{candidate}-{suffix}"
        suffix += 1
    used.add(result)
    return result


def content_type(url: str, payload: bytes) -> str:
    guessed = mimetypes.guess_type(urlparse(url).path)[0]
    if guessed:
        return guessed
    if payload.startswith(b"\x89PNG"):
        return "image/png"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.lstrip().startswith(b"<svg"):
        return "image/svg+xml"
    return "application/octet-stream"


def embed_images(soup: BeautifulSoup) -> tuple[int, int]:
    embedded = 0
    failed = 0
    cache: dict[str, str] = {}

    for image in soup.select("img"):
        lazy_source = image.get("data-src")
        if lazy_source:
            image["src"] = lazy_source
        source = image.get("src", "")
        if not source or source.startswith("data:"):
            continue
        source = urljoin(SOURCE_PAGE, source)
        try:
            if source not in cache:
                payload = fetch(source)
                mime = content_type(source, payload)
                encoded = base64.b64encode(payload).decode("ascii")
                cache[source] = f"data:{mime};base64,{encoded}"
            image["src"] = cache[source]
            embedded += 1
        except Exception:
            image["src"] = source
            failed += 1

        for attribute in (
            "data-src",
            "data-srcset",
            "srcset",
            "loading",
            "data-image-key",
            "data-image-name",
        ):
            image.attrs.pop(attribute, None)
        image["loading"] = "lazy"
        image["decoding"] = "async"

    return embedded, failed


def clean_article(raw_html: str) -> tuple[BeautifulSoup, list[dict[str, str | int]]]:
    soup = BeautifulSoup(raw_html, "html.parser")

    # This first table is a wiki-wide navigation template, not part of the guide.
    for table in list(soup.select("table.wikitable")):
        if table.get_text(" ", strip=True).startswith("More about Antimatter Dimensions"):
            table.decompose()

    for selector in (
        "script",
        "style",
        "noscript",
        ".mw-editsection",
        ".mw-empty-elt",
        "#toc",
        ".toc",
    ):
        for node in soup.select(selector):
            node.decompose()

    # Remove behavior and tracking attributes while retaining semantic content.
    for node in soup.find_all(True):
        for attribute in list(node.attrs):
            if attribute.lower().startswith("on") or attribute in {
                "data-tracking",
                "data-ref",
                "data-expanded",
            }:
                node.attrs.pop(attribute, None)

    # Fandom emits both accessible MathML and a fallback image. Keep the image in
    # the static copy to avoid duplicate formulas in browsers.
    for mathml in soup.select(".mwe-math-mathml-inline"):
        mathml.decompose()

    # Make links usable outside Fandom and protect the local document when a link
    # opens a new tab.
    for link in soup.select("a[href]"):
        href = link.get("href", "")
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = urljoin(SOURCE_PAGE, href)
        link["href"] = href
        if href.startswith(("http://", "https://")):
            link["target"] = "_blank"
            link["rel"] = "noopener noreferrer"

    used: set[str] = set()
    sections: list[dict[str, str | int]] = []
    for heading in soup.select("h2, h3, h4, h5, h6"):
        headline = heading.select_one(".mw-headline")
        title = (headline or heading).get_text(" ", strip=True)
        preferred = (headline.get("id") if headline else None) or slugify(title)
        section_id = unique_id(preferred, used)
        heading["id"] = section_id
        if headline:
            headline.attrs.pop("id", None)

        anchor = soup.new_tag("a", attrs={"class": "heading-anchor", "href": f"#{section_id}"})
        anchor["aria-label"] = f"Lien vers la section {title}"
        anchor.string = "#"
        heading.append(NavigableString(" "))
        heading.append(anchor)
        sections.append({"level": int(heading.name[1]), "title": title, "id": section_id})

    # Keep wide tables from breaking the reading column.
    for table in list(soup.select("table")):
        wrapper = soup.new_tag("div", attrs={"class": "table-scroll", "role": "region"})
        wrapper["aria-label"] = "Tableau défilant horizontalement"
        table.wrap(wrapper)

    # Paragraphs used only as layout spacers add a lot of visual noise offline.
    for paragraph in list(soup.find_all("p")):
        if not paragraph.get_text(strip=True) and not paragraph.find(("img", "br")):
            paragraph.decompose()

    return soup, sections


def toc_markup(sections: list[dict[str, str | int]]) -> str:
    items = []
    for section in sections:
        level = int(section["level"])
        title = str(section["title"])
        item_soup = BeautifulSoup("", "html.parser")
        link = item_soup.new_tag("a", href=f"#{section['id']}")
        link.string = html.unescape(title)
        item = item_soup.new_tag("li", attrs={"class": f"toc-level-{level}"})
        item.append(link)
        items.append(str(item))
    return "\n".join(items)


def build_document(article: BeautifulSoup, sections: list[dict[str, str | int]]) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    toc = toc_markup(sections)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>Antimatter Dimensions — Guide</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f5f3ee;
      --surface: #fffdf8;
      --text: #24231f;
      --muted: #68645b;
      --line: #ddd8cb;
      --accent: #6b4eff;
      --accent-soft: #eeeaff;
      --code: #eeeae1;
      --shadow: 0 16px 48px rgba(43, 37, 24, .08);
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #171716;
        --surface: #222220;
        --text: #eeece5;
        --muted: #aaa69c;
        --line: #3b3934;
        --accent: #ae9dff;
        --accent-soft: #302b49;
        --code: #302f2b;
        --shadow: none;
      }}
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; scroll-padding-top: 1.5rem; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 17px/1.68 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    a {{ color: var(--accent); text-underline-offset: .16em; }}
    a:hover {{ text-decoration-thickness: 2px; }}
    .page-header {{
      padding: clamp(2.5rem, 7vw, 6rem) max(1.2rem, calc((100vw - 1600px) / 2));
      background: linear-gradient(135deg, #141319, #2d235c 58%, #6149dc);
      color: white;
    }}
    .eyebrow {{ margin: 0 0 .45rem; color: #c9c0ff; font-size: .78rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }}
    .page-header h1 {{ max-width: 900px; margin: 0; font-size: clamp(2.4rem, 7vw, 5.2rem); line-height: .98; letter-spacing: -.055em; }}
    .lede {{ max-width: 720px; margin: 1.25rem 0 0; color: #ded9f4; font-size: 1.05rem; }}
    .layout {{ display: grid; grid-template-columns: minmax(250px, 290px) minmax(0, 1fr); gap: clamp(2rem, 3vw, 4rem); max-width: 1600px; margin: 0 auto; padding: 2.5rem 1.2rem 5rem; align-items: start; }}
    .toc {{ position: sticky; top: 1rem; max-height: calc(100vh - 2rem); overflow: auto; border: 1px solid var(--line); border-radius: 16px; background: var(--surface); box-shadow: var(--shadow); }}
    .toc summary {{ cursor: pointer; padding: 1rem 1.1rem; font-weight: 800; border-bottom: 1px solid var(--line); }}
    .toc ol {{ list-style: none; margin: 0; padding: .75rem 1rem 1.1rem; font-size: .82rem; line-height: 1.35; }}
    .toc li {{ margin: .38rem 0; }}
    .toc li.toc-level-3 {{ padding-left: .85rem; }}
    .toc li.toc-level-4 {{ padding-left: 1.7rem; font-size: .77rem; }}
    .toc li.toc-level-5, .toc li.toc-level-6 {{ padding-left: 2.55rem; font-size: .74rem; }}
    .toc a {{ color: var(--muted); text-decoration: none; }}
    .toc a:hover {{ color: var(--accent); }}
    .toc li.is-done a {{ opacity: .62; text-decoration: line-through; }}
    .toc li.is-done a::after {{ content: " ✓"; color: #55b97a; text-decoration: none; }}
    .progress-panel {{ padding: .9rem 1rem; border-bottom: 1px solid var(--line); background: var(--bg); }}
    .progress-count {{ display: block; margin-bottom: .55rem; font-size: .8rem; }}
    .progress-track {{ height: 6px; overflow: hidden; border-radius: 999px; background: var(--line); }}
    .progress-track span {{ display: block; width: 0; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--accent), #55b97a); transition: width .2s ease; }}
    .progress-meta {{ display: flex; align-items: center; justify-content: space-between; gap: .5rem; margin-top: .6rem; }}
    .storage-status {{ color: var(--muted); font-size: .67rem; line-height: 1.25; }}
    .storage-status.is-error {{ color: #d36b6b; }}
    .reset-progress {{ padding: 0; border: 0; background: none; color: var(--muted); font: inherit; font-size: .67rem; text-decoration: underline; cursor: pointer; }}
    .reset-progress:hover {{ color: var(--accent); }}
    .article {{ min-width: 0; border: 1px solid var(--line); border-radius: 20px; background: var(--surface); padding: clamp(1.25rem, 4vw, 3.5rem); box-shadow: var(--shadow); }}
    .article > :first-child {{ margin-top: 0; }}
    h2, h3, h4, h5, h6 {{ line-height: 1.2; letter-spacing: -.02em; scroll-margin-top: 1.5rem; }}
    h2 {{ margin: 3.8rem 0 1.1rem; padding-top: 1.2rem; border-top: 1px solid var(--line); font-size: clamp(1.65rem, 3vw, 2.25rem); }}
    h3 {{ margin: 2.7rem 0 .8rem; font-size: 1.42rem; }}
    h4 {{ margin: 2rem 0 .65rem; font-size: 1.16rem; }}
    h5, h6 {{ margin: 1.6rem 0 .55rem; font-size: 1rem; }}
    .heading-anchor {{ opacity: 0; font-weight: 500; text-decoration: none; }}
    h2:hover .heading-anchor, h3:hover .heading-anchor, h4:hover .heading-anchor, h5:hover .heading-anchor, h6:hover .heading-anchor, .heading-anchor:focus {{ opacity: 1; }}
    .section-done-control {{ display: inline-flex; align-items: center; gap: .3rem; margin-left: .35rem; padding: .28rem .5rem; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); font-size: .68rem; font-weight: 650; letter-spacing: 0; vertical-align: middle; cursor: pointer; user-select: none; }}
    .section-done-control:hover {{ border-color: var(--accent); color: var(--accent); }}
    .section-done-control input {{ width: .9rem; height: .9rem; margin: 0; accent-color: #55b97a; }}
    .is-complete .section-done-control {{ border-color: #55b97a; background: color-mix(in srgb, #55b97a 14%, transparent); color: #55b97a; }}
    p, ul, ol {{ margin: .85rem 0; }}
    li + li {{ margin-top: .28rem; }}
    img {{ max-width: 100%; height: auto; border-radius: 8px; }}
    .thumb, figure {{ max-width: 100%; margin: 1.3rem auto; padding: .75rem; border: 1px solid var(--line); border-radius: 12px; background: var(--bg); }}
    .thumbcaption, figcaption {{ margin-top: .45rem; color: var(--muted); font-size: .84rem; }}
    .tright {{ float: right; margin: .3rem 0 1rem 1.3rem; }}
    .tleft {{ float: left; margin: .3rem 1.3rem 1rem 0; }}
    code, pre {{ background: var(--code); border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    code {{ padding: .12em .35em; font-size: .88em; }}
    pre {{ overflow: auto; padding: 1rem; }}
    blockquote {{ margin: 1.2rem 0; padding: .2rem 1.1rem; border-left: 4px solid var(--accent); color: var(--muted); background: var(--accent-soft); }}
    .table-scroll {{ width: 100%; margin: 1.25rem 0; overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .88rem; background: var(--surface); }}
    th, td {{ min-width: 7rem; padding: .65rem .75rem; border: 1px solid var(--line); vertical-align: top; text-align: left; }}
    th {{ background: var(--accent-soft); }}
    .reference, .references {{ font-size: .86rem; }}
    .mw-collapsible-content {{ display: block !important; }}
    .back-to-top {{ position: fixed; right: 1rem; bottom: 1rem; display: grid; place-items: center; width: 2.75rem; height: 2.75rem; border-radius: 999px; background: var(--accent); color: white; text-decoration: none; box-shadow: var(--shadow); }}
    .license {{ max-width: 1600px; margin: 0 auto; padding: 0 1.2rem 3rem; color: var(--muted); font-size: .82rem; }}
    @media (max-width: 860px) {{
      .layout {{ grid-template-columns: 1fr; gap: 1.3rem; padding-top: 1.3rem; }}
      .toc {{ position: static; max-height: none; }}
      .toc details:not([open]) ol {{ display: none; }}
      .article {{ border-radius: 14px; }}
    }}
    @media (max-width: 580px) {{
      body {{ font-size: 16px; }}
      .article {{ padding: 1.1rem; }}
      .tright, .tleft {{ float: none; margin: 1rem auto; }}
      .back-to-top {{ width: 2.5rem; height: 2.5rem; }}
    }}
    @media print {{
      body {{ background: white; color: black; }}
      .page-header {{ padding: 1.5rem 0; background: none; color: black; }}
      .page-header .eyebrow, .page-header .lede, .toc, .back-to-top, .section-done-control {{ display: none; }}
      .layout {{ display: block; max-width: none; padding: 0; }}
      .article {{ border: 0; box-shadow: none; padding: 0; }}
      a {{ color: inherit; }}
    }}
  </style>
</head>
<body id="top">
  <header class="page-header">
    <p class="eyebrow">Offline reading edition</p>
    <h1>Antimatter Dimensions Guide</h1>
    <p class="lede">The guide content, cleaned of Fandom navigation, advertisements and interface elements. Generated {generated}.</p>
  </header>
  <main class="layout">
    <nav class="toc" aria-label="Table of contents">
      <details open>
        <summary>Table of contents</summary>
        <div class="progress-panel">
          <strong class="progress-count" id="progress-count">0 / {len(sections)} sections terminées</strong>
          <div class="progress-track" role="progressbar" aria-label="Progression" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><span id="progress-bar"></span></div>
          <div class="progress-meta">
            <span class="storage-status" id="storage-status">Sauvegardé sur cet appareil</span>
            <button class="reset-progress" id="reset-progress" type="button">Réinitialiser</button>
          </div>
        </div>
        <ol>{toc}</ol>
      </details>
    </nav>
    <article class="article">
      {article.decode_contents()}
    </article>
  </main>
  <a class="back-to-top" href="#top" aria-label="Back to top">↑</a>
  <footer class="license">
    Source: <a href="{SOURCE_PAGE}">Antimatter Dimensions Wiki — Guide</a> ·
    <a href="{SOURCE_PAGE}?action=history">contributors and revision history</a> ·
    community content is available under
    <a href="https://creativecommons.org/licenses/by-sa/3.0/">CC BY-SA</a> unless otherwise noted.
  </footer>
{PROGRESS_SCRIPT}
</body>
</html>
"""


def main() -> None:
    data = load_page_data()
    raw_html = data["parse"]["text"]["*"]
    article, sections = clean_article(raw_html)
    embedded, failed = embed_images(article)
    OUTPUT_FILE.write_text(build_document(article, sections), encoding="utf-8")
    print(
        f"Created {OUTPUT_FILE} with {len(sections)} sections; "
        f"embedded {embedded} images ({failed} left remote)."
    )


if __name__ == "__main__":
    main()
