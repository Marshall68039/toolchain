#!/usr/bin/env python3
"""
html2ce.py - convert HTML pages into TI-84 Plus CE AppVars for an on-calculator HTML reader.

Usage:
    python html2ce.py PAGE [PAGE ...] [-o OUTDIR] [-n NAME] [--dump] [--bin] [--page-size N]

PAGE is a local .html file or an http(s):// URL. Each page becomes NAME.8xv (an archived
AppVar you can send with TI Connect CE). Pages bigger than --page-size are split into
chained pages NAME, NAME02, NAME03, ... that point at each other.

If you pass several pages at once, links between them are rewritten to point at each
other's AppVars, so the calculator can follow them. Links to anything else are kept as
non-selectable link text.

Needs only the Python 3.7+ standard library.

---------------------------------------------------------------------------------------
PAGE FORMAT (version 1) - this is what the calculator-side viewer reads
---------------------------------------------------------------------------------------
Header:
    2 bytes   magic "HR"
    1 byte    format version (1)
    1 byte    reserved (0)
    8 bytes   previous page AppVar name (NUL padded, all zero = none)
    8 bytes   next page AppVar name     (NUL padded, all zero = none)
    1 byte    title length (0-40)
    N bytes   title (ASCII)
Then a token stream. Any byte >= 0x20 is a plain ASCII text character. Bytes below 0x20
are commands:

    0x00           END of page
    0x01 n         HEADING style n (1 big, 2 medium, 3 small); lasts until the next 0x02
    0x02           PARAGRAPH break (blank line)
    0x03           LINE break
    0x04 v         CENTER on/off (v = 1 or 0)
    0x05 name[8]   LINK start; name = target AppVar, or 8 zero bytes if not on the calculator
    0x06           LINK end
    0x07 v         BOLD on/off
    0x08 v         ITALIC on/off
    0x09 len alt   IMAGE placeholder; alt text follows (len bytes, 0-40)
    0x0A n         INDENT level n (0-3)
    0x0B v         PREFORMATTED on/off (keep spaces, don't word-wrap)
    0x0C           HORIZONTAL RULE (occupies its own line)

On/off commands take an explicit value (not a toggle), and every page starts in the plain
state, so a page that was split from a longer one re-states whatever was active at the
split and can never get out of sync.

Text is plain ASCII only (32-126); "(c)", "--", "..." etc. replace symbols the calculator
font lacks. Words longer than the screen are the viewer's job to hard-wrap.
"""

import argparse
import re
import struct
import sys
import unicodedata
from collections import namedtuple
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

MAGIC = b"HR"
FORMAT_VERSION = 1
HEADER_RESERVE = 96  # bytes held back per page for the header and closing tokens

(END, HEAD, PARA, BR, CENTER, LINK, LINK_END, BOLD, ITALIC,
 IMAGE, INDENT, PRE, RULE) = range(13)

NO_LINK = bytes(8)  # link target that isn't on the calculator

# State the viewer is in at a given point in the stream.
St = namedtuple("St", "bold ital center pre indent head link",
                defaults=(False, False, False, False, 0, 0, None))

# ----------------------------------------------------------------------------------------
# Text -> ASCII
# ----------------------------------------------------------------------------------------

CHAR_MAP = {
    "\u00a0": " ", "\u2009": " ", "\u200b": "", "\ufeff": "",
    "\u00a9": "(c)", "\u00ae": "(R)", "\u2122": "(TM)",
    "\u2018": "'", "\u2019": "'", "\u201a": ",", "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2013": "-", "\u2014": "--", "\u2026": "...", "\u2022": "*", "\u00b7": "*",
    "\u00d7": "x", "\u00f7": "/", "\u00b0": "deg", "\u00b1": "+/-",
    "\u00bd": "1/2", "\u00bc": "1/4", "\u00be": "3/4",
    "\u2192": "->", "\u2190": "<-", "\u00ab": "<<", "\u00bb": ">>", "\u00df": "ss",
    "\u2264": "<=", "\u2265": ">=", "\u2260": "!=",
    "\u20ac": "EUR", "\u00a3": "GBP", "\u00a5": "JPY",
}


def asciify(s):
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in s:
        if ch in CHAR_MAP:
            out.append(CHAR_MAP[ch])
        elif ch in "\n\t" or 32 <= ord(ch) < 127:
            out.append(ch)
        elif ch.isspace():
            out.append(" ")
        elif ord(ch) < 32 or ord(ch) == 127:
            continue
        else:
            base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            if base:
                out.append(base)
            elif not unicodedata.combining(ch):
                out.append("?")
    return "".join(out)


# ----------------------------------------------------------------------------------------
# HTML -> token items
# ----------------------------------------------------------------------------------------

HEAD_TAGS = {"title", "meta", "link", "style", "script", "base", "noscript"}
SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "object", "select",
        "textarea"}
VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed",
        "param", "source", "track", "wbr"}
HEADINGS = {"h1": 1, "h2": 1, "h3": 2, "h4": 2, "h5": 3, "h6": 3}
BLOCK1 = {"div", "section", "article", "header", "footer", "main", "nav", "aside",
          "address", "figure", "figcaption", "form", "fieldset", "center", "dl",
          "details", "summary", "caption", "tr", "dt", "dd", "li"}

# Old-style HTML leaves <p>, <li>, <td>... unclosed. These tables say what a new start
# tag implicitly closes, and how far up the stack to look before giving up.
IMPLIED = {"p": {"p"}, "li": {"li"}, "td": {"td", "th"}, "th": {"td", "th"},
           "tr": {"tr", "td", "th"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}}
SCOPE = {"p": {"div", "td", "th", "table", "li", "ul", "ol", "blockquote", "body"},
         "li": {"ul", "ol"}, "td": {"tr", "table"}, "th": {"tr", "table"},
         "tr": {"table"}, "dt": {"dl"}, "dd": {"dl"}}


class Converter(HTMLParser):
    """Walks the HTML and builds a list of (bytes, state_after) items."""

    def __init__(self, resolve):
        super().__init__(convert_charrefs=True)
        self.resolve = resolve          # href -> 8-byte AppVar name (or NO_LINK)
        self.items = []                 # (token bytes, St after emitting it)
        self.stack = []                 # open tags: (tag, desired state before it)
        self.lists = []                 # per open <ul>/<ol>: None or running count
        self.des = St()                 # state the markup asks for right now
        self.em = St()                  # state already written to the stream
        self.skip = 0
        self.in_head = False
        self.in_title = False
        self.title_buf = []
        self.started = False
        self.at_line_start = True
        self.last_break_idx = None
        self.pending_space = False
        self.pending_sep = False
        self.skip_nl = False

    # ---- emitting ------------------------------------------------------------------

    def push(self, b):
        self.items.append((b, self.em))

    def brk(self, level):
        """Request a line (1) or paragraph (2) break without doubling up."""
        if not self.started:
            return
        if self.at_line_start:
            i = self.last_break_idx
            if (level == 2 and i is not None and i == len(self.items) - 1
                    and self.items[i][0] == bytes([BR])):
                self.em = self.em._replace(head=0)
                self.items[i] = (bytes([PARA]), self.em)
            return
        self.sync_close()  # a link that has ended closes before the break, not after it
        if level == 2:
            self.em = self.em._replace(head=0)  # a paragraph break ends a heading
        self.push(bytes([PARA if level == 2 else BR]))
        self.at_line_start = True
        self.last_break_idx = len(self.items) - 1
        self.pending_space = False

    def force_break(self):
        if not self.started:
            return
        self.sync_close()
        self.push(bytes([BR]))
        self.at_line_start = True
        self.last_break_idx = len(self.items) - 1
        self.pending_space = False

    def sync_close(self):
        if self.em.link is not None and self.des.link != self.em.link:
            self.em = self.em._replace(link=None)
            self.push(bytes([LINK_END]))

    def sync_open(self):
        d = self.des
        for field, op in (("center", CENTER), ("bold", BOLD), ("ital", ITALIC), ("pre", PRE)):
            want = getattr(d, field)
            if want != getattr(self.em, field):
                self.em = self.em._replace(**{field: want})
                self.push(bytes([op, int(want)]))
        indent = min(d.indent, 3)
        if indent != self.em.indent:
            self.em = self.em._replace(indent=indent)
            self.push(bytes([INDENT, indent]))
        if d.head and d.head != self.em.head:
            self.em = self.em._replace(head=d.head)
            self.push(bytes([HEAD, d.head]))
        if d.link is not None and d.link != self.em.link:
            self.em = self.em._replace(link=d.link)
            self.push(bytes([LINK]) + d.link)

    def lead(self):
        """Everything that must precede visible content: link close, spacing, state."""
        self.sync_close()
        if self.pending_sep:
            if not self.at_line_start:
                self.push(b" | ")
            self.pending_sep = False
        elif self.pending_space and not self.at_line_start:
            self.push(b" ")
        self.pending_space = False
        self.sync_open()
        self.at_line_start = False
        self.started = True

    def emit(self, text):
        self.lead()
        self.push(text.encode("ascii"))

    # ---- tag handling --------------------------------------------------------------

    def find(self, tag):
        bounds = SCOPE.get(tag, ())
        for i in range(len(self.stack) - 1, -1, -1):
            t = self.stack[i][0]
            if t == tag:
                return i
            if t in bounds:
                return None
        return None

    def pop_to(self, i):
        for t, _ in self.stack[i:]:
            if t in ("ul", "ol") and self.lists:
                self.lists.pop()
        self.des = self.stack[i][1]
        del self.stack[i:]

    def implied(self, tag):
        targets = IMPLIED.get(tag)
        if not targets:
            return
        bounds = SCOPE.get(tag, ())
        found = None
        for i in range(len(self.stack) - 1, -1, -1):
            t = self.stack[i][0]
            if t in targets:
                found = i
            if t in bounds:
                break
        if found is not None:
            self.pop_to(found)

    def begin(self, tag, a):
        old = new = self.des
        if (tag == "center" or a.get("align", "").lower() == "center"
                or re.search(r"text-align\s*:\s*center", a.get("style", ""), re.I)):
            new = new._replace(center=True)
        if tag in ("b", "strong", "th", "dt"):
            new = new._replace(bold=True)
        elif tag in ("i", "em", "cite", "var", "dfn"):
            new = new._replace(ital=True)
        elif tag == "pre":
            new = new._replace(pre=True)
        elif tag in ("ul", "ol", "blockquote"):
            new = new._replace(indent=min(old.indent + 1, 3))
        elif tag in HEADINGS:
            new = new._replace(head=HEADINGS[tag])
        elif tag == "a":
            href = a.get("href", "").strip()
            if href and not href.startswith("#") and not re.match(
                    r"(mailto|javascript|tel|data):", href, re.I):
                new = new._replace(link=self.resolve(href))

        if tag in ("p", "blockquote", "table", "pre") or tag in HEADINGS:
            self.brk(2)
        elif tag in BLOCK1 or tag in ("ul", "ol"):
            self.brk(1)
        if tag == "pre":
            self.skip_nl = True
        if tag in ("td", "th") and not self.at_line_start:
            self.pending_sep = True
        if tag in ("ul", "ol"):
            self.lists.append(0 if tag == "ol" else None)

        self.stack.append((tag, old))
        self.des = new

        if tag == "li":
            if self.lists and self.lists[-1] is not None:
                self.lists[-1] += 1
                mark = "%d." % self.lists[-1]
            else:
                mark = "*"
            self.emit(mark)
            self.pending_space = True

    def end_tag(self, tag):
        i = self.find(tag)
        if i is None:
            if tag == "p":
                self.brk(2)
            return
        self.pop_to(i)
        if tag in HEADINGS or tag in ("p", "pre", "table", "blockquote"):
            self.brk(2)
        elif tag in ("ul", "ol"):
            self.brk(2 if self.des.indent == 0 else 1)
        elif tag in BLOCK1:
            self.brk(1)

    def void(self, tag, a):
        if tag == "br":
            self.force_break()
        elif tag == "hr":
            self.brk(1)
            self.push(bytes([RULE]))
            self.started = True
            self.at_line_start = True
            self.last_break_idx = None
            self.pending_space = False
        elif tag == "img":
            def px(v):
                m = re.match(r"\d+", v or "")
                return int(m.group()) if m else None
            w, h = px(a.get("width")), px(a.get("height"))
            if (w is not None and w <= 2) or (h is not None and h <= 2):
                return  # tracking pixel / spacer
            alt = re.sub(r"\s+", " ", asciify(a.get("alt", ""))).strip()[:40]
            b = alt.encode("ascii")
            self.lead()
            self.push(bytes([IMAGE, len(b)]) + b)

    # ---- HTMLParser callbacks ------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self.in_title = True
            return
        if self.in_head and tag not in HEAD_TAGS:
            self.in_head = False
        if tag == "head":
            self.in_head = True
            return
        if tag == "body":
            self.skip = 0
        if tag in SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in VOID:
            self.void(tag, a)
            return
        self.implied(tag)
        self.begin(tag, a)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            return
        if tag == "head":
            self.in_head = False
            return
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in VOID:
            return
        self.end_tag(tag)

    def handle_data(self, data):
        if self.in_title:
            self.title_buf.append(data)
            return
        if self.skip or self.in_head:
            return
        text = asciify(data)
        if self.des.pre:
            text = text.expandtabs(4)
            if self.skip_nl and text.startswith("\n"):
                text = text[1:]
            self.skip_nl = False
            for n, line in enumerate(text.split("\n")):
                if n:
                    self.force_break()
                if line:
                    self.emit(line)
            return
        for m in re.finditer(r"\s+|\S+", text):
            tok = m.group()
            if tok.isspace():
                self.pending_space = True
            else:
                self.emit(tok)

    def get_title(self):
        return re.sub(r"\s+", " ", asciify("".join(self.title_buf))).strip()[:40]


# ----------------------------------------------------------------------------------------
# Pagination and file output
# ----------------------------------------------------------------------------------------

def restore_tokens(st):
    """Commands that put the viewer back into state `st` at the start of a split page."""
    b = b""
    if st.center:
        b += bytes([CENTER, 1])
    if st.bold:
        b += bytes([BOLD, 1])
    if st.ital:
        b += bytes([ITALIC, 1])
    if st.pre:
        b += bytes([PRE, 1])
    if st.indent:
        b += bytes([INDENT, st.indent])
    if st.head:
        b += bytes([HEAD, st.head])
    if st.link is not None:
        b += bytes([LINK]) + st.link
    return b


def paginate(items, limit):
    avail = limit - HEADER_RESERVE
    pages, n, i, prev_state = [], len(items), 0, St()
    while True:
        head = restore_tokens(prev_state)
        size, j = len(head), i
        while j < n and size + len(items[j][0]) <= avail:
            size += len(items[j][0])
            j += 1
        if j < n:  # must split: prefer to end on a paragraph break
            k = j
            while k > i and items[k - 1][0] != bytes([PARA]):
                k -= 1
            if k > i and sum(len(x[0]) for x in items[i:k]) >= avail // 2:
                j = k
            if j == i:
                j = i + 1
        body = head + b"".join(x[0] for x in items[i:j])
        end_state = items[j - 1][1] if j > i else prev_state
        if end_state.link is not None:
            body += bytes([LINK_END])
        pages.append(body + bytes([END]))
        if j >= n:
            return pages
        prev_state, i = end_state, j


def pad8(name):
    return name.encode("ascii").ljust(8, b"\0") if name else bytes(8)


def build_page(tokens, title, prev, nxt):
    t = title.encode("ascii")[:40]
    return (MAGIC + bytes([FORMAT_VERSION, 0]) + pad8(prev) + pad8(nxt)
            + bytes([len(t)]) + t + tokens)


def make_8xv(name, content):
    """Wrap `content` in a TI-83+/84+ family archived AppVar file."""
    data = struct.pack("<H", len(content)) + content  # AppVar data starts with its size
    entry = (struct.pack("<HH", 13, len(data)) + bytes([0x15])
             + name.encode("ascii").ljust(8, b"\0") + bytes([0x00, 0x80])
             + struct.pack("<H", len(data)) + data)
    header = (b"**TI83F*" + bytes([0x1A, 0x0A, 0x00])
              + b"Created by html2ce".ljust(42, b"\0") + struct.pack("<H", len(entry)))
    return header + entry + struct.pack("<H", sum(entry) & 0xFFFF)


def dump_page(name, data):
    """Readable rendering of a page, so you can check the converter's output."""
    if data[:2] != MAGIC:
        return "(not an html2ce page)"
    prev = data[4:12].rstrip(b"\0").decode() or "-"
    nxt = data[12:20].rstrip(b"\0").decode() or "-"
    tl = data[20]
    title = data[21:21 + tl].decode()
    pos = 21 + tl
    out = ["== %s  title=%r  prev=%s  next=%s ==" % (name, title, prev, nxt)]
    line = []
    flags = {CENTER: "CENTER", BOLD: "B", ITALIC: "I", PRE: "PRE"}
    while pos < len(data):
        c = data[pos]
        if c >= 0x20:
            end = pos
            while end < len(data) and data[end] >= 0x20:
                end += 1
            line.append(data[pos:end].decode())
            pos = end
            continue
        pos += 1
        if c == END:
            line.append("{END}")
            break
        elif c == HEAD:
            line.append("{H%d}" % data[pos]); pos += 1
        elif c == PARA:
            line.append("{P}"); out.append("".join(line)); out.append(""); line = []
        elif c == BR:
            line.append("{BR}"); out.append("".join(line)); line = []
        elif c in flags:
            line.append("{%s:%d}" % (flags[c], data[pos])); pos += 1
        elif c == INDENT:
            line.append("{IND:%d}" % data[pos]); pos += 1
        elif c == LINK:
            nm = data[pos:pos + 8].rstrip(b"\0").decode() or "none"
            line.append("{LINK>%s}" % nm); pos += 8
        elif c == LINK_END:
            line.append("{/LINK}")
        elif c == IMAGE:
            n = data[pos]
            line.append("{IMG:%s}" % data[pos + 1:pos + 1 + n].decode()); pos += 1 + n
        elif c == RULE:
            line.append("{HR}"); out.append("".join(line)); line = []
        else:
            line.append("{?0x%02x}" % c)
    out.append("".join(line))
    return "\n".join(out)


# ----------------------------------------------------------------------------------------
# Loading pages, naming, links
# ----------------------------------------------------------------------------------------

def is_url(s):
    return re.match(r"https?://", s, re.I) is not None


def decode_bytes(raw, hint=None):
    sniff = re.search(rb"charset=[\"']?([\w-]+)", raw[:4096], re.I)
    for enc in (hint, sniff.group(1).decode("ascii") if sniff else None, "utf-8", "cp1252"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("latin-1")


def load(src):
    """Return (html text, base URL used for resolving that page's links)."""
    if is_url(src):
        req = Request(src, headers={"User-Agent": "Mozilla/5.0 (html2ce)"})
        with urlopen(req, timeout=20) as r:
            raw = r.read()
            hint = r.headers.get_content_charset()
            base = r.geturl()
        return decode_bytes(raw, hint), base
    p = Path(src)
    return decode_bytes(p.read_bytes()), p.resolve().as_uri()


def norm_key(url):
    """Identity of a page for link matching (ignores #fragment, scheme and www.)."""
    p = urlparse(urldefrag(url)[0])
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path or "/"
    if path.endswith("/"):
        path += "index.html"
    if p.scheme == "file":
        path = path.lower()
    return host + path + ("?" + p.query if p.query else "")


def clean_name(s):
    name = re.sub(r"[^A-Za-z0-9]", "", s).upper()[:8] or "PAGE"
    return "P" + name[:7] if name[0].isdigit() else name


def derive_name(src):
    if is_url(src):
        p = urlparse(src)
        stem = Path(p.path).stem or p.netloc.replace("www.", "").split(".")[0]
    else:
        stem = Path(src).stem
    return clean_name(stem)


def unique_name(name, used):
    cand = name
    n = 0
    while cand in used:
        n += 1
        if n > 99:
            sys.exit("Could not find a free AppVar name for " + name)
        cand = name[:6] + "%02d" % n
    used.add(cand)
    return cand


def page_names(base, count):
    if count > 99:
        sys.exit("%s would need %d pages; try a larger --page-size" % (base, count))
    return [base] + [base[:6] + "%02d" % n for n in range(2, count + 1)]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert HTML pages (files or URLs) into TI-84 Plus CE AppVars.")
    ap.add_argument("inputs", nargs="+", metavar="PAGE", help="HTML file or http(s) URL")
    ap.add_argument("-o", "--outdir", default=".", help="where to write files (default: .)")
    ap.add_argument("-n", "--name", help="AppVar name (single input only; max 8 letters/digits)")
    ap.add_argument("--dump", action="store_true", help="print a readable view of each page")
    ap.add_argument("--bin", action="store_true",
                    help="also write the raw page data as NAME.bin (for convbin)")
    ap.add_argument("--page-size", type=int, default=40000,
                    help="max bytes per AppVar page (default 40000, limit 65000)")
    args = ap.parse_args(argv)

    if args.name and len(args.inputs) > 1:
        ap.error("-n/--name only works with a single input")
    if not 1000 <= args.page_size <= 65000:
        ap.error("--page-size must be between 1000 and 65000")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    docs = []
    for src in args.inputs:
        try:
            html, base = load(src)
        except Exception as e:  # noqa: BLE001 - show any load problem plainly
            sys.exit("Could not read %s: %s" % (src, e))
        docs.append((src, html, base))

    used = set()
    names = [unique_name(clean_name(args.name) if args.name else derive_name(s), used)
             for s, _, _ in docs]

    # Map every page's address (including the address typed, before redirects) to its name
    # so links between the given pages can be wired up.
    key_map = {}
    for (src, _, base), name in zip(docs, names):
        key_map[norm_key(base)] = name
        if is_url(src):
            key_map[norm_key(src)] = name

    written = set()
    for (src, html, base), name in zip(docs, names):
        stats = {"ok": 0, "ext": 0}

        def resolve(href, base=base, stats=stats):
            target = key_map.get(norm_key(urljoin(base, href)))
            if target:
                stats["ok"] += 1
                return pad8(target)
            stats["ext"] += 1
            return NO_LINK

        conv = Converter(resolve)
        conv.feed(html)
        conv.close()
        title = conv.get_title() or name
        pages = paginate(conv.items, args.page_size)
        pnames = page_names(name, len(pages))

        print("%s -> %s  (%d page%s)" % (src, name, len(pages), "" if len(pages) == 1 else "s"))
        for idx, (pn, tokens) in enumerate(zip(pnames, pages)):
            if pn in written:
                sys.exit("AppVar name clash on %s; use -n or rename your inputs" % pn)
            written.add(pn)
            content = build_page(
                tokens, title,
                pnames[idx - 1] if idx else None,
                pnames[idx + 1] if idx + 1 < len(pnames) else None)
            path = outdir / (pn + ".8xv")
            path.write_bytes(make_8xv(pn, content))
            if args.bin:
                (outdir / (pn + ".bin")).write_bytes(content)
            print("  wrote %s  (%d bytes of page data)" % (path, len(content)))
            if args.dump:
                print(dump_page(pn, content))
                print()
        if stats["ok"] or stats["ext"]:
            print("  links: %d point at converted pages, %d are not on the calculator"
                  % (stats["ok"], stats["ext"]))


if __name__ == "__main__":
    main()
