"""Minimal PDF reader (stdlib only): xref, objects, streams, pages."""
import re
import zlib


class Ref:
    __slots__ = ("num", "gen")

    def __init__(self, num, gen):
        self.num = num
        self.gen = gen

    def __repr__(self):
        return f"Ref({self.num},{self.gen})"

    def __eq__(self, o):
        return isinstance(o, Ref) and o.num == self.num and o.gen == self.gen

    def __hash__(self):
        return hash((self.num, self.gen))


class Name(str):
    pass


class Stream:
    def __init__(self, dic, raw):
        self.dic = dic
        self.raw = raw

    def get(self, key, default=None):
        return self.dic.get(key, default)

    def __repr__(self):
        return f"Stream({self.dic})"


class Lexer:
    """Tokenizer/parser for PDF object syntax."""

    WS = b"\x00\t\n\x0c\r "
    DELIM = b"()<>[]{}/%"

    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def skip_ws(self):
        d = self.data
        n = len(d)
        while self.pos < n:
            c = d[self.pos]
            if c in self.WS:
                self.pos += 1
            elif c == 0x25:  # %
                while self.pos < n and d[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def parse_object(self):
        self.skip_ws()
        d = self.data
        if self.pos >= len(d):
            raise EOFError("eof")
        c = d[self.pos:self.pos + 1]
        if c == b"/":
            return self.parse_name()
        if c == b"(":
            return self.parse_literal_string()
        if c == b"<":
            if d[self.pos:self.pos + 2] == b"<<":
                return self.parse_dict()
            return self.parse_hex_string()
        if c == b"[":
            return self.parse_array()
        if c in b"+-.0123456789":
            return self.parse_number_or_ref()
        # keyword
        m = re.match(rb"[A-Za-z*'\"]+", d[self.pos:])
        if not m:
            raise ValueError(f"bad token at {self.pos}: {d[self.pos:self.pos+20]!r}")
        kw = m.group(0)
        self.pos += len(kw)
        if kw == b"true":
            return True
        if kw == b"false":
            return False
        if kw == b"null":
            return None
        return Keyword(kw.decode("latin-1"))

    def parse_name(self):
        self.pos += 1
        d = self.data
        start = self.pos
        n = len(d)
        while self.pos < n:
            c = d[self.pos]
            if c in self.WS or c in self.DELIM:
                break
            self.pos += 1
        raw = d[start:self.pos]
        if b"#" in raw:
            raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)
        return Name(raw.decode("latin-1"))

    def parse_literal_string(self):
        d = self.data
        self.pos += 1
        depth = 1
        out = bytearray()
        n = len(d)
        while self.pos < n:
            c = d[self.pos]
            if c == 0x5C:  # backslash
                self.pos += 1
                if self.pos >= n:
                    break
                e = d[self.pos]
                if e in b"nrtbf":
                    out.append({0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}[e])
                    self.pos += 1
                elif e in b"01234567":
                    j = self.pos
                    while j < n and j < self.pos + 3 and d[j] in b"01234567":
                        j += 1
                    out.append(int(d[self.pos:j], 8) & 0xFF)
                    self.pos = j
                elif e == 0x0A:
                    self.pos += 1
                elif e == 0x0D:
                    self.pos += 1
                    if self.pos < n and d[self.pos] == 0x0A:
                        self.pos += 1
                else:
                    out.append(e)
                    self.pos += 1
            elif c == 0x28:
                depth += 1
                out.append(c)
                self.pos += 1
            elif c == 0x29:
                depth -= 1
                self.pos += 1
                if depth == 0:
                    break
                out.append(c)
            else:
                out.append(c)
                self.pos += 1
        return bytes(out)

    def parse_hex_string(self):
        d = self.data
        self.pos += 1
        end = d.index(b">", self.pos)
        hx = re.sub(rb"[^0-9A-Fa-f]", b"", d[self.pos:end])
        self.pos = end + 1
        if len(hx) % 2:
            hx += b"0"
        return bytes.fromhex(hx.decode("ascii"))

    def parse_array(self):
        d = self.data
        self.pos += 1
        out = []
        while True:
            self.skip_ws()
            if self.pos >= len(d):
                break
            if d[self.pos] == 0x5D:
                self.pos += 1
                break
            out.append(self.parse_object())
        return out

    def parse_dict(self):
        d = self.data
        self.pos += 2
        out = {}
        while True:
            self.skip_ws()
            if self.pos >= len(d):
                break
            if d[self.pos:self.pos + 2] == b">>":
                self.pos += 2
                break
            key = self.parse_object()
            if not isinstance(key, Name):
                # tolerate junk
                continue
            val = self.parse_object()
            out[key] = val
        return out

    def parse_number_or_ref(self):
        d = self.data
        m = re.match(rb"[+-]?[\d.]+", d[self.pos:])
        tok = m.group(0)
        self.pos += len(tok)
        if re.fullmatch(rb"[+-]?\d+", tok):
            num = int(tok)
            # maybe indirect reference "num gen R"
            save = self.pos
            m2 = re.match(rb"\s+(\d+)\s+R(?![A-Za-z])", d[self.pos:self.pos + 24])
            if m2:
                self.pos += m2.end()
                return Ref(num, int(m2.group(1)))
            self.pos = save
            return num
        return float(tok)


class Keyword(str):
    pass


def inflate(data):
    try:
        return zlib.decompress(data)
    except zlib.error:
        d = zlib.decompressobj()
        try:
            return d.decompress(data)
        except zlib.error:
            return b""


class PDF:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        self.xref = {}
        self.trailer = {}
        self.cache = {}
        self._load_xref()

    # ---------- xref ----------
    def _load_xref(self):
        data = self.data
        idx = data.rfind(b"startxref")
        if idx < 0:
            raise ValueError("no startxref")
        lx = Lexer(data, idx + 9)
        off = lx.parse_object()
        seen = set()
        while off is not None and off not in seen:
            seen.add(off)
            trailer = self._load_xref_at(off)
            for k, v in trailer.items():
                self.trailer.setdefault(k, v)
            off = trailer.get("Prev")

    def _load_xref_at(self, off):
        data = self.data
        lx = Lexer(data, off)
        lx.skip_ws()
        if data[lx.pos:lx.pos + 4] == b"xref":
            lx.pos += 4
            while True:
                lx.skip_ws()
                if data[lx.pos:lx.pos + 7] == b"trailer":
                    lx.pos += 7
                    trailer = lx.parse_object()
                    self._read_xref_stream_if_any(trailer)
                    return trailer
                start = lx.parse_object()
                count = lx.parse_object()
                for i in range(count):
                    lx.skip_ws()
                    m = re.match(rb"(\d{10}) (\d{5}) ([nf])", data[lx.pos:lx.pos + 20])
                    if not m:
                        raise ValueError("bad xref entry")
                    lx.pos += m.end()
                    if m.group(3) == b"n":
                        self.xref[start + i] = ("off", int(m.group(1)))
        else:
            # xref stream
            obj = self._parse_indirect_at(off)
            self._ingest_xref_stream(obj)
            return obj.dic

    def _read_xref_stream_if_any(self, trailer):
        stm = trailer.get("XRefStm")
        if isinstance(stm, int):
            off = stm
            obj = self._parse_indirect_at(off)
            self._ingest_xref_stream(obj)

    def _ingest_xref_stream(self, obj):
        if not isinstance(obj, Stream):
            return
        dic = obj.dic
        w = dic.get("W", [1, 2, 1])
        size = dic.get("Size", 0)
        index = dic.get("Index") or [0, size]
        data = self.decode_stream(obj)
        rec = sum(w)
        pos = 0
        for k in range(0, len(index) - 1, 2):
            start, count = index[k], index[k + 1]
            for i in range(count):
                if pos + rec > len(data):
                    break
                fields = []
                for width in w:
                    v = 0
                    for _ in range(width):
                        v = (v << 8) | data[pos]
                        pos += 1
                    fields.append(v)
                t = fields[0] if w[0] else 1
                if t == 1:
                    self.xref[start + i] = ("off", fields[1])
                elif t == 2:
                    self.xref[start + i] = ("stm", fields[1], fields[2])

    # ---------- objects ----------
    def _parse_indirect_at(self, off):
        lx = Lexer(self.data, off)
        lx.skip_ws()
        num = lx.parse_object()
        gen = lx.parse_object()
        kw = lx.parse_object()
        if kw != "obj":
            raise ValueError(f"expected obj at {off}")
        obj = lx.parse_object()
        lx.skip_ws()
        if self.data[lx.pos:lx.pos + 6] == b"stream":
            p = lx.pos + 6
            if self.data[p:p + 2] == b"\r\n":
                p += 2
            elif self.data[p:p + 1] in (b"\n", b"\r"):
                p += 1
            length = obj.get("Length")
            if isinstance(length, Ref):
                length = self.get_object(length)
            if not isinstance(length, int):
                length = None
            if length is None or p + length > len(self.data):
                end = self.data.find(b"endstream", p)
                raw = self.data[p:end]
            else:
                raw = self.data[p:p + length]
            return Stream(obj, raw)
        return obj

    def get_object(self, ref_or_obj):
        if isinstance(ref_or_obj, Ref):
            return self._get_by_num(ref_or_obj.num)
        return ref_or_obj

    def _get_by_num(self, num):
        if num in self.cache:
            return self.cache[num]
        ent = self.xref.get(num)
        if ent is None:
            obj = None
        elif ent[0] == "off":
            obj = self._parse_indirect_at(ent[1])
        else:
            container = self._get_by_num(ent[1])
            obj = self._from_objstm(container, ent[2])
        self.cache[num] = obj
        return obj

    def _from_objstm(self, stm, idx):
        if not isinstance(stm, Stream):
            return None
        data = self.decode_stream(stm)
        n = stm.dic.get("N", 0)
        first = stm.dic.get("First", 0)
        lx = Lexer(data, 0)
        pairs = []
        for _ in range(n):
            pairs.append((lx.parse_object(), lx.parse_object()))
        if idx >= len(pairs):
            return None
        off = pairs[idx][1]
        lx2 = Lexer(data, first + off)
        return lx2.parse_object()

    def resolve(self, obj, depth=0):
        while isinstance(obj, Ref) and depth < 32:
            obj = self.get_object(obj)
            depth += 1
        return obj

    # ---------- streams ----------
    def decode_stream(self, stm):
        if not isinstance(stm, Stream):
            return b""
        filters = self.resolve(stm.dic.get("Filter"))
        if filters is None:
            return stm.raw
        if not isinstance(filters, list):
            filters = [filters]
        parms = self.resolve(stm.dic.get("DecodeParms")) or []
        if not isinstance(parms, list):
            parms = [parms]
        data = stm.raw
        for i, f in enumerate(filters):
            f = str(f)
            p = parms[i] if i < len(parms) else None
            p = self.resolve(p) or {}
            if f in ("FlateDecode", "Fl"):
                data = inflate(data)
                if isinstance(p, dict) and p.get("Predictor", 1) >= 10:
                    data = png_unpredict(data, p)
            elif f in ("ASCIIHexDecode", "AHx"):
                hx = re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">")[0])
                if len(hx) % 2:
                    hx += b"0"
                data = bytes.fromhex(hx.decode())
            elif f in ("ASCII85Decode", "A85"):
                data = ascii85(data)
            elif f in ("LZWDecode", "LZW"):
                data = lzw_decode(data)
            else:
                # unsupported filter (DCT/JPX/CCITT/JBIG2 are image codecs: keep raw)
                break
        return data

    # ---------- pages ----------
    def pages(self):
        root = self.resolve(self.trailer.get("Root"))
        if isinstance(root, dict):
            node = self.resolve(root.get("Pages"))
        else:
            node = None
            for num in sorted(self.xref):
                o = self._get_by_num(num)
                if isinstance(o, dict) and o.get("Type") == "Catalog":
                    node = self.resolve(o.get("Pages"))
                    break
        out = []

        def walk(n, inherited):
            n = self.resolve(n)
            if not isinstance(n, dict):
                return
            inh = dict(inherited)
            if "Resources" in n:
                inh["Resources"] = n["Resources"]
            kids = self.resolve(n.get("Kids"))
            if kids:
                for k in kids:
                    walk(k, inh)
            else:
                out.append((n, inh))

        walk(node, {})
        return out


def png_unpredict(data, parms):
    colors = parms.get("Colors", 1)
    bpc = parms.get("BitsPerComponent", 8)
    columns = parms.get("Columns", 1)
    bpp = max(1, (colors * bpc + 7) // 8)
    row_len = (columns * colors * bpc + 7) // 8
    out = bytearray()
    prev = bytearray(row_len)
    i = 0
    while i + row_len + 1 <= len(data):
        ft = data[i]
        row = bytearray(data[i + 1:i + 1 + row_len])
        i += row_len + 1
        if ft == 1:
            for j in range(bpp, row_len):
                row[j] = (row[j] + row[j - bpp]) & 0xFF
        elif ft == 2:
            for j in range(row_len):
                row[j] = (row[j] + prev[j]) & 0xFF
        elif ft == 3:
            for j in range(row_len):
                left = row[j - bpp] if j >= bpp else 0
                row[j] = (row[j] + ((left + prev[j]) >> 1)) & 0xFF
        elif ft == 4:
            for j in range(row_len):
                a = row[j - bpp] if j >= bpp else 0
                b = prev[j]
                c = prev[j - bpp] if j >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[j] = (row[j] + pr) & 0xFF
        out += row
        prev = row
    return bytes(out)


def ascii85(data):
    data = re.sub(rb"\s", b"", data)
    if data.startswith(b"<~"):
        data = data[2:]
    end = data.find(b"~>")
    if end >= 0:
        data = data[:end]
    out = bytearray()
    group = []
    for ch in data:
        if ch == 0x7A and not group:
            out += b"\x00\x00\x00\x00"
            continue
        group.append(ch - 33)
        if len(group) == 5:
            v = 0
            for g in group:
                v = v * 85 + g
            out += v.to_bytes(4, "big")
            group = []
    if group:
        k = len(group)
        group += [84] * (5 - k)
        v = 0
        for g in group:
            v = v * 85 + g
        out += v.to_bytes(4, "big")[:k - 1]
    return bytes(out)


def lzw_decode(data):
    out = bytearray()
    table = [bytes([i]) for i in range(256)] + [b"", b""]
    bits = 9
    buf = 0
    nbits = 0
    prev = None
    for byte in data:
        buf = (buf << 8) | byte
        nbits += 8
        while nbits >= bits:
            nbits -= bits
            code = (buf >> nbits) & ((1 << bits) - 1)
            if code == 256:
                table = [bytes([i]) for i in range(256)] + [b"", b""]
                bits = 9
                prev = None
                continue
            if code == 257:
                return bytes(out)
            if prev is None:
                entry = table[code]
            elif code < len(table):
                entry = table[code]
                table.append(prev + entry[:1])
            else:
                entry = prev + prev[:1]
                table.append(entry)
            out += entry
            prev = entry
            if len(table) + 1 >= (1 << bits) and bits < 12:
                bits += 1
    return bytes(out)
