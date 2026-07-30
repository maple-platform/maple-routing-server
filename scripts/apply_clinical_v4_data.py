import asyncio
import hashlib
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from config.database import close_database, connect_database, get_database
from config.settings import GRIDFS_BUCKET
from services.attachments import extract_metadata
from services.medical_file_service import MedicalFileService
from services.risk_policy_service import RiskPolicyService


CHESTXRAY_POLICY = {
    "rules": [
        {
            "tier": "Critical",
            "labels": ["Pneumothorax", "Edema"],
            "prediction_value": 1,
            "min_probability": None,
            "detection_count_gte": None,
        },
        {
            "tier": "High",
            "labels": [
                "Pneumonia",
                "Infiltration",
                "Consolidation",
                "Effusion",
            ],
            "prediction_value": 1,
            "min_probability": None,
            "detection_count_gte": None,
        },
    ],
    "default_tier": "Low",
}

YOLO_POLICY = {
    "rules": [
        {
            "tier": "High",
            "labels": [],
            "prediction_value": 1,
            "min_probability": None,
            "detection_count_gte": 1,
        }
    ],
    "default_tier": "Low",
}

DEMO_FILES = {
    "source": Path("data/input/general/20260718_142130/dicom/000_sample_02.dcm"),
    "base": Path(
        "data/input/general/20260718_142130/_converted/s2/000_sample_02.png"
    ),
    "heat": Path(
        "data/output/general/20260718_142130/"
        "s2_ChestXray14_Multilabel_Classification_result_1.png"
    ),
    "box": Path(
        "data/output/general/20260718_142130/"
        "s1_RSNA_Pneumonia_YOLO26x_result_1.png"
    ),
}


async def seed_risk_policies(db) -> int:
    document = await db["departments"].find_one({})
    if not document:
        raise RuntimeError("departments model registry is empty")
    changed = 0
    for department in document.get("departments") or []:
        for project in (department.get("projects") or {}).values():
            name = project.get("project_name")
            if name == "ChestXray14_Multilabel_Classification":
                project["risk_policy"] = CHESTXRAY_POLICY
                changed += 1
            elif name == "RSNA_Pneumonia_YOLO26x":
                project["risk_policy"] = YOLO_POLICY
                changed += 1
    if changed:
        await db["departments"].replace_one({"_id": document["_id"]}, document)
    return changed


async def migrate_notes(db) -> int:
    changed = 0
    cursor = db["notes"].find({
        "$or": [
            {"note_id": {"$exists": False}},
            {"text": {"$exists": False}},
            {"author_id": {"$exists": False}},
        ]
    })
    async for note in cursor:
        author_id = note.get("author_id") or note.get("doctor_id")
        doctor = (
            await db["doctors"].find_one({"_id": author_id})
            if author_id is not None
            else None
        )
        await db["notes"].update_one(
            {"_id": note["_id"]},
            {
                "$set": {
                    "note_id": note.get("note_id") or str(uuid.uuid4()),
                    "author_id": author_id,
                    "author_name": (
                        note.get("author_name")
                        or f"{(doctor or {}).get('name', '알 수 없는 작성자')} · 접수"
                    ),
                    "source": note.get("source") or "registration",
                    "text": note.get("text") or note.get("content") or "",
                },
                "$unset": {"doctor_id": "", "content": ""},
            },
        )
        changed += 1
    return changed


async def backfill_dicom_metadata(db) -> int:
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=GRIDFS_BUCKET)
    changed = 0
    cursor = db["medical_files"].find({
        "extension": {"$in": ["dcm", "dicom"]},
        "status": "active",
        "dicom_metadata": {"$exists": False},
    })
    async for item in cursor:
        stream = await bucket.open_download_stream(item["gridfs_id"])
        content = await stream.read()
        try:
            metadata = await extract_metadata(
                item["original_filename"],
                content,
                item.get("content_type") or "application/dicom",
            )
        except Exception:
            metadata = {}
        await db["medical_files"].update_one(
            {"_id": item["_id"]},
            {"$set": {"dicom_metadata": metadata}},
        )
        changed += 1
    return changed


async def _store_demo_source(db, bucket, analysis: dict) -> str:
    existing = await db["medical_files"].find_one({
        "analysis_id": analysis["analysis_id"],
        "kind": "input",
        "role": "source",
        "status": "active",
    })
    if existing:
        return existing["file_id"]
    path = DEMO_FILES["source"]
    content = path.read_bytes()
    file_id = f"F-{uuid.uuid4()}"
    gridfs_id = await bucket.upload_from_stream(
        path.name,
        content,
        metadata={"file_id": file_id, "status": "active"},
    )
    dicom_metadata = await extract_metadata(
        path.name,
        content,
        "application/dicom",
    )
    await db["medical_files"].insert_one({
        "file_id": file_id,
        "gridfs_id": gridfs_id,
        "hospital_id": analysis["hospital_id"],
        "patient_id": analysis["patient_id"],
        "visit_id": analysis["visit_id"],
        "analysis_id": analysis["analysis_id"],
        "kind": "input",
        "role": "source",
        "slice_index": None,
        "original_filename": path.name,
        "content_type": "application/dicom",
        "extension": "dcm",
        "dicom_metadata": dicom_metadata,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "status": "active",
        "created_by_doctor_id": analysis["requested_by_doctor_id"],
        "created_at": datetime.now(timezone.utc),
    })
    return file_id


async def backfill_demo_assets(db) -> int:
    missing_paths = [str(path) for path in DEMO_FILES.values() if not path.is_file()]
    if missing_paths:
        raise RuntimeError(f"demo assets missing: {', '.join(missing_paths)}")
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=GRIDFS_BUCKET)
    medical_files = MedicalFileService(db)
    changed = 0
    cursor = db["analyses"].find({
        "worker_id": "demo-seed",
        "status": "done",
    })
    async for analysis in cursor:
        existing = await db["medical_files"].find({
            "analysis_id": analysis["analysis_id"],
            "kind": "derived_image",
            "status": "active",
        }).to_list(None)
        existing_by_role = {item["role"]: item["file_id"] for item in existing}
        paths_and_roles = [
            (DEMO_FILES[role], role)
            for role in ("base", "heat", "box")
            if role not in existing_by_role
        ]
        created = (
            await medical_files.store_derived(
                paths_and_roles=paths_and_roles,
                analysis=analysis,
            )
            if paths_and_roles
            else {"base": [], "heat": [], "box": []}
        )
        result_ids = {
            role: (
                [existing_by_role[role]] if role in existing_by_role else created[role]
            )
            for role in ("base", "heat", "box")
        }
        source_id = await _store_demo_source(db, bucket, analysis)
        await db["analyses"].update_one(
            {"_id": analysis["_id"]},
            {
                "$set": {
                    "input_file_ids": [source_id],
                    "result_file_ids": result_ids,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        changed += bool(paths_and_roles or not analysis.get("input_file_ids"))
    return changed


async def backfill_risk_assessments(db) -> int:
    service = RiskPolicyService(db)
    changed = 0
    cursor = db["analyses"].find({
        "status": "done",
        "risk_status": "unavailable",
        "normalized_result.steps": {"$exists": True},
    })
    async for analysis in cursor:
        normalized_steps = (analysis.get("normalized_result") or {}).get("steps") or []
        prediction_entries = analysis.get("predictions") or []
        if not isinstance(prediction_entries, list):
            continue
        predictions_by_step = {
            str(item.get("step_id")): item.get("predictions")
            for item in prediction_entries
            if isinstance(item, dict)
        }
        departments = [
            value.strip()
            for value in str(analysis.get("department") or "").split("·")
            if value.strip()
        ]
        if not departments:
            continue
        execution_steps = [
            {
                "step_id": str(step.get("step_id")),
                "department": (
                    departments[index]
                    if index < len(departments)
                    else departments[-1]
                ),
                "project": step.get("project"),
            }
            for index, step in enumerate(normalized_steps)
            if step.get("step_id") and step.get("project")
        ]
        step_results = [
            {
                "step_id": str(step.get("step_id")),
                "predictions": predictions_by_step.get(str(step.get("step_id"))),
                "model_output": step.get("model_output"),
            }
            for step in normalized_steps
        ]
        risk_tier, risk_status = await service.assess(
            execution_steps,
            step_results,
        )
        if risk_status != "assessed":
            continue
        outcome = await db["analyses"].update_one(
            {"_id": analysis["_id"], "risk_status": "unavailable"},
            {
                "$set": {
                    "risk_tier": risk_tier,
                    "risk_status": risk_status,
                    "risk_assessed_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        changed += outcome.modified_count
    return changed


async def main() -> None:
    await connect_database()
    try:
        db = get_database()
        result = {
            "risk_policies": await seed_risk_policies(db),
            "risk_assessments_backfilled": await backfill_risk_assessments(db),
            "notes_migrated": await migrate_notes(db),
            "dicom_metadata_backfilled": await backfill_dicom_metadata(db),
            "demo_analyses_with_assets": await backfill_demo_assets(db),
        }
        suspicious = []
        async for patient in db["patients"].find({}):
            created = patient.get("created_at")
            if (
                created is not None
                and str(patient.get("birth_date")) == created.date().isoformat()
            ):
                suspicious.append(patient["patient_id"])
        result["birth_date_review_required"] = suspicious
        print(result)
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
