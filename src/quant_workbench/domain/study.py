"""Spaced repetition of the README concepts, with the SM-2 algorithm (pure).

SM-2 (SuperMemo 2) schedules each card by how well it was remembered: a good answer pushes
the next review further away (1 day, 6 days, then multiplied by an *ease factor* that adapts to
the card), a bad one sends the card back to the start.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import IntEnum

MIN_EASE = 1.3
DEFAULT_EASE = 2.5
_FIRST_INTERVAL = 1
_SECOND_INTERVAL = 6


class Grade(IntEnum):
    """How well the answer was remembered (SM-2 quality, 0-5)."""

    BLACKOUT = 0
    WRONG = 1
    WRONG_BUT_FAMILIAR = 2
    HARD = 3
    GOOD = 4
    EASY = 5

    @property
    def passed(self) -> bool:
        return self >= Grade.HARD


@dataclass(frozen=True, slots=True)
class CardState:
    """Where a card is in its schedule."""

    ease: float = DEFAULT_EASE
    interval_days: int = 0
    repetitions: int = 0
    due: date | None = None  # ``None``: never reviewed, due now
    lapses: int = 0

    def is_due(self, today: date) -> bool:
        return self.due is None or self.due <= today


def review(state: CardState, grade: Grade, today: date) -> CardState:
    """The state after answering with ``grade`` on ``today`` (the SM-2 update)."""
    if not grade.passed:
        # forgotten: start over tomorrow, keep the ease (SM-2 leaves it unchanged on failure)
        return CardState(
            ease=state.ease,
            interval_days=_FIRST_INTERVAL,
            repetitions=0,
            due=today + timedelta(days=_FIRST_INTERVAL),
            lapses=state.lapses + 1,
        )
    if state.repetitions == 0:
        interval = _FIRST_INTERVAL
    elif state.repetitions == 1:
        interval = _SECOND_INTERVAL
    else:
        interval = max(1, round(state.interval_days * state.ease))
    q = int(grade)
    ease = max(MIN_EASE, state.ease + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    return CardState(
        ease=ease,
        interval_days=interval,
        repetitions=state.repetitions + 1,
        due=today + timedelta(days=interval),
        lapses=state.lapses,
    )


def due_cards(states: dict[str, CardState], card_ids: list[str], today: date) -> list[str]:
    """Ids of the cards to study today: new cards first, then the most overdue."""

    def urgency(card_id: str) -> tuple[int, date]:
        state = states.get(card_id, CardState())
        return (0 if state.due is None else 1, state.due or today)

    return sorted((c for c in card_ids if states.get(c, CardState()).is_due(today)), key=urgency)


@dataclass(frozen=True, slots=True)
class StudyStats:
    total: int
    new: int  # never reviewed
    due: int  # to study today: the new ones and the overdue ones
    learned: int  # reviewed and not due yet

    @property
    def mastered_share(self) -> float:
        return self.learned / self.total if self.total else 0.0


def stats(states: dict[str, CardState], card_ids: list[str], today: date) -> StudyStats:
    """Counts for a set of cards."""
    known = [states.get(c, CardState()) for c in card_ids]
    new = sum(1 for s in known if s.due is None)
    due_now = sum(1 for s in known if s.is_due(today))  # includes the new ones
    return StudyStats(total=len(known), new=new, due=due_now, learned=len(known) - due_now)
