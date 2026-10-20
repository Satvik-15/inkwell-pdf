"""
The bridge between the web interface and the engine.

Every public method takes and returns plain JSON-friendly values, so the same
class serves the desktop window (pywebview's js_api) and the browser dev
server (dev_server.py).
"""
from __future__ import annotations

import base64
import json
import os
import sys
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
        self._front()
        kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
        res = self.window.create_file_dialog(kind, file_types=("PDF files (*.pdf)",))
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

    # ------------------------------------------------------------ dialogs
    def _front(self):
        """Bring the app forward so a native dialog shows where the user is (macOS)."""
        if sys.platform != "darwin":
            return
        try:
            import AppKit
            from PyObjCTools import AppHelper
            AppHelper.callAfter(lambda: AppKit.NSApp.activateIgnoringOtherApps_(True))
        except Exception:
            pass

    def confirm(self, title, message):
        """Yes/no question in a native dialog. Never call from a window event handler."""
        if not self.window:
            return True
        self._front()
        return bool(self.window.create_confirmation_dialog(title, message))

    # ------------------------------------------------------------ saving
    def _default_copy(self) -> Path:
        """'<name> (edited).pdf' next to the original, numbered if that name is taken."""
        p = Path(self.editor.path)
        cand, n = p.with_name(f"{p.stem} (edited).pdf"), 2
        while cand.exists():
            cand, n = p.with_name(f"{p.stem} (edited {n}).pdf"), n + 1
        return cand

    def _write(self, target: str):
        with self._lock:
            try:
                self.editor.save(target)
            except Exception as ex:
                return {"error": f"Couldn't save: {ex}"}
            self.save_path = target
            return {"path": target, "name": Path(target).name, "state": self._state()}

    def save(self, choose=False):
        """Save a copy without asking: '<name> (edited).pdf' next to the original, updated on
        later saves. With choose=True, ask where (Save as)."""
        if not self.editor:
            return {"error": "Open a PDF first."}
        if choose:
            return self.save_as()
        if self.save_path:
            return self._write(self.save_path)
        target = self._default_copy()
        if not os.access(target.parent, os.W_OK):
            return self.save_as()  # e.g. a read-only folder: let the user pick
        return self._write(str(target))

    def save_as(self):
        p = Path(self.editor.path)
        if self.window:
            import webview
            self._front()
            kind = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or webview.SAVE_DIALOG
            res = self.window.create_file_dialog(kind, directory=str(p.parent),
                                                 save_filename=p.stem + " (edited).pdf")
            if not res:
                return {"cancelled": True}
            target = res if isinstance(res, str) else res[0]
        else:  # browser dev mode
            out = Path.cwd() / "out"
            out.mkdir(exist_ok=True)
            target = str(out / (p.stem + " (edited).pdf"))
        if not target.lower().endswith(".pdf"):
            target += ".pdf"
        return self._write(target)

    def reveal(self):
        """Show the saved copy in Finder / Explorer."""
        if not self.save_path:
            return False
        import subprocess
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", self.save_path])
            elif sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", self.save_path])
            else:
                subprocess.Popen(["xdg-open", str(Path(self.save_path).parent)])
            return True
        except OSError:
            return False
