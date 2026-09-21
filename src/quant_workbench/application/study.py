"""Study mode: turn the READMEs' concepts into flash cards scheduled with SM-2."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from quant_workbench.domain.ports import Clock, ProjectFiles, StudyRepository
from quant_workbench.domain.project import Project
from quant_workbench.domain.readme import StudyCard, study_cards
from quant_workbench.domain.study import CardState, Grade, StudyStats, due_cards, review, stats


@dataclass(frozen=True, slots=True)
class StudyItem:
    """A card to study now, with where it is in its schedule."""

    card: StudyCard
    state: CardState

    @property
    def is_new(self) -> bool:
        return self.state.due is None


class StudyService:
    def __init__(self, *, files: ProjectFiles, repository: StudyRepository, clock: Clock) -> None:
        self._files = files
        self._repository = repository
        self._clock = clock

    def cards(self, project: Project) -> tuple[StudyCard, ...]:
        """The cards a project's README contains (none if it has no README)."""
        text = self._files.read_text(project, "README.md")
        return study_cards(project.slug, text) if text else ()

    def queue(self, projects: Sequence[Project], limit: int | None = None) -> list[StudyItem]:
        """What to study today across ``projects``: new cards first, then the most overdue."""
        by_id = {c.id: c for project in projects for c in self.cards(project)}
        states = self._repository.states()
        today = self._clock.now().date()
        ordered = due_cards(states, list(by_id), today)
        items = [StudyItem(by_id[i], states.get(i, CardState())) for i in ordered]
        return items if limit is None else items[:limit]

    def answer(self, item: StudyItem, grade: Grade) -> CardState:
        """Record how well ``item`` was remembered and return its new schedule."""
        now = self._clock.now()
        state = review(item.state, grade, now.date())
        self._repository.save(item.card.id, item.card.project, state, now)  # type: ignore[arg-type]
        return state

    def summary(self, projects: Sequence[Project]) -> dict[str, StudyStats]:
        """Per-project counts (total, new, due today, learned)."""
        states = self._repository.states()
        today = self._clock.now().date()
        return {
            project.slug: stats(states, [c.id for c in self.cards(project)], today)
            for project in projects
        }
