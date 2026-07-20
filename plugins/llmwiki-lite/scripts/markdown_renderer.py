"""Safe, dependency-free Markdown rendering for the local Wiki website."""

from __future__ import annotations
import html
import re
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse


def _route(project_id: str, kind: str, path: str) -> str:
    return f"/project/{quote(project_id, safe='')}/{kind}/{quote(path.replace(chr(92), '/').lstrip('/'), safe='/')}"


def _relative(current: str, target: str) -> str:
    target = target.replace("\\", "/").strip()
    if target.startswith("/"):
        return target.lstrip("/")
    parts: list[str] = []
    for part in (PurePosixPath(current).parent / target).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def render_inline(text: str, project_id: str, current_page: str) -> str:
    held: list[str] = []

    def hold(fragment: str) -> str:
        token = f"\ufff0{len(held)}\ufff1"
        held.append(fragment)
        return token

    text = re.sub(
        r"`([^`\n]+)`", lambda m: hold(f"<code>{html.escape(m.group(1))}</code>"), text
    )

    def wiki(m: re.Match[str]) -> str:
        raw = m.group(1)
        target, _, label = raw.partition("|")
        page, _, anchor = target.partition("#")
        page = (
            page.strip()
            if page.strip().lower().endswith(".md")
            else page.strip() + ".md"
        )
        href = _route(project_id, "page", page)
        if anchor:
            href += "#" + quote(anchor.strip().lower().replace(" ", "-"), safe="-")
        return hold(
            f'<a class="wikilink" href="{href}">{html.escape((label or target).strip())}</a>'
        )

    text = re.sub(r"\[\[([^\]]+)\]\]", wiki, text)

    def image(m: re.Match[str]) -> str:
        alt, raw = m.group(1), m.group(2).strip()
        parsed = urlparse(raw)
        if parsed.scheme.lower() in {"http", "https"}:
            return hold(
                f'<a href="{html.escape(raw, quote=True)}" target="_blank" rel="noreferrer">远程图片：{html.escape(alt or raw)}</a>'
            )
        src = _route(project_id, "asset", _relative(current_page, raw))
        return hold(
            f'<figure><img src="{src}" alt="{html.escape(alt, quote=True)}" loading="lazy"><figcaption>{html.escape(alt)}</figcaption></figure>'
        )

    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image, text)

    def link(m: re.Match[str]) -> str:
        label, raw = m.group(1), m.group(2).strip()
        parsed = urlparse(raw)
        if parsed.scheme.lower() in {"http", "https", "mailto"}:
            href = html.escape(raw, quote=True)
            attrs = ' target="_blank" rel="noreferrer"'
        else:
            raw_path, sep, anchor = raw.partition("#")
            target = _relative(current_page, raw_path)
            kind = "page" if target.lower().endswith((".md", ".markdown")) else "asset"
            href = _route(project_id, kind, target) + (
                ("#" + quote(anchor.lower().replace(" ", "-"), safe="-")) if sep else ""
            )
            attrs = ""
        return hold(f'<a href="{href}"{attrs}>{html.escape(label)}</a>')

    text = re.sub(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", link, text)
    text = html.escape(text)
    text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
    text = re.sub(
        r"\*\*(.+?)\*\*|__(.+?)__",
        lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>",
        text,
    )
    text = re.sub(
        r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)",
        lambda m: f"<em>{m.group(1) or m.group(2)}</em>",
        text,
    )
    for i, fragment in enumerate(held):
        text = text.replace(html.escape(f"\ufff0{i}\ufff1"), fragment)
    return text


def _frontmatter(text: str) -> tuple[list[str], str]:
    if not text.startswith("---\n"):
        return [], text
    end = text.find("\n---\n", 4)
    return ([], text) if end < 0 else (text[4:end].splitlines(), text[end + 5 :])


def _slug(text: str) -> str:
    return (
        re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", text.strip().lower()).strip("-")
        or "section"
    )


def _table_sep(line: str) -> bool:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c) for c in cells)


def _block_start(lines: list[str], i: int) -> bool:
    line = lines[i]
    return (
        not line.strip()
        or bool(
            re.match(
                r"^ {0,3}(#{1,6})\s+|^ {0,3}(```|~~~)|^\s*(?:[-+*]|\d+[.)])\s+|^\s*>",
                line,
            )
        )
        or bool(re.fullmatch(r"\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*", line))
        or (i + 1 < len(lines) and "|" in line and _table_sep(lines[i + 1]))
    )


def render_markdown(text: str, project_id: str, current_page: str) -> str:
    meta, body = _frontmatter(text.replace("\r\n", "\n").replace("\r", "\n"))
    out: list[str] = []
    if meta:
        rows = []
        for raw in meta:
            key, sep, value = raw.partition(":")
            rows.append(
                f"<dt>{html.escape(key.strip()) if sep and not raw[:1].isspace() else ''}</dt><dd>{render_inline(value.strip() if sep and not raw[:1].isspace() else raw, project_id, current_page) or '&nbsp;'}</dd>"
            )
        out.append(
            '<details class="frontmatter" open><summary>文档元数据</summary><dl>'
            + "".join(rows)
            + "</dl></details>"
        )
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = re.match(r"^ {0,3}(```|~~~)\s*([^ ]*)\s*$", line)
        if fence:
            marker, lang = fence.groups()
            i += 1
            code = []
            while i < len(lines) and not re.match(
                rf"^ {{0,3}}{re.escape(marker)}\s*$", lines[i]
            ):
                code.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            label = (
                f'<span class="code-language">{html.escape(lang)}</span>'
                if lang
                else ""
            )
            out.append(
                f'<div class="code-block">{label}<pre><code>{html.escape(chr(10).join(code))}</code></pre></div>'
            )
            continue
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2)
            out.append(
                f'<h{level} id="{html.escape(_slug(title), quote=True)}">{render_inline(title, project_id, current_page)}</h{level}>'
            )
            i += 1
            continue
        if re.fullmatch(r"\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*", line):
            out.append("<hr>")
            i += 1
            continue
        if i + 1 < len(lines) and "|" in line and _table_sep(lines[i + 1]):
            headers = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(
                f"<th>{render_inline(c, project_id, current_page)}</th>"
                for c in headers
            )
            trs = []
            for row in rows:
                row += [""] * max(0, len(headers) - len(row))
                trs.append(
                    "<tr>"
                    + "".join(
                        f"<td>{render_inline(c, project_id, current_page)}</td>"
                        for c in row[: len(headers)]
                    )
                    + "</tr>"
                )
            out.append(
                f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>'
            )
            continue
        if line.lstrip().startswith(">"):
            quoted = []
            while i < len(lines) and (
                lines[i].lstrip().startswith(">") or not lines[i].strip()
            ):
                quoted.append(
                    re.sub(r"^\s*>\s?", "", lines[i]) if lines[i].strip() else ""
                )
                i += 1
            callout = re.match(
                r"^\[!([A-Za-z0-9_-]+)\][+-]?\s*(.*)$", quoted[0] if quoted else ""
            )
            if callout:
                title = callout.group(2).strip() or callout.group(1).title()
                inner = render_markdown("\n".join(quoted[1:]), project_id, current_page)
                out.append(
                    f'<aside class="callout"><div class="callout-title">{html.escape(title)}</div>{inner}</aside>'
                )
            else:
                out.append(
                    f"<blockquote>{render_markdown(chr(10).join(quoted), project_id, current_page)}</blockquote>"
                )
            continue
        item = re.match(r"^\s*(?P<mark>[-+*]|\d+[.)])\s+(?P<body>.+)$", line)
        if item:
            ordered = item.group("mark")[0].isdigit()
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines):
                match = re.match(
                    r"^\s*(?P<mark>[-+*]|\d+[.)])\s+(?P<body>.+)$", lines[i]
                )
                if not match or match.group("mark")[0].isdigit() != ordered:
                    break
                value = match.group("body")
                task = re.match(r"^\[([ xX])\]\s*(.*)$", value)
                if task:
                    box = (
                        '<input type="checkbox" disabled'
                        + (" checked" if task.group(1).lower() == "x" else "")
                        + ">"
                    )
                    items.append(
                        f'<li class="task-item">{box}{render_inline(task.group(2), project_id, current_page)}</li>'
                    )
                else:
                    items.append(
                        f"<li>{render_inline(value, project_id, current_page)}</li>"
                    )
                i += 1
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        paragraph = [line.strip()]
        i += 1
        while i < len(lines) and not _block_start(lines, i):
            paragraph.append(lines[i].strip())
            i += 1
        out.append(
            f"<p>{render_inline(' '.join(paragraph), project_id, current_page)}</p>"
        )
    return "\n".join(out)
