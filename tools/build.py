"""Convert doocs-jvm.pdf into Markdown with extracted native images."""
import sys, os, re, io, json
sys.path.insert(0, os.path.dirname(__file__))
from pdfcore import PDF, Stream
from runs import extract_runs, tokenize, mmul, apply
from pdftext import FontInfo

PDF_PATH = r"C:\Users\沨烨\Desktop\JVM\doocs-jvm.pdf"
OUT_DIR = r"C:\Users\沨烨\Desktop\JVM"
IMG_DIR = os.path.join(OUT_DIR, "images")
SCALE = 0.75


# ---------------------------------------------------------------- geometry
def page_geometry(pdf, page, inh, page_index):
    c = pdf.resolve(page.get("Contents"))
    chunks = c if isinstance(c, list) else [c]
    data = b"\n".join(pdf.decode_stream(pdf.resolve(x)) for x in chunks)
    media = pdf.resolve(page.get("MediaBox")) or [0, 0, 612, 792]
    H = float(media[3]) - float(media[1])
    ctm = [1, 0, 0, 1, 0, 0]
    stack = []
    cur = None
    fills, strokes, images = [], [], []

    def add_pt(x, y):
        nonlocal cur
        px, py = apply(ctm, x, y)
        if cur is None:
            cur = [px, py, px, py]
        else:
            cur[0] = min(cur[0], px)
            cur[1] = min(cur[1], py)
            cur[2] = max(cur[2], px)
            cur[3] = max(cur[3], py)

    for ops, op in tokenize(data):
        if op == "q":
            stack.append(list(ctm))
        elif op == "Q":
            if stack:
                ctm = stack.pop()
        elif op == "cm" and len(ops) == 6:
            ctm = mmul([float(v) for v in ops], ctm)
        elif op in ("m", "l") and len(ops) == 2:
            add_pt(float(ops[0]), float(ops[1]))
        elif op == "c" and len(ops) == 6:
            for i in (0, 2, 4):
                add_pt(float(ops[i]), float(ops[i + 1]))
        elif op in ("v", "y") and len(ops) == 4:
            for i in (0, 2):
                add_pt(float(ops[i]), float(ops[i + 1]))
        elif op == "re" and len(ops) == 4:
            x, y, w, h = [float(v) for v in ops]
            add_pt(x, y)
            add_pt(x + w, y + h)
        elif op in ("f", "F", "f*", "B", "B*", "b", "b*"):
            if cur:
                fills.append(tuple(cur))
            cur = None
        elif op in ("S", "s"):
            if cur:
                strokes.append(tuple(cur))
            cur = None
        elif op == "n":
            cur = None
        elif op == "Do" and ops:
            name = str(ops[-1])
            res = pdf.resolve(inh.get("Resources")) or {}
            xs = pdf.resolve(res.get("XObject")) or {}
            ref = xs.get(name)
            o = pdf.resolve(ref)
            if isinstance(o, Stream) and str(o.get("Subtype")) == "Image":
                x0, y0 = apply(ctm, 0, 0)
                x1, y1 = apply(ctm, 1, 1)
                bx0, bx1 = sorted((x0, x1))
                by0, by1 = sorted((y0, y1))
                images.append({
                    "num": ref.num if hasattr(ref, "num") else None,
                    "obj": o,
                    "x0": bx0 / SCALE,
                    "x1": bx1 / SCALE,
                    "y0": (H - by1) / SCALE + (H / SCALE) * page_index,
                    "y1": (H - by0) / SCALE + (H / SCALE) * page_index,
                })

    def to_canvas(b):
        return (b[0] / SCALE,
                (H - b[3]) / SCALE + (H / SCALE) * page_index,
                b[2] / SCALE,
                (H - b[1]) / SCALE + (H / SCALE) * page_index)

    return [to_canvas(b) for b in fills], [to_canvas(b) for b in strokes], images


# ---------------------------------------------------------------- images
def save_image(obj, pdf, path_noext):
    filt = pdf.resolve(obj.get("Filter"))
    filters = filt if isinstance(filt, list) else ([filt] if filt else [])
    fnames = [str(f) for f in filters]
    if "DCTDecode" in fnames:
        with open(path_noext + ".jpg", "wb") as f:
            f.write(obj.raw)
        return os.path.basename(path_noext + ".jpg")
    data = pdf.decode_stream(obj)
    w = int(pdf.resolve(obj.get("Width")))
    h = int(pdf.resolve(obj.get("Height")))
    cs = pdf.resolve(obj.get("ColorSpace"))
    bpc = int(pdf.resolve(obj.get("BitsPerComponent", 8)) or 8)
    from PIL import Image
    if bpc != 8:
        return None
    if str(cs) == "DeviceRGB":
        mode, ch = "RGB", 3
    elif str(cs) == "DeviceGray":
        mode, ch = "L", 1
    elif str(cs) == "DeviceCMYK":
        mode, ch = "CMYK", 4
    elif isinstance(cs, list) and str(cs[0]) == "ICCBased":
        stm = pdf.resolve(cs[1])
        n = int(pdf.resolve(stm.dic.get("N", 3)))
        mode, ch = ("RGB", 3) if n == 3 else (("L", 1) if n == 1 else ("CMYK", 4))
    elif isinstance(cs, list) and str(cs[0]) == "Indexed":
        base = str(pdf.resolve(cs[1]))
        hival = int(pdf.resolve(cs[2]))
        lookup = pdf.resolve(cs[3])
        pal = pdf.decode_stream(lookup) if isinstance(lookup, Stream) else lookup
        img = Image.frombytes("P", (w, h), data[:w * h])
        img.putpalette(list(pal)[:3 * (hival + 1)] + [0] * (768 - 3 * (hival + 1)))
        img = img.convert("RGB")
        img.save(path_noext + ".png")
        return os.path.basename(path_noext + ".png")
    else:
        mode, ch = "RGB", 3
    need = w * h * ch
    if len(data) < need:
        return None
    img = Image.frombytes(mode, (w, h), data[:need])
    img.save(path_noext + ".png")
    return os.path.basename(path_noext + ".png")


# ---------------------------------------------------------------- lines
FIXES = [
    ("So Reference", "Soft Reference"),
]


def _clean(text):
    if "\x00" in text:
        text = text.replace("\x00", "")
        for a, b in FIXES:
            text = text.replace(a, b)
    return text


class Line:
    __slots__ = ("y", "x0", "x1", "size", "runs", "text", "page", "mono", "marker", "kind")

    def __init__(self, runs):
        self.runs = sorted(runs, key=lambda r: r.x)
        self.y = runs[0].y
        self.page = runs[0].page
        self.x0 = min(r.x for r in self.runs)
        self.x1 = max(r.x + max(r.w, 0) for r in self.runs)
        self.size = max(r.size for r in self.runs)
        self.mono = all("Mono" in (r.font.base or "") or "Courier" in (r.font.base or "") for r in self.runs)
        self.marker = 0
        self.text = self._text()

    def _text(self):
        out = []
        prev = None
        for r in self.runs:
            if prev is not None:
                gap = r.x - (prev.x + prev.w)
                need = gap > 0.32 * r.size
                # Chinese <-> latin boundary usually has a visual word gap
                if not need and gap > 0.06 * r.size:
                    a = prev.text[-1:] if prev.text else ""
                    b = r.text[:1] if r.text else ""
                    if (a.isascii() and a.isalnum() and not b.isascii()) or (
                        b.isascii() and b.isalnum() and not a.isascii()
                    ):
                        need = True
                if need and not prev.text.endswith(" ") and not r.text.startswith(" "):
                    n = 1
                    if self.mono and prev.w > 0:
                        adv = prev.w / max(1, len(prev.text))
                        if adv > 0:
                            n = max(1, int(round(gap / adv)))
                    out.append(" " * n)
            out.append(r.text)
            prev = r
        return _clean("".join(out).rstrip())


def build(pdf_path=PDF_PATH):
    pdf = PDF(pdf_path)
    pages = pdf.pages()
    cache = {}
    all_runs = []
    markers = []
    rules = []
    images = []
    labels = []
    for i, (page, inh) in enumerate(pages):
        runs = extract_runs(pdf, i, page, inh, cache)
        all_runs.extend(runs)
        fills, strokes, imgs = page_geometry(pdf, page, inh, i)
        for b in fills:
            w, h = b[2] - b[0], b[3] - b[1]
            if h < 3 and w > 90:
                rules.append(b)
            elif 1.5 <= w <= 14 and 1.5 <= h <= 14 and b[0] < 200:
                markers.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
        for b in strokes:
            w, h = b[2] - b[0], b[3] - b[1]
            if 1.5 <= w <= 14 and 1.5 <= h <= 14 and b[0] < 200:
                markers.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
        images.extend(imgs)
    return pdf, all_runs, markers, rules, images


def group_lines(runs, tol=2.5):
    rs = sorted(runs, key=lambda r: (r.y, r.x))
    lines = []
    cur = []
    cy = None
    for r in rs:
        if cy is None or abs(r.y - cy) <= tol:
            cur.append(r)
            cy = r.y if cy is None else cy
        else:
            lines.append(Line(cur))
            cur = [r]
            cy = r.y
    if cur:
        lines.append(Line(cur))
    lines.sort(key=lambda l: l.y)
    return lines


if __name__ == "__main__":
    pdf, runs, markers, rules, images = build()
    lines = group_lines(runs)
    print("runs", len(runs), "lines", len(lines), "markers", len(markers), "rules", len(rules), "images", len(images))
    with open(os.path.join(OUT_DIR, "tools", "debug_lines.txt"), "w", encoding="utf-8") as f:
        for ln in lines:
            mk = ""
            for mx, my in markers:
                if abs(my - (ln.y - 5)) < 9 and 0 < ln.x0 - mx < 25:
                    mk = f"B@{mx:.0f}"
                    break
            f.write(f"y={ln.y:9.1f} x={ln.x0:6.1f}-{ln.x1:6.1f} s={ln.size:5.1f} {mk:6s} {'M' if ln.mono else ' '} | {ln.text}\n")
    print("wrote debug_lines.txt")
