from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import httpx

from app import dashboard
from app.services import credential_service
from app.services.credential_service import CredentialStoreError


class FakeKeyring:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, name: str, value: str):
        if self.fail:
            raise RuntimeError("store unavailable")
        self.values[(service, name)] = value

    def get_password(self, service: str, name: str):
        if self.fail:
            raise RuntimeError("store unavailable")
        return self.values.get((service, name))

    def delete_password(self, service: str, name: str):
        if self.fail:
            raise RuntimeError("store unavailable")
        self.values.pop((service, name), None)


def test_credential_service_uses_keyring_and_masks(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(credential_service, "_keyring", lambda: fake)

    credential_service.set_secret("OPENAI_API_KEY", "openai-test-secret-value")

    assert credential_service.get_secret("OPENAI_API_KEY") == "openai-test-secret-value"
    assert credential_service.has_secret("OPENAI_API_KEY") is True
    assert credential_service.secret_source("OPENAI_API_KEY") == "keyring"
    assert credential_service.mask_secret("openai-test-secret-value") == "op****ue"
    fake_openai_key = "s" + "k-test-secret-value"
    assert credential_service.mask_sensitive_text(f"Authorization: Bearer {fake_openai_key}") == "Authorization: Bearer ***REDACTED***"

    credential_service.delete_secret("OPENAI_API_KEY")
    assert credential_service.has_secret("OPENAI_API_KEY") is False


def test_credential_service_does_not_fallback_to_plaintext_on_save_failure(monkeypatch):
    monkeypatch.setattr(credential_service, "_keyring", lambda: FakeKeyring(fail=True))

    try:
        credential_service.set_secret("KRX_API_KEY", "secret")
    except CredentialStoreError:
        pass
    else:
        raise AssertionError("CredentialStoreError was not raised")


def test_developer_settings_status_save_delete_without_secret_leak(monkeypatch, client):
    fake = FakeKeyring()
    monkeypatch.setattr(credential_service, "_keyring", lambda: fake)

    response = client.put("/developer/settings/openai", json={"api_key": "openai-test-secret-value", "model": "gpt-test"})
    assert response.status_code == 200
    body = response.json()

    assert body["api_key_configured"] is True
    assert "openai-test-secret-value" not in response.text

    status_response = client.get("/developer/settings/status")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["openai"]["source"] == "keyring"
    assert status_body["openai"]["model"] == "gpt-test"
    assert "openai-test-secret-value" not in status_response.text

    delete_response = client.delete("/developer/settings/openai")
    assert delete_response.status_code == 200


def test_developer_settings_rejects_nonlocal_requests(monkeypatch, client):
    response = client.get("/developer/settings/status", headers={"host": "example.com"})
    assert response.status_code == 200

    from app.backend.routes import developer_settings

    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"))
    try:
        developer_settings.require_local_request(request)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("non-local request was not rejected")


def test_developer_connection_tests_do_not_return_secret(monkeypatch, client):
    fake = FakeKeyring()
    fake.set_password("StockAILab", "OPENAI_API_KEY", "openai-test-secret-value")
    monkeypatch.setattr(credential_service, "_keyring", lambda: fake)

    async def fake_http_get_json(*args, **kwargs):
        return {"data": [{"id": "model"}]}

    monkeypatch.setattr("app.backend.routes.developer_settings._http_get_json", fake_http_get_json)

    response = client.post("/developer/settings/test/openai")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "openai-test-secret-value" not in response.text


def test_dashboard_main_menu_moves_dev_pages(monkeypatch):
    captured = {}

    def fake_selectbox(_label, options, **_kwargs):
        captured["options"] = options
        return "개발자 도구"

    monkeypatch.setattr(dashboard.st, "set_page_config", lambda **_kwargs: None)
    monkeypatch.setattr(dashboard.st, "selectbox", fake_selectbox)
    monkeypatch.setattr(dashboard, "render_developer_tools_page", lambda: captured.setdefault("rendered", True))

    dashboard.main()

    assert captured["options"] == ["홈", "뉴스", "시장 분석", "모의투자", "개발자 도구"]
    assert "AI 분석" not in captured["options"]
    assert "테마 분석" not in captured["options"]
    assert "Error Center" not in captured["options"]
    assert "종목 기준정보" not in captured["options"]
    assert captured["rendered"] is True


def test_developer_tools_contains_ai_and_theme_admin_tabs(monkeypatch):
    captured = {}

    class FakeTab:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_tabs(labels):
        captured["labels"] = labels
        return [FakeTab() for _ in labels]

    monkeypatch.setattr(dashboard.st, "title", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dashboard.st, "tabs", fake_tabs)
    monkeypatch.setattr(dashboard, "render_api_settings_tab", lambda: None)
    monkeypatch.setattr(dashboard, "render_data_sync_page", lambda: None)
    monkeypatch.setattr(dashboard, "render_security_master_page", lambda: None)
    monkeypatch.setattr(dashboard, "render_ai_analysis_admin_panel", lambda: None)
    monkeypatch.setattr(dashboard, "render_theme_analysis_admin_panel", lambda: None)
    monkeypatch.setattr(dashboard, "render_error_center_page", lambda: None)

    dashboard.render_developer_tools_page()

    assert captured["labels"] == ["API 설정", "데이터 동기화", "종목 기준정보", "AI 분석 관리", "테마 분석 관리", "Error Center"]

def test_clear_widget_keys_before_render_clears_naver_fields(monkeypatch):
    monkeypatch.setitem(dashboard.st.session_state, "clear_developer_naver_form", True)
    monkeypatch.setitem(dashboard.st.session_state, "developer_naver_client_id", "client-id")
    monkeypatch.setitem(dashboard.st.session_state, "developer_naver_client_secret", "client-secret")

    dashboard.clear_widget_keys_before_render("clear_developer_naver_form", dashboard.DEVELOPER_NAVER_WIDGET_KEYS)

    assert "clear_developer_naver_form" not in dashboard.st.session_state
    assert "developer_naver_client_id" not in dashboard.st.session_state
    assert "developer_naver_client_secret" not in dashboard.st.session_state


def test_clear_widget_keys_before_render_clears_openai_and_krx_fields_independently(monkeypatch):
    monkeypatch.setitem(dashboard.st.session_state, "clear_developer_openai_form", True)
    monkeypatch.setitem(dashboard.st.session_state, "developer_openai_api_key", "openai-secret")
    monkeypatch.setitem(dashboard.st.session_state, "developer_krx_api_key", "krx-secret")
    monkeypatch.setitem(dashboard.st.session_state, "developer_krx_base_url", "https://example.test")

    dashboard.clear_widget_keys_before_render("clear_developer_openai_form", dashboard.DEVELOPER_OPENAI_WIDGET_KEYS)

    assert "developer_openai_api_key" not in dashboard.st.session_state
    assert dashboard.st.session_state["developer_krx_api_key"] == "krx-secret"
    assert dashboard.st.session_state["developer_krx_base_url"] == "https://example.test"

    monkeypatch.setitem(dashboard.st.session_state, "clear_developer_krx_form", True)
    dashboard.clear_widget_keys_before_render("clear_developer_krx_form", dashboard.DEVELOPER_KRX_WIDGET_KEYS)

    assert "developer_krx_api_key" not in dashboard.st.session_state
    assert "developer_krx_base_url" not in dashboard.st.session_state


def test_save_failure_without_clear_flag_keeps_input_values(monkeypatch):
    monkeypatch.setitem(dashboard.st.session_state, "developer_sec_user_agent", "contact value")

    dashboard.clear_widget_keys_before_render("clear_developer_sec_form", dashboard.DEVELOPER_SEC_WIDGET_KEYS)

    assert dashboard.st.session_state["developer_sec_user_agent"] == "contact value"


def test_dashboard_does_not_directly_clear_instantiated_widget_keys():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")

    forbidden = [
        'st.session_state["developer_naver_client_id"] = ""',
        'st.session_state["developer_naver_client_secret"] = ""',
        'st.session_state["developer_openai_api_key"] = ""',
        'st.session_state["developer_sec_user_agent"] = ""',
        'st.session_state["developer_krx_api_key"] = ""',
    ]
    for pattern in forbidden:
        assert pattern not in source


def test_delete_buttons_use_distinct_clear_flags():
    assert "clear_developer_naver_form" != "clear_developer_openai_form"
    assert "clear_developer_openai_form" != "clear_developer_sec_form"
    assert "clear_developer_sec_form" != "clear_developer_krx_form"


def _krx_response(status_code: int, payload: dict | None = None, content_type: str = "application/json") -> httpx.Response:
    request = httpx.Request("GET", "https://example.test/krx")
    if payload is None:
        return httpx.Response(status_code, content=b"<html>error</html>", headers={"content-type": content_type}, request=request)
    return httpx.Response(status_code, json=payload, headers={"content-type": content_type}, request=request)


def _save_krx_settings(client):
    response = client.put(
        "/developer/settings/krx",
        json={
            "api_key": "krx-test-secret",
            "base_url": "https://example.test/openapi",
            "kospi_api_id": "stk_isu_base_info",
            "kosdaq_api_id": "ksq_isu_base_info",
            "konex_api_id": "knx_isu_base_info",
            "etf_api_id": "etf_bydd_trd",
        },
    )
    assert response.status_code == 200


def test_krx_test_uses_auth_key_header_not_query(monkeypatch, client):
    _save_krx_settings(client)
    captured = {}

    async def fake_get_response(url, headers=None, params=None, timeout=10.0):
        captured["url"] = url
        captured["headers"] = headers or {}
        captured["params"] = params or {}
        return _krx_response(200, {"OutBlock_1": [{"ISU_SRT_CD": "005930"}]})

    monkeypatch.setattr("app.backend.routes.developer_settings._http_get_response", fake_get_response)
    response = client.post("/developer/settings/test/krx/kospi")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["headers"]["AUTH_KEY"] == "krx-test-secret"
    assert "krx-test-secret" not in captured["params"].values()
    assert "apiKey" not in captured["params"]
    assert captured["url"].endswith("/sto/stk_isu_base_info")
    assert "krx-test-secret" not in response.text


def test_krx_service_paths_and_etf_basdd(monkeypatch, client):
    _save_krx_settings(client)
    calls = []

    async def fake_get_response(url, headers=None, params=None, timeout=10.0):
        calls.append((url, params or {}))
        return _krx_response(200, {"OutBlock_1": []})

    monkeypatch.setattr("app.backend.routes.developer_settings._http_get_response", fake_get_response)
    for service_name in ("kospi", "kosdaq", "konex", "etf"):
        client.post(f"/developer/settings/test/krx/{service_name}")

    assert calls[0][0].endswith("/sto/stk_isu_base_info")
    assert calls[1][0].endswith("/sto/ksq_isu_base_info")
    assert calls[2][0].endswith("/sto/knx_isu_base_info")
    assert calls[3][0].endswith("/etp/etf_bydd_trd")
    base_dates = [params.get("basDd") for _url, params in calls]
    assert all(base_dates)
    assert len(set(base_dates)) == 1


def test_krx_http_status_classification(monkeypatch, client):
    _save_krx_settings(client)

    async def fake_get_response(url, headers=None, params=None, timeout=10.0):
        return _krx_response(403, None, "text/html")

    monkeypatch.setattr("app.backend.routes.developer_settings._http_get_response", fake_get_response)
    response = client.post("/developer/settings/test/krx/kospi")
    body = response.json()

    assert body["status"] == "service_not_approved"
    assert body["details"]["upstream_status_code"] == 403
    assert body["details"]["response_content_type"] == "text/html"
    assert body["details"]["body_length"] > 0


def test_krx_401_404_and_200_business_error(monkeypatch, client):
    _save_krx_settings(client)
    responses = [
        _krx_response(401, {"respCode": "AUTH"}),
        _krx_response(404, {"respCode": "NO_URL"}),
        _krx_response(200, {"respCode": "9001", "respMsg": "not approved"}),
    ]

    async def fake_get_response(url, headers=None, params=None, timeout=10.0):
        return responses.pop(0)

    monkeypatch.setattr("app.backend.routes.developer_settings._http_get_response", fake_get_response)

    assert client.post("/developer/settings/test/krx/kospi").json()["status"] == "authentication_failed"
    assert client.post("/developer/settings/test/krx/kosdaq").json()["status"] == "invalid_endpoint"
    business = client.post("/developer/settings/test/krx/konex").json()
    assert business["status"] == "krx_business_error"
    assert business["details"]["krx_resp_code"] == "9001"
    assert business["details"]["krx_resp_message"] == "not approved"


def test_krx_200_outblock_success(monkeypatch, client):
    _save_krx_settings(client)

    async def fake_get_response(url, headers=None, params=None, timeout=10.0):
        return _krx_response(200, {"OutBlock_1": [{"a": 1}, {"a": 2}]})

    monkeypatch.setattr("app.backend.routes.developer_settings._http_get_response", fake_get_response)
    body = client.post("/developer/settings/test/krx/kospi").json()

    assert body["status"] == "connected"
    assert body["success"] is True
    assert body["details"]["row_count"] == 2
    assert body["details"]["converted_count"] == 0
    assert body["details"]["requested_base_date"]
    assert body["details"]["base_date_parameter_present"] is True


def test_krx_200_empty_outblock_is_empty_response(monkeypatch, client):
    _save_krx_settings(client)

    async def fake_get_response(url, headers=None, params=None, timeout=10.0):
        return _krx_response(200, {"OutBlock_1": []})

    monkeypatch.setattr("app.backend.routes.developer_settings._http_get_response", fake_get_response)
    body = client.post("/developer/settings/test/krx/kospi").json()

    assert body["success"] is False
    assert body["status"] == "empty_response"
    assert body["details"]["row_count"] == 0
    assert body["details"]["converted_count"] == 0
    assert body["details"]["requested_base_date"]
    assert body["details"]["base_date_parameter_present"] is True


def test_krx_test_requires_service_api_id(monkeypatch, client):
    client.put(
        "/developer/settings/krx",
        json={
            "api_key": "krx-test-secret",
            "base_url": "https://example.test/openapi",
            "kosdaq_api_id": "ksq_isu_base_info",
            "konex_api_id": "knx_isu_base_info",
            "etf_api_id": "etf_bydd_trd",
        },
    )
    called = False

    async def fake_get_response(url, headers=None, params=None, timeout=10.0):
        nonlocal called
        called = True
        return _krx_response(200, {"OutBlock_1": []})

    monkeypatch.setattr("app.backend.routes.developer_settings._http_get_response", fake_get_response)
    body = client.post("/developer/settings/test/krx/kospi").json()

    assert called is False
    assert body["status"] == "not_configured"
    assert body["details"]["configuration_missing"] == ["KRX_KOSPI_BASIC_API_ID"]


def test_krx_test_rejects_invalid_api_id_before_request(monkeypatch, client):
    client.put(
        "/developer/settings/krx",
        json={
            "api_key": "krx-test-secret",
            "base_url": "https://example.test/openapi",
            "kospi_api_id": "custom_kospi_id",
            "kosdaq_api_id": "ksq_isu_base_info",
            "konex_api_id": "knx_isu_base_info",
            "etf_api_id": "etf_bydd_trd",
        },
    )
    captured = {}

    async def fake_get_response(url, headers=None, params=None, timeout=10.0):
        captured["url"] = url
        return _krx_response(200, {"OutBlock_1": [{"ISU_SRT_CD": "005930", "ISU_NM": "삼성전자"}]})

    monkeypatch.setattr("app.backend.routes.developer_settings._http_get_response", fake_get_response)
    body = client.post("/developer/settings/test/krx/kospi").json()

    assert captured == {}
    assert body["status"] == "not_configured"

