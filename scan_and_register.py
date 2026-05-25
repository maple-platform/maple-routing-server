"""
AI_Models/ 폴더를 스캔하여 meta.json이 있는 모델을 MongoDB + ChromaDB에 자동 등록.

사용법:
  python scan_and_register.py           # 전체 스캔
  python scan_and_register.py --dry-run # 변경 없이 탐지 결과만 출력
"""

import sys
import io
import json
import asyncio
import argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import httpx
from pymongo import MongoClient

from config.settings import MONGO_URI, AGENT_URL, AI_MODELS_DIR, DB_NAME

MONGO_URL = MONGO_URI
AI_MODELS = Path(AI_MODELS_DIR)


# ── MongoDB 헬퍼 ─────────────────────────────────────────────────────────────

def get_department_doc(col, department_name: str):
    return col.find_one({"departments.department_name": department_name})


def find_project_id(dept: dict, project_name: str) -> str | None:
    for pid, pv in dept.get("projects", {}).items():
        if pv.get("project_name") == project_name:
            return pid
    return None


def find_model_id(dept: dict, project_id: str, model_name: str) -> str | None:
    models = dept.get("projects", {}).get(project_id, {}).get("ai_models", {})
    for mid, mv in models.items():
        if mv.get("model_name") == model_name:
            return mid
    return None


def next_id(mapping: dict) -> str:
    if not mapping:
        return "1"
    return str(max(int(k) for k in mapping.keys()) + 1)


def upsert_model_to_mongo(col, department_name, project_name, model_name, model_info: dict):
    """MongoDB에 모델 upsert. project에 모델 정보를 직접 저장."""
    doc = col.find_one({})
    if not doc:
        col.insert_one({"departments": []})
        doc = col.find_one({})
        print("  + maple_db 초기 문서 생성")

    depts = doc.get("departments", [])

    # department 찾기 (없으면 생성)
    dept = next((d for d in depts if d["department_name"] == department_name), None)
    if dept is None:
        dept = {"department_name": department_name, "projects": {}}
        depts.append(dept)
        print(f"  + department 생성: {department_name}")

    # project 찾기 (없으면 생성)
    pid = find_project_id(dept, project_name)
    if pid is None:
        pid = next_id(dept["projects"])
        dept["projects"][pid] = {}
        print(f"  + project 생성: {project_name} (id={pid})")
    else:
        print(f"  ~ project 업데이트: {project_name} (id={pid})")

    # project에 모델 정보 직접 저장
    dept["projects"][pid].update(model_info)

    col.replace_one({"_id": doc["_id"]}, doc)
    return True


async def register_to_chromadb(model_name, department, project, meta: dict, doc_id: str):
    """Agent /agent/models/register 호출"""
    payload = {
        "id":           doc_id,
        "model_name":   model_name,
        "department":   department,
        "project":      project,
        "description":  meta.get("description", ""),
        "task_type":    meta.get("task_type", ""),
        "disease":      meta.get("disease", ""),
        "required_data": meta.get("required_data", []),
        "result_type":  meta.get("result_type", ""),
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{AGENT_URL}/agent/models/register", json=payload)
        resp.raise_for_status()


def slugify(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ── 스캔 ─────────────────────────────────────────────────────────────────────

def scan_models() -> list[dict]:
    """AI_MODELS/{dept}/{project}/meta.json 구조를 스캔 (2단계)."""
    found = []
    for meta_path in sorted(AI_MODELS.rglob("meta.json")):
        rel_parts = meta_path.relative_to(AI_MODELS).parts
        if len(rel_parts) != 3:
            continue
        dept, project, _ = rel_parts
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        found.append({"dept": dept, "project": project,
                      "meta": meta, "meta_path": meta_path})
    return found


def build_model_info(dept: str, project: str, meta: dict) -> dict:
    """DB에 저장할 model_info dict 구성. project_name = model_name."""
    model_dir      = AI_MODELS / dept / project
    checkpoint_dir = model_dir / "checkpoint"

    model_path = {}
    if checkpoint_dir.exists():
        for f in checkpoint_dir.iterdir():
            if f.is_file() and f.suffix not in (".py", ".txt", ".md"):
                logical_path = Path("AI_Models") / f.relative_to(AI_MODELS)
                model_path[f.name] = str(logical_path).replace("\\", "/")

    inference_script  = str(model_dir / "inference.py")  if (model_dir / "inference.py").exists()  else None
    requirements_path = str(model_dir / "requirements.txt") if (model_dir / "requirements.txt").exists() else None

    docker_meta  = meta.get("docker", {})
    docker_image = f"{slugify(dept)}-{slugify(project)}:latest"

    info = {
        "model_id":          meta.get("model_id", f"{dept}/{project}"),
        "project_name":      project,
        "model_name":        meta.get("model_name", project),
        "model_description": meta.get("description", ""),
        "model_path":        model_path,
        "required_data":     meta.get("required_data", []),
        "task_type":         meta.get("task_type", ""),
        "result_type":       meta.get("result_type", "text"),
        "inference_script":  inference_script,
        "inference_server":  meta.get("inference_server", "local"),
        "endpoint":          meta.get("endpoint", "/infer"),
        "parallel_safe":     meta.get("parallel_safe", True),
        "docker": {
            "image":       docker_meta.get("image", docker_image),
            "service_url": docker_meta.get("service_url", ""),
        },
    }
    if requirements_path:
        info["requirements_path"] = requirements_path
    return info


# ── 메인 ─────────────────────────────────────────────────────────────────────

async def main(dry_run: bool):
    models = scan_models()
    if not models:
        print("meta.json을 찾지 못했습니다.")
        return

    print(f"발견된 모델: {len(models)}개\n")

    if dry_run:
        for m in models:
            print(f"  {m['dept']} / {m['project']}")
            print(f"    model_name    : {m['meta'].get('model_name', m['project'])}")
            print(f"    required_data : {m['meta'].get('required_data')}")
            print(f"    result_type   : {m['meta'].get('result_type')}")
            print(f"    service_url   : {m['meta'].get('docker', {}).get('service_url')}")
        return

    client = MongoClient(MONGO_URL)
    col = client[DB_NAME]["departments"]

    for m in models:
        dept, project, meta = m["dept"], m["project"], m["meta"]
        model_name = meta.get("model_name", project)
        print(f"▶ {dept} / {project} (model: {model_name})")

        # 1. MongoDB
        model_info = build_model_info(dept, project, meta)
        ok = upsert_model_to_mongo(col, dept, project, model_name, model_info)
        if ok:
            print(f"  ✅ MongoDB 등록 완료")

        # 2. ChromaDB (Agent 서버 필요)
        doc_id = f"{slugify(dept)}-{slugify(project)}"
        try:
            await register_to_chromadb(model_name, dept, project, meta, doc_id)
            print(f"  ✅ ChromaDB 등록 완료")
        except Exception as e:
            print(f"  ⚠️  ChromaDB 등록 실패: {e}")

        print()

    print("완료.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="등록 없이 탐지 결과만 출력")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
