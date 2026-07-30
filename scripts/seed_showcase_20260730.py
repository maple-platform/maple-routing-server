"""Seed the client showcase schedule requested for 2026-07-30.

This seed is intentionally date-specific.  It preserves the six active patients
already on that date, restores two useful cancelled imaging cases, and creates
twelve demographic-only demo patients.  Re-running it updates the same records.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config.database import close_database, connect_database, ensure_indexes, get_database
from config.settings import APP_TIMEZONE, HOSPITAL_ID


SHOWCASE_DATE = "2026-07-30"
FROZEN_LEASE = datetime(2099, 12, 31, tzinfo=timezone.utc)
DOCTOR_EMPLOYEE_ID = "1234"  # chestKim

RISK_PLAN = [
    ("Critical", 0.92),
    ("High", 0.78),
    ("Critical", 0.88),
    ("Low", 0.24),
    ("High", 0.71),
    ("Low", 0.31),
    ("High", 0.66),
    ("Critical", 0.85),
]

SLOTS = [
    # Existing active patients retain their original times.  PT-1053 and
    # PT-1056 are restored from cancelled appointments for useful spine/CT cases.
    {"time": "08:00", "patient_id": "PT-1047", "appointment_id": "A-20260730-0001", "visit_id": "V-20260730-0001", "analysis_id": "AN-20260730-0001", "name": "김윤서", "gender": "M", "birth_date": "1950-04-11"},
    {"time": "08:20", "patient_id": "PT-1048", "appointment_id": "A-20260730-0002", "visit_id": "V-20260730-0002", "analysis_id": "AN-20260730-0002", "name": "김준섭", "gender": "M", "birth_date": "1950-06-20"},
    {"time": "08:40", "patient_id": "PT-1053", "appointment_id": "A-20260730-0006", "visit_id": "V-20260730-0006", "analysis_id": "AN-20260730-0006", "name": "정성호", "gender": "M", "birth_date": "1944-06-11"},
    {"time": "09:00", "patient_id": "PT-1049", "appointment_id": "A-20260730-0003", "visit_id": "V-20260730-0003", "analysis_id": "AN-20260730-0003", "name": "한다현", "gender": "F", "birth_date": "1951-06-19"},
    {"time": "09:20", "patient_id": "PT-1056", "appointment_id": "A-20260730-0009", "visit_id": "V-20260730-0009", "analysis_id": "AN-20260730-0009", "name": "김호현", "gender": "M", "birth_date": "1962-06-08"},
    {"time": "09:40", "patient_id": "PT-1058", "appointment_id": "A-20260730-0013", "visit_id": "V-20260730-0013", "analysis_id": "AN-20260730-0013", "name": "박서진", "gender": "F", "birth_date": "1971-02-14"},
    {"time": "10:00", "patient_id": "PT-1054", "appointment_id": "A-20260730-0007", "visit_id": "V-20260730-0007", "analysis_id": "AN-20260730-0007", "name": "정성호", "gender": "M", "birth_date": "1980-10-11"},
    {"time": "10:20", "patient_id": "PT-1059", "appointment_id": "A-20260730-0014", "visit_id": "V-20260730-0014", "analysis_id": "AN-20260730-0014", "name": "이수민", "gender": "F", "birth_date": "1992-08-23"},
    {"time": "10:40", "patient_id": "PT-1060", "appointment_id": "A-20260730-0015", "visit_id": "V-20260730-0015", "analysis_id": "AN-20260730-0015", "name": "최영수", "gender": "M", "birth_date": "1942-11-05"},
    {"time": "11:00", "patient_id": "PT-1055", "appointment_id": "A-20260730-0008", "visit_id": "V-20260730-0008", "analysis_id": "AN-20260730-0008", "name": "김준현", "gender": "M", "birth_date": "1956-09-15"},
    {"time": "11:20", "patient_id": "PT-1061", "appointment_id": "A-20260730-0016", "visit_id": "V-20260730-0016", "analysis_id": "AN-20260730-0016", "name": "윤미경", "gender": "F", "birth_date": "1965-03-30"},
    {"time": "11:40", "patient_id": "PT-1062", "appointment_id": "A-20260730-0017", "visit_id": "V-20260730-0017", "analysis_id": "AN-20260730-0017", "name": "한태욱", "gender": "M", "birth_date": "1978-12-09"},
    {"time": "12:00", "patient_id": "PT-1063", "appointment_id": "A-20260730-0018", "visit_id": "V-20260730-0018", "name": "송지연", "gender": "F", "birth_date": "1985-05-17"},
    {"time": "12:20", "patient_id": "PT-1064", "appointment_id": "A-20260730-0019", "visit_id": "V-20260730-0019", "name": "강동현", "gender": "M", "birth_date": "1998-01-26"},
    {"time": "12:40", "patient_id": "PT-1065", "appointment_id": "A-20260730-0020", "visit_id": "V-20260730-0020", "name": "오하늘", "gender": "F", "birth_date": "2003-07-07"},
    {"time": "13:00", "patient_id": "PT-1057", "appointment_id": "A-20260730-0010", "visit_id": "V-20260730-0010", "name": "이경규", "gender": "M", "birth_date": "1987-06-24"},
    {"time": "13:20", "patient_id": "PT-1066", "appointment_id": "A-20260730-0021", "visit_id": "V-20260730-0021", "name": "문정희", "gender": "F", "birth_date": "1948-04-19"},
    {"time": "13:40", "patient_id": "PT-1067", "appointment_id": "A-20260730-0022", "visit_id": "V-20260730-0022", "name": "배성민", "gender": "M", "birth_date": "1960-10-02"},
    {"time": "14:00", "patient_id": "PT-1068", "appointment_id": "A-20260730-0023", "visit_id": "V-20260730-0023", "name": "신예은", "gender": "F", "birth_date": "1974-06-28"},
    {"time": "14:20", "patient_id": "PT-1069", "appointment_id": "A-20260730-0024", "visit_id": "V-20260730-0024", "name": "조민재", "gender": "M", "birth_date": "2000-09-12"},
]

ANALYSIS_TEXT = [
    ("양측 폐야의 광범위한 침윤과 흉수 의심 소견이 관찰됩니다.", "급성 호흡기 상태 악화 가능성이 있어 우선 평가가 필요한 영상입니다.", "산소포화도 확인과 흉부 영상의 전문의 판독을 즉시 진행하십시오."),
    ("양측 하폐야에 폐렴성 불투명도와 소량의 흉수가 의심됩니다.", "감염성 폐질환 가능성이 높으며 임상 증상과의 연관 평가가 필요합니다.", "활력징후와 염증 수치를 확인하고 호흡기내과 진료를 권고합니다."),
    ("척추 정렬 이상과 골절 의심 부위가 함께 관찰됩니다.", "큰 Cobb 각과 골절 가능성을 고려할 때 신경학적 위험 평가가 필요합니다.", "척추 전문의의 긴급 검토와 추가 단면 영상 확인을 권고합니다."),
    ("흉부 영상에서 뚜렷한 급성 폐병변은 관찰되지 않습니다.", "현재 영상만으로 중증 흉부 질환 가능성은 낮아 보입니다.", "증상이 지속되면 임상 추적과 비교 영상을 검토하십시오."),
    ("척추체 형태 변화와 다분절 정렬 이상이 관찰됩니다.", "압박성 변화 또는 퇴행성 병변의 동반 가능성이 있습니다.", "통증 및 신경학적 증상과 연계해 척추 전문의 판독을 권고합니다."),
    ("경미한 선상 음영 외에 뚜렷한 국소성 병변은 보이지 않습니다.", "비특이적 변화로 판단되며 즉각적인 위험 소견은 낮습니다.", "임상 경과를 관찰하고 필요 시 추적 촬영하십시오."),
    ("원위 전완부에 골절을 시사하는 피질 불연속이 관찰됩니다.", "불안정성 골절 가능성이 있어 조기 고정 및 정형외과 평가가 필요합니다.", "환부를 고정하고 추가 방사선 촬영과 전문의 진료를 진행하십시오."),
    ("척추 만곡과 회전 변형이 뚜렷하게 측정됩니다.", "중증 척추측만 범주로 기능 저하 가능성을 함께 평가해야 합니다.", "전척추 기립 영상 검토와 척추 전문의 상담을 권고합니다."),
]


def _scheduled_at(slot_time: str) -> datetime:
    local = datetime.fromisoformat(f"{SHOWCASE_DATE}T{slot_time}:00").replace(
        tzinfo=ZoneInfo(APP_TIMEZONE)
    )
    return local.astimezone(timezone.utc)


def _validate_plan() -> None:
    assert len(SLOTS) == 20
    assert len({slot["patient_id"] for slot in SLOTS}) == 20
    assert len({slot["time"] for slot in SLOTS}) == 20
    start = datetime.fromisoformat(f"{SHOWCASE_DATE}T08:00:00")
    assert [slot["time"] for slot in SLOTS] == [
        (start + timedelta(minutes=20 * index)).strftime("%H:%M")
        for index in range(20)
    ]


async def _upsert_patient(db, slot: dict, doctor: dict, now: datetime) -> None:
    await db["patients"].update_one(
        {"patient_id": slot["patient_id"]},
        {
            "$set": {
                "hospital_id": HOSPITAL_ID,
                "name": slot["name"],
                "search_name": "".join(slot["name"].split()).lower(),
                "gender": slot["gender"],
                "birth_date": slot["birth_date"],
                "assigned_doctor_id": doctor["_id"],
                "care_status": "관찰중",
                "is_archived": False,
                "updated_at": now,
            },
            "$setOnInsert": {
                "patient_id": slot["patient_id"],
                "created_by_doctor_id": doctor["_id"],
                "created_at": now,
            },
        },
        upsert=True,
    )


async def _upsert_appointment_and_visit(
    db, slot: dict, doctor: dict, ordinal: int, now: datetime
) -> None:
    scheduled_at = _scheduled_at(slot["time"])
    appointment_status = "completed" if ordinal == 1 else "scheduled"
    await db["appointments"].update_one(
        {"appointment_id": slot["appointment_id"]},
        {
            "$set": {
                "hospital_id": HOSPITAL_ID,
                "patient_id": slot["patient_id"],
                "doctor_id": doctor["_id"],
                "scheduled_at": scheduled_at,
                "scheduled_date": SHOWCASE_DATE,
                "scheduled_time": slot["time"],
                "status": appointment_status,
                "slot_claimed": appointment_status == "scheduled",
                "updated_at": now,
            },
            "$setOnInsert": {
                "appointment_id": slot["appointment_id"],
                "exam_type": "",
                "created_at": now,
            },
        },
        upsert=True,
    )
    await db["visits"].update_one(
        {"visit_id": slot["visit_id"]},
        {
            "$set": {
                "hospital_id": HOSPITAL_ID,
                "patient_id": slot["patient_id"],
                "appointment_id": slot["appointment_id"],
                "doctor_id": doctor["_id"],
                "visit_date": SHOWCASE_DATE,
                "status": "completed" if ordinal == 1 else "open",
                "completed_at": now if ordinal == 1 else None,
                "updated_at": now,
            },
            "$setOnInsert": {
                "visit_id": slot["visit_id"],
                "exam_type": "",
                "modalities": [],
                "created_at": now,
            },
        },
        upsert=True,
    )


async def _seed_done_analysis(
    db, slot: dict, doctor: dict, ordinal: int, now: datetime
) -> None:
    risk_tier, confidence = RISK_PLAN[ordinal - 1]
    finding, interpretation, recommendation = ANALYSIS_TEXT[ordinal - 1]
    await db["analyses"].update_one(
        {"analysis_id": slot["analysis_id"]},
        {
            "$set": {
                "hospital_id": HOSPITAL_ID,
                "patient_id": slot["patient_id"],
                "visit_id": slot["visit_id"],
                "appointment_id": slot["appointment_id"],
                "requested_by_doctor_id": doctor["_id"],
                "visit_doctor_id": doctor["_id"],
                "scheduled_at": _scheduled_at(slot["time"]),
                "status": "done",
                "worker_id": "showcase-seed",
                "lease_expires_at": None,
                "completed_at": now,
                "risk_tier": risk_tier,
                "risk_status": "assessed",
                "risk_assessed_at": now,
                "confidence": confidence,
                "finding": finding,
                "interpretation": interpretation,
                "recommendation": recommendation,
                "error": None,
                "superseded_at": None,
                "updated_at": now,
            },
            "$setOnInsert": {
                "analysis_id": slot["analysis_id"],
                "queued_at": now,
                "started_at": now,
                "attempt": 1,
                "mode": "auto",
                "query": "2026-07-30 시연 분석",
                "department": doctor["department"],
                "project": "showcase-demo",
                "model_name": "showcase-demo",
                "predictions": None,
                "input_file_ids": [],
                "result_file_ids": {"base": [], "heat": [], "box": []},
                "created_at": now,
            },
        },
        upsert=True,
    )


async def _seed_frozen_analysis(
    db, slot: dict, doctor: dict, now: datetime
) -> None:
    await db["analyses"].update_one(
        {"analysis_id": slot["analysis_id"]},
        {
            "$set": {
                "hospital_id": HOSPITAL_ID,
                "patient_id": slot["patient_id"],
                "visit_id": slot["visit_id"],
                "appointment_id": slot["appointment_id"],
                "requested_by_doctor_id": doctor["_id"],
                "visit_doctor_id": doctor["_id"],
                "scheduled_at": _scheduled_at(slot["time"]),
                "status": "analyzing",
                "worker_id": "showcase-frozen",
                "started_at": now,
                "completed_at": None,
                "lease_expires_at": FROZEN_LEASE,
                "risk_tier": None,
                "risk_status": "pending",
                "confidence": None,
                "finding": None,
                "interpretation": None,
                "recommendation": None,
                "error": None,
                "superseded_at": None,
                "updated_at": now,
            },
            "$setOnInsert": {
                "analysis_id": slot["analysis_id"],
                "queued_at": now,
                "attempt": 1,
                "mode": "auto",
                "query": "2026-07-30 시연 분석 중",
                "department": doctor["department"],
                "project": "showcase-demo",
                "model_name": "showcase-demo",
                "predictions": None,
                "input_file_ids": [],
                "result_file_ids": {"base": [], "heat": [], "box": []},
                "created_at": now,
            },
        },
        upsert=True,
    )


async def seed() -> None:
    _validate_plan()
    await connect_database()
    try:
        await ensure_indexes()
        db = get_database()
        doctor = await db["doctors"].find_one(
            {"employee_id": DOCTOR_EMPLOYEE_ID, "hospital_id": HOSPITAL_ID}
        )
        if not doctor:
            raise RuntimeError("showcase doctor chestKim (employee_id=1234) not found")

        selected_ids = {slot["patient_id"] for slot in SLOTS}
        unexpected = await db["appointments"].find(
            {
                "hospital_id": HOSPITAL_ID,
                "scheduled_date": SHOWCASE_DATE,
                "status": {"$ne": "cancelled"},
                "patient_id": {"$nin": list(selected_ids)},
            },
            {"appointment_id": 1, "patient_id": 1},
        ).to_list(None)
        if unexpected:
            conflicts = ", ".join(
                f"{item['appointment_id']}({item['patient_id']})" for item in unexpected
            )
            raise RuntimeError(f"unexpected active appointments on showcase date: {conflicts}")

        now = datetime.now(timezone.utc)
        # Release the showcase slots before moving them so two selected
        # appointments cannot temporarily collide with the unique slot index.
        await db["appointments"].update_many(
            {"appointment_id": {"$in": [slot["appointment_id"] for slot in SLOTS]}},
            {"$set": {"slot_claimed": False, "updated_at": now}},
        )
        for ordinal, slot in enumerate(SLOTS, 1):
            await _upsert_patient(db, slot, doctor, now)
            await _upsert_appointment_and_visit(db, slot, doctor, ordinal, now)
            if ordinal <= 8:
                await _seed_done_analysis(db, slot, doctor, ordinal, now)
            elif ordinal <= 12:
                await _seed_frozen_analysis(db, slot, doctor, now)
            else:
                # No active analysis document means waiting_for_files in list APIs.
                await db["analyses"].update_many(
                    {
                        "visit_id": slot["visit_id"],
                        "status": {"$ne": "superseded"},
                    },
                    {
                        "$set": {
                            "status": "superseded",
                            "superseded_at": now,
                            "lease_expires_at": None,
                            "updated_at": now,
                        }
                    },
                )

        await db["counters"].update_one(
            {"_id": "patient_id"},
            {"$max": {"seq": 1069}, "$set": {"updated_at": now}},
            upsert=True,
        )
        for prefix in ("appointment", "visit", "analysis"):
            await db["counters"].update_one(
                {"_id": f"{prefix}:20260730"},
                {"$max": {"seq": 24}, "$set": {"updated_at": now}},
                upsert=True,
            )
        print("seeded 2026-07-30 showcase: done=8 analyzing=4 waiting_for_files=8")
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(seed())
