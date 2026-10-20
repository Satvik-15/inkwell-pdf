"""Desktop shell: a native window (WebKit on macOS, Edge WebView2 on Windows) showing the web interface.

Two rules keep the window responsive:
  * Never wait for a dialog inside a window event handler. macOS runs those handlers on
    the main thread, and dialogs also need the main thread, so waiting would freeze the app.
  * Serve the interface over pywebview's local server (127.0.0.1 only). Inside a packaged
    .app the web folder sits behind a symlink, which WebKit refuses to read as file://.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

from .api import Api

WEB = (Path(__file__).parent / "web").resolve() / "index.html"


def self_test() -> int:
    """Used by the build scripts: proves the packaged app can open, edit and save a PDF."""
    import tempfile

    import pymupdf as fitz

    from .engine import BUNDLED_FONT_DIR, PDFEditor
    tmp = Path(tempfile.mkdtemp())
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="os", fontfile=str(BUNDLED_FONT_DIR / "OpenSans-Regular.ttf"))
    page.insert_text((72, 100), "Total 4,500", fontname="os", fontsize=12)
    doc.save(tmp / "in.pdf")
    ed = PDFEditor(tmp / "in.pdf")
    res = ed.replace_run(ed.runs(0)[0], "Total 4,750 Zq")
    ed.save(str(tmp / "out.pdf"))
    ok = res.ok and "Total 4,750 Zq" in fitz.open(tmp / "out.pdf")[0].get_text() and WEB.exists()
    print("LocalPDF self-test:", "OK" if ok else f"FAILED {res}")
    return 0 if ok else 1


def main():
    if "--self-test" in sys.argv:
        return self_test()
    probe = "--probe" in sys.argv
    pdf_arg = next((a for a in sys.argv[1:] if a.lower().endswith(".pdf")), None)

    api = Api()
    window = webview.create_window(
        "LocalPDF", url=str(WEB), js_api=api,
        width=1280, height=860, min_size=(820, 560), background_color="#F7F7F5",
    )
    api.window = window
    state = {"closing_ok": False, "asking": False}

    def show_opened(st):
        window.evaluate_js(f"window.LocalPDF && window.LocalPDF.opened({json.dumps(st)})")

    def on_drop(e):
        files = e.get("dataTransfer", {}).get("files", [])
        pdfs = [f.get("pywebviewFullPath") for f in files if str(f.get("name", "")).lower().endswith(".pdf")]
        if pdfs and pdfs[0]:
            show_opened(api.open_path(pdfs[0]))

    def on_loaded():
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        if pdf_arg and not probe:
            show_opened(api.open_path(pdf_arg))

    def ask_then_close():
        try:
            if api.confirm("Close without saving?", "Your edits haven't been saved to a copy yet."):
                state["closing_ok"] = True
                window.destroy()
        finally:
            state["asking"] = False

    def on_closing():
        # Runs on the main thread on macOS: decide instantly, ask in the background.
        if state["closing_ok"] or not (api.editor and api.editor.dirty):
            return True
        if not state["asking"]:
            state["asking"] = True
            threading.Thread(target=ask_then_close, daemon=True).start()
        return False

    window.events.loaded += on_loaded
    window.events.closing += on_closing

    def run_probe():
        """Build check: open a PDF in the real window, report what the page shows, quit."""
        import time
        time.sleep(2)
        if pdf_arg:
            show_opened(api.open_path(pdf_arg))
            time.sleep(4)
        print("LocalPDF probe:", window.evaluate_js(
            "JSON.stringify({ui: !!window.LocalPDF, bridge: !!window.pywebview,"
            " pages: document.querySelectorAll('.page img').length,"
            " runs: document.querySelectorAll('.run').length})"), flush=True)
        if api.editor:
            import tempfile
            api.save_path = str(Path(tempfile.mkdtemp()) / "probe.pdf")  # don't touch the user's folder
            t = time.time()
            api.commit(0, 0, api.editor.runs(0)[0].text + " ok")
            res = api.save()
            print(f"LocalPDF probe save: {'OK' if res.get('path') else res} in {time.time() - t:.1f}s", flush=True)
        state["closing_ok"] = True
        window.destroy()

    webview.start(run_probe if probe else None, http_server=True, private_mode=True)
    return 0
