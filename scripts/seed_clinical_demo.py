import asyncio
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from config.database import close_database, connect_database, ensure_indexes, get_database
from config.settings import APP_TIMEZONE, HOSPITAL_ID
from scripts.apply_clinical_v4_data import backfill_demo_assets


PATIENT_NAMES = [
    "김민준", "이서연", "박지훈", "최하윤", "정도윤", "강지우", "조현우", "윤서아",
    "장민재", "임지민", "한예준", "오수빈", "서준호", "신채원", "권도현", "황유진",
    "안시우", "송예린", "전우진", "홍가은", "유건우", "고나연", "문준서", "양다은",
    "손현준", "배소율", "백승민", "허지안", "남주원", "심하린", "노태윤", "하윤아",
    "곽성현", "성예은", "차재윤", "주아린", "우선호", "민서윤", "진도경", "엄채린",
]


def _demo_date() -> date:
    raw = os.getenv("DEMO_DATE")
    return date.fromisoformat(raw) if raw else datetime.now(ZoneInfo(APP_TIMEZONE)).date()


async def seed() -> None:
    await connect_database()
    try:
        await ensure_indexes()
        db = get_database()
        doctors = await db["doctors"].find(
            {"employee_id": {"$in": ["chest01", "rad02", "mapleadmin03"]}}
        ).to_list(None)
        doctors_by_id = {doctor["employee_id"]: doctor for doctor in doctors}
        missing = {"chest01", "rad02", "mapleadmin03"} - set(doctors_by_id)
        if missing:
            raise SystemExit(f"seed doctors first; missing: {', '.join(sorted(missing))}")

        doctor_order = ["chest01"] * 14 + ["rad02"] * 13 + ["mapleadmin03"] * 13
        local_tz = ZoneInfo(APP_TIMEZONE)
        selected_date = _demo_date()
        now = datetime.now(timezone.utc)
        per_doctor_index = {employee_id: 0 for employee_id in doctors_by_id}
        risk_tiers = ("Critical", "High", "Low")
        care_statuses = ("관찰중", "치료중", "추적관찰", "퇴원")

        for index, (name, employee_id) in enumerate(zip(PATIENT_NAMES, doctor_order), 1):
            doctor = doctors_by_id[employee_id]
            doctor_slot = per_doctor_index[employee_id]
            per_doctor_index[employee_id] += 1
            scheduled_local = datetime.combine(
                selected_date, time(8, 0), tzinfo=local_tz
            ) + timedelta(minutes=40 * doctor_slot)
            scheduled_at = scheduled_local.astimezone(timezone.utc)
            appointment_status = "completed" if scheduled_at <= now else "scheduled"
            visit_status = "completed" if appointment_status == "completed" else "open"
            patient_id = f"PT-{1000 + index}"
            date_key = selected_date.strftime("%Y%m%d")
            appointment_id = f"A-{date_key}-{index:04d}"
            visit_id = f"V-{date_key}-{index:04d}"
            analysis_id = f"AN-{date_key}-{index:04d}"
            birth_date = date(
                1945 + ((index * 7) % 55),
                ((index * 3) % 12) + 1,
                ((index * 5) % 27) + 1,
            )
            has_demo_analysis = index % 3 != 0
            exam_type = "DICOM" if has_demo_analysis else ""

            await db["patients"].update_one(
                {"patient_id": patient_id},
                {"$setOnInsert": {
                    "patient_id": patient_id,
                    "hospital_id": HOSPITAL_ID,
                    "name": name,
                    "search_name": "".join(name.split()).lower(),
                    "gender": "F" if index % 2 == 0 else "M",
                    "birth_date": birth_date.isoformat(),
                    "assigned_doctor_id": doctor["_id"],
                    "created_by_doctor_id": doctor["_id"],
                    "care_status": care_statuses[(index - 1) % len(care_statuses)],
                    "is_archived": False,
                    "created_at": now,
                    "updated_at": now,
                }},
                upsert=True,
            )
            await db["appointments"].update_one(
                {"appointment_id": appointment_id},
                {"$setOnInsert": {
                    "appointment_id": appointment_id,
                    "hospital_id": HOSPITAL_ID,
                    "patient_id": patient_id,
                    "doctor_id": doctor["_id"],
                    "scheduled_at": scheduled_at,
                    "scheduled_date": selected_date.isoformat(),
                    "scheduled_time": scheduled_local.strftime("%H:%M"),
                    "exam_type": exam_type,
                    "status": appointment_status,
                    "slot_claimed": appointment_status == "scheduled",
                    "created_at": now,
                    "updated_at": now,
                }},
                upsert=True,
            )
            await db["visits"].update_one(
                {"visit_id": visit_id},
                {"$setOnInsert": {
                    "visit_id": visit_id,
                    "hospital_id": HOSPITAL_ID,
                    "patient_id": patient_id,
                    "appointment_id": appointment_id,
                    "doctor_id": doctor["_id"],
                    "visit_date": selected_date.isoformat(),
                    "exam_type": exam_type,
                    "modalities": [],
                    "status": visit_status,
                    "created_at": now,
                    "updated_at": now,
                    "completed_at": now if visit_status == "completed" else None,
                }},
                upsert=True,
            )
            if has_demo_analysis:
                risk_tier = risk_tiers[(index - 1) % len(risk_tiers)]
                await db["analyses"].update_one(
                    {"analysis_id": analysis_id},
                    {"$setOnInsert": {
                        "analysis_id": analysis_id,
                        "hospital_id": HOSPITAL_ID,
                        "patient_id": patient_id,
                        "visit_id": visit_id,
                        "appointment_id": appointment_id,
                        "requested_by_doctor_id": doctor["_id"],
                        "visit_doctor_id": doctor["_id"],
                        "scheduled_at": scheduled_at,
                        "queued_at": now,
                        "started_at": now,
                        "completed_at": now,
                        "cancel_requested_at": None,
                        "status": "done",
                        "attempt": 1,
                        "worker_id": "demo-seed",
                        "lease_expires_at": None,
                        "mode": "auto",
                        "query": "데모 영상 분석",
                        "department": doctor["department"],
                        "project": "demo",
                        "model_name": "demo-model",
                        "risk_tier": risk_tier,
                        "risk_status": "assessed",
                        "confidence": round(0.71 + (index % 20) * 0.01, 2),
                        "finding": f"{risk_tier} 위험도 데모 소견",
                        "interpretation": "시연을 위한 예시 분석 결과입니다.",
                        "recommendation": "담당 의료진의 임상 판단이 필요합니다.",
                        "predictions": None,
                        "input_file_ids": [],
                        "result_file_ids": {"base": [], "heat": [], "box": []},
                        "error": None,
                        "created_at": now,
                        "updated_at": now,
                        "superseded_at": None,
                    }},
                    upsert=True,
                )

        await db["counters"].update_one(
            {"_id": "patient_id"},
            {"$max": {"seq": 1040}, "$set": {"updated_at": now}},
            upsert=True,
        )
        for prefix in ("appointment", "visit", "analysis"):
            await db["counters"].update_one(
                {"_id": f"{prefix}:{selected_date.strftime('%Y%m%d')}"},
                {"$max": {"seq": 40}, "$set": {"updated_at": now}},
                upsert=True,
            )
        await backfill_demo_assets(db)
        print(
            f"seeded 40 demo patients for {selected_date.isoformat()} "
            "(chest01=14, rad02=13, mapleadmin03=13)"
        )
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(seed())
