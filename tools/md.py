"""Assemble Markdown from extracted PDF primitives."""
import sys, os, re
sys.path.insert(0, os.path.dirname(__file__))
from build import build, group_lines, save_image, OUT_DIR, IMG_DIR

TITLE = "JVM 学习指南"

TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_.:-]*)>")


def esc(t):
    """Escape tag-like text such as <clinit> so renderers keep it visible."""
    return TAG_RE.sub(lambda m: "&lt;" + m.group(1) + "&gt;", t)


def make_slug(text):
    """GitHub/Typora style heading slug: lowercase, drop punctuation, spaces -> '-'."""
    t = text.strip().lower()
    t = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", t)
    t = re.sub(r"\s+", "-", t)
    return t.strip("-") or "section"


def join_text(a, b):
    """Join two wrapped lines, inserting a space only across an ASCII/CJK seam."""
    if not a:
        return b
    if not b:
        return a
    pa, pb = a[-1], b[0]
    if pa in " \t" or pb in " \t":
        return a + b
    a_ascii = pa.isascii() and pa.isalnum()
    b_ascii = pb.isascii() and pb.isalnum()
    if a_ascii and b_ascii:
        return a + " " + b
    if a_ascii != b_ascii:
        return a + " " + b
    return a + b


def detect_tables(rules, lines):
    """Group horizontal rules by their column signature to find table regions."""
    if not rules:
        return []
    groups = {}
    for r in rules:
        sig = (round(r[0] / 4), round(r[2] / 4))
        if r[0] > 780 or (r[2] - r[0]) < 20:
            continue
        groups.setdefault(sig, []).append(r)

    tables = []
    for sig, reg in groups.items():
        ys = sorted({round(r[1], 1) for r in reg})
        if len(ys) < 3:
            continue
        bands = []
        for i in range(len(ys) - 1):
            a, b = ys[i], ys[i + 1]
            if b - a > 120:
                bands = []
                continue
            rows = [ln for ln in lines if a + 0.5 < ln.y < b - 0.5]
            if not rows:
                continue
            # split rows into cells by horizontal gaps
            allruns = sorted([r for ln in rows for r in ln.runs], key=lambda r: (r.x, r.y))
            cells = []
            curcell = []
            prev_right = None
            for r in allruns:
                if prev_right is not None and r.x - prev_right > 18:
                    cells.append(curcell)
                    curcell = []
                curcell.append(r)
                prev_right = max(prev_right or 0, r.x + max(r.w, 0.1)) if curcell else r.x + max(r.w, 0.1)
            if curcell:
                cells.append(curcell)
            texts = []
            for c in cells:
                c = sorted(c, key=lambda r: (r.y, r.x))
                texts.append(re.sub(r"\s+", " ", "".join(r.text for r in c)).strip())
            bands.append({"cells": texts, "lines": rows})
        if len(bands) >= 1:
            tables.append({"y0": ys[0], "y1": ys[-1], "bands": bands, "sig": sig})
    # drop duplicate detections of the same table (one per column segment signature)
    tables.sort(key=lambda t: (t["y0"], -len(t["bands"])))
    merged = []
    for t in tables:
        dup = None
        for m in merged:
            overlap = min(t["y1"], m["y1"]) - max(t["y0"], m["y0"])
            span = min(t["y1"] - t["y0"], m["y1"] - m["y0"]) or 1
            if overlap / span > 0.5:
                dup = m
                break
        if dup is None:
            merged.append(t)
        elif len(t["bands"]) > len(dup["bands"]):
            merged[merged.index(dup)] = t
    tables = [t for t in merged if len(t["bands"]) >= 2]
    tables.sort(key=lambda t: t["y0"])
    return tables


def main():
    pdf, runs, markers, rules, images = build()
    lines = group_lines(runs)

    def marker_kind(ln):
        for mx, my in markers:
            if abs(my - (ln.y - 5)) < 9 and 0 < ln.x0 - mx < 25:
                if mx < 118:
                    return 1
                if mx < 145:
                    return 2
                return 2
        return 0

    for ln in lines:
        ln.marker = marker_kind(ln)

    tables = detect_tables(rules, lines)
    consumed = set()
    for t in tables:
        for b in t["bands"]:
            for ln in b["lines"]:
                consumed.add(id(ln))

    # ---- image extraction
    os.makedirs(IMG_DIR, exist_ok=True)
    saved = {}
    img_events = []
    idx_img = 0
    for im in images:
        key = im["num"]
        if im["y0"] < 95 and (im["x1"] - im["x0"]) < 60:
            continue  # running-header icon
        if key not in saved:
            idx_img += 1
            name = save_image(im["obj"], pdf, os.path.join(IMG_DIR, f"img-{idx_img:02d}"))
            saved[key] = name
        img_events.append({"y": im["y0"], "file": saved[key]})

    events = []
    for ln in lines:
        if id(ln) in consumed:
            continue
        if abs(ln.y - 66) < 2 and "GitHub" in ln.text:
            continue
        events.append(("line", ln))
    for t in tables:
        events.append(("table", t))
    for e in img_events:
        events.append(("img", e))
    def ev_y(e):
        kind, obj = e
        if kind == "line":
            return obj.y
        if kind == "img":
            return obj["y"] - 6
        return obj["y0"]

    events.sort(key=ev_y)

    # ---- block assembly
    blocks = []
    cur = None
    pending_lang = None

    def flush():
        nonlocal cur
        if cur is not None:
            blocks.append(cur)
            cur = None

    for kind, obj in events:
        if kind == "img":
            flush()
            blocks.append({"type": "img", "file": obj["file"]})
            continue
        if kind == "table":
            flush()
            blocks.append({"type": "table", "bands": obj["bands"]})
            continue
        ln = obj
        if ln.size < 11 and ln.x0 > 600:
            pending_lang = ln.text.strip().lower()
            continue
        if ln.size >= 19:
            flush()
            lvl = {32: 2, 28: 3, 24: 4, 20: 5}.get(round(ln.size), 4)
            blocks.append({"type": "h", "level": lvl, "text": ln.text.strip(), "y": ln.y})
            continue
        if ln.size < 14 and 118 <= ln.x0 <= 140:
            if cur and cur["type"] == "code" and (ln.page != cur["page"] or ln.y - cur["lasty"] < 40):
                cur["lines"].append(ln.text)
                cur["lasty"] = ln.y
                cur["page"] = ln.page
            else:
                flush()
                cur = {"type": "code", "lang": pending_lang, "lines": [ln.text],
                       "lasty": ln.y, "page": ln.page}
                pending_lang = None
            continue
        if cur and cur["type"] in ("p", "li", "quote") and abs(ln.x0 - cur["x0"]) < 6 and not ln.marker:
            same_page = ln.page == cur.get("page")
            if 12 < ln.y - cur["lasty"] < 34 or (not same_page and not cur["text"].rstrip().endswith(("。", "！", "？", "：", "；", "."))):
                cur["text"] = join_text(cur["text"], ln.text)
                cur["lasty"] = ln.y
                cur["page"] = ln.page
                continue
        if not ln.text.strip():
            continue
        flush()
        if ln.marker:
            cur = {"type": "li", "level": ln.marker, "text": ln.text, "x0": ln.x0, "lasty": ln.y, "page": ln.page}
        elif 140 < ln.x0 <= 155:
            cur = {"type": "li", "level": 2, "text": ln.text, "x0": ln.x0, "lasty": ln.y, "page": ln.page}
        elif 112 <= ln.x0 <= 140:
            cur = {"type": "quote", "text": ln.text, "x0": ln.x0, "lasty": ln.y, "page": ln.page}
        else:
            cur = {"type": "p", "text": ln.text, "x0": ln.x0, "lasty": ln.y, "page": ln.page}
    flush()

    # ---- markdown
    pieces = []  # (kind, text)

    pieces.append(("h", f"# {TITLE}"))
    pieces.append(("p", "> 本文档由 `doocs-jvm.pdf` 转换生成，正文结构与 PDF 保持一致，插图均为 PDF 内嵌的原生图片（见 `images/` 目录）。"))

    heads = [b for b in blocks if b["type"] == "h"]
    idx = {}
    toc = []
    seen = {}
    for b in heads:
        slug = make_slug(b["text"])
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 0
        idx[id(b)] = slug
        indent = "  " * max(0, b["level"] - 2)
        toc.append(f"{indent}- [{b['text']}](#{slug})")
    pieces.append(("h", "## 目录"))
    pieces.append(("li", "\n".join(toc)))
    pieces.append(("hr", "---"))

    current_alt = TITLE
    for b in blocks:
        t = b["type"]
        if t == "h":
            current_alt = b["text"]
            pieces.append(("h", "#" * b["level"] + " " + esc(b["text"])))
        elif t == "p":
            pieces.append(("p", esc(b["text"])))
        elif t == "li":
            pad = "  " * (b["level"] - 1)
            pieces.append(("li", f"{pad}- {esc(b['text'])}"))
        elif t == "quote":
            pieces.append(("quote", "> " + esc(b["text"])))
        elif t == "code":
            lang = b["lang"] or "java"
            pieces.append(("code", f"```{lang}\n" + "\n".join(b["lines"]) + "\n```"))
        elif t == "img":
            pieces.append(("img", f"![{current_alt}](images/{b['file']})"))
        elif t == "table":
            bands = b["bands"]
            ncol = max(len(x["cells"]) for x in bands)
            rows = []
            for x in bands:
                cells = x["cells"] + [""] * (ncol - len(x["cells"]))
                cells = [esc(c.replace("|", "\\|")) for c in cells]
                rows.append("| " + " | ".join(cells) + " |")
            rows.insert(1, "|" + "|".join([" --- "] * ncol) + "|")
            pieces.append(("table", "\n".join(rows)))

    md_parts = []
    for i, (kind, text) in enumerate(pieces):
        md_parts.append(text)
        if i + 1 < len(pieces):
            nk = pieces[i + 1][0]
            if kind == "li" and nk == "li":
                md_parts.append("\n")
            else:
                md_parts.append("\n\n")
    md = "".join(md_parts) + "\n"
    target = os.path.join(OUT_DIR, "doocs-jvm.md")
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    print("wrote", target, len(md), "chars")
    return blocks


if __name__ == "__main__":
    main()
