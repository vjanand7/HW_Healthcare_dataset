from psycopg.rows import dict_row

from database import get_connection

# Columns of care_interventions a caller may set (guards the dynamic SQL below).
UPDATABLE_INTERVENTION_FIELDS = ("assigned_to", "notes")


def get_all_encounters(limit: int = 100, offset: int = 0):
    """Retrieves a page of patient encounters in a stable order."""
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM patient_encounters ORDER BY encounter_id LIMIT %s OFFSET %s;",
                (limit, offset),
            )
            return cursor.fetchall()


def get_encounter_by_id(encounter_id: str):
    """Retrieves a single encounter by its unique ID."""
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM patient_encounters WHERE encounter_id = %s;",
                (encounter_id,),
            )
            return cursor.fetchone()


def get_care_queue(risk_levels: list, limit: int = 50, offset: int = 0):
    """
    Open (not CLOSED) encounters at the given risk levels, highest score first,
    joined with their care intervention. Ties are broken by encounter_id so paging
    is stable. Encounters nobody has touched yet show status PENDING_REVIEW.
    """
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT pe.*,
                       COALESCE(ci.status, 'PENDING_REVIEW') AS status,
                       ci.assigned_to, ci.notes, ci.updated_at
                FROM patient_encounters pe
                LEFT JOIN care_interventions ci ON ci.encounter_id = pe.encounter_id
                WHERE pe.risk_level = ANY(%s)
                  AND COALESCE(ci.status, 'PENDING_REVIEW') <> 'CLOSED'
                ORDER BY pe.risk_score DESC, pe.encounter_id
                LIMIT %s OFFSET %s;
                """,
                (risk_levels, limit, offset),
            )
            return cursor.fetchall()


def get_intervention(encounter_id: str):
    """Returns the care intervention for an encounter, or None."""
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM care_interventions WHERE encounter_id = %s;",
                (encounter_id,),
            )
            return cursor.fetchone()


def upsert_intervention(encounter_id: str, status: str, fields: dict):
    """
    Creates or updates the care intervention for an encounter in one statement.
    Only `status` and the keys present in `fields` are written; other columns keep
    their current values.
    """
    unknown = set(fields) - set(UPDATABLE_INTERVENTION_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported intervention field(s): {', '.join(sorted(unknown))}")

    # Static SQL: every value (and the "was this field sent?" flags) is a bound parameter.
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO care_interventions (encounter_id, status, assigned_to, notes)
                VALUES (%(encounter_id)s, %(status)s, %(assigned_to)s, %(notes)s)
                ON CONFLICT (encounter_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    assigned_to = CASE WHEN %(set_assigned_to)s
                                       THEN EXCLUDED.assigned_to ELSE care_interventions.assigned_to END,
                    notes = CASE WHEN %(set_notes)s
                                 THEN EXCLUDED.notes ELSE care_interventions.notes END,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *;
                """,
                {
                    "encounter_id": encounter_id,
                    "status": status,
                    "assigned_to": fields.get("assigned_to"),
                    "notes": fields.get("notes"),
                    "set_assigned_to": "assigned_to" in fields,
                    "set_notes": "notes" in fields,
                },
            )
            return cursor.fetchone()


def get_operations_metrics():
    """
    Operational counts in one query. "Pending" means a queue-level (MEDIUM/HIGH)
    encounter nobody has started on, plus any intervention explicitly pending review.
    """
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_encounters,
                    COUNT(*) FILTER (WHERE pe.risk_level = 'HIGH') AS high_risk,
                    COUNT(*) FILTER (WHERE pe.risk_level = 'MEDIUM') AS medium_risk,
                    COUNT(*) FILTER (WHERE pe.risk_level = 'LOW') AS low_risk,
                    COUNT(*) FILTER (
                        WHERE ci.status = 'PENDING_REVIEW'
                           OR (ci.status IS NULL AND pe.risk_level IN ('MEDIUM', 'HIGH'))
                    ) AS pending_interventions,
                    COUNT(*) FILTER (WHERE ci.status = 'CLOSED') AS completed_interventions
                FROM patient_encounters pe
                LEFT JOIN care_interventions ci ON ci.encounter_id = pe.encounter_id;
                """
            )
            return cursor.fetchone()


def get_readmission_stats():
    """Historical <30-day readmissions for every risk group (HIGH figures also at top level)."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT risk_level,
                       COUNT(*),
                       COUNT(*) FILTER (WHERE readmitted = '<30')
                FROM patient_encounters
                GROUP BY risk_level;
                """
            )
            found = {level: (patients, readmitted) for level, patients, readmitted in cursor.fetchall()}

    groups = {}
    for level in ("HIGH", "MEDIUM", "LOW"):
        patients, readmitted = found.get(level, (0, 0))
        groups[level] = {
            "patients": patients,
            "readmitted_under_30_days": readmitted,
            "readmission_rate": round(readmitted / patients * 100, 2) if patients else 0.0,
        }

    high = groups["HIGH"]
    return {
        "high_risk_patients": high["patients"],
        "high_risk_readmitted_under_30_days": high["readmitted_under_30_days"],
        "high_risk_readmission_rate": high["readmission_rate"],
        "risk_groups": groups,
    }
