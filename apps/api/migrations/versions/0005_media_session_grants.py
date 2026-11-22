"""Allow one media session to grant multiple resources.

Revision ID: 0005_media_session_grants
Revises: 0004_media_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_media_session_grants"
down_revision: str | None = "0004_media_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEDIA_SESSIONS_TABLE = "media_sessions"
_MEDIA_SESSIONS_SHADOW = "_alembic_tmp_media_sessions"
_MEDIA_SESSION_BASE_COLUMNS = {
    "id",
    "subject",
    "created_at",
    "last_used_at",
    "idle_expires_at",
    "absolute_expires_at",
    "revoked_at",
}
_MEDIA_SESSION_LEGACY_COLUMNS = {"resource_kind", "resource_id"}


def _table_names(connection: sa.Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


def _columns(connection: sa.Connection, table_name: str) -> dict[str, dict[str, object]]:
    return {
        str(column["name"]): column
        for column in sa.inspect(connection).get_columns(table_name)
    }


def _index_names(connection: sa.Connection, table_name: str) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(connection).get_indexes(table_name)
        if index["name"] is not None
    }


def _validate_media_sessions_shadow(
    connection: sa.Connection,
    *,
    include_legacy_resources: bool,
) -> None:
    expected_columns = set(_MEDIA_SESSION_BASE_COLUMNS)
    if include_legacy_resources:
        expected_columns.update(_MEDIA_SESSION_LEGACY_COLUMNS)
    shadow_columns = _columns(connection, _MEDIA_SESSIONS_SHADOW)
    if set(shadow_columns) != expected_columns:
        raise RuntimeError(
            "cannot recover Alembic media_sessions shadow with unexpected columns"
        )
    primary_key = sa.inspect(connection).get_pk_constraint(_MEDIA_SESSIONS_SHADOW)
    if primary_key.get("constrained_columns") != ["id"]:
        raise RuntimeError(
            "cannot recover Alembic media_sessions shadow without the expected primary key"
        )
    required_columns = expected_columns - {"revoked_at"}
    if any(bool(shadow_columns[column]["nullable"]) for column in required_columns):
        raise RuntimeError(
            "cannot recover Alembic media_sessions shadow with nullable required columns"
        )
    null_predicate = " OR ".join(f"{column} IS NULL" for column in sorted(required_columns))
    if connection.execute(
        sa.text(
            f"SELECT id FROM {_MEDIA_SESSIONS_SHADOW} "
            f"WHERE {null_predicate} LIMIT 1"
        )
    ).first() is not None:
        raise RuntimeError(
            "cannot recover Alembic media_sessions shadow containing incomplete rows"
        )


def _ensure_media_sessions_expiry_index(connection: sa.Connection) -> None:
    if "ix_media_sessions_expires" not in _index_names(
        connection,
        _MEDIA_SESSIONS_TABLE,
    ):
        op.create_index(
            "ix_media_sessions_expires",
            _MEDIA_SESSIONS_TABLE,
            ["absolute_expires_at"],
        )


def _recover_media_sessions_shadow(
    connection: sa.Connection,
    *,
    include_legacy_resources: bool,
) -> None:
    tables = _table_names(connection)
    if _MEDIA_SESSIONS_SHADOW not in tables:
        if _MEDIA_SESSIONS_TABLE not in tables:
            raise RuntimeError("media_sessions and its Alembic shadow are both missing")
        _ensure_media_sessions_expiry_index(connection)
        return

    _validate_media_sessions_shadow(
        connection,
        include_legacy_resources=include_legacy_resources,
    )
    grants_exist = "media_session_grants" in tables
    if include_legacy_resources and grants_exist:
        raise RuntimeError(
            "cannot recover downgrade media_sessions shadow before grants are dropped"
        )
    if not include_legacy_resources and not grants_exist:
        raise RuntimeError(
            "cannot recover upgrade media_sessions shadow without session grants"
        )

    if _MEDIA_SESSIONS_TABLE in tables:
        op.drop_table(_MEDIA_SESSIONS_SHADOW)
        _ensure_media_sessions_expiry_index(connection)
        return

    if not include_legacy_resources:
        missing_shadow_session = connection.execute(
            sa.text(
                "SELECT media_session_grants.media_session_id FROM media_session_grants "
                f"LEFT JOIN {_MEDIA_SESSIONS_SHADOW} ON "
                f"{_MEDIA_SESSIONS_SHADOW}.id = media_session_grants.media_session_id "
                f"WHERE {_MEDIA_SESSIONS_SHADOW}.id IS NULL LIMIT 1"
            )
        ).first()
        missing_shadow_grant = connection.execute(
            sa.text(
                f"SELECT {_MEDIA_SESSIONS_SHADOW}.id FROM {_MEDIA_SESSIONS_SHADOW} "
                "LEFT JOIN media_session_grants ON media_session_grants.media_session_id = "
                f"{_MEDIA_SESSIONS_SHADOW}.id "
                "WHERE media_session_grants.media_session_id IS NULL LIMIT 1"
            )
        ).first()
        if missing_shadow_session is not None or missing_shadow_grant is not None:
            raise RuntimeError(
                "cannot recover upgrade media_sessions shadow with inconsistent grants"
            )

    op.rename_table(_MEDIA_SESSIONS_SHADOW, _MEDIA_SESSIONS_TABLE)
    _ensure_media_sessions_expiry_index(connection)


def upgrade() -> None:
    connection = op.get_bind()
    _recover_media_sessions_shadow(
        connection,
        include_legacy_resources=False,
    )
    legacy_columns = {"resource_kind", "resource_id"} & set(
        _columns(connection, "media_sessions")
    )
    grants_exist = "media_session_grants" in _table_names(connection)
    if not grants_exist:
        if legacy_columns != {"resource_kind", "resource_id"}:
            raise RuntimeError(
                "cannot resume media-session grant upgrade without legacy resource columns"
            )
        op.create_table(
            "media_session_grants",
            sa.Column("media_session_id", sa.String(length=64), primary_key=True),
            sa.Column("resource_kind", sa.String(length=32), primary_key=True),
            sa.Column("resource_id", sa.String(length=36), primary_key=True),
            sa.Column("granted_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["media_session_id"],
                ["media_sessions.id"],
                ondelete="CASCADE",
            ),
        )
    if "ix_media_session_grants_resource" not in _index_names(
        connection, "media_session_grants"
    ):
        op.create_index(
            "ix_media_session_grants_resource",
            "media_session_grants",
            ["resource_kind", "resource_id"],
        )
    if legacy_columns == {"resource_kind", "resource_id"}:
        op.execute(
            sa.text(
                "INSERT INTO media_session_grants "
                "(media_session_id, resource_kind, resource_id, granted_at) "
                "SELECT media_sessions.id, media_sessions.resource_kind, "
                "media_sessions.resource_id, media_sessions.created_at "
                "FROM media_sessions WHERE NOT EXISTS ("
                "SELECT 1 FROM media_session_grants "
                "WHERE media_session_id = media_sessions.id "
                "AND resource_kind = media_sessions.resource_kind "
                "AND resource_id = media_sessions.resource_id)"
            )
        )
    elif connection.execute(
        sa.text(
            "SELECT media_sessions.id FROM media_sessions "
            "LEFT JOIN media_session_grants "
            "ON media_session_grants.media_session_id = media_sessions.id "
            "WHERE media_session_grants.media_session_id IS NULL LIMIT 1"
        )
    ).first() is not None:
        raise RuntimeError(
            "cannot resume media-session grant upgrade with an unbackfilled session"
        )
    if legacy_columns:
        with op.batch_alter_table("media_sessions", recreate="always") as batch_op:
            for column_name in sorted(legacy_columns, reverse=True):
                batch_op.drop_column(column_name)


def downgrade() -> None:
    connection = op.get_bind()
    _recover_media_sessions_shadow(
        connection,
        include_legacy_resources=True,
    )
    grants_exist = "media_session_grants" in _table_names(connection)
    session_columns = _columns(connection, "media_sessions")
    if grants_exist:
        multi_grant_session = connection.execute(
            sa.text(
                "SELECT media_session_id FROM media_session_grants "
                "GROUP BY media_session_id HAVING COUNT(*) > 1 LIMIT 1"
            )
        ).first()
        if multi_grant_session is not None:
            raise RuntimeError(
                "cannot downgrade a media session with multiple resource grants"
            )
        missing_grant_session = connection.execute(
            sa.text(
                "SELECT media_sessions.id FROM media_sessions "
                "LEFT JOIN media_session_grants "
                "ON media_session_grants.media_session_id = media_sessions.id "
                "WHERE media_session_grants.media_session_id IS NULL LIMIT 1"
            )
        ).first()
        if missing_grant_session is not None:
            raise RuntimeError(
                "cannot downgrade a media session without a resource grant"
            )
    elif not {"resource_kind", "resource_id"} <= set(session_columns):
        raise RuntimeError(
            "cannot resume media-session grant downgrade without restored resource columns"
        )

    if "resource_kind" not in session_columns:
        op.add_column(
            "media_sessions",
            sa.Column("resource_kind", sa.String(length=32), nullable=True),
        )
    if "resource_id" not in session_columns:
        op.add_column(
            "media_sessions",
            sa.Column("resource_id", sa.String(length=36), nullable=True),
        )
    if grants_exist:
        op.execute(
            sa.text(
                "UPDATE media_sessions SET "
                "resource_kind = ("
                "SELECT resource_kind FROM media_session_grants "
                "WHERE media_session_id = media_sessions.id"
                "), resource_id = ("
                "SELECT resource_id FROM media_session_grants "
                "WHERE media_session_id = media_sessions.id"
                ")"
            )
        )
    if connection.execute(
        sa.text(
            "SELECT id FROM media_sessions "
            "WHERE resource_kind IS NULL OR resource_id IS NULL LIMIT 1"
        )
    ).first() is not None:
        raise RuntimeError("cannot downgrade a media session without restored resource data")
    if grants_exist:
        if "ix_media_session_grants_resource" in _index_names(
            connection, "media_session_grants"
        ):
            op.drop_index(
                "ix_media_session_grants_resource",
                table_name="media_session_grants",
            )
        op.drop_table("media_session_grants")
    session_columns = _columns(connection, "media_sessions")
    if any(
        bool(session_columns[column_name]["nullable"])
        for column_name in ("resource_kind", "resource_id")
    ):
        with op.batch_alter_table("media_sessions", recreate="always") as batch_op:
            batch_op.alter_column(
                "resource_kind",
                existing_type=sa.String(length=32),
                nullable=False,
            )
            batch_op.alter_column(
                "resource_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )
