import os

from fastapi.testclient import TestClient

from app.backend.main import app


def test_create_error_log() -> None:
    with TestClient(app) as client:
        payload = {
            "error_code": "TEST_ERROR",
            "severity": "warning",
            "component": "test_component",
            "error_type": "TestError",
            "message": "민감 정보 제거 테스트",
            "context_json": {
                "api_key": "secret_value",
                "nested": {"authorization": "token_value"},
            },
        }
        response = client.post("/errors", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["severity"] == "WARNING"
        assert data["error_code"] == "TEST_ERROR"
        assert data["context_json"]["api_key"] == "***REDACTED***"
        assert data["context_json"]["nested"]["authorization"] == "***REDACTED***"


def test_list_errors_and_summary() -> None:
    with TestClient(app) as client:
        payload = {
            "error_code": "LIST_ERROR",
            "severity": "info",
            "component": "list_component",
            "error_type": "ListError",
            "message": "목록 조회 테스트",
        }
        create_response = client.post("/errors", json=payload)
        assert create_response.status_code == 201

        list_response = client.get("/errors")
        assert list_response.status_code == 200
        assert isinstance(list_response.json(), list)
        assert len(list_response.json()) >= 1

        summary_response = client.get("/errors/summary")
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert summary["total"] >= 1
        assert summary["info"] >= 1


def test_get_error_by_id_and_update_status() -> None:
    with TestClient(app) as client:
        payload = {
            "error_code": "UPDATE_ERROR",
            "severity": "error",
            "component": "update_component",
            "error_type": "UpdateError",
            "message": "상태 변경 테스트",
        }
        create_response = client.post("/errors", json=payload)
        assert create_response.status_code == 201
        error_id = create_response.json()["id"]

        get_response = client.get(f"/errors/{error_id}")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == error_id

        patch_response = client.patch(f"/errors/{error_id}/status", json={"status": "resolved"})
        assert patch_response.status_code == 200
        assert patch_response.json()["status"] == "resolved"


def test_invalid_severity_rejected() -> None:
    with TestClient(app) as client:
        payload = {
            "error_code": "BAD_SEVERITY",
            "severity": "bad",
            "component": "test",
            "error_type": "BadSeverity",
            "message": "잘못된 severity 테스트",
        }
        response = client.post("/errors", json=payload)
        assert response.status_code == 400


def test_missing_error_returns_404() -> None:
    with TestClient(app) as client:
        response = client.get("/errors/999999")
        assert response.status_code == 404


def test_demo_error_creation() -> None:
    with TestClient(app) as client:
        response = client.post("/errors/demo")
        assert response.status_code == 201
        assert response.json()["error_code"] == "DEMO_ERROR"
