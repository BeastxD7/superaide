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
