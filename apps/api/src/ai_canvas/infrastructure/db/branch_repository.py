from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ai_canvas.infrastructure.db.models import (
    GraphBranchHeadRow,
    GraphBranchRow,
    GraphDocumentRow,
    GraphPromotionRow,
    GraphSelectionPointerRow,
    NodeMaterializationStateRow,
)


RowT = TypeVar("RowT")


@dataclass(frozen=True)
class CasResult(Generic[RowT]):
    applied: bool
    current: RowT


class BranchRepository:
    def branch(self, session: Session, branch_id: str) -> GraphBranchRow | None:
        return session.get(GraphBranchRow, branch_id)

    def branch_for_project(
        self, session: Session, project_id: str, branch_id: str
    ) -> GraphBranchRow | None:
        return session.scalar(
            select(GraphBranchRow)
            .join(GraphDocumentRow, GraphDocumentRow.id == GraphBranchRow.graph_document_id)
            .where(
                GraphDocumentRow.project_id == project_id,
                GraphBranchRow.id == branch_id,
            )
        )

    def branch_by_normalized_name(
        self, session: Session, graph_document_id: str, normalized_name: str
    ) -> GraphBranchRow | None:
        return session.scalar(
            select(GraphBranchRow).where(
                GraphBranchRow.graph_document_id == graph_document_id,
                GraphBranchRow.normalized_name == normalized_name,
            )
        )

    def branches_for_document(
        self, session: Session, graph_document_id: str
    ) -> list[GraphBranchRow]:
        return list(
            session.scalars(
                select(GraphBranchRow)
                .where(GraphBranchRow.graph_document_id == graph_document_id)
                .order_by(
                    (GraphBranchRow.kind != "MAIN"),
                    GraphBranchRow.created_at,
                    GraphBranchRow.id,
                )
            )
        )

    def head(self, session: Session, branch_id: str) -> GraphBranchHeadRow | None:
        return session.get(GraphBranchHeadRow, branch_id)

    def selection(
        self, session: Session, graph_document_id: str
    ) -> GraphSelectionPointerRow | None:
        return session.get(GraphSelectionPointerRow, graph_document_id)

    def create_branch_with_head(
        self, session: Session, branch: GraphBranchRow, head: GraphBranchHeadRow
    ) -> None:
        if head.branch_id != branch.id:
            raise ValueError("branch head belongs to another branch")
        session.add(branch)
        session.flush()
        session.add(head)

    def advance_head(
        self,
        session: Session,
        *,
        branch_id: str,
        expected_revision_id: str,
        expected_version: int,
        new_revision_id: str,
        updated_at: datetime | None = None,
    ) -> CasResult[GraphBranchHeadRow]:
        values: dict[str, object] = {
            "graph_revision_id": new_revision_id,
            "version": expected_version + 1,
        }
        if updated_at is not None:
            values["updated_at"] = updated_at
        result = session.execute(
            update(GraphBranchHeadRow)
            .where(
                GraphBranchHeadRow.branch_id == branch_id,
                GraphBranchHeadRow.graph_revision_id == expected_revision_id,
                GraphBranchHeadRow.version == expected_version,
            )
            .values(**values)
        )
        current = session.get(GraphBranchHeadRow, branch_id, populate_existing=True)
        if current is None:
            raise LookupError(f"branch head {branch_id} not found")
        return CasResult(applied=result.rowcount == 1, current=current)

    def move_selection(
        self,
        session: Session,
        *,
        graph_document_id: str,
        expected_branch_id: str,
        expected_revision_id: str,
        expected_version: int,
        new_branch_id: str,
        new_revision_id: str,
        updated_at: datetime | None = None,
    ) -> CasResult[GraphSelectionPointerRow]:
        values: dict[str, object] = {
            "branch_id": new_branch_id,
            "graph_revision_id": new_revision_id,
            "version": expected_version + 1,
        }
        if updated_at is not None:
            values["updated_at"] = updated_at
        result = session.execute(
            update(GraphSelectionPointerRow)
            .where(
                GraphSelectionPointerRow.graph_document_id == graph_document_id,
                GraphSelectionPointerRow.branch_id == expected_branch_id,
                GraphSelectionPointerRow.graph_revision_id == expected_revision_id,
                GraphSelectionPointerRow.version == expected_version,
            )
            .values(**values)
        )
        current = session.get(
            GraphSelectionPointerRow, graph_document_id, populate_existing=True
        )
        if current is None:
            raise LookupError(f"selection pointer {graph_document_id} not found")
        return CasResult(applied=result.rowcount == 1, current=current)

    def add_promotion(self, session: Session, promotion: GraphPromotionRow) -> None:
        session.add(promotion)

    def promotion(self, session: Session, promotion_id: str) -> GraphPromotionRow | None:
        return session.get(GraphPromotionRow, promotion_id)

    def promotion_for_mutation(
        self, session: Session, project_id: str, client_mutation_id: str
    ) -> GraphPromotionRow | None:
        return session.scalar(
            select(GraphPromotionRow).where(
                GraphPromotionRow.project_id == project_id,
                GraphPromotionRow.client_mutation_id == client_mutation_id,
            )
        )

    def effective_latest_promotion(
        self, session: Session, project_id: str
    ) -> GraphPromotionRow | None:
        return session.scalar(
            select(GraphPromotionRow)
            .where(
                GraphPromotionRow.project_id == project_id,
                GraphPromotionRow.state == "APPLIED",
            )
            .order_by(GraphPromotionRow.applied_at.desc(), GraphPromotionRow.id.desc())
            .limit(1)
        )

    def materialization_rows(
        self, session: Session, graph_revision_id: str
    ) -> list[NodeMaterializationStateRow]:
        return list(
            session.scalars(
                select(NodeMaterializationStateRow)
                .where(NodeMaterializationStateRow.graph_revision_id == graph_revision_id)
                .order_by(NodeMaterializationStateRow.node_id)
            )
        )

    def replace_materialization_rows(
        self,
        session: Session,
        graph_revision_id: str,
        rows: list[NodeMaterializationStateRow],
    ) -> None:
        if any(row.graph_revision_id != graph_revision_id for row in rows):
            raise ValueError("materialization row belongs to another graph revision")
        session.execute(
            delete(NodeMaterializationStateRow).where(
                NodeMaterializationStateRow.graph_revision_id == graph_revision_id
            )
        )
        session.add_all(rows)
