from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ai_canvas.infrastructure.db.models import (
    C4MutationReceiptRow,
    PromptBatchItemAttemptRow,
    PromptBatchItemDecisionRow,
    PromptBatchItemRow,
    PromptBatchRow,
    PromptProfileCertificationRow,
    PromptProfileExtractionRow,
)


class PromptBatchRepository:
    """C4 persistence primitives; application services own transactions."""

    def extraction(
        self, session: Session, extraction_id: str
    ) -> PromptProfileExtractionRow | None:
        return session.get(PromptProfileExtractionRow, extraction_id)

    def add_extraction(
        self, session: Session, extraction: PromptProfileExtractionRow
    ) -> None:
        session.add(extraction)

    def certification_for_revision(
        self, session: Session, resource_revision_id: str
    ) -> PromptProfileCertificationRow | None:
        return session.scalar(
            select(PromptProfileCertificationRow).where(
                PromptProfileCertificationRow.resource_revision_id
                == resource_revision_id
            )
        )

    def add_certification(
        self, session: Session, certification: PromptProfileCertificationRow
    ) -> None:
        session.add(certification)

    def batch(self, session: Session, batch_id: str) -> PromptBatchRow | None:
        return session.get(PromptBatchRow, batch_id)

    def batch_for_mutation(
        self, session: Session, project_id: str, client_mutation_id: str
    ) -> PromptBatchRow | None:
        return session.scalar(
            select(PromptBatchRow).where(
                PromptBatchRow.project_id == project_id,
                PromptBatchRow.client_mutation_id == client_mutation_id,
            )
        )

    def add_batch(self, session: Session, batch: PromptBatchRow) -> None:
        session.add(batch)

    def mutation_receipt(
        self, session: Session, project_id: str, client_mutation_id: str
    ) -> C4MutationReceiptRow | None:
        return session.scalar(
            select(C4MutationReceiptRow).where(
                C4MutationReceiptRow.project_id == project_id,
                C4MutationReceiptRow.client_mutation_id == client_mutation_id,
            )
        )

    def add_mutation_receipt(
        self, session: Session, receipt: C4MutationReceiptRow
    ) -> None:
        session.add(receipt)

    def item(self, session: Session, item_id: str) -> PromptBatchItemRow | None:
        return session.get(PromptBatchItemRow, item_id)

    def items_for_batch(
        self, session: Session, batch_id: str
    ) -> list[PromptBatchItemRow]:
        return list(
            session.scalars(
                select(PromptBatchItemRow)
                .where(PromptBatchItemRow.batch_id == batch_id)
                .order_by(PromptBatchItemRow.shot_order, PromptBatchItemRow.id)
            )
        )

    def add_item(self, session: Session, item: PromptBatchItemRow) -> None:
        session.add(item)

    def attempt(
        self, session: Session, attempt_id: str
    ) -> PromptBatchItemAttemptRow | None:
        return session.get(PromptBatchItemAttemptRow, attempt_id)

    def attempts_for_item(
        self, session: Session, item_id: str
    ) -> list[PromptBatchItemAttemptRow]:
        return list(
            session.scalars(
                select(PromptBatchItemAttemptRow)
                .where(PromptBatchItemAttemptRow.item_id == item_id)
                .order_by(PromptBatchItemAttemptRow.attempt_number)
            )
        )

    def add_attempt(
        self, session: Session, attempt: PromptBatchItemAttemptRow
    ) -> None:
        session.add(attempt)

    def set_current_attempt(
        self,
        session: Session,
        *,
        item_id: str,
        expected_attempt_id: str | None,
        attempt_id: str,
        updated_at,
    ) -> bool:
        statement = update(PromptBatchItemRow).where(
            PromptBatchItemRow.id == item_id
        )
        if expected_attempt_id is None:
            statement = statement.where(
                PromptBatchItemRow.current_attempt_id.is_(None)
            )
        else:
            statement = statement.where(
                PromptBatchItemRow.current_attempt_id == expected_attempt_id
            )
        result = session.execute(
            statement.values(current_attempt_id=attempt_id, updated_at=updated_at)
        )
        return result.rowcount == 1

    def add_decision_with_cas(
        self,
        session: Session,
        *,
        item_id: str,
        expected_version: int,
        decision: PromptBatchItemDecisionRow,
        updated_at,
    ) -> bool:
        result = session.execute(
            update(PromptBatchItemRow)
            .where(
                PromptBatchItemRow.id == item_id,
                PromptBatchItemRow.decision_version == expected_version,
            )
            .values(
                decision_version=expected_version + 1,
                updated_at=updated_at,
            )
        )
        if result.rowcount != 1:
            return False
        session.add(decision)
        return True

    def decisions_for_item(
        self, session: Session, item_id: str
    ) -> list[PromptBatchItemDecisionRow]:
        return list(
            session.scalars(
                select(PromptBatchItemDecisionRow)
                .where(PromptBatchItemDecisionRow.item_id == item_id)
                .order_by(PromptBatchItemDecisionRow.decision_sequence)
            )
        )

    def advance_batch_version(
        self,
        session: Session,
        *,
        batch_id: str,
        expected_version: int,
        values: dict[str, object],
    ) -> bool:
        result = session.execute(
            update(PromptBatchRow)
            .where(
                PromptBatchRow.id == batch_id,
                PromptBatchRow.version == expected_version,
            )
            .values(version=expected_version + 1, **values)
        )
        return result.rowcount == 1

    def mark_dispatching_unknown(self, session: Session, *, updated_at) -> int:
        extraction_result = session.execute(
            update(PromptProfileExtractionRow)
            .where(PromptProfileExtractionRow.state == "DISPATCHING")
            .values(
                state="UNKNOWN_TERMINAL",
                failure_code="RESTART_DURING_DISPATCH",
                updated_at=updated_at,
                completed_at=updated_at,
            )
        )
        attempt_result = session.execute(
            update(PromptBatchItemAttemptRow)
            .where(PromptBatchItemAttemptRow.state == "DISPATCHING")
            .values(
                state="UNKNOWN_TERMINAL",
                failure_code="RESTART_DURING_DISPATCH",
                updated_at=updated_at,
                completed_at=updated_at,
            )
        )
        return extraction_result.rowcount + attempt_result.rowcount
