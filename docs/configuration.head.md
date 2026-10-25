# Configuration

Every setting is an environment variable. This page is the readable reference; [`backend/.env.example`](../backend/.env.example) is the source of truth (it lists every variable once, with the accepted values and a longer comment) and its top is an index of every true/false switch. The table below is generated from that file by `scripts/gen_configuration_doc.py`, and `make check-env` fails when a setting exists in the code but is missing from the example file, its feature-flags index, or this page.

Decided and tested features are **on by default**. A flag is only for risky, costly or experimental work and defaults to off. A feature that needs an external key turns on when the key is present.

## Set up

1. **Backend:** `cp backend/.env.example backend/.env` (`start.command` does this for you and fills in `ADMIN_TOKEN`). The file lives next to the code the API runs from, `backend/`. Never commit it; it is git-ignored.
2. **Frontend** (only if you change anything): `frontend/.env.example` to `frontend/.env.local`. Variables starting `NEXT_PUBLIC_` are baked in at build time, so rebuild after changing them. The installable app (`NEXT_PUBLIC_PWA=1`) is opt-in locally and on by default in the production compose file.
3. **Restart** the API (and the worker, if you run one) after every change. Environment variables win over `backend/.env`; keys connected in the app (Workspace, Connections) win over both.
4. A line `# NAME=value` is commented out and shows the built-in default: uncomment it only to change it.
5. `SECRETS_MASTER_KEY`, `SECRETS_MASTER_KEY_FILE` and `SUPERAIDE_KEY_FILE` are read from the process environment only, not from `backend/.env`. On a Mac leave them unset (the Keychain holds the key).
6. **Production** uses `.env.prod.example` at the repository root, see [Production differences](#production-differences).

## Variables by area

Long comments are shortened here; the full text is next to the variable in `backend/.env.example`. "Same note as the entry above" means the variable belongs to the group commented above it.

