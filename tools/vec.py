"""Collect vector path fill bboxes (bullet markers) and image placements per page."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from pdfcore import PDF, Stream
from runs import tokenize, mmul, apply
from collections import defaultdict


def page_vectors(pdf, page, inh, page_index, scale=0.75):
    """Return list of (bbox canvas coords) for small filled paths and all filled path bboxes."""
    c = pdf.resolve(page.get("Contents"))
    chunks = c if isinstance(c, list) else [c]
    data = b"\n".join(pdf.decode_stream(pdf.resolve(x)) for x in chunks)
    media = pdf.resolve(page.get("MediaBox")) or [0, 0, 612, 792]
    H = float(media[3]) - float(media[1])

    ctm = [1, 0, 0, 1, 0, 0]
    stack = []
    cur = None  # [x0,y0,x1,y1] in device space
    fills = []
    imgs = []

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
        elif op in ("c",) and len(ops) == 6:
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
        elif op in ("S", "s", "n"):
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
                imgs.append((
                    ref.num if hasattr(ref, "num") else None,
                    bx0 / scale,
                    (H - by1) / scale + (H / scale) * page_index,
                    bx1 / scale,
                    (H - by0) / scale + (H / scale) * page_index,
                ))
    # converts fill bboxes to canvas coords
    out = []
    for b in fills:
        x0 = b[0] / scale
        x1 = b[2] / scale
        y0 = (H - b[3]) / scale + (H / scale) * page_index
        y1 = (H - b[1]) / scale + (H / scale) * page_index
        out.append((x0, y0, x1, y1))
    return out, imgs
