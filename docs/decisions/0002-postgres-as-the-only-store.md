# 2. Postgres (with pgvector) as the only data store

Date: 2026-10-21. Status: accepted.

## Context

The assistant needs relational data (accounts, identities, contacts, preferences), an append-only log of conversations, and soon semantic search over what it knows about its owner. Each owner has hundreds to tens of thousands of memory items, not millions.

## Decision

Use Postgres 17 for everything, with the pgvector extension for embeddings when retrieval needs them. Don't add a separate vector database.

## Consequences

- Access rules (who may see a memory) are applied in the same SQL query as similarity search, so a private item cannot be returned by mistake.
- Deleting a person's data is one transaction that covers facts, embeddings and documents.
- If one tenant ever outgrows a single Postgres, revisit with measurements.

## Addendum, 2026-11-20 (#647): one derived search index

Still Postgres, still no vector database. Recall gets one derived table, `recall_doc` (with `recall_embedding` for its vectors where pgvector exists), rebuilt from the source tables, which stay the truth. Both carry `tenant_id`, `agent_id` and Row-Level Security (ADR 0009). The search is one statement: the audience gate and the instance scope in every arm, full-text, trigram and meaning arms, reciprocal rank fusion, then a check that each candidate's source is still live (so an erased or retired source, or a note whose audience changed since indexing, is hidden even before the next rebuild). Exact vector scan is the default; a job builds a partial HNSW index per model, on a cast to that model's dimension, when one instance passes 200,000 embedded rows, and records the row count, the threshold and the measured p95 in the audit log. The index is not yet what `recall` reads: it is compared with today's recall in shadow first.
