"""A dependency-free guard against ONE defect class in the screen templates.

⛔ THE DEFECT THIS PINS: a raw newline inside a JavaScript string literal is a
syntax error, and it takes the WHOLE page's behaviour down with it — Alpine then
reports every binding on the page as "not defined". The static chrome still
renders, so a markup-only assertion stays GREEN while the page is dead.

⭐ It happened on 16-Aug-2026 while editing `strategy_health.html` through a
shell heredoc, which collapsed `\\n` into a real newline inside `out.join(...)`.
The whole screen went blank of data and every test still passed.
"""
from __future__ import annotations


def script_of(tpl: str) -> str:
    """The template's inline `<script>` body."""
    i = tpl.index("<script>")
    return tpl[i + len("<script>"):tpl.index("</script>", i)]


def string_literals_spanning_a_newline(js: str) -> list:
    """Every `'...'` / `"..."` literal that contains a RAW newline.

    Walks the source tracking comments and escapes, so an apostrophe inside a
    double-quoted string is not mistaken for a quote. ⛔ Template literals are
    NOT flagged: a backtick string may legally span lines.
    """
    out: list = []
    i, n = 0, len(js)
    while i < n:
        c = js[i]
        if c == "\\":
            i += 2
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "/":
            j = js.find("\n", i)
            if j < 0:
                break
            i = j
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "*":
            j = js.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == "`":                       # template literal — may span lines
            j = i + 1
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == "`":
                    break
                j += 1
            i = j + 1
            continue
        if c in ("'", '"'):
            quote, j = c, i + 1
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == "\n":
                    out.append(js[max(0, i - 40):j].splitlines()[-1][-70:])
                    break
                if js[j] == quote:
                    break
                j += 1
            i = j + 1
            continue
        i += 1
    return out


def unbalanced_braces(js: str) -> int:
    """`{` minus `}` outside strings and comments — 0 for a well-formed body."""
    depth, i, n = 0, 0, len(js)
    while i < n:
        c = js[i]
        if c == "\\":
            i += 2
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "/":
            j = js.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "*":
            j = js.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in ("'", '"', "`"):
            quote, j = c, i + 1
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == quote or (quote != "`" and js[j] == "\n"):
                    break
                j += 1
            i = j + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return depth
