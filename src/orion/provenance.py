"""Canonical content-hashing for tamper-evident provenance references.

`orion.analysis.evidence.evidence_fingerprint`, `orion.decision.schemas.
decision_fingerprint`, and `orion.risk.schemas.RiskPolicyRef` all need to hash
a Pydantic model into a stable, reproducible identifier — the same mechanism,
because two independent implementations that merely happen to agree today can
silently drift apart tomorrow, either breaking every legitimate provenance
match or, worse, masking a real one. This module is the one place that
mechanism lives.

Deliberately dependency-free beyond pydantic and the standard library: every
layer of ORION (analysis, reasoning, decision, risk) may need to import this,
so it must never gain a dependency that would make importing it a layering
violation for any of them.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def canonical_hash(model: BaseModel) -> str:
    """A deterministic SHA-256 hash of `model`'s full content.

    Canonical form: Pydantic's own JSON mode (stable datetime encoding, no
    Python-specific representations) with sorted keys and compact separators,
    so a purely cosmetic field reorder never changes the hash. This proves
    content equality, not identity — two independently-constructed models
    with identical field values hash identically.
    """
    payload = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
