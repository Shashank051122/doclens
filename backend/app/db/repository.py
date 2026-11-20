import json
import uuid

from app.db.database import get_connection, utc_now


def pick_current_data(row) -> dict:
    return json.loads(row["corrected_json"] or row["data_json"])


def to_summary(row) -> dict:
    data = pick_current_data(row)
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "filename": row["filename"],
        "file_type": row["file_type"],
        "status": row["status"],
        "risk": row["risk"],
        "provider": row["provider"],
        "duration_ms": row["duration_ms"],
        "vendor": data.get("vendor"),
        "total": data.get("total"),
        "currency": data.get("currency"),
    }


def save_document(record: dict) -> str:
    document_id = uuid.uuid4().hex

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO documents (
                id, created_at, filename, file_type, size_bytes, mode, pages_read,
                provider, model, duration_ms, status, risk,
                data_json, validation_json, trace_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                utc_now(),
                record["filename"],
                record["file_type"],
                record["size_bytes"],
                record["mode"],
                record["pages_read"],
                record["provider"],
                record["model"],
                record["duration_ms"],
                record["status"],
                record["risk"],
                json.dumps(record["data"]),
                json.dumps(record["validation"]),
                json.dumps(record["trace"]),
            ),
        )

    return document_id


def list_documents(limit: int = 50) -> list:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM documents ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

    return [to_summary(row) for row in rows]


def get_document(document_id: str):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()

    if row is None:
        return None

    document = to_summary(row)
    document.update(
        {
            "size_bytes": row["size_bytes"],
            "mode": row["mode"],
            "pages_read": row["pages_read"],
            "model": row["model"],
            "data": pick_current_data(row),
            "original_data": json.loads(row["data_json"]),
            "was_corrected": row["corrected_json"] is not None,
            "validation": json.loads(row["validation_json"]),
            "trace": json.loads(row["trace_json"]),
        }
    )
    return document


def update_correction(document_id: str, data: dict, validation: dict) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE documents
            SET corrected_json = ?, validation_json = ?, status = 'reviewed'
            WHERE id = ?
            """,
            (json.dumps(data), json.dumps(validation), document_id),
        )
        return cursor.rowcount > 0


def delete_document(document_id: str) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM documents WHERE id = ?", (document_id,)
        )
        return cursor.rowcount > 0


def get_stats() -> dict:
    with get_connection() as connection:
        totals = connection.execute(
            """
            SELECT
                COUNT(*) AS total_documents,
                COALESCE(SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END), 0) AS ok_count,
                COALESCE(SUM(CASE WHEN status = 'needs_review' THEN 1 ELSE 0 END), 0) AS review_count,
                COALESCE(SUM(CASE WHEN status = 'reviewed' THEN 1 ELSE 0 END), 0) AS reviewed_count,
                COALESCE(SUM(CASE WHEN corrected_json IS NOT NULL THEN 1 ELSE 0 END), 0) AS corrected_count,
                COALESCE(AVG(duration_ms), 0) AS average_ms
            FROM documents
            """
        ).fetchone()

        providers = connection.execute(
            """
            SELECT provider, COUNT(*) AS uses, AVG(duration_ms) AS average_ms
            FROM documents
            GROUP BY provider
            ORDER BY uses DESC
            """
        ).fetchall()

    return {
        "total_documents": totals["total_documents"],
        "ok": totals["ok_count"],
        "needs_review": totals["review_count"],
        "reviewed": totals["reviewed_count"],
        "corrected_by_users": totals["corrected_count"],
        "average_duration_ms": int(round(totals["average_ms"])),
        "providers": [
            {
                "provider": row["provider"],
                "uses": row["uses"],
                "average_duration_ms": int(round(row["average_ms"])),
            }
            for row in providers
        ],
    }