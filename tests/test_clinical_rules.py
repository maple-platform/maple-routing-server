import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from fastapi import UploadFile

from config.settings import MAX_UPLOAD_BYTES
from services.clinical_service import ClinicalService, ClinicalServiceError
from services.medical_file_service import (
    MedicalFileService,
    MedicalFileError,
    classify_filename,
    derive_exam_type,
)
from services.attachments import extract_metadata


class ClinicalRulesTest(unittest.TestCase):
    def setUp(self):
        self.service = object.__new__(ClinicalService)
        self.service.timezone = ZoneInfo("Asia/Seoul")
        self.service.clock = lambda: datetime(
            2026, 7, 29, 5, 35, tzinfo=timezone.utc
        )

    def test_valid_slot_is_converted_to_utc(self):
        scheduled = self.service._scheduled_at(
            datetime(2026, 7, 30).date(),
            time(8, 10),
        )
        self.assertEqual(
            scheduled,
            datetime(2026, 7, 29, 23, 10, tzinfo=timezone.utc),
        )

    def test_accepts_past_slot(self):
        scheduled = self.service._scheduled_at(
            datetime(2026, 7, 28).date(),
            time(10, 0),
        )
        self.assertEqual(
            scheduled,
            datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc),
        )
        same_day_past = self.service._scheduled_at(
            datetime(2026, 7, 29).date(),
            time(8, 0),
        )
        self.assertEqual(
            same_day_past,
            datetime(2026, 7, 28, 23, 0, tzinfo=timezone.utc),
        )

    def test_rejects_non_ten_minute_or_out_of_hours_slot(self):
        with self.assertRaises(ClinicalServiceError):
            self.service._scheduled_at(datetime(2026, 7, 30).date(), time(10, 5))
        with self.assertRaises(ClinicalServiceError):
            self.service._scheduled_at(datetime(2026, 7, 30).date(), time(17, 10))

    def test_exam_type_has_fixed_order(self):
        self.assertEqual(
            derive_exam_type(["이미지", "CSV", "DICOM", "이미지"]),
            "DICOM · CSV · 이미지",
        )
        self.assertEqual(classify_filename("study.nii.gz"), ("NIfTI", "nii.gz"))
        with self.assertRaises(MedicalFileError):
            classify_filename("malware.exe")


class DicomMetadataTest(unittest.IsolatedAsyncioTestCase):
    async def test_dicom_metadata_is_deidentified(self):
        path = (
            "data/input/general/20260718_142130/dicom/"
            "000_sample_02.dcm"
        )
        with open(path, "rb") as stream:
            metadata = await extract_metadata(
                "000_sample_02.dcm",
                stream.read(),
                "application/dicom",
            )
        self.assertIn("modality", metadata)
        self.assertNotIn("patient_name", metadata)
        self.assertNotIn("birth_date", metadata)
        self.assertNotIn("patient_id", metadata)


class _FakeGridFSBucket:
    def __init__(self):
        self.received = b""
        self.deleted = []

    async def upload_from_stream(self, _filename, source, metadata=None):
        if isinstance(source, bytes):
            raise AssertionError("업로드 본문을 bytes로 통째로 전달하면 안 됩니다.")
        chunks = []
        while chunk := source.read(64 * 1024):
            chunks.append(chunk)
        self.received = b"".join(chunks)
        return "gridfs-id"

    async def delete(self, gridfs_id):
        self.deleted.append(gridfs_id)


class _FakeMedicalFiles:
    async def insert_one(self, document):
        return SimpleNamespace(inserted_id="medical-file-id")

    async def update_many(self, *_args, **_kwargs):
        return None


class MedicalFileUploadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = object.__new__(MedicalFileService)
        self.service.bucket = _FakeGridFSBucket()
        self.service.collection = _FakeMedicalFiles()

    async def test_upload_is_staged_as_stream(self):
        content = b"nifti-data" * 100_000
        upload = UploadFile(file=BytesIO(content), filename="study.nii.gz")

        staged, exam_type = await self.service.stage([upload], "doctor-id")

        self.assertEqual(exam_type, "NIfTI")
        self.assertEqual(self.service.bucket.received, content)
        self.assertEqual(staged[0]["size_bytes"], len(content))
        self.assertEqual(
            staged[0]["sha256"],
            __import__("hashlib").sha256(content).hexdigest(),
        )

    async def test_upload_over_limit_returns_413(self):
        upload = UploadFile(file=BytesIO(b"12345"), filename="study.nii.gz")
        with patch("services.medical_file_service.MAX_UPLOAD_BYTES", 4):
            with self.assertRaises(MedicalFileError) as raised:
                await self.service.stage([upload], "doctor-id")

        self.assertEqual(raised.exception.status_code, 413)

    def test_default_limit_covers_current_largest_model_sample(self):
        # 현재 모델 인벤토리 최대 샘플은 약 503 MiB이다.
        self.assertEqual(MAX_UPLOAD_BYTES, 512 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
