"""
The bridge between the web interface and the engine.

Every public method takes and returns plain JSON-friendly values, so the same
class serves the desktop window (pywebview's js_api) and the browser dev
server (dev_server.py).
"""
from __future__ import annotations

import base64
import json
import tempfile
import threading
from pathlib import Path

import pymupdf as fitz

from .engine import PDFEditor

CONFIG_DIR = Path.home() / ".localpdf"
RECENT_FILE = CONFIG_DIR / "recent.json"


def _run_dict(i, r):
    return {
        "id": i, "text": r.text, "font": r.base_font, "size": round(r.size, 2), "color": r.hex,
        "bold": r.bold, "italic": r.italic, "editable": r.editable,
        "lockReason": "" if r.editable else ("This line has symbols the PDF doesn't label" if r.unlabeled
                                             else "Rotated text can't be edited yet"),
        "x0": r.bbox.x0, "y0": r.bbox.y0, "x1": r.bbox.x1, "y1": r.bbox.y1,
        "ox": r.origin.x, "oy": r.origin.y,
    }


class Api:
    def __init__(self):
        self.editor: PDFEditor | None = None
        self.save_path: str | None = None
        self.window = None  # set by the desktop app
        self._lock = threading.RLock()

    # ------------------------------------------------------------ helpers
    def _state(self):
        e = self.editor
        if not e:
            return {"open": False, "recent": self.recent()}
        return {
            "open": True,
            "name": Path(e.path).name,
            "path": e.path,
            "savePath": self.save_path,
            "dirty": e.dirty,
            "edits": e.edit_count,
            "canUndo": e.can_undo(),
            "canRedo": e.can_redo(),
            "editedPages": sorted(e.edited_pages),
            "pages": [{"w": w, "h": h} for w, h in (e.page_size(i) for i in range(e.page_count))],
        }

    def _font_payload(self, choice):
        css = self.editor.font_css(choice)
        return {
            "status": choice.status, "label": choice.label, "missing": choice.missing, "standIn": choice.stand_in,
            "css": css,
            "ascender": self._metrics(choice)[0], "descender": self._metrics(choice)[1],
        }

    def _metrics(self, choice):
        try:
            f = fitz.Font(choice.base14) if choice.base14 else fitz.Font(fontbuffer=choice.buffer)
            return f.ascender, f.descender
        except Exception:
            return 0.9, -0.25

    def _remember_recent(self, path):
        try:
            CONFIG_DIR.mkdir(exist_ok=True)
            items = [p for p in self.recent() if p["path"] != path]
            items.insert(0, {"path": path, "name": Path(path).name})
            RECENT_FILE.write_text(json.dumps(items[:8]))
        except OSError:
            pass

    # ------------------------------------------------------------ documents
    def state(self):
        return self._state()

    def recent(self):
        try:
            items = json.loads(RECENT_FILE.read_text())
            return [p for p in items if Path(p["path"]).exists()]
        except Exception:
            return []

    def open_path(self, path):
        with self._lock:
            try:
                self.editor = PDFEditor(path, getattr(self, "_library", None))
                self._library = self.editor.library
                self._library.warm_up()
            except Exception as ex:
                return {"error": str(ex) or "That file couldn't be opened."}
            self.save_path = None
            self._remember_recent(str(path))
            return self._state()

    def open_upload(self, name, data_b64):
        """Browser dev mode: the file arrives as bytes."""
        tmp = Path(tempfile.mkdtemp(prefix="localpdf-")) / Path(name).name
        tmp.write_bytes(base64.b64decode(data_b64))
        return self.open_path(str(tmp))

    def open_dialog(self):
        if not self.window:
            return {"cancelled": True}
        import webview
        res = self.window.create_file_dialog(webview.OPEN_DIALOG, file_types=("PDF files (*.pdf)",))
        if not res:
            return {"cancelled": True}
        return self.open_path(res[0])

    def close(self):
        self.editor, self.save_path = None, None
        return self._state()

    # ------------------------------------------------------------ pages
    def page_image(self, page, zoom):
        with self._lock:
            png = self.editor.render_png(int(page), max(0.25, min(float(zoom), 8.0)))
        return "data:image/png;base64," + base64.b64encode(png).decode()

    def page_runs(self, page):
        with self._lock:
            return [_run_dict(i, r) for i, r in enumerate(self.editor.runs(int(page)))]

    # ------------------------------------------------------------ editing
    def _run(self, page, run_id):
        runs = self.editor.runs(int(page))
        rid = int(run_id)
        return runs[rid] if 0 <= rid < len(runs) else None

    def begin_edit(self, page, run_id):
        with self._lock:
            run = self._run(page, run_id)
            if not run:
                return {"error": "That text changed. Click it again."}
            choice = self.editor.choose_font(run, run.text)
            return {"bg": self.editor.background_hex(run), "font": self._font_payload(choice)}

    def preview(self, page, run_id, text):
        with self._lock:
            run = self._run(page, run_id)
            if not run:
                return {"error": "That text changed. Click it again."}
            return self._font_payload(self.editor.choose_font(run, text))

    def font_data(self, alias):
        with self._lock:
            return self.editor.font_base64(alias)

    def commit(self, page, run_id, text):
        with self._lock:
            run = self._run(page, run_id)
            if not run:
                return {"ok": False, "error": "That text changed. Click it again."}
            res = self.editor.replace_run(run, text)
            out = {"ok": res.ok, "error": res.error, "warnings": res.warnings, "state": self._state()}
            if res.font:
                out["font"] = {"status": res.font.status, "label": res.font.label}
            return out

    def undo(self):
        with self._lock:
            before = set(self.editor.edited_pages)
            ok = self.editor.undo()
            return {"ok": ok, "pages": sorted(before | self.editor.edited_pages), "state": self._state()}

    def redo(self):
        with self._lock:
            before = set(self.editor.edited_pages)
            ok = self.editor.redo()
            return {"ok": ok, "pages": sorted(before | self.editor.edited_pages), "state": self._state()}

    # ------------------------------------------------------------ find
    def find(self, needle, match_case=False):
        with self._lock:
            hits = []
            for p, i, n in self.editor.find(needle, bool(match_case)):
                r = self.editor.runs(p)[i]
                hits.append({"page": p, "id": i, "count": n,
                             "x0": r.bbox.x0, "y0": r.bbox.y0, "x1": r.bbox.x1, "y1": r.bbox.y1})
            return {"hits": hits, "total": sum(h["count"] for h in hits)}

    def replace_all(self, needle, replacement, match_case=False):
        with self._lock:
            before = set(self.editor.edited_pages)
            count, warnings = self.editor.find_replace(needle, replacement, bool(match_case))
            return {"count": count, "warnings": warnings,
                    "pages": sorted(before | self.editor.edited_pages), "state": self._state()}

    # ------------------------------------------------------------ saving
    def _default_name(self):
        p = Path(self.editor.path)
        return p.stem + " (edited).pdf"

    def save(self, choose=False):
        """Save a copy. First time (or choose=True) asks where; later saves update that copy."""
        with self._lock:
            target = self.save_path
            if choose or not target:
                if self.window:
                    import webview
                    res = self.window.create_file_dialog(
                        webview.SAVE_DIALOG, directory=str(Path(self.editor.path).parent),
                        save_filename=self._default_name())
                    if not res:
                        return {"cancelled": True}
                    target = res if isinstance(res, str) else res[0]
                else:  # browser dev mode
                    out = Path.cwd() / "out"
                    out.mkdir(exist_ok=True)
                    target = str(out / self._default_name())
            if not target.lower().endswith(".pdf"):
                target += ".pdf"
            try:
                self.editor.save(target)
            except Exception as ex:
                return {"error": f"Couldn't save: {ex}"}
            self.save_path = target
            return {"path": target, "name": Path(target).name, "state": self._state()}
