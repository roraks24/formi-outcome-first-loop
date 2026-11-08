"""Shared Cohort abstraction used across detection, experimentation, and governance.

A Cohort encapsulates feature matching (AND across features, OR within feature values),
stable canonical identifiers, sample size qualification based on statistical power,
and hierarchical fallback to parent cohorts when sample sizes are insufficient.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .store import Store

# Simulator supported features
FEATURES: dict[str, list[str]] = {
    "lang": ["hi", "en", "ta", "kn", "te", "mr", "bn", "gu"],
    "city_tier": ["1", "2", "3"],
    "age_band": ["18-30", "31-45", "46-60", "60+"],
    "value_band": ["low", "mid", "high"],
}


@dataclass(frozen=True)
class Cohort:
    """Represents a discrete, rule-based audience segment."""

    cohort_id: str
    workflow_id: str
    where: dict[str, list[str]] = field(default_factory=dict)
    priority: int = 0
    parent_id: str = "default"

    @property
    def canonical_id(self) -> str:
        """Stable deterministic hash of the normalized cohort definition."""
        norm_where = {k: sorted(v) for k, v in sorted(self.where.items())}
        payload = f"{self.workflow_id}|{self.cohort_id}|{json.dumps(norm_where, sort_keys=True)}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def matches(self, features: dict[str, Any]) -> bool:
        """Evaluates whether lead features satisfy this cohort's where clause.

        Logic: AND across different features, OR within a feature's allowed list.
        """
        if not self.where or self.cohort_id == "default":
            return True
        for feat, allowed in self.where.items():
            val = str(features.get(feat, ""))
            if val not in allowed:
                return False
        return True

    def to_config_dict(self) -> dict[str, Any]:
        """Formats the cohort for inclusion in simulator workflow config."""
        return {
            "id": self.cohort_id,
            "where": {k: list(v) for k, v in self.where.items()},
        }


class CohortRegistry:
    """Manages workflow cohorts in strict priority order with fallback handling."""

    def __init__(self, workflow_id: str, cohorts: list[Cohort] | None = None):
        self.workflow_id = workflow_id
        self._cohorts: list[Cohort] = sorted(cohorts or [], key=lambda c: c.priority)
        self._by_id: dict[str, Cohort] = {c.cohort_id: c for c in self._cohorts}
        # Ensure 'default' cohort is always registered
        if "default" not in self._by_id:
            default_c = Cohort(cohort_id="default", workflow_id=workflow_id, where={}, priority=999)
            self._cohorts.append(default_c)
            self._by_id["default"] = default_c

    def get(self, cohort_id: str) -> Cohort:
        return self._by_id.get(cohort_id, self._by_id["default"])

    def assign(self, features: dict[str, Any]) -> str:
        """Returns the first matching cohort_id in priority order, else 'default'."""
        for c in self._cohorts:
            if c.cohort_id != "default" and c.matches(features):
                return c.cohort_id
        return "default"

    def list_cohorts(self) -> list[Cohort]:
        """Returns ordered cohorts excluding 'default'."""
        return [c for c in self._cohorts if c.cohort_id != "default"]

    def estimate_size(self, store: Store, cohort_id: str) -> int:
        """Estimates matching lead count for this cohort from recent cached leads."""
        if cohort_id == "default":
            cur = store._conn.cursor()
            cur.execute("SELECT COUNT(*) AS total FROM leads_cache WHERE workflow_id = ?;", (self.workflow_id,))
            row = cur.fetchone()
            cur.close()
            return int(row["total"] or 0)

        cohort = self.get(cohort_id)
        cur = store._conn.cursor()
        cur.execute(
            "SELECT lang, city_tier, age_band, value_band FROM leads_cache WHERE workflow_id = ?;",
            (self.workflow_id,),
        )
        rows = cur.fetchall()
        cur.close()

        count = 0
        for r in rows:
            feat = {
                "lang": r["lang"],
                "city_tier": str(r["city_tier"]),
                "age_band": r["age_band"],
                "value_band": r["value_band"],
            }
            if cohort.matches(feat):
                count += 1
        return count

    def fallback(self, cohort_id: str, sample_size: int, required_min_n: int) -> tuple[str, bool]:
        """Checks if cohort sample size meets min_n; falls back to parent if insufficient.

        Returns (effective_cohort_id, did_fallback).
        """
        if sample_size >= required_min_n or cohort_id == "default":
            return cohort_id, False
        parent = self.get(cohort_id).parent_id
        return parent, True
