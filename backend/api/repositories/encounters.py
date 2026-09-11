"""Patient/Encounter repository (Phase 7)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.models.orm import Encounter, Patient


class PatientRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def create(
        self,
        *,
        mrn: str | None,
        full_name: str,
        date_of_birth: str | None,
        sex: str | None,
        notes: str,
        created_by: str | None,
    ) -> Patient:
        async with self._sm() as session:
            patient = Patient(
                mrn=mrn, full_name=full_name, date_of_birth=date_of_birth,
                sex=sex, notes=notes, created_by=created_by,
            )
            session.add(patient)
            await session.commit()
            await session.refresh(patient)
            return patient

    async def get(self, patient_id: str) -> Patient | None:
        async with self._sm() as session:
            return await session.get(Patient, patient_id)

    async def search(self, query: str, *, limit: int = 25) -> list[Patient]:
        async with self._sm() as session:
            stmt = select(Patient).order_by(Patient.created_at.desc()).limit(limit)
            if query:
                like = f"%{query}%"
                stmt = (
                    select(Patient)
                    .where(or_(Patient.full_name.ilike(like), Patient.mrn.ilike(like)))
                    .order_by(Patient.created_at.desc())
                    .limit(limit)
                )
            result = await session.execute(stmt)
            return list(result.scalars())

    @staticmethod
    def to_dict(patient: Patient) -> dict[str, Any]:
        return {
            "patient_id": patient.id,
            "mrn": patient.mrn,
            "full_name": patient.full_name,
            "date_of_birth": patient.date_of_birth,
            "sex": patient.sex,
            "notes": patient.notes,
            "created_at": patient.created_at.isoformat() if patient.created_at else "",
        }


class EncounterRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def create(
        self,
        *,
        patient_id: str | None,
        encounter_date: str | None,
        privacy_required: bool,
        note: str,
        created_by: str | None,
        encounter_id: str | None = None,
    ) -> Encounter:
        async with self._sm() as session:
            encounter = Encounter(
                id=encounter_id,
                patient_id=patient_id,
                encounter_date=encounter_date,
                privacy_required=privacy_required,
                note=note,
                created_by=created_by,
            )
            session.add(encounter)
            await session.commit()
            await session.refresh(encounter)
            return encounter

    async def get(self, encounter_id: str) -> Encounter | None:
        async with self._sm() as session:
            return await session.get(Encounter, encounter_id)

    async def list_for_patient(self, patient_id: str) -> list[Encounter]:
        async with self._sm() as session:
            result = await session.execute(
                select(Encounter)
                .where(Encounter.patient_id == patient_id)
                .order_by(Encounter.created_at.desc())
            )
            return list(result.scalars())

    @staticmethod
    def to_dict(encounter: Encounter) -> dict[str, Any]:
        return {
            "encounter_id": encounter.id,
            "patient_id": encounter.patient_id,
            "encounter_date": encounter.encounter_date,
            "status": encounter.status,
            "privacy_required": encounter.privacy_required,
            "note": encounter.note,
            "created_at": encounter.created_at.isoformat() if encounter.created_at else "",
        }
