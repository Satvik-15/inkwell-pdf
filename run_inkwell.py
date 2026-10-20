"""Entry point used by the packaged app (PyInstaller)."""
import sys

from inkwellpdf.desktop import main

if __name__ == "__main__":
    sys.exit(main())
