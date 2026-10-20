"""Engine tests. Each test builds its own PDF, so no private documents are needed."""
from pathlib import Path

import pymupdf as fitz
import pytest

from inkwellpdf.engine import EXACT, FALLBACK, SAME, PDFEditor

FONTS = Path(__file__).resolve().parent.parent / "inkwellpdf" / "fonts"


def subset(font_file, text, out):
    """Keep only the characters used, with a character map, like PDFs exported by browsers and Word."""
    from fontTools import subset as fts
    opts = fts.Options()
    opts.name_IDs = ["*"]
    font = fts.load_font(str(font_file), opts)
    sub = fts.Subsetter(opts)
    sub.populate(text=text)
    sub.subset(font)
    fts.save_font(font, str(out), opts)
    return out


@pytest.fixture
def sample_pdf(tmp_path):
    """A one-page invoice drawn with *subsets* of Open Sans, like real exported PDFs."""
    regular_text = "Total due" + "Rs 4,500" + "Thanks for your business"
    reg = subset(FONTS / "OpenSans-Regular.ttf", regular_text, tmp_path / "r.ttf")
    bold = subset(FONTS / "OpenSans-Bold.ttf", "Invoice 1043", tmp_path / "b.ttf")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(60, 150, 535, 180), color=None, fill=(0.95, 0.96, 0.98))  # shaded table row
    page.insert_font(fontname="os", fontfile=str(reg))
    page.insert_font(fontname="osb", fontfile=str(bold))
    page.insert_text((72, 100), "Invoice 1043", fontname="osb", fontsize=20, color=(0.2, 0.2, 0.3))
    page.insert_text((72, 170), "Total due", fontname="os", fontsize=11)
    page.insert_text((300, 170), "Rs 4,500", fontname="os", fontsize=11)
    page.insert_text((72, 220), "Thanks for your business", fontname="os", fontsize=11)
    path = tmp_path / "invoice.pdf"
    doc.save(path)
    return path


def run_with(editor, text, page=0):
    return next(r for r in editor.runs(page) if r.text == text)


def test_reads_runs_with_font_size_colour(sample_pdf):
    e = PDFEditor(sample_pdf)
    title = run_with(e, "Invoice 1043")
    assert "OpenSans" in title.base_font.replace(" ", "")
    assert title.size == pytest.approx(20, abs=0.1)
    assert title.bold


def test_edit_uses_embedded_font_when_it_has_the_characters(sample_pdf, tmp_path):
    e = PDFEditor(sample_pdf)
    res = e.replace_run(run_with(e, "Rs 4,500"), "Rs 4,050")
    assert res.ok and res.font.status == EXACT
    text = e.doc[0].get_text()
    assert "Rs 4,050" in text and "Rs 4,500" not in text


def test_edit_uses_full_font_for_characters_missing_from_subset(sample_pdf):
    e = PDFEditor(sample_pdf)
    res = e.replace_run(run_with(e, "Total due"), "Total due (Q4, VAT incl.)")
    assert res.ok and res.font.status == SAME
    spans = [s for b in e.doc[0].get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]]
    new = [s for s in spans if "VAT" in s["text"]]
    assert new and "Open Sans" in new[0]["font"].replace("OpenSans", "Open Sans")


def test_background_and_neighbours_untouched(sample_pdf):
    e = PDFEditor(sample_pdf)
    before = e.doc[0].get_drawings()
    e.replace_run(run_with(e, "Total due"), "Amount")
    assert len(e.doc[0].get_drawings()) == len(before)  # shaded row survives
    assert "Rs 4,500" in e.doc[0].get_text()             # neighbour on the same line survives
    assert "Thanks for your business" in e.doc[0].get_text()


def test_stand_in_font_only_for_missing_characters(sample_pdf):
    e = PDFEditor(sample_pdf)
    choice = e.choose_font(run_with(e, "Rs 4,500"), "Rs 4,500 → paid")
    assert choice.status == FALLBACK or choice.missing  # → isn't in Open Sans
    assert "→" in (choice.stand_in or choice.missing)
    own = [t for t, c in choice.segments if c.status != FALLBACK]
    assert "".join(own).replace(" ", "")  # the rest stays in the original font


def test_find_replace_and_undo(sample_pdf):
    e = PDFEditor(sample_pdf)
    count, _ = e.find_replace("business", "order")
    assert count == 1 and "Thanks for your order" in e.doc[0].get_text()
    assert e.undo()
    assert "Thanks for your business" in e.doc[0].get_text()
    assert e.redo()
    assert "Thanks for your order" in e.doc[0].get_text()


def test_save_copy_keeps_original(sample_pdf, tmp_path):
    original = sample_pdf.read_bytes()
    e = PDFEditor(sample_pdf)
    e.replace_run(run_with(e, "Invoice 1043"), "Invoice 1044")
    out = tmp_path / "copy.pdf"
    e.save(str(out))
    assert sample_pdf.read_bytes() == original
    assert "Invoice 1044" in fitz.open(out)[0].get_text()
    assert not e.dirty


def test_unlabeled_glyphs_are_locked(sample_pdf):
    e = PDFEditor(sample_pdf)
    r = run_with(e, "Total due")
    r.text = "Total �0"
    assert not r.editable
    res = e.replace_run(r, "Total 0")
    assert not res.ok


@pytest.mark.parametrize("over_original", [False, True])
def test_edits_after_saving_keep_new_characters(sample_pdf, tmp_path, over_original):
    """Saving trims fonts in the written file only. A later edit may need characters the
    first save didn't (regression: they disappeared from the second saved file)."""
    e = PDFEditor(sample_pdf)
    e.replace_run(run_with(e, "Invoice 1043"), "Invoice 1043 Q")      # loads the full bold font
    first = sample_pdf if over_original else tmp_path / "first.pdf"
    e.save(str(first))
    e.replace_run(run_with(e, "Invoice 1043 Q"), "Invoice JQXZ 7")     # characters not in that save
    assert "Invoice JQXZ 7" in e.doc[0].get_text()
    second = tmp_path / "second.pdf"
    e.save(str(second))
    assert "Invoice JQXZ 7" in fitz.open(second)[0].get_text()


def test_saving_leaves_open_document_unchanged(sample_pdf, tmp_path):
    e = PDFEditor(sample_pdf)
    e.replace_run(run_with(e, "Total due"), "Total due (incl. GST)")
    def font_sizes(doc):
        return sorted(len(doc.extract_font(f[0])[3] or b"") for f in doc[0].get_fonts(full=True))
    before = font_sizes(e.doc)
    e.save(str(tmp_path / "copy.pdf"))
    assert font_sizes(e.doc) == before                      # nothing trimmed in the open document
    assert font_sizes(fitz.open(tmp_path / "copy.pdf")) < before  # the saved file is trimmed
