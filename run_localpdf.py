"""Entry point used by the packaged app (PyInstaller)."""
import sys

from localpdf.desktop import main

if __name__ == "__main__":
    sys.exit(main())
