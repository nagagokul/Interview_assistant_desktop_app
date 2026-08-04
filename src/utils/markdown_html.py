"""Lightweight Markdown → HTML (no Qt dependency)."""

from __future__ import annotations

import html
import re


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _inline(text: str) -> str:
    escaped = _esc(text)
    escaped = re.sub(
        r"`([^`]+)`",
        r"<code style='background:#1A2230;padding:1px 4px;border-radius:3px;'>\1</code>",
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", escaped)
    return escaped


def _format_prose(text: str) -> str:
    lines_out: list[str] = []
    for line in text.split("\n"):
        if line.startswith("### "):
            lines_out.append(f"<h4 style='color:#A8C1FF;margin:8px 0 4px;'>{_esc(line[4:])}</h4>")
        elif line.startswith("## "):
            lines_out.append(f"<h3 style='color:#A8C1FF;margin:10px 0 4px;'>{_esc(line[3:])}</h3>")
        elif line.startswith("# "):
            lines_out.append(f"<h2 style='color:#A8C1FF;margin:10px 0 4px;'>{_esc(line[2:])}</h2>")
        elif line.startswith("- ") or line.startswith("* "):
            lines_out.append(f"<div style='margin-left:12px;'>• {_inline(line[2:])}</div>")
        elif re.match(r"^\d+\.\s", line):
            lines_out.append(f"<div style='margin-left:12px;'>{_inline(line)}</div>")
        elif line.strip() == "":
            lines_out.append("<br/>")
        else:
            lines_out.append(f"<div>{_inline(line)}</div>")
    return "\n".join(lines_out)


def markdown_to_html(md: str) -> str:
    """Lightweight Markdown → HTML for QTextBrowser (no external deps)."""
    if not md:
        return ""
    text = md.replace("\r\n", "\n")

    parts: list[str] = []
    cursor = 0
    fence_re = re.compile(r"```(\w*)\n([\s\S]*?)```")
    for match in fence_re.finditer(text):
        prose = text[cursor : match.start()]
        if prose:
            parts.append(_format_prose(prose))
        lang = match.group(1) or ""
        code = _esc(match.group(2))
        parts.append(
            f'<pre style="background:#0B1018;color:#D6E4FF;padding:8px;border-radius:6px;'
            f'font-family:Consolas,Cascadia Code,monospace;font-size:11px;white-space:pre-wrap;">'
            f'<span style="color:#7F92B0;font-size:10px;">{_esc(lang)}</span>\n{code}</pre>'
        )
        cursor = match.end()
    tail = text[cursor:]
    if tail:
        parts.append(_format_prose(tail))
    return "\n".join(parts)
