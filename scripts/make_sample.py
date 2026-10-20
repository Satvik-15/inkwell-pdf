"""Builds docs/sample-invoice.pdf: a fictional invoice for screenshots and trying the app."""
import tempfile
from pathlib import Path

import pymupdf as fitz
from fontTools import subset as fts

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "inkwellpdf" / "fonts"
INK, MUTED, LINE, FILL = (0.15, 0.16, 0.2), (0.45, 0.46, 0.5), (0.88, 0.88, 0.9), (0.965, 0.965, 0.975)

ROWS = [("Brand identity workshop", "1", "45,000"), ("Logo and wordmark", "1", "60,000"),
        ("Website design, 6 pages", "6", "90,000"), ("Print collateral", "3", "18,000")]
TEXT = {
    "reg": "Northwind Studio 14 Lake Road, Pune 411001 hello@northwind.example Bill to Fernwood Foods Pvt Ltd "
           "Mumbai Invoice date Due date 20 Oct 2026 19 Nov 2026 Description Qty Amount (Rs) Subtotal GST 18% "
           "Thank you for your business. Payment within 30 days." + "".join("".join(r) for r in ROWS)
           + "213,000 38,340 251,340 NW-2026-0142",
    "bold": "Invoice NW-2026-0142 Northwind Studio Total due Rs 251,340",
}


def subset(src, text, out):
    o = fts.Options()
    o.name_IDs = ["*"]
    f = fts.load_font(str(src), o)
    s = fts.Subsetter(o)
    s.populate(text=text)
    s.subset(f)
    fts.save_font(f, str(out), o)
    return str(out)


def main():
    tmp = Path(tempfile.mkdtemp())
    reg = subset(FONTS / "OpenSans-Regular.ttf", TEXT["reg"], tmp / "r.ttf")
    bold = subset(FONTS / "OpenSans-Bold.ttf", TEXT["bold"], tmp / "b.ttf")
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_font(fontname="r", fontfile=reg)
    p.insert_font(fontname="b", fontfile=bold)

    def t(x, y, s, f="r", size=10, color=INK):
        p.insert_text((x, y), s, fontname=f, fontsize=size, color=color)

    t(56, 80, "Northwind Studio", "b", 14)
    t(56, 98, "14 Lake Road, Pune 411001", size=9, color=MUTED)
    t(56, 111, "hello@northwind.example", size=9, color=MUTED)
    t(390, 84, "Invoice", "b", 26)
    t(390, 104, "NW-2026-0142", size=10, color=MUTED)
    p.draw_line((56, 140), (539, 140), color=LINE, width=0.8)
    t(56, 170, "Bill to", size=8.5, color=MUTED)
    t(56, 186, "Fernwood Foods Pvt Ltd", "r", 11)
    t(56, 200, "Mumbai", size=9.5, color=MUTED)
    t(330, 170, "Invoice date", size=8.5, color=MUTED)
    t(440, 170, "Due date", size=8.5, color=MUTED)
    t(330, 186, "20 Oct 2026", size=10.5)
    t(440, 186, "19 Nov 2026", size=10.5)
    p.draw_rect(fitz.Rect(56, 236, 539, 260), color=None, fill=FILL)
    t(66, 252, "Description", size=8.5, color=MUTED)
    t(380, 252, "Qty", size=8.5, color=MUTED)
    t(456, 252, "Amount (Rs)", size=8.5, color=MUTED)
    y = 284
    for d, q, a in ROWS:
        t(66, y, d, size=10.5)
        t(384, y, q, size=10.5)
        t(529 - fitz.get_text_length(a, "helv", 10.5), y, a, size=10.5)
        p.draw_line((56, y + 12), (539, y + 12), color=LINE, width=0.5)
        y += 30
    for label, val, bold_row in (("Subtotal", "213,000", False), ("GST 18%", "38,340", False), ("Total due", "Rs 251,340", True)):
        y += 6
        t(330, y, label, "b" if bold_row else "r", 12 if bold_row else 10.5)
        t(529 - fitz.get_text_length(val, "helv", 12 if bold_row else 10.5), y, val, "b" if bold_row else "r", 12 if bold_row else 10.5)
        y += 22
    t(56, 760, "Thank you for your business. Payment within 30 days.", size=9.5, color=MUTED)
    (ROOT / "docs").mkdir(exist_ok=True)
    doc.save(ROOT / "docs" / "sample-invoice.pdf", garbage=3, deflate=True)


if __name__ == "__main__":
    main()
