"""Drop-name ignore policy and dependency-graph evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


class DropPolicyItem(Protocol):
    """Structural view of a timed drop used by the pure policy evaluator."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def is_claimed(self) -> bool: ...

    @property
    def benefits(self) -> Sequence[object]: ...

    @property
    def precondition_drops(self) -> Sequence[str]: ...

    @property
    def is_watch_drop(self) -> bool: ...


@dataclass(frozen=True)
class DropIgnoreReason:
    """Why a drop is ignored by the configured policy."""

    kind: Literal["keyword", "precondition"]
    detail: str


@dataclass(frozen=True)
class DropIgnoreEvaluation:
    """Dynamic ignore and mineability result for one campaign graph."""

    reasons: dict[str, DropIgnoreReason]
    mineable_ids: frozenset[str]


class DropIgnorePolicy:
    """Normalize ignored keywords and evaluate a campaign dependency graph."""

    def __init__(self, keywords: object = None) -> None:
        self.keywords = tuple(self.normalize_keywords(keywords))
        self._folded_keywords = tuple(keyword.casefold() for keyword in self.keywords)

    @staticmethod
    def normalize_keywords(keywords: object) -> list[str]:
        """Return trimmed, nonblank, casefold-deduplicated keywords in input order."""
        if isinstance(keywords, str):
            values: Sequence[object] = [keywords]
        elif isinstance(keywords, (list, tuple)):
            values = keywords
        else:
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            keyword = value.strip()
            folded = keyword.casefold()
            if not keyword or folded in seen:
                continue
            normalized.append(keyword)
            seen.add(folded)
        return normalized

    def matching_keyword(self, drop_name: str) -> str | None:
        """Return the first configured case-insensitive substring match."""
        folded_name = drop_name.casefold()
        for keyword, folded_keyword in zip(
            self.keywords, self._folded_keywords, strict=True
        ):
            if folded_keyword in folded_name:
                return keyword
        return None

    def evaluate(self, drops: Sequence[DropPolicyItem]) -> DropIgnoreEvaluation:
        """Evaluate direct ignores, blocked descendants, and useful prerequisites."""
        drops_by_id = {drop.id: drop for drop in drops}
        reasons: dict[str, DropIgnoreReason] = {}

        for drop in drops:
            if drop.is_claimed:
                continue
            if (keyword := self.matching_keyword(drop.name)) is not None:
                reasons[drop.id] = DropIgnoreReason("keyword", keyword)

        # Preconditions are AND-based. One ignored unclaimed prerequisite makes
        # every dependent descendant impossible until that prerequisite is claimed.
        changed = True
        while changed:
            changed = False
            for drop in drops:
                if drop.is_claimed or drop.id in reasons:
                    continue
                for prerequisite_id in drop.precondition_drops:
                    prerequisite = drops_by_id.get(prerequisite_id)
                    if (
                        prerequisite is not None
                        and not prerequisite.is_claimed
                        and prerequisite_id in reasons
                    ):
                        reasons[drop.id] = DropIgnoreReason(
                            "precondition", prerequisite.name
                        )
                        changed = True
                        break

        def collect_branch(
            drop_id: str, visiting: frozenset[str]
        ) -> set[str] | None:
            drop = drops_by_id.get(drop_id)
            if drop is None:
                return None
            if drop.is_claimed:
                return set()
            if drop_id in reasons or not drop.is_watch_drop or drop_id in visiting:
                return None

            branch = {drop_id}
            next_visiting = visiting | {drop_id}
            for prerequisite_id in drop.precondition_drops:
                prerequisite_branch = collect_branch(prerequisite_id, next_visiting)
                if prerequisite_branch is None:
                    return None
                branch.update(prerequisite_branch)
            return branch

        mineable_ids: set[str] = set()
        for drop in drops:
            if (
                drop.is_claimed
                or drop.id in reasons
                or not drop.is_watch_drop
                or not drop.benefits
            ):
                continue
            if (branch := collect_branch(drop.id, frozenset())) is not None:
                mineable_ids.update(branch)

        return DropIgnoreEvaluation(reasons, frozenset(mineable_ids))
