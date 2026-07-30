import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from config.settings import HOSPITAL_NAME
from dependencies import get_auth_service
from routes.api import router
from services.auth_service import AuthService
from tests.test_auth_service import FakeDoctorsRepository, FakeSessionsRepository


class AuthApiTest(unittest.TestCase):
    def setUp(self):
        self.service = AuthService(FakeDoctorsRepository(), FakeSessionsRepository())
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_auth_service] = lambda: self.service
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def signup(self):
        return self.client.post(
            "/auth/signup",
            json={
                "employee_id": "doctor01",
                "password": "strong-password",
                "name": "테스트 의사",
                "hospital": HOSPITAL_NAME,
                "department": "영상의학과",
                "title": "전문의",
            },
        )

    def test_signup_me_and_logout(self):
        signup = self.signup()
        self.assertEqual(signup.status_code, 201)
        tokens = signup.json()

        me = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["employee_id"], "doctor01")

        logout = self.client.post(
            "/auth/logout",
            json={"refresh_token": tokens["refresh_token"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        self.assertEqual(logout.status_code, 204)

        rejected = self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        self.assertEqual(rejected.status_code, 401)

    def test_existing_routes_require_auth_and_admin_role(self):
        unauthenticated = self.client.get("/projects/")
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(
            self.client.get("/patients/PT-1001/notes").status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/visits/V-test/chat").status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/files/F-test/metadata").status_code,
            401,
        )

        signup = self.signup().json()
        forbidden = self.client.get(
            "/admin/departments",
            headers={"Authorization": f"Bearer {signup['access_token']}"},
        )
        self.assertEqual(forbidden.status_code, 403)


if __name__ == "__main__":
    unittest.main()
