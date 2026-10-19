"""Extract the CSS rules an extracted JS bundle needs from a set of stylesheets.

Keeps a style rule when at least one of its selectors references only
class/id names that appear somewhere in the JS sources (so `.ge-panel .active`
is kept but `.sidebar .active` is not). Selectors with no class or id are kept
only when they come from a file listed in --whole. @media/@supports blocks are
filtered recursively; @keyframes are kept when a kept rule names them.

usage: extract_css.py --js DIR [--js DIR...] --whole FILE... --out OUT CSS...
"""
import argparse
import pathlib
import re
import sys

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
SEL_NAME_RE = re.compile(r"[.#]((?:[A-Za-z0-9_-]|\\.)+)")


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def parse_blocks(css, i=0):
    """Return list of (prelude, body_or_children, is_at_group) until a closing brace."""
    out = []
    n = len(css)
    while i < n:
        j = i
        depth_paren = 0
        while j < n and not (css[j] in "{};" and depth_paren == 0) and css[j] != "}":
            if css[j] == "(":
                depth_paren += 1
            elif css[j] == ")":
                depth_paren -= 1
            elif css[j] in "\"'":
                q = css[j]
                j += 1
                while j < n and css[j] != q:
                    j += 2 if css[j] == "\\" else 1
            j += 1
        if j >= n:
            break
        prelude = css[i:j].strip()
        ch = css[j]
        if ch == "}":
            return out, j + 1
        if ch == ";":
            if prelude:
                out.append(("stmt", prelude, None))
            i = j + 1
            continue
        # ch == "{"
        if prelude.startswith("@") and re.match(r"@(media|supports|layer|container|document)\b", prelude):
            children, i = parse_blocks(css, j + 1)
            out.append(("group", prelude, children))
            continue
        # raw block: find matching brace
        depth = 1
        k = j + 1
        while k < n and depth:
            c = css[k]
            if c in "\"'":
                q = c
                k += 1
                while k < n and css[k] != q:
                    k += 2 if css[k] == "\\" else 1
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            k += 1
        body = css[j + 1:k - 1]
        kind = "at" if prelude.startswith("@") else "rule"
        out.append((kind, prelude, body))
        i = k
    return out, n


def split_selectors(prelude):
    parts, depth, cur = [], 0, ""
    for c in prelude:
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if c == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def selector_names(sel):
    # ignore names inside :not(...) etc. only partially: keep simple approach
    sel = re.sub(r"\[[^\]]*\]", "", sel)
    return [m.group(1).replace("\\", "") for m in SEL_NAME_RE.finditer(sel)
            if not re.match(r"^\d", m.group(1))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--js", action="append", required=True)
    ap.add_argument("--whole", nargs="*", default=[])
    ap.add_argument("--deny", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("css", nargs="+")
    a = ap.parse_args()

    tokens = set()
    for d in a.js:
        for p in pathlib.Path(d).rglob("*"):
            if p.suffix in (".js", ".html") and p.is_file():
                tokens.update(TOKEN_RE.findall(p.read_text(encoding="utf-8", errors="replace")))
    whole = {pathlib.Path(w).name for w in a.whole}
    deny = set(a.deny)

    out_parts = []
    keyframes = {}
    used_anims = set()
    stats = []

    def keep_rule(prelude, fname):
        sels = split_selectors(prelude)
        kept = []
        for s in sels:
            names = selector_names(s)
            if not names:
                if fname in whole:
                    kept.append(s)
                continue
            if any(nm in deny or any(nm.startswith(d.rstrip("*")) for d in deny if d.endswith("*")) for nm in names):
                continue
            if all(nm in tokens for nm in names):
                kept.append(s)
        return kept

    def walk(items, fname):
        res = []
        for kind, prelude, body in items:
            if kind == "rule":
                kept = keep_rule(prelude, fname)
                if kept:
                    for m in re.finditer(r"animation(?:-name)?\s*:\s*([^;]+)", body):
                        used_anims.update(TOKEN_RE.findall(m.group(1)))
                    res.append(",\n".join(kept) + " {" + body.rstrip() + "\n}")
            elif kind == "group":
                inner = walk(body, fname)
                if inner:
                    res.append(prelude + " {\n" + "\n".join(inner) + "\n}")
            elif kind == "at":
                m = re.match(r"@(?:-webkit-)?keyframes\s+([\w-]+)", prelude)
                if m:
                    keyframes.setdefault(m.group(1), prelude + " {" + body + "}")
                elif prelude.startswith("@font-face"):
                    if fname in whole:
                        res.append(prelude + " {" + body + "}")
                elif prelude.startswith("@property"):
                    res.append(prelude + " {" + body + "}")
            elif kind == "stmt":
                if fname in whole and prelude.startswith("@import") is False:
                    res.append(prelude + ";")
        return res

    for path in a.css:
        p = pathlib.Path(path)
        css = strip_comments(p.read_text(encoding="utf-8"))
        items, _ = parse_blocks(css)
        kept = walk(items, p.name)
        if kept:
            text = "\n".join(kept)
            stats.append((p.name, len(text)))
            out_parts.append(f"/* ---- from odysseus/static/css/{p.name} ---- */\n" + text)

    kf = [v for k, v in keyframes.items() if k in used_anims]
    if kf:
        out_parts.append("/* ---- keyframes ---- */\n" + "\n".join(kf))

    pathlib.Path(a.out).write_text("\n\n".join(out_parts) + "\n", encoding="utf-8")
    for name, size in stats:
        print(f"{size:>8}  {name}", file=sys.stderr)
    print(f"keyframes kept: {len(kf)}", file=sys.stderr)


if __name__ == "__main__":
    main()
