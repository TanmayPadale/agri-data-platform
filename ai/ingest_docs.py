"""Day 6: agronomy documents -> chunks -> embeddings -> public.doc_chunks.

    uv run python -m ai.fetch_docs        # once: download the references
    uv run python -m ai.ingest_docs       # extract, chunk, embed, store

Pipeline for each document (PDF, HTML or Markdown):

  1. extract   text per page (PDF) or per section under a heading (HTML, Markdown).
               Each HTML table becomes its own section, one line per row
               ("Sweet Peppers (bell) | - | 1.05^2 | 0.90 | 0.7"), with its first row
               kept as the column names.
  2. chunk     text: about 500 tokens each, starting fresh at headings, with a 50-token
               overlap so a sentence cut at a boundary is still whole in one of the two.
               tables: one chunk per row, each starting with the column names. A chunk
               that is mostly numbers embeds poorly, so "Kc mid for sweet peppers"
               never found the 40-row table chunk that held the answer. A single row
               with its column names ("Crop | Kc mid ... / Sweet Peppers | 1.05") does.
  3. embed     with nomic-embed-text (768 numbers per chunk), locally through Ollama.
  4. store     delete the document's old chunks and insert the new ones in one
               transaction. A plain upsert would leave stale chunks behind whenever
               a re-ingest (say, with a smaller chunk size) produces fewer of them.

Chunk size is a tunable: try --chunk-tokens 150 and 1500, re-run the evals and
compare hit@5. That is how the 500 default is chosen, not by guessing.
"""

from __future__ import annotations

import argparse
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from ai import llm
from ingest.db import connect

AI_DIR = Path(__file__).resolve().parent
DOCS_DIR = AI_DIR / "docs"
PLANTED_DIR = AI_DIR / "planted"  # our own test documents (e.g. a prompt-injection note)

TOKENS_PER_WORD = 1.33  # rough English average; good enough to size chunks


@dataclass
class Section:
    page: int  # PDF page number, or section number for HTML and Markdown
    text: str
    header: str | None = None  # a table's column names, repeated in each of its chunks


@dataclass
class Chunk:
    source: str
    page: int
    chunk_index: int
    text: str


# ---------------------------------------------------------------- 1. extract


def extract_pdf(path: Path) -> list[Section]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    return [
        Section(number, text)
        for number, page in enumerate(reader.pages, start=1)
        if (text := _tidy(page.extract_text() or ""))
    ]


class _HTMLSections(HTMLParser):
    """Collect text, starting a new section at every heading and around every table.

    Inside a table each row becomes one line of cells joined by " | ", empty cells
    become "-" so columns stay aligned, and the first row is kept as the header.
    Superscripts become "^2" so a footnote marker cannot fuse with a number.
    """

    SKIP = {"script", "style", "nav", "head"}
    HEADINGS = {"h1", "h2", "h3", "h4"}
    BLOCKS = {"p", "br", "li", "div", "pre", "h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[dict] = [self._new()]
        self.skipping = 0
        self.table_depth = 0
        self.row: list[str] | None = None
        self.cell: list[str] = []

    @staticmethod
    def _new(table: bool = False) -> dict:
        return {"parts": [], "header": None, "table": table}

    def _text(self, data: str) -> None:
        if self.skipping:
            return
        if self.row is not None:
            self.cell.append(data)
        else:
            self.sections[-1]["parts"].append(data)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP:
            self.skipping += 1
        elif tag == "table":
            self.table_depth += 1
            if self.table_depth == 1:
                self.sections.append(self._new(table=True))
        elif tag == "tr":
            self.row, self.cell = [], []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag == "sup":
            self._text("^")
        elif tag == "img":
            self._text("[image]")
        elif tag in self.HEADINGS and not self.table_depth:
            self.sections.append(self._new())
        if tag in self.BLOCKS:
            self._text("\n" if self.row is None else " ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in ("td", "th") and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()) or "-")
            self.cell = []
        elif tag == "tr" and self.row is not None:
            line = " | ".join(self.row)
            section = self.sections[-1]
            if any(c != "-" for c in self.row):
                if section["table"] and section["header"] is None:
                    section["header"] = line  # the first row names the columns
                else:
                    section["parts"].append("\n" + line + "\n")
            self.row = None
        elif tag == "table" and self.table_depth:
            self.table_depth -= 1
            if self.table_depth == 0:
                self.sections.append(self._new())  # text after the table
        elif tag in self.HEADINGS:
            self._text("\n")

    def handle_data(self, data: str) -> None:
        self._text(data)


def extract_html(path: Path) -> list[Section]:
    parser = _HTMLSections()
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    sections = []
    for raw in parser.sections:
        text = _tidy(html.unescape("".join(raw["parts"])))
        if text:
            header = f"Table columns: {raw['header']}" if raw["header"] else None
            sections.append(Section(len(sections) + 1, text, header))
    return sections


def extract_markdown(path: Path) -> list[Section]:
    parts = re.split(r"\n(?=#{1,4} )", path.read_text(encoding="utf-8"))
    return [Section(i, _tidy(p)) for i, p in enumerate(parts, start=1) if _tidy(p)]


def extract(path: Path) -> list[Section]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix in (".html", ".htm"):
        return extract_html(path)
    if suffix in (".md", ".txt"):
        return extract_markdown(path)
    raise ValueError(f"unsupported document type: {path.name}")


def _tidy(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


# ---------------------------------------------------------------- 2. chunk


def chunk_text(text: str, max_tokens: int = 500, overlap_tokens: int = 50) -> list[str]:
    """Split text into chunks of at most about max_tokens, cutting between lines.

    Lines are packed into a chunk until the next line would not fit. Then the chunk
    is closed and the next one starts with the last `overlap` words of it. A line
    longer than a whole chunk is first cut into pieces that fit. Line breaks are
    kept, so table rows stay one per line.
    """
    max_words = max(20, int(max_tokens / TOKENS_PER_WORD))
    overlap_words = min(int(overlap_tokens / TOKENS_PER_WORD), max_words // 2)
    piece = max_words - overlap_words  # so overlap + one piece always fits in a chunk

    lines: list[list[str]] = []
    for line in text.splitlines():
        words = line.split()
        lines += [words[i : i + piece] for i in range(0, len(words), piece)]

    chunks: list[str] = []
    current: list[list[str]] = []
    size = 0  # words in `current`
    fresh = 0  # words added since the last chunk was closed (not counting the overlap)
    for line in lines:
        if fresh and size + len(line) > max_words:
            chunks.append(_render(current))
            current = _tail(current, overlap_words)
            size, fresh = sum(map(len, current)), 0
        current.append(line)
        size += len(line)
        fresh += len(line)
    if fresh:
        chunks.append(_render(current))
    return chunks


def _render(lines: list[list[str]]) -> str:
    return "\n".join(" ".join(words) for words in lines)


def _tail(lines: list[list[str]], n: int) -> list[list[str]]:
    """The last n words of `lines`, keeping their line breaks."""
    kept: list[list[str]] = []
    for words in reversed(lines):
        if n <= 0:
            break
        kept.insert(0, words[-n:])
        n -= len(words)
    return kept


def chunk_document(
    source: str, sections: list[Section], max_tokens: int, overlap: int
) -> list[Chunk]:
    chunks = []
    for section in sections:
        if section.header:  # a table: one chunk per row, each with the column names
            pieces = [f"{section.header}\n{row}" for row in section.text.splitlines() if row]
        else:
            pieces = chunk_text(section.text, max_tokens, overlap)
        chunks += [Chunk(source, section.page, i, text) for i, text in enumerate(pieces)]
    return chunks


# ---------------------------------------------------------------- 3 + 4. embed and store


def to_vector(values: list[float]) -> str:
    """pgvector's text format, e.g. '[0.12,-0.5,...]'. Cast with ::vector in SQL."""
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


def store(conn, source: str, chunks: list[Chunk], vectors: list[list[float]]) -> int:
    """Replace one document's chunks atomically (the caller's transaction)."""
    conn.execute("DELETE FROM public.doc_chunks WHERE source = %s", (source,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO public.doc_chunks (source, page, chunk_index, chunk, embedding) "
            "VALUES (%s, %s, %s, %s, %s::vector)",
            [
                (c.source, c.page, c.chunk_index, c.text, to_vector(v))
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )
    return len(chunks)


def document_paths() -> list[Path]:
    docs = sorted(p for p in DOCS_DIR.glob("*") if p.suffix.lower() in (".pdf", ".html", ".htm"))
    return docs + sorted(PLANTED_DIR.glob("*.md"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chunk, embed and store the agronomy documents.")
    parser.add_argument("--chunk-tokens", type=int, default=500)
    parser.add_argument("--overlap-tokens", type=int, default=50)
    args = parser.parse_args(argv)

    paths = document_paths()
    if not any(p.parent == DOCS_DIR for p in paths):
        print("No documents in ai/docs yet. Run: uv run python -m ai.fetch_docs")
        return 1
    total = 0
    with connect() as conn:
        for path in paths:
            chunks = chunk_document(
                path.name, extract(path), args.chunk_tokens, args.overlap_tokens
            )
            vectors = llm.embed([llm.DOC_PREFIX + c.text for c in chunks])
            total += store(conn, path.name, chunks, vectors)
            conn.commit()  # one document at a time: a failure later keeps the earlier ones
            print(f"{path.name}: {len(chunks)} chunks")
    print(f"stored {total} chunks of about {args.chunk_tokens} tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
