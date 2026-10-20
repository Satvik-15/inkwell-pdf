"""Bundle the SVG icons in localpdf/web/icons into web/icons.js (no network needed at runtime)."""
import json
import re
from pathlib import Path

web = Path(__file__).resolve().parent.parent / "localpdf" / "web"
icons = {}
for f in sorted((web / "icons").glob("*.svg")):
    inner = re.search(r">\s*(.*)</svg>", f.read_text(), re.S).group(1)
    icons[f.stem] = re.sub(r"\s+", " ", inner).strip()
(web / "icons.js").write_text("window.ICONS = " + json.dumps(icons, indent=0) + ";\n")
print(f"{len(icons)} icons")
