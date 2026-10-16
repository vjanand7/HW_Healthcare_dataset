from typing import Annotated

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

import service
from database import DatabaseNotConfigured
from models import InterventionUpdate

MAX_PAGE_SIZE = 1000
Limit = Annotated[int, Query(ge=0, le=MAX_PAGE_SIZE)]
Offset = Annotated[int, Query(ge=0, le=2_147_483_647)]

app = FastAPI(
    title="Diabetes Readmission Intervention Queue MVP",
    description="FDE Enterprise Healthcare Care-Management Backend",
    version="1.0.0"
)


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.exception_handler(service.NotFoundError)
def handle_not_found(request: Request, error: service.NotFoundError):
    return _error(404, str(error))


@app.exception_handler(service.ConflictError)
def handle_conflict(request: Request, error: service.ConflictError):
    return _error(409, str(error))


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(DatabaseNotConfigured)
def handle_database_unavailable(request: Request, error: Exception):
    return _error(503, "The database is unavailable.")


@app.get("/encounters")
def list_encounters(limit: Limit = 100, offset: Offset = 0):
    """Retrieves a paginated list of all patient encounters."""
    return service.list_encounters(limit, offset)


@app.get("/encounters/{encounter_id}")
def get_encounter(encounter_id: str):
    """Retrieves a single patient encounter by its unique ID, raising a 404 if not found."""
    return service.get_encounter(encounter_id)


@app.get("/care-queue")
def care_queue(limit: Limit = 50, offset: Offset = 0):
    """Open MEDIUM and HIGH risk encounters for the care team, highest score first."""
    return service.get_care_queue(["MEDIUM", "HIGH"], limit, offset)


@app.get("/care-queue/high")
def high_risk_care_queue(limit: Limit = 50, offset: Offset = 0):
    """Open encounters restricted strictly to HIGH risk, highest score first."""
    return service.get_care_queue(["HIGH"], limit, offset)


@app.patch("/interventions/{encounter_id}")
def update_intervention(encounter_id: str, payload: InterventionUpdate):
    """Updates or creates the workflow status for an encounter.

    `assigned_to` and `notes` are only changed when they are included in the request.
    """
    fields = {
        name: getattr(payload, name)
        for name in ("assigned_to", "notes")
        if name in payload.model_fields_set
    }
    updated = service.update_intervention(encounter_id, payload.status, fields)
    return {"message": "Intervention updated successfully", "data": updated}


@app.get("/operations/summary")
def operations_summary():
    """Provides operational metrics and queue backlogs calculated from database records."""
    return service.get_operations_metrics()


@app.get("/operations/readmission-analysis")
def readmission_analysis():
    """Compares every risk group against historical <30-day readmission outcomes."""
    return service.get_readmission_stats()


@app.get("/management/dashboard")
def management_dashboard():
    """Executive summary combining overall operational metrics and clinical validation signals."""
    return {
        "operations": service.get_operations_metrics(),
        "clinical_validation": service.get_readmission_stats()
    }
