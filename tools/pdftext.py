import sys, os, re
sys.path.insert(0, os.path.dirname(__file__))
from pdfcore import PDF, Stream, Ref


def parse_cmap(data):
    """Return dict code->unicode str from a ToUnicode CMap stream."""
    m = {}
    # bfchar
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", blk):
            code = int(src, 16)
            m[code] = hex_to_str(dst)
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        # <lo> <hi> <dst>  or <lo> <hi> [<d1> <d2> ...]
        for mm in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", blk, re.S):
            lo = int(mm.group(1), 16)
            hi = int(mm.group(2), 16)
            dst = mm.group(3)
            if dst.startswith(b"["):
                items = re.findall(rb"<([0-9A-Fa-f]*)>", dst)
                for i, it in enumerate(items):
                    if lo + i <= hi:
                        m[lo + i] = hex_to_str(it)
            else:
                base = hex_to_str(dst[1:-1])
                if not base:
                    continue
                codes = [ord(c) for c in base]
                for i in range(hi - lo + 1):
                    # increment last code point
                    vals = codes[:]
                    vals[-1] += i
                    m[lo + i] = "".join(chr(v) for v in vals)
    return m


def hex_to_str(hx):
    if isinstance(hx, str):
        hx = hx.encode()
    if len(hx) % 2:
        hx += b"0"
    b = bytes.fromhex(hx.decode())
    # UTF-16BE
    try:
        return b.decode("utf-16-be")
    except Exception:
        return ""


class FontInfo:
    def __init__(self, pdf, fdict, name):
        self.pdf = pdf
        self.name = name
        self.d = fdict
        self.subtype = str(fdict.get("Subtype"))
        self.base = str(fdict.get("BaseFont", ""))
        tu = pdf.resolve(fdict.get("ToUnicode"))
        self.cmap = parse_cmap(pdf.decode_stream(tu)) if isinstance(tu, Stream) else {}
        desc = pdf.resolve(fdict.get("FontDescriptor"))
        self.flags = desc.get("Flags", 0) if isinstance(desc, dict) else 0
        self.fixed = bool(self.flags & (1 << 0))
        self.is_type0 = self.subtype == "Type0"
        self.code_len = 2 if self.is_type0 else 1
        self.widths = {}
        self.dw = 0.0
        self.fm = 1.0
        if self.is_type0:
            descs = pdf.resolve(fdict.get("DescendantFonts")) or []
            if descs:
                d0 = pdf.resolve(descs[0])
                if isinstance(d0, dict):
                    self.dw = float(pdf.resolve(d0.get("DW", 1000)) or 1000) / 1000.0
                    w = pdf.resolve(d0.get("W")) or []
                    i = 0
                    while i < len(w):
                        a = pdf.resolve(w[i])
                        if i + 1 >= len(w):
                            break
                        b = pdf.resolve(w[i + 1])
                        if isinstance(b, list):
                            for k, val in enumerate(b):
                                self.widths[a + k] = float(pdf.resolve(val)) / 1000.0
                            i += 2
                        else:
                            c = pdf.resolve(w[i + 2])
                            for k in range(a, b + 1):
                                self.widths[k] = float(c) / 1000.0
                            i += 3
        else:
            fmat = pdf.resolve(fdict.get("FontMatrix"))
            self.fm = float(fmat[0]) if fmat else 0.001
            ws = pdf.resolve(fdict.get("Widths")) or []
            first = int(pdf.resolve(fdict.get("FirstChar", 0)) or 0)
            for k, val in enumerate(ws):
                self.widths[first + k] = float(pdf.resolve(val) or 0) * self.fm
            self.dw = 0.5

    def decode(self, s):
        out = []
        cl = self.code_len
        if cl == 2:
            for i in range(0, len(s) - 1, 2):
                code = (s[i] << 8) | s[i + 1]
                out.append(self.cmap.get(code, ""))
        else:
            for b in s:
                out.append(self.cmap.get(b, ""))
        return "".join(out)

    def decode2(self, s):
        """Return (text, advance_in_em)."""
        out = []
        w = 0.0
        cl = self.code_len
        if cl == 2:
            for i in range(0, len(s) - 1, 2):
                code = (s[i] << 8) | s[i + 1]
                out.append(self.cmap.get(code, ""))
                w += self.widths.get(code, self.dw)
        else:
            for b in s:
                out.append(self.cmap.get(b, ""))
                w += self.widths.get(b, self.dw)
        return "".join(out), w

    def __repr__(self):
        return f"<{self.name} {self.base} {self.subtype}>"


