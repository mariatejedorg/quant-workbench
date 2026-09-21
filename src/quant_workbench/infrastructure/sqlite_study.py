"""SQLite persistence of the study cards' schedule (a table of the workbench database)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Engine, text

from quant_workbench.domain.ids import Slug
from quant_workbench.domain.study import CardState

_UPSERT = text(
    """
    INSERT INTO study_cards
        (id, project, ease, interval_days, repetitions, lapses, due, last_reviewed, reviews)
    VALUES (:id, :project, :ease, :interval, :repetitions, :lapses, :due, :at, 1)
    ON CONFLICT(id) DO UPDATE SET
        ease = excluded.ease, interval_days = excluded.interval_days,
        repetitions = excluded.repetitions, lapses = excluded.lapses, due = excluded.due,
        last_reviewed = excluded.last_reviewed, reviews = study_cards.reviews + 1
    """
)


class SqliteStudyRepository:
    """Implements :class:`~quant_workbench.domain.ports.StudyRepository` on a shared engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def states(self, project: Slug | None = None) -> dict[str, CardState]:
        statement = "SELECT id, ease, interval_days, repetitions, lapses, due FROM study_cards"
        parameters: dict[str, str] = {}
        if project is not None:
            statement += " WHERE project = :project"
            parameters["project"] = project
        with self._engine.connect() as connection:
            rows = connection.execute(text(statement), parameters).all()
        return {
            row.id: CardState(
                ease=row.ease,
                interval_days=row.interval_days,
                repetitions=row.repetitions,
                due=date.fromisoformat(row.due),
                lapses=row.lapses,
            )
            for row in rows
        }

    def save(self, card_id: str, project: Slug, state: CardState, reviewed_at: datetime) -> None:
        if state.due is None:
            raise ValueError("a reviewed card always has a due date")
        with self._engine.begin() as connection:
            connection.execute(
                _UPSERT,
                {
                    "id": card_id,
                    "project": project,
                    "ease": state.ease,
                    "interval": state.interval_days,
                    "repetitions": state.repetitions,
                    "lapses": state.lapses,
                    "due": state.due.isoformat(),
                    "at": reviewed_at.isoformat(),
                },
            )
