"""`superaide.db.rls` is frozen: applied Alembic revisions import it (ADR 0015, Risks)."""

import sys

from superaide.platform.db import rls as _real

sys.modules[__name__] = _real
