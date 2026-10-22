# Memory passport (schema version 1)

The memory passport is an open file format for everything Ava knows about her owner, so the owner can take it
elsewhere or bring memory in from another assistant. Nothing here is Superaide-only: it is a zip of plain JSON.

Where: Dashboard > Notebook > Memory passport. API: `/app/passport/*` (owner only).

## What is in the zip

| File | Content |
| --- | --- |
| `manifest.json` | `format` = `superaide-memory-passport`, `schema_version` (integer), `exported_at`, `assistant`, `owner`, `counts`, `files` |
| `facts.jsonl` | One JSON object per line, one per fact |
| `sources.jsonl` | The files and links the owner shared, with their text |
| `people.jsonl` | The owner's own contacts: name, relationship, notes |
| `preferences.json` | Digest, quiet hours, urgent topics, sharing rules. For reading and moving by hand; imports never apply it |

### `facts.jsonl`

```json
{"id": "fact_1", "content": "Maya runs the Lisbon office.", "category": "work", "confidence": 1.0,
 "origin": "chat", "valid_from": "2024-03-01T09:00:00+00:00", "valid_to": null, "source_id": "src_1", "shareable": false}
```

- `content` (required, up to 2000 characters), `category` (`about`, `work`, `person`, `preference`, `urgency`,
  `availability`, `sharing`, `other`; anything else becomes `other`).
- `valid_from` (ISO 8601; dates in the future are clamped to now), `valid_to` (a fact with a `valid_to` is history and is skipped on import).
- `source_id` points at an `id` in `sources.jsonl`. `origin` and `shareable` are informational.

### `sources.jsonl`

`{"id": "src_1", "kind": "text", "title": "bio.txt", "source": "bio.txt", "text": "...", "warnings": []}`.
`kind` is one of `pdf`, `docx`, `text`, `web`, `github`.

### `people.jsonl`

`{"name": "Arjun Mehta", "relationship": "team", "notes": "Runs the ops team"}`

A bare `facts.jsonl` (JSON Lines, no zip) is also accepted, so memory from another assistant can be moved in with a one-line-per-fact export.

## What is exported

Only the owner's own data: live facts that are about the owner and no one else and were never shared with another named person,
the owner's shared files and links, the owner's contacts (no email, phone or channel IDs), and preferences.
Never exported: forgotten facts (forgetting is erasure), facts about or from other people, credentials, tokens,
sessions, other owners' rows. Text of shared files may mention third parties because the owner shared it; untick
"sources" (`?sources=false`) to leave it out.

## What import does

1. **Preview.** `POST /app/passport/import` with `confirm=false` validates the file and reports: new facts, facts Ava already knows, sources, people, records skipped and why. Nothing is written.
2. **Import.** With `confirm=true`, the new records are written in one transaction.

The file never decides who may see what:

- Every imported fact is about the signed-in owner and private to the owner (`persons`, minimum trust 2). `subject`, `audience`, `shareable`, ids and tenant fields in the file are ignored.
- Imported facts have `source = imported` and confidence capped at 0.6, below anything the owner told Ava directly.
- A fact Ava already knows (same text after normalisation, or very similar) is skipped. Imports never replace or edit existing facts, and never change preferences.
- People come in as contacts with no authority and never as VIPs (that changes when Ava interrupts the owner).
- Sources are stored as text; no model reads them, so an import costs nothing against the spend cap.
- Limits: 25 MB upload, 20 MB per file inside the zip, 5000 facts, 200 sources, 500 people. Encrypted zips, unknown schema versions and files without a manifest are refused. Only named members are read; nothing is extracted to disk.

## Erasing an import

Every import is listed with its size. `POST /app/passport/imports/{id}/erase` deletes exactly the facts, sources and people it brought (hard delete, including embeddings). Facts the owner added since are untouched.
