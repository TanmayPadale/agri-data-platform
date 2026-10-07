-- Day 6: agronomy documents, split into chunks, each with its embedding.
-- Written by ai/ingest_docs.py, read by the RAG assistant (ai/ask.py).
--
-- The vectors live in the same Postgres as the marts: one database to back up,
-- SQL filters next to similarity search, and no extra service on an 8 GB laptop.
CREATE EXTENSION IF NOT EXISTS vector;  -- shipped in the pgvector/pgvector image

CREATE TABLE IF NOT EXISTS public.doc_chunks (
    id           BIGSERIAL    PRIMARY KEY,
    source       TEXT         NOT NULL,   -- file name in ai/docs, e.g. fao56_ch6_crop_coefficient.html
    page         INT          NOT NULL,   -- PDF page, or section number for HTML
    chunk_index  INT          NOT NULL,   -- position of the chunk within that page/section
    chunk        TEXT         NOT NULL,
    embedding    VECTOR(768)  NOT NULL,   -- 768 numbers: the size nomic-embed-text produces
    ingested_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (source, page, chunk_index)    -- citations point at exactly one chunk
);

-- HNSW: an approximate nearest-neighbour index. vector_cosine_ops matches the <=>
-- (cosine distance) operator used in queries, so ORDER BY embedding <=> q LIMIT 5
-- walks the graph instead of comparing the question with every chunk.
CREATE INDEX IF NOT EXISTS doc_chunks_embedding_hnsw
    ON public.doc_chunks USING hnsw (embedding vector_cosine_ops);
