"""Patient + Encounter endpoints (Phase 7, spec §13).

Structured clinical entities — persistence-backed. When the database is not
configured these answer 501 DB_NOT_CONFIGURED (honest capability reporting)
rather than silently pretending.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from api.auth.deps import CurrentPrincipal
from api.errors import ApiError, ErrorCode
from api.repositories.encounters import EncounterRepository, PatientRepository

router = APIRouter(tags=["clinical"])


def _require_db(request: Request):
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise ApiError(
            501,
            ErrorCode.DB_NOT_CONFIGURED,
            "patients/encounters require a configured database (MS_DATABASE__URL)",
        )
    return db


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    mrn: str | None = Field(default=None, max_length=40)
    date_of_birth: str | None = Field(default=None, max_length=20)
    sex: str | None = Field(default=None, max_length=20)
    notes: str = Field(default="", max_length=4000)


class EncounterCreate(BaseModel):
    patient_id: str | None = Field(default=None, max_length=40)
    encounter_date: str | None = Field(default=None, max_length=40)
    privacy_required: bool = True
    note: str = Field(default="", max_length=4000)


# -- patients ---------------------------------------------------------------


@router.get("/patients", status_code=200)
async def search_patients(
    request: Request,
    principal: CurrentPrincipal,
    q: str = Query(default="", max_length=80),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict:
    db = _require_db(request)
    repo = PatientRepository(db.sessionmaker)
    patients = await repo.search(q, limit=limit)
    return {"patients": [PatientRepository.to_dict(p) for p in patients], "total": len(patients)}


@router.post("/patients", status_code=201)
async def create_patient(
    request: Request, body: PatientCreate, principal: CurrentPrincipal
) -> dict:
    db = _require_db(request)
    repo = PatientRepository(db.sessionmaker)
    patient = await repo.create(
        mrn=body.mrn,
        full_name=body.full_name,
        date_of_birth=body.date_of_birth,
        sex=body.sex,
        notes=body.notes,
        created_by=principal.user_id,
    )
    request.app.state.audit.emit(
        "patient_created", patient_id=patient.id, user_id=principal.user_id,
        has_mrn=body.mrn is not None,
    )
    return PatientRepository.to_dict(patient)


@router.get("/patients/{patient_id}")
async def get_patient(request: Request, patient_id: str, principal: CurrentPrincipal) -> dict:
    db = _require_db(request)
    repo = PatientRepository(db.sessionmaker)
    patient = await repo.get(patient_id)
    if patient is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"patient '{patient_id}' not found")
    encounters = EncounterRepository(db.sessionmaker)
    rows = await encounters.list_for_patient(patient_id)
    out = PatientRepository.to_dict(patient)
    out["encounters"] = [EncounterRepository.to_dict(e) for e in rows]
    return out


# -- encounters -----------------------------------------------------------------


@router.post("/encounters", status_code=201)
async def create_encounter(
    request: Request, body: EncounterCreate, principal: CurrentPrincipal
) -> dict:
    db = _require_db(request)
    if body.patient_id is not None:
        patients = PatientRepository(db.sessionmaker)
        if await patients.get(body.patient_id) is None:
            raise ApiError(
                404, ErrorCode.NOT_FOUND, f"patient '{body.patient_id}' not found"
            )
    repo = EncounterRepository(db.sessionmaker)
    encounter = await repo.create(
        patient_id=body.patient_id,
        encounter_date=body.encounter_date,
        privacy_required=body.privacy_required,
        note=body.note,
        created_by=principal.user_id,
    )
    request.app.state.audit.emit(
        "encounter_created", encounter_id=encounter.id, user_id=principal.user_id,
        privacy_required=body.privacy_required,
    )
    return EncounterRepository.to_dict(encounter)


@router.get("/encounters/{encounter_id}")
async def get_encounter(request: Request, encounter_id: str, principal: CurrentPrincipal) -> dict:
    db = _require_db(request)
    repo = EncounterRepository(db.sessionmaker)
    encounter = await repo.get(encounter_id)
    if encounter is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"encounter '{encounter_id}' not found")
    out = EncounterRepository.to_dict(encounter)
    # linked activity (memory + durable views)
    out["reports"] = [
        r.summary()
        for r in await request.app.state.report_store.list_by_encounter(encounter_id)
    ]
    out["transcript_sessions"] = request.app.state.transcript_store.sessions_for_encounter(
        encounter_id
    )[:20]
    return out
