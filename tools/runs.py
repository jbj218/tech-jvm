import sys, os, re
sys.path.insert(0, os.path.dirname(__file__))
from pdfcore import PDF, Stream, Ref, Lexer, Name, Keyword
from pdftext import FontInfo

WS = b"\x00\t\n\x0c\r "
DELIM = b"()<>[]{}/%"


def tokenize(data):
    """Yield (operands_list, operator_str) for a content stream."""
    i = 0
    n = len(data)
    operands = []
    while i < n:
        c = data[i]
        if c in WS:
            i += 1
            continue
        if c == 0x25:  # %
            while i < n and data[i] not in b"\r\n":
                i += 1
            continue
        if c == 0x2F:  # /
            m = re.match(rb"/([^\s()<>\[\]{}/%]*)", data[i:])
            operands.append(Name(m.group(1).decode("latin-1")))
            i += m.end()
            continue
        if c == 0x28:  # (
            lx = Lexer(data, i)
            s = lx.parse_literal_string()
            operands.append(s)
            i = lx.pos
            continue
        if c == 0x3C:  # <
            if data[i:i + 2] == b"<<":
                i += 2
                operands.append(Name("<<"))
                continue
            j = data.find(b">", i)
            hx = re.sub(rb"[^0-9A-Fa-f]", b"", data[i + 1:j])
            if len(hx) % 2:
                hx += b"0"
            operands.append(bytes.fromhex(hx.decode()))
            i = j + 1
            continue
        if c in b"+-.0123456789":
            m = re.match(rb"[+-]?[0-9.]+", data[i:])
            tok = m.group(0)
            i += m.end()
            operands.append(float(tok) if b"." in tok else int(tok))
            continue
        if c == 0x5B:  # [
            operands.append(Name("ArrStart"))
            i += 1
            continue
        if c == 0x5D:
            # collect array elements since last ArrStart
            arr = []
            while operands and not (isinstance(operands[-1], Name) and operands[-1] == "ArrStart"):
                arr.append(operands.pop())
            if operands:
                operands.pop()
            arr.reverse()
            operands.append(arr)
            i += 1
            continue
        if c in b">]{}":
            i += 1
            continue
        if c == 0x2A:  # '*' operator
            yield [], "*"
            i += 1
            continue
        if c == 0x27:  # ' operator
            yield [], "'"
            i += 1
            continue
        if c == 0x22:  # " operator
            yield [], '"'
            i += 1
            continue
        m = re.match(rb"[A-Za-z][A-Za-z0-9*]*", data[i:])
        if not m:
            i += 1
            continue
        op = m.group(0).decode("latin-1")
        i += m.end()
        if op == "BI":
            # inline image: skip to EI
            j = data.find(b"EI", i)
            i = j + 2 if j > 0 else n
            operands = []
            continue
        yield operands, op
        operands = []


def mmul(a, b):
    """Multiply two 6-element matrices."""
    return [
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    ]


def apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


class Run:
    __slots__ = ("x", "y", "size", "font", "text", "page", "seq", "w")

    def __init__(self, x, y, size, font, text, page, seq, w=0.0):
        self.x, self.y, self.size, self.font, self.text, self.page, self.seq = x, y, size, font, text, page, seq
        self.w = w

    def __repr__(self):
        return f"Run({self.x:.1f},{self.y:.1f},{self.size:.1f},{self.font.base},{self.text!r})"


def extract_runs(pdf, page_index, page, inh, fonts_cache, scale=0.75, canvas_w=816.0):
    res = pdf.resolve(inh.get("Resources")) or {}
    fonts = pdf.resolve(res.get("Font")) or {}
    fontmap = {}
    for k, v in fonts.items():
        fd = pdf.resolve(v)
        key = (k, id(fd))
        if key not in fonts_cache:
            fonts_cache[key] = FontInfo(pdf, fd, k)
        fontmap[k] = fonts_cache[key]

    c = pdf.resolve(page.get("Contents"))
    chunks = c if isinstance(c, list) else [c]
    data = b"\n".join(pdf.decode_stream(pdf.resolve(x)) for x in chunks)

    ctm = [1, 0, 0, 1, 0, 0]
    stack = []
    tm = [1, 0, 0, 1, 0, 0]
    tlm = [1, 0, 0, 1, 0, 0]
    font = None
    size = 0
    leading = 0
    runs = []
    seq = 0
    media = pdf.resolve(page.get("MediaBox")) or [0, 0, 612, 792]
    H = float(media[3]) - float(media[1])

    def flush(raw):
        nonlocal seq
        if not raw or not isinstance(raw, bytes) or font is None:
            return
        m = mmul(tm, ctm)
        xd, yd = apply(m, 0, 0)
        # device -> canvas
        xc = xd / scale
        yc = (H - yd) / scale + (H / scale) * page_index
        fsz = size * (abs(m[1]) ** 2 + abs(m[3]) ** 2) ** 0.5 / scale
        text, wem = font.decode2(raw)
        if text:
            runs.append(Run(xc, yc, fsz, font, text, page_index, seq, wem * fsz))
            seq += 1

    for ops, op in tokenize(data):
        if op == "q":
            stack.append(list(ctm))
        elif op == "Q":
            if stack:
                ctm = stack.pop()
        elif op == "cm" and len(ops) == 6:
            ctm = mmul([float(v) for v in ops], ctm)
        elif op == "BT":
            tm = [1, 0, 0, 1, 0, 0]
            tlm = [1, 0, 0, 1, 0, 0]
        elif op == "Tf" and len(ops) == 2:
            font = fontmap.get(str(ops[0]))
            size = float(ops[1])
        elif op == "TL" and ops:
            leading = float(ops[0])
        elif op == "Tm" and len(ops) == 6:
            tm = [float(v) for v in ops]
            tlm = list(tm)
        elif op in ("Td", "TD") and len(ops) == 2:
            tx, ty = float(ops[0]), float(ops[1])
            if op == "TD":
                leading = -ty
            tlm = mmul([1, 0, 0, 1, tx, ty], tlm)
            tm = list(tlm)
        elif op == "T*":
            tlm = mmul([1, 0, 0, 1, 0, -leading], tlm)
            tm = list(tlm)
        elif op == "Tj" and ops:
            s = ops[-1]
            if isinstance(s, bytes):
                flush(s if isinstance(s, bytes) else b"")
        elif op == "'" and ops:
            tlm = mmul([1, 0, 0, 1, 0, -leading], tlm)
            tm = list(tlm)
            s = ops[-1]
            if isinstance(s, bytes):
                flush(s if isinstance(s, bytes) else b"")
        elif op == '"' and len(ops) >= 3:
            tlm = mmul([1, 0, 0, 1, 0, -leading], tlm)
            tm = list(tlm)
            flush(ops[-1] if isinstance(ops[-1], bytes) else b"")
        elif op == "TJ" and ops:
            arr = ops[-1]
            if isinstance(arr, list):
                for el in arr:
                    if isinstance(el, bytes):
                        flush(el)
    return runs

