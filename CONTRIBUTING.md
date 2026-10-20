# Contributing

Thanks for helping. A few things keep Inkwell PDF easy to use and easy to work on.

- **Product rule:** every feature should work by clicking on the page and typing. If it needs a dialog, ask whether a ⌘K command or an inline control would do.
- **Engine and interface stay separate.** PDF logic lives in `inkwellpdf/engine.py` and has tests in `tests/`. The interface in `inkwellpdf/web/` talks to it only through `inkwellpdf/api.py`.
- **No build step for the interface.** Plain HTML, CSS and JavaScript. Run `python -m inkwellpdf.dev_server` and refresh the browser.
- **Privacy:** never add network calls, analytics or telemetry.
- **Test files:** never commit real or private PDFs. Build test PDFs in code, like `tests/test_engine.py` and `scripts/make_sample.py` do.

Before opening a pull request, run `pytest` and try your change in the dev server.
