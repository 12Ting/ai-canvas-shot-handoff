from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_canvas.infrastructure.db.models import (
    AgentProposalRow,
    ModelProfileRevisionRow,
    ModelProfileRow,
    ProviderRow,
)


class ModelProfileRepository:
    def provider(self, session: Session, provider_id: str) -> ProviderRow | None:
        return session.get(ProviderRow, provider_id)

    def provider_by_key(self, session: Session, provider_key: str) -> ProviderRow | None:
        return session.scalar(select(ProviderRow).where(ProviderRow.provider_key == provider_key))

    def add_provider(self, session: Session, provider: ProviderRow) -> None:
        session.add(provider)

    def profile(self, session: Session, profile_id: str) -> ModelProfileRow | None:
        return session.get(ModelProfileRow, profile_id)

    def profile_by_key(self, session: Session, profile_key: str) -> ModelProfileRow | None:
        return session.scalar(
            select(ModelProfileRow).where(ModelProfileRow.profile_key == profile_key)
        )

    def add_profile(self, session: Session, profile: ModelProfileRow) -> None:
        session.add(profile)

    def revision(
        self, session: Session, revision_id: str
    ) -> ModelProfileRevisionRow | None:
        return session.get(ModelProfileRevisionRow, revision_id)

    def add_revision(self, session: Session, revision: ModelProfileRevisionRow) -> None:
        session.add(revision)

    def next_sequence(self, session: Session, profile_id: str) -> int:
        current = session.scalar(
            select(func.max(ModelProfileRevisionRow.sequence)).where(
                ModelProfileRevisionRow.model_profile_id == profile_id
            )
        )
        return 1 if current is None else current + 1

    def revisions_for_profile(
        self, session: Session, profile_id: str
    ) -> list[ModelProfileRevisionRow]:
        return list(
            session.scalars(
                select(ModelProfileRevisionRow)
                .where(ModelProfileRevisionRow.model_profile_id == profile_id)
                .order_by(ModelProfileRevisionRow.sequence.desc())
            )
        )


class AgentProposalRepository:
    def add(self, session: Session, proposal: AgentProposalRow) -> None:
        session.add(proposal)

    def get(self, session: Session, proposal_id: str) -> AgentProposalRow | None:
        return session.get(AgentProposalRow, proposal_id)

    def for_run_request(
        self, session: Session, run_request_id: str
    ) -> AgentProposalRow | None:
        return session.scalar(
            select(AgentProposalRow).where(AgentProposalRow.run_request_id == run_request_id)
        )

    def dispatching(self, session: Session) -> list[AgentProposalRow]:
        return list(
            session.scalars(
                select(AgentProposalRow)
                .where(AgentProposalRow.state == "DISPATCHING")
                .order_by(AgentProposalRow.created_at, AgentProposalRow.id)
            )
        )
