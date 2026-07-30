import unittest
from datetime import datetime

from bson import ObjectId

from config.settings import HOSPITAL_NAME
from models.auth_schemas import LoginRequest, SignupRequest
from services.auth_service import AuthService, AuthServiceError


class FakeDoctorsRepository:
    def __init__(self):
        self.by_id = {}
        self.by_employee_id = {}

    async def create(self, document):
        created = {**document, "_id": ObjectId()}
        self.by_id[created["_id"]] = created
        self.by_employee_id[created["employee_id"]] = created
        return created

    async def get_by_employee_id(self, employee_id):
        return self.by_employee_id.get(employee_id)

    async def get_by_id(self, doctor_id):
        return self.by_id.get(doctor_id)

    async def update_last_login(self, doctor_id, at):
        self.by_id[doctor_id]["last_login_at"] = at

    async def update_password_hash(self, doctor_id, password_hash, at):
        self.by_id[doctor_id]["password_hash"] = password_hash
        self.by_id[doctor_id]["updated_at"] = at


class FakeSessionsRepository:
    def __init__(self):
        self.sessions = {}

    async def create(self, document):
        self.sessions[document["session_id"]] = dict(document)
        return document

    async def get_active(self, session_id, now):
        session = self.sessions.get(session_id)
        if not session or session["revoked_at"] is not None or session["expires_at"] <= now:
            return None
        return session

    async def rotate(self, session_id, current_hash, next_hash, at):
        session = self.sessions.get(session_id)
        if not session or session["refresh_token_hash"] != current_hash:
            return False
        session["refresh_token_hash"] = next_hash
        session["last_used_at"] = at
        return True

    async def revoke(self, session_id, refresh_token_hash, doctor_id, at):
        session = self.sessions.get(session_id)
        if (
            not session
            or session["doctor_id"] != doctor_id
            or session["refresh_token_hash"] != refresh_token_hash
            or session["revoked_at"] is not None
        ):
            return False
        session["revoked_at"] = at
        return True

    async def revoke_by_session_id(self, session_id, at):
        if session_id in self.sessions:
            self.sessions[session_id]["revoked_at"] = at


class AuthServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.doctors = FakeDoctorsRepository()
        self.sessions = FakeSessionsRepository()
        self.service = AuthService(self.doctors, self.sessions)

    async def signup(self):
        return await self.service.signup(
            SignupRequest(
                employee_id="doctor01",
                password="strong-password",
                name="테스트 의사",
                hospital=HOSPITAL_NAME,
                department="영상의학과",
                title="전문의",
            ),
            ip="127.0.0.1",
            user_agent="test",
        )

    async def test_signup_issues_tokens_and_forces_doctor_role(self):
        result = await self.signup()

        self.assertTrue(result.access_token)
        self.assertTrue(result.refresh_token)
        self.assertEqual(result.doctor.roles, ["doctor"])
        self.assertEqual(result.doctor.employee_id, "doctor01")

    async def test_signup_rejects_unknown_hospital(self):
        with self.assertRaises(AuthServiceError) as caught:
            await self.service.signup(
                SignupRequest(
                    employee_id="doctor02",
                    password="strong-password",
                    name="테스트 의사",
                    hospital="다른 병원",
                    department="영상의학과",
                    title="전문의",
                ),
                ip=None,
                user_agent=None,
            )
        self.assertEqual(caught.exception.status_code, 422)

    async def test_refresh_rotates_token_and_old_token_is_rejected(self):
        signed_up = await self.signup()
        refreshed = await self.service.refresh(signed_up.refresh_token)

        self.assertNotEqual(refreshed.refresh_token, signed_up.refresh_token)
        with self.assertRaises(AuthServiceError):
            await self.service.refresh(signed_up.refresh_token)

    async def test_logout_revokes_access_session(self):
        signed_up = await self.signup()
        doctor = await self.service.authenticate_access_token(signed_up.access_token)
        await self.service.logout(signed_up.refresh_token, doctor)

        with self.assertRaises(AuthServiceError):
            await self.service.authenticate_access_token(signed_up.access_token)

    async def test_login_rejects_wrong_password(self):
        await self.signup()
        with self.assertRaises(AuthServiceError) as caught:
            await self.service.login(
                LoginRequest(employee_id="doctor01", password="wrong-password"),
                ip=None,
                user_agent=None,
            )
        self.assertEqual(caught.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
