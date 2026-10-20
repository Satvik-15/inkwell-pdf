"""Desktop shell: a native window (WebKit on macOS) showing the web interface."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

from .api import Api

WEB = Path(__file__).resolve().parent / "web" / "index.html"


def main():
    api = Api()
    window = webview.create_window(
        "LocalPDF", url=str(WEB), js_api=api,
        width=1280, height=860, min_size=(820, 560), background_color="#F7F7F5",
    )
    api.window = window

    def on_drop(e):
        files = e.get("dataTransfer", {}).get("files", [])
        pdfs = [f.get("pywebviewFullPath") for f in files if str(f.get("name", "")).lower().endswith(".pdf")]
        if pdfs and pdfs[0]:
            state = api.open_path(pdfs[0])
            window.evaluate_js(f"window.LocalPDF && window.LocalPDF.opened({json.dumps(state)})")

    def on_loaded():
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".pdf"):
            state = api.open_path(sys.argv[1])
            window.evaluate_js(f"window.LocalPDF && window.LocalPDF.opened({json.dumps(state)})")

    def on_closing():
        if api.editor and api.editor.dirty:
            return window.create_confirmation_dialog(
                "Close without saving?", "Your edits haven't been saved to a copy yet.")
        return True

    window.events.loaded += on_loaded
    window.events.closing += on_closing
    webview.start()
    return 0
