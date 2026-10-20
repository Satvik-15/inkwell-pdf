"""
Inkwell PDF editing engine (no UI code here).

One edit works like this:
  1. find the text run (same font, size and colour) the user clicked
  2. pick a font that draws the new text in the *same* typeface:
       a. the font embedded in the PDF, if it already has every character
       b. else the full version of that font (bundled, user folder, or installed)
       c. else the closest standard font, reported to the user as a fallback
  3. truly remove the old characters (text-only redaction; backgrounds,
     lines and images are left untouched)
  4. write the new text on the same baseline, at the same size and colour
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

if getattr(sys, "_MEIPASS", None):  # inside the packaged app
    PKG_DIR = Path(sys._MEIPASS) / "inkwellpdf"
else:
    PKG_DIR = Path(__file__).resolve().parent
BUNDLED_FONT_DIR = PKG_DIR / "fonts"
USER_FONT_DIR = Path.home() / "Inkwell PDF Fonts"  # drop extra .ttf/.otf files here
SYSTEM_FONT_DIRS = [
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts"),
    Path.home() / ".local/share/fonts",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
]

FLAG_ITALIC, FLAG_SERIF, FLAG_MONO, FLAG_BOLD = 2, 4, 8, 16

EXACT = "exact"        # the PDF's own embedded font
SAME = "same-font"     # full version of the same font, from disk
FALLBACK = "fallback"  # a different font that can draw the characters

BASE14_CSS = {
    "helv": ("Helvetica, Arial, sans-serif", 400, "normal"), "hebo": ("Helvetica, Arial, sans-serif", 700, "normal"),
    "heit": ("Helvetica, Arial, sans-serif", 400, "italic"), "hebi": ("Helvetica, Arial, sans-serif", 700, "italic"),
    "tiro": ("'Times New Roman', Times, serif", 400, "normal"), "tibo": ("'Times New Roman', Times, serif", 700, "normal"),
    "tiit": ("'Times New Roman', Times, serif", 400, "italic"), "tibi": ("'Times New Roman', Times, serif", 700, "italic"),
    "cour": ("'Courier New', Courier, monospace", 400, "normal"), "cobo": ("'Courier New', Courier, monospace", 700, "normal"),
    "coit": ("'Courier New', Courier, monospace", 400, "italic"), "cobi": ("'Courier New', Courier, monospace", 700, "italic"),
}


# ----------------------------------------------------------------- data types

@dataclass
class TextRun:
    """A piece of text on one line sharing one font, size and colour."""
    page: int
    text: str
    bbox: fitz.Rect
    origin: fitz.Point
    font: str
    size: float
    color: int
    flags: int
    direction: tuple = (1.0, 0.0)

    @property
    def base_font(self) -> str:
        return strip_subset(self.font)

    @property
    def rgb(self) -> tuple:
        c = self.color
        return ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)

    @property
    def hex(self) -> str:
        return f"#{self.color & 0xFFFFFF:06x}"

    @property
    def bold(self) -> bool:
        n = self.base_font.lower()
        return bool(self.flags & FLAG_BOLD) or "bold" in n or "black" in n or "heavy" in n

    @property
    def italic(self) -> bool:
        n = self.base_font.lower()
        return bool(self.flags & FLAG_ITALIC) or "italic" in n or "oblique" in n

    @property
    def unlabeled(self) -> bool:
        """The PDF draws some glyphs here without saying which character they are."""
        return "\ufffd" in self.text

    @property
    def editable(self) -> bool:
        return abs(self.direction[1]) < 0.01 and self.direction[0] > 0 and not self.unlabeled


@dataclass
class FontChoice:
    status: str
    label: str
    buffer: bytes | None
    base14: str | None = None
    missing: str = ""
    segments: list = field(default_factory=list)   # [(text, FontChoice)] in drawing order
    stand_in: str = ""                             # characters drawn with a different font

    @property
    def alias(self) -> str:
        return self.base14 or "F" + hashlib.md5(self.buffer).hexdigest()[:12]


@dataclass
class EditResult:
    ok: bool
    font: FontChoice | None = None
    warnings: list = field(default_factory=list)
    error: str = ""


# ----------------------------------------------------------------- font names

def strip_subset(name: str) -> str:
    """'BAAAAA+OpenSans-Regular' -> 'OpenSans-Regular'."""
    return re.sub(r"^[A-Z]{6}\+", "", name or "")


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def key_variants(name: str) -> set:
    """Several keys per name so 'ArialMT' matches 'Arial' and 'Foo-Roman' matches 'Foo Regular'."""
    k = norm(name)
    out = {k}
    for suffix in ("psmt", "mt", "regular", "roman", "book", "normal", "std", "pro"):
        if k.endswith(suffix) and len(k) > len(suffix) + 2:
            out.add(k[: -len(suffix)])
    out |= {v + "regular" for v in list(out) if not v.endswith("regular")}
    return out


_CP_CACHE: dict = {}


def codepoints(font: fitz.Font, key: str) -> set:
    if key not in _CP_CACHE:
        cps = set(font.valid_codepoints())
        if key in BASE14_CSS:  # standard fonts are written with WinAnsi encoding
            def winansi(cp):
                try:
                    chr(cp).encode("cp1252")
                    return True
                except UnicodeEncodeError:
                    return False
            cps = {cp for cp in cps if winansi(cp)}
        _CP_CACHE[key] = cps
    return _CP_CACHE[key]


def missing_chars(cps: set, text: str) -> str:
    return "".join(sorted({c for c in text if ord(c) not in cps and not c.isspace()}))


class FontLibrary:
    """Index of full font files on this computer (bundled + user folder + system).

    Fonts are matched by their PostScript and full names, which is what PDFs
    record. Font collections (.ttc, common on macOS) are split into single fonts.
    """

    def __init__(self, extra_dirs=None):
        self.dirs = [BUNDLED_FONT_DIR, USER_FONT_DIR, *SYSTEM_FONT_DIRS, *(extra_dirs or [])]
        self._index = None
        self._cache = {}
        self._lock = threading.Lock()

    def warm_up(self):
        """Build the index in the background so the first edit is instant."""
        threading.Thread(target=self._ensure, daemon=True).start()

    def _ensure(self):
        with self._lock:
            if self._index is None:
                self._index = self._scan()

    def _scan(self):
        from fontTools.ttLib import TTCollection, TTFont
        index = {}
        for d in self.dirs:
            try:
                if not d.is_dir():
                    continue
                files = sorted(d.rglob("*"))
            except OSError:
                continue
            for p in files:
                ext = p.suffix.lower()
                if ext not in (".ttf", ".otf", ".ttc", ".otc"):
                    continue
                try:
                    faces = TTCollection(str(p), lazy=True).fonts if ext in (".ttc", ".otc") \
                        else [TTFont(str(p), lazy=True)]
                except Exception:
                    continue
                for i, f in enumerate(faces):
                    try:
                        names = {f["name"].getDebugName(6), f["name"].getDebugName(4)} - {None}
                    except Exception:
                        continue
                    display = f["name"].getDebugName(4) or p.stem
                    if i == 0 and ext in (".ttf", ".otf"):
                        names.add(p.stem)
                    for n in names:
                        for k in key_variants(n):
                            index.setdefault(k, (p, i, display))
        return index

    def find(self, pdf_font_name: str):
        self._ensure()
        for k in key_variants(strip_subset(pdf_font_name)):
            if k in self._index:
                hit = self._load(self._index[k])
                if hit:
                    return hit
        return None

    def any_with_glyphs(self, text: str, bold=False, italic=False, sans=True):
        self._ensure()

        def rank(h):
            n = h[2].lower()
            return (("bold" in n) != bold, ("italic" in n or "oblique" in n) != italic,
                    ("sans" in n or "arial" in n or "helvetica" in n) != sans, n)
        for hit in sorted(set(self._index.values()), key=rank)[:400]:
            loaded = self._load(hit)
            if loaded and not missing_chars(codepoints(fitz.Font(fontbuffer=loaded[0]), "lib:" + loaded[1]), text):
                return loaded
        return None

    def _load(self, hit):
        if hit in self._cache:
            return self._cache[hit]
        path, face, display = hit
        try:
            if path.suffix.lower() in (".ttc", ".otc"):
                from fontTools.ttLib import TTCollection
                buf = io.BytesIO()
                TTCollection(str(path)).fonts[face].save(buf)
                raw = buf.getvalue()
            else:
                raw = path.read_bytes()
            fitz.Font(fontbuffer=raw)  # make sure MuPDF can use it
            self._cache[hit] = (raw, display)
        except Exception:
            self._cache[hit] = None
        return self._cache[hit]


def base14_for(run: TextRun) -> str:
    n = run.base_font.lower()
    if "courier" in n or "mono" in n or ("sans" not in n and run.flags & FLAG_MONO):
        fam = ("cour", "cobo", "coit", "cobi")
    elif any(s in n for s in ("sans", "arial", "helvetica", "inter", "roboto", "calibri", "verdana")):
        fam = ("helv", "hebo", "heit", "hebi")
    elif any(s in n for s in ("times", "serif", "georgia", "garamond", "cambria")) or run.flags & FLAG_SERIF:
        fam = ("tiro", "tibo", "tiit", "tibi")
    else:
        fam = ("helv", "hebo", "heit", "hebi")
    return fam[(1 if run.bold else 0) + (2 if run.italic else 0)]


# --------------------------------------------------------------------- engine

class PDFEditor:
    def __init__(self, path: str, library: FontLibrary | None = None):
        self.path = str(path)
        self.doc = fitz.open(self.path)
        if not self.doc.is_pdf:
            raise ValueError("That file isn't a PDF.")
        if self.doc.needs_pass:
            raise ValueError("This PDF is password protected.")
        self.library = library or FontLibrary()
        self._undo, self._redo = [], []
        self.dirty = False
        self.edit_count = 0
        self.edited_pages: set = set()
        self._emb = {}
        self._runs = {}
        self.fonts_by_alias: dict = {}

    # ---------------------------------------------------------------- reading
    @property
    def page_count(self) -> int:
        return len(self.doc)

    def page_size(self, page_no: int) -> tuple:
        r = self.doc[page_no].rect
        return r.width, r.height

    def render_png(self, page_no: int, zoom: float = 2.0) -> bytes:
        return self.doc[page_no].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).tobytes("png")

    def runs(self, page_no: int) -> list[TextRun]:
        if page_no in self._runs:
            return self._runs[page_no]
        page = self.doc[page_no]
        raw = []
        data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
        for block in data["blocks"]:
            for line in block.get("lines", []):
                cur = None
                for s in line["spans"]:
                    if not s["text"]:
                        continue
                    if cur and cur.font == s["font"] and abs(cur.size - s["size"]) < 0.01 \
                            and cur.color == s["color"] and abs(cur.origin.y - s["origin"][1]) < 0.5:
                        cur.text += s["text"]
                        cur.bbox |= fitz.Rect(s["bbox"])
                    else:
                        cur = TextRun(page_no, s["text"], fitz.Rect(s["bbox"]), fitz.Point(s["origin"]),
                                      s["font"], s["size"], s["color"], s["flags"], tuple(line["dir"]))
                        raw.append(cur)
        out = []
        for r in raw:
            if not r.text.strip():
                continue
            lead = len(r.text) - len(r.text.lstrip())
            if lead:
                r.origin = fitz.Point(r.origin.x + fitz.get_text_length(r.text[:lead], "helv", r.size), r.origin.y)
            r.text = r.text.strip()
            out.append(r)
        self._runs[page_no] = out
        return out

    def background_hex(self, run: TextRun) -> str:
        """Colour behind a run, so the live editor can cover the original pixels."""
        clip = fitz.Rect(run.bbox.x0 - 2, run.bbox.y0 - 1, run.bbox.x1 + 2, run.bbox.y1 + 1)
        pix = self.doc[run.page].get_pixmap(clip=clip, alpha=False)
        counts = {}
        w, h, n = pix.width, pix.height, pix.n
        s = pix.samples
        for y in (0, h - 1):
            for x in range(w):
                i = (y * w + x) * n
                counts[s[i:i + 3]] = counts.get(s[i:i + 3], 0) + 1
        for x in (0, w - 1):
            for y in range(h):
                i = (y * w + x) * n
                counts[s[i:i + 3]] = counts.get(s[i:i + 3], 0) + 1
        best = max(counts, key=counts.get) if counts else b"\xff\xff\xff"
        return "#" + best.hex()

    # ------------------------------------------------------------------ fonts
    def _embedded(self, run: TextRun):
        key = (run.page, run.font)
        if key in self._emb:
            return self._emb[key]
        found = None
        for xref, ext, ftype, name, *_ in self.doc[run.page].get_fonts(full=True):
            if name == run.font or key_variants(strip_subset(name)) & key_variants(run.base_font):
                try:
                    _, fext, _, buf = self.doc.extract_font(xref)
                    if buf and fext in ("ttf", "otf", "cff", "cid"):
                        f = fitz.Font(fontbuffer=buf)
                        if f.valid_codepoints():
                            found = buf
                except Exception:
                    found = None
                break
        self._emb[key] = found
        return found

    def _sources(self, run: TextRun):
        """Fonts to draw this run with, most faithful first."""
        out = []
        emb = self._embedded(run)
        if emb:
            out.append((FontChoice(EXACT, run.base_font, emb), "emb:" + hashlib.md5(emb).hexdigest()))
        full = self.library.find(run.font)
        if full:
            out.append((FontChoice(SAME, full[1], full[0]), "lib:" + full[1]))
        b14 = base14_for(run)
        out.append((FontChoice(FALLBACK, fitz.Font(b14).name, None, base14=b14), b14))
        return out

    def choose_font(self, run: TextRun, new_text: str) -> FontChoice:
        """Pick a font for every character: the PDF's own font where it can, the
        full version of that font next, and a stand-in only for what neither has."""
        sources = [(c, codepoints(fitz.Font(c.base14) if c.base14 else fitz.Font(fontbuffer=c.buffer), key))
                   for c, key in self._sources(run)]
        primary = None
        for c, cps in sources:  # the font for the whole line if it can draw everything
            if not missing_chars(cps, new_text):
                primary = c
                break
        if primary and primary.status != FALLBACK:
            primary.segments = [(new_text, primary)]
            return self._remember(primary)

        # mix fonts character by character, keeping the run's own font wherever possible
        own = [(c, cps) for c, cps in sources if c.status != FALLBACK] or sources
        segments, missing, stand_in = [], "", ""
        cur = None
        for ch in new_text:
            pick = None
            if ch.isspace() and cur:
                pick = cur
            else:
                for c, cps in sources:
                    if ord(ch) in cps:
                        pick = c
                        break
                if pick is None:
                    found = self.library.any_with_glyphs(ch, run.bold, run.italic, base14_for(run).startswith("he"))
                    if found:
                        pick = self._remember(FontChoice(FALLBACK, found[1], found[0]))
                if pick is None:
                    missing += ch
                    continue
            if pick.status == FALLBACK and not ch.isspace():
                stand_in += ch
            if segments and segments[-1][1] is pick:
                segments[-1] = (segments[-1][0] + ch, pick)
            else:
                segments.append((ch, pick))
            cur = pick
        main = own[0][0]
        status = FALLBACK if stand_in else main.status
        label = main.label if stand_in and len(stand_in) < len(new_text.replace(" ", "")) else \
            (segments[0][1].label if segments else main.label)
        result = FontChoice(status, label, main.buffer, main.base14,
                            missing="".join(sorted(set(missing))), segments=segments,
                            stand_in="".join(sorted(set(stand_in))))
        for _, c in segments:
            self._remember(c)
        return self._remember(result)

    def _remember(self, choice: FontChoice) -> FontChoice:
        if choice.buffer:
            self.fonts_by_alias.setdefault(choice.alias, choice.buffer)
        return choice

    def font_css(self, choice: FontChoice) -> dict:
        """What the UI needs to render the live editor in the real font."""
        if choice.base14:
            fam, weight, style = BASE14_CSS[choice.base14]
            return {"family": fam, "weight": weight, "style": style, "alias": None}
        return {"family": f"'{choice.alias}'", "weight": 400, "style": "normal", "alias": choice.alias}

    def font_base64(self, alias: str) -> str | None:
        buf = self.fonts_by_alias.get(alias)
        return base64.b64encode(buf).decode() if buf else None

    # ---------------------------------------------------------------- editing
    def _snapshot(self):
        self._undo.append((self.doc.tobytes(), set(self.edited_pages), self.edit_count))
        self._undo = self._undo[-40:]
        self._redo.clear()

    def _restore(self, state):
        data, pages, count = state
        self.doc = fitz.open("pdf", data)
        self.edited_pages, self.edit_count = set(pages), count
        self._emb.clear()
        self._runs.clear()
        self.dirty = True

    def undo(self):
        if self._undo:
            self._redo.append((self.doc.tobytes(), set(self.edited_pages), self.edit_count))
            self._restore(self._undo.pop())
            return True
        return False

    def redo(self):
        if self._redo:
            self._undo.append((self.doc.tobytes(), set(self.edited_pages), self.edit_count))
            self._restore(self._redo.pop())
            return True
        return False

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def replace_run(self, run: TextRun, new_text: str, snapshot=True) -> EditResult:
        new_text = " ".join(new_text.split("\n"))
        if new_text == run.text:
            return EditResult(True)
        page = self.doc[run.page]
        if run.unlabeled:
            return EditResult(False, error="This line has symbols the PDF doesn't label, so it can't be rewritten safely.")
        if not run.editable or page.rotation:
            return EditResult(False, error="Rotated text can't be edited yet.")
        choice = self.choose_font(run, new_text)
        if choice.missing:
            return EditResult(False, choice, error=f"No font on this computer can draw “{choice.missing}”.")
        result = EditResult(True, choice)
        if choice.stand_in:
            result.warnings.append(f"{run.base_font} has no “{choice.stand_in}”, so a similar font draws "
                                   f"{'that character' if len(choice.stand_in) == 1 else 'those characters'}.")

        def font_of(c):
            return fitz.Font(c.base14) if c.base14 else fitz.Font(fontbuffer=c.buffer)
        new_w = sum(font_of(c).text_length(t, run.size) for t, c in choice.segments)
        new_rect = fitz.Rect(run.origin.x, run.bbox.y0, run.origin.x + new_w, run.bbox.y1)
        neighbours = [] if new_w <= run.bbox.x1 - run.origin.x + 0.5 else self.runs(run.page)
        for o in neighbours:
            if abs(o.origin.x - run.origin.x) < 0.5 and abs(o.origin.y - run.origin.y) < 0.5:
                continue  # the run itself
            inter = new_rect & o.bbox
            if not inter.is_empty and inter.width > 0.5 and inter.height > run.size * 0.3:
                result.warnings.append("The new text runs into nearby text.")
                break

        if snapshot:
            self._snapshot()
        y = run.origin.y
        band = fitz.Rect(run.bbox.x0 + 0.3, y - run.size * 0.55, run.bbox.x1 - 0.3, y - run.size * 0.15)
        page.add_redact_annot(band, fill=False, cross_out=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
        if page.get_text("text", clip=band).strip():
            result.warnings.append("Some of the original characters couldn't be removed.")
        x = run.origin.x
        for seg, c in choice.segments:
            if seg.strip():
                if c.base14 is None:
                    page.insert_font(fontname=c.alias, fontbuffer=c.buffer)
                page.insert_text((x, run.origin.y), seg, fontname=c.alias, fontsize=run.size, color=run.rgb)
            x += font_of(c).text_length(seg, run.size)
        self._runs.pop(run.page, None)
        self.edited_pages.add(run.page)
        self.edit_count += 1
        self.dirty = True
        return result

    def find(self, needle: str, match_case=False) -> list:
        """[(page, run_index, count)] for runs containing needle."""
        if not needle:
            return []
        pat = re.compile(re.escape(needle), 0 if match_case else re.IGNORECASE)
        hits = []
        for p in range(self.page_count):
            for i, r in enumerate(self.runs(p)):
                n = len(pat.findall(r.text))
                if n:
                    hits.append((p, i, n))
        return hits

    def find_replace(self, needle: str, replacement: str, match_case=False) -> tuple:
        pat = re.compile(re.escape(needle), 0 if match_case else re.IGNORECASE)
        hits = self.find(needle, match_case)
        if not hits:
            return 0, []
        self._snapshot()
        count, warnings = 0, []
        for p in sorted({h[0] for h in hits}):
            for r in [r for r in self.runs(p) if pat.search(r.text)]:
                n = len(pat.findall(r.text))
                res = self.replace_run(r, pat.sub(lambda _: replacement, r.text), snapshot=False)
                if res.ok:
                    count += n
                    warnings += [f"Page {p + 1}: {w}" for w in res.warnings]
                else:
                    warnings.append(f"Page {p + 1}: {res.error}")
        return count, warnings

    # ----------------------------------------------------------------- saving
    def save(self, out_path: str):
        """Write the document to out_path with fonts trimmed to the characters used.

        Trimming happens on a copy. The open document keeps its full fonts, so later
        edits can still use characters the saved file doesn't need.
        """
        raw = self.doc.tobytes()
        copy = fitz.open("pdf", raw)
        try:
            copy.subset_fonts()
        except Exception:
            pass
        same = os.path.abspath(out_path) == os.path.abspath(self.path)
        target = out_path + ".tmp" if same else out_path
        copy.save(target, garbage=3, deflate=True)
        copy.close()
        if same:
            # release the file before replacing it (Windows locks open files), then keep
            # editing the untrimmed version in memory
            self.doc.close()
            os.replace(target, out_path)
            self.doc = fitz.open("pdf", raw)
            self._emb.clear()
            self._runs.clear()
        self.dirty = False
