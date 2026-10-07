"""Day 6: download the agronomy references listed in ai/sources.json into ai/docs/.

    uv run python -m ai.fetch_docs          # skips files already downloaded
    uv run python -m ai.fetch_docs --force  # download everything again

The documents are not committed to git: their publishers keep the copyright and
their websites are the canonical copy. This script makes the corpus reproducible.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from ingest.retry import retry

AI_DIR = Path(__file__).resolve().parent
DOCS_DIR = AI_DIR / "docs"
SOURCES = AI_DIR / "sources.json"


def load_sources() -> list[dict[str, str]]:
    return json.loads(SOURCES.read_text())["documents"]


@retry(times=3, retry_on=(httpx.TransportError, httpx.HTTPStatusError))
def download(client: httpx.Client, url: str, target: Path) -> int:
    resp = client.get(url)
    resp.raise_for_status()
    target.write_bytes(resp.content)
    return len(resp.content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="download even if the file exists")
    args = parser.parse_args(argv)
    DOCS_DIR.mkdir(exist_ok=True)
    headers = {"User-Agent": "agri-data-platform (learning project)"}
    with httpx.Client(timeout=60, follow_redirects=True, headers=headers) as client:
        for doc in load_sources():
            target = DOCS_DIR / doc["file"]
            if target.exists() and not args.force:
                print(f"have  {doc['file']}")
                continue
            size = download(client, doc["url"], target)
            print(f"got   {doc['file']} ({size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
