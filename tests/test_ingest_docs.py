"""Document extraction and chunking, plus the atomic replace in Postgres."""

import pytest

from ai.ingest_docs import (
    Chunk,
    chunk_document,
    chunk_text,
    extract_html,
    extract_markdown,
    store,
)

TABLE_PAGE = """
<html><head><title>t</title><script>ignore()</script></head><body>
<h2>Chapter 6</h2><p>Crop coefficients integrate transpiration and evaporation.</p>
<table border>
  <tr><td>Crop</td><td><img src="kcini.gif"></td>
      <td>K<sub>c mid</sub></td><td>K<sub>c end</sub></td></tr>
  <tr><td>Sweet Peppers (bell)</td><td></td><td>1.05<sup>2</sup></td><td>0.90</td></tr>
  <tr><td>Tomato</td><td></td><td>1.15</td><td>0.70</td></tr>
</table>
<h2>Chapter 8</h2><p>Readily available water is p times total available water.</p>
</body></html>
"""


@pytest.fixture
def html_file(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(TABLE_PAGE)
    return path


def test_tables_keep_columns_aligned_and_their_header(html_file):
    sections = extract_html(html_file)
    table = next(s for s in sections if s.header)
    assert table.header == "Table columns: Crop | [image] | Kc mid | Kc end"
    assert "Sweet Peppers (bell) | - | 1.05 (footnote 2) | 0.90" in table.text.splitlines()
    assert all("ignore()" not in s.text for s in sections)  # scripts are skipped


def test_superscripts_read_the_way_a_person_reads_them(tmp_path):
    page = tmp_path / "sup.html"
    page.write_text(
        "<p>ET is in mm day<sup>-1</sup>, volume in m<sup>3</sup>, on the 1<sup>st</sup>.</p>"
        "<table><tr><td>Crop</td><td>Kc</td></tr>"
        "<tr><td>Alfalfa<sup>4</sup></td><td>0.95<sup>3</sup></td></tr></table>"
    )
    text = "\n".join(s.text for s in extract_html(page))
    assert "mm day^-1" in text and "m^3" in text and "1st" in text  # units and ordinals
    assert "Alfalfa^4 | 0.95 (footnote 3)" in text  # after a number: a footnote marker


def test_headings_start_new_sections(html_file):
    texts = [s.text for s in extract_html(html_file)]
    assert any(t.startswith("Chapter 6") for t in texts)
    assert any(t.startswith("Chapter 8") for t in texts)


def test_tables_are_indexed_one_row_per_chunk_with_the_column_names(html_file):
    sections = extract_html(html_file)
    table = next(s for s in sections if s.header)
    chunks = chunk_document("p.html", [table], 500, 50)
    assert [c.text.splitlines()[1] for c in chunks] == [
        "Sweet Peppers (bell) | - | 1.05 (footnote 2) | 0.90",
        "Tomato | - | 1.15 | 0.70",
    ]
    assert all(c.text.startswith("Table columns: Crop |") for c in chunks)


def test_chunks_respect_the_budget_and_overlap():
    text = "\n".join(" ".join(f"w{i}-{j}" for j in range(40)) for i in range(60))
    chunks = chunk_text(text, max_tokens=200, overlap_tokens=30)
    max_words, overlap_words = int(200 / 1.33), int(30 / 1.33)
    assert all(len(c.split()) <= max_words for c in chunks)
    # Each chunk starts with the last words of the one before: a sentence cut at a
    # boundary is still whole in one of the two.
    for before, after in zip(chunks, chunks[1:], strict=False):
        assert after.split()[:overlap_words] == before.split()[-overlap_words:]


def test_a_line_longer_than_a_chunk_is_split_and_terminates():
    one_huge_line = " ".join(f"w{i}" for i in range(5000))
    chunks = chunk_text(one_huge_line, max_tokens=500, overlap_tokens=50)
    assert 10 < len(chunks) < 30
    assert chunks[-1].split()[-1] == "w4999"  # nothing lost at the end


def test_empty_text_gives_no_chunks():
    assert chunk_text("") == []


def test_markdown_splits_on_headings(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# Title\nintro\n## Part two\nmore text")
    assert [s.text.splitlines()[0] for s in extract_markdown(path)] == ["# Title", "## Part two"]


@pytest.mark.db
def test_reingest_replaces_a_documents_chunks(test_dsn, db):
    import psycopg

    vector = [0.0] * 767 + [1.0]
    with psycopg.connect(test_dsn) as conn:
        conn.execute("DELETE FROM public.doc_chunks")
        first = [Chunk("a.pdf", 1, i, f"text {i}") for i in range(3)]
        store(conn, "a.pdf", first, [vector] * 3)
        conn.commit()
        second = [Chunk("a.pdf", 1, 0, "fewer, bigger chunks")]
        store(conn, "a.pdf", second, [vector])
        conn.commit()
        rows = conn.execute("SELECT chunk FROM public.doc_chunks WHERE source = 'a.pdf'").fetchall()
    assert rows == [("fewer, bigger chunks",)]  # no stale chunks 1 and 2 left behind
