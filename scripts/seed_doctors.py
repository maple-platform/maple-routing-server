import asyncio
import os
from datetime import datetime, timezone

from argon2 import PasswordHasher

from config.database import close_database, connect_database, ensure_indexes, get_database
from config.settings import HOSPITAL_ID, HOSPITAL_NAME
from repositories.doctors_repository import DoctorsRepository


SEED_DOCTORS = [
    {
        "employee_id": "chest01",
        "name": "김체스트 교수",
        "department": "정형외과",
        "title": "전문의",
        "roles": ["doctor"],
    },
    {
        "employee_id": "rad02",
        "name": "이영상 전공의",
        "department": "영상의학과",
        "title": "전공의",
        "roles": ["doctor"],
    },
    {
        "employee_id": "mapleadmin03",
        "name": "박메이플 교수",
        "department": "영상의학과",
        "title": "전문의",
        "roles": ["doctor", "admin"],
    },
]


async def seed() -> None:
    doctor_password = os.getenv("SEED_DOCTOR_PASSWORD")
    admin_password = os.getenv("SEED_ADMIN_PASSWORD") or doctor_password
    if not doctor_password:
        raise SystemExit(
            "SEED_DOCTOR_PASSWORD is required. "
            "Optionally set SEED_ADMIN_PASSWORD for the doctor/admin account."
        )

    await connect_database()
    try:
        await ensure_indexes()
        repository = DoctorsRepository(get_database())
        hasher = PasswordHasher()
        now = datetime.now(timezone.utc)

        for seed_doctor in SEED_DOCTORS:
            password = admin_password if "admin" in seed_doctor["roles"] else doctor_password
            values = {
                **seed_doctor,
                "password_hash": hasher.hash(password),
                "hospital_id": HOSPITAL_ID,
                "hospital": HOSPITAL_NAME,
                "initial": seed_doctor["name"][:1],
                "is_active": True,
                "updated_at": now,
                "last_login_at": None,
            }
            doctor = await repository.upsert_seed(seed_doctor["employee_id"], values)
            print(
                f"seeded {doctor['employee_id']} "
                f"roles={','.join(doctor['roles'])}"
            )
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(seed())
