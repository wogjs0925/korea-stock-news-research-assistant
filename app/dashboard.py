import httpx
from json import JSONDecodeError
import logging
import streamlit as st

from app.utils.display_labels import (
    ASSET_TYPE_LABELS,
    EXCLUSION_FLAG_LABELS,
    IMPACT_DIRECTION_LABELS,
    MATCH_STATUS_LABELS,
    RISK_FLAG_LABELS,
    STAGE_LABELS,
    STATUS_LABELS,
    TIME_HORIZON_LABELS,
    label_list,
    label_value,
)
BASE_URL = "http://127.0.0.1:8000"
TIMEOUT_SECONDS = 5.0

def _status_label(value: str | None) -> str:
    return label_value(value, STATUS_LABELS)


def _localized_candidate_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "종목명": item.get("name"),
            "ticker/종목코드": item.get("ticker"),
            "국가": item.get("country_code"),
            "거래소": item.get("exchange_code"),
            "자산 유형": label_value(item.get("asset_type"), ASSET_TYPE_LABELS),
            "후보 점수": item.get("final_candidate_score"),
            "최종 점수": item.get("final_candidate_score"),
            "매칭 상태": label_value(item.get("match_status"), MATCH_STATUS_LABELS),
            "매칭 방식": item.get("match_method"),
            "원천 회사/키워드": item.get("source_company_name") or item.get("source_keyword"),
            "선정 근거": item.get("reason_summary"),
            "위험 플래그": ", ".join(label_list(item.get("risk_flags") or [], RISK_FLAG_LABELS)),
        }
        for item in rows
    ]


def _localized_recommendation_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "순위": item.get("rank"),
            "종목명": item.get("security_name"),
            "ticker/종목코드": item.get("ticker"),
            "국가": item.get("country_code"),
            "거래소": item.get("exchange_code"),
            "자산 유형": label_value(item.get("asset_type"), ASSET_TYPE_LABELS),
            "최종 점수": item.get("final_score"),
            "후보 점수": item.get("candidate_score"),
            "근거 점수": item.get("evidence_score"),
            "후보 선정 이유": item.get("selection_reason"),
            "근거 요약": item.get("evidence_summary"),
            "위험 플래그": ", ".join(label_list(item.get("risk_flags") or [], RISK_FLAG_LABELS)),
            "제외 사유": item.get("excluded_reason")
            or ", ".join(label_list(item.get("exclusion_flags") or [], EXCLUSION_FLAG_LABELS)),
        }
        for item in rows
    ]


CANDIDATE_REASON_LABELS = {
    "negative_theme_excluded": "부정적 영향 가능성이 있어 위험 알림으로 분류했습니다.",
    "mixed_theme_risk_penalty": "긍정과 부정 요인이 섞여 있어 주의 테마로 분류하고 위험 패널티를 적용했습니다.",
    "no_matched_security": "종목 마스터에서 검증된 후보를 찾지 못했습니다.",
    "no_kr_stock_candidate": "한국 상장 주식으로 검증된 후보가 없습니다.",
    "only_us_candidates": "미국 주식 후보만 발견되어 해외 참고 후보로 분리했습니다.",
    "only_etf_candidates": "개별 주식보다 ETF 후보만 발견되었습니다.",
    "all_candidates_below_score_threshold": "후보 점수가 기준보다 낮아 제외되었습니다.",
    "all_candidates_excluded_by_policy": "정책 기준에 따라 모든 후보가 제외되었습니다.",
    "ambiguous_only": "매칭 불확실 후보만 발견되었습니다.",
    "insufficient_evidence": "후보 근거가 부족합니다.",
    "private_or_unlisted_entity": "비상장 또는 종목 마스터 미등록 기업은 직접 후보로 만들지 않습니다.",
    "no_candidate_search_tags": "후보 검색 태그가 부족합니다.",
    "missing_theme_tags": "테마 태그 보강이 필요합니다.",
    "risk_alert_excluded": "규제, 단속, 논란 등 위험 알림 성격이라 후보 생성에서 제외했습니다.",
    "macro_background_excluded": "시장 배경 정보이나 직접 후보 연결성이 낮아 후보 생성에서 제외했습니다.",
    "low_actionability_excluded": "주가 영향과 상장 후보 연결성이 낮아 후보 생성에서 제외했습니다.",
    "watchlist_not_included": "관찰 테마는 기본 후보 생성 대상이 아닙니다.",
}

THEME_BUCKET_LABELS = {
    "investable_opportunity": "분석 기반 관심 테마",
    "watchlist": "관찰 테마",
    "risk_alert": "위험 알림",
    "macro_background": "시장 배경",
    "low_actionability": "낮은 실행 가능성",
}


def _reason_text(diagnostics: dict | None, default: str) -> str:
    reasons = (diagnostics or {}).get("candidate_exclusion_reasons") or []
    labels = [CANDIDATE_REASON_LABELS.get(reason, str(reason)) for reason in reasons]
    return " / ".join(labels) if labels else default


def _security_error_message(endpoint_name: str, exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "FastAPI 서버에 연결할 수 없습니다."
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout)):
        return "종목 기준정보 API 응답 시간이 초과되었습니다."
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{endpoint_name} 중 백엔드 오류 {exc.response.status_code}이 발생했습니다."
    if isinstance(exc, (JSONDecodeError, ValueError)):
        return "종목 기준정보 응답 형식이 올바르지 않습니다."
    return f"{endpoint_name} 중 예상하지 못한 오류가 발생했습니다."


def _security_get_json(endpoint_key: str, endpoint_name: str, path: str, expected_type: type, params: dict | None = None):
    error_key = f"security_error_{endpoint_key}"
    cache_key = f"security_cache_{endpoint_key}"
    try:
        response = httpx.get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, expected_type):
            raise ValueError("unexpected response schema")
        st.session_state.pop(error_key, None)
        st.session_state[cache_key] = data
        return data, None, False
    except Exception as exc:
        message = _security_error_message(endpoint_name, exc)
        st.session_state[error_key] = message
        cached = st.session_state.get(cache_key)
        return cached, message, cached is not None


def fetch_summary() -> tuple[dict[str, int] | None, str | None]:
    try:
        response = httpx.get(f"{BASE_URL}/errors/summary", timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def fetch_errors(
    limit: int,
    severity: str | None,
    component: str | None,
    status: str | None,
    ticker: str | None,
) -> tuple[list[dict[str, str | int | dict]] | None, str | None]:
    try:
        params: dict[str, str | int] = {"limit": limit}
        if severity:
            params["severity"] = severity
        if component:
            params["component"] = component
        if status:
            params["status"] = status
        if ticker:
            params["ticker"] = ticker

        response = httpx.get(f"{BASE_URL}/errors", params=params, timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def create_demo_error() -> tuple[dict[str, str | int | dict] | None, str | None]:
    try:
        response = httpx.post(f"{BASE_URL}/errors/demo", timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def update_error_status(error_id: int, status: str) -> tuple[dict[str, str | int | dict] | None, str | None]:
    try:
        response = httpx.patch(
            f"{BASE_URL}/errors/{error_id}/status",
            json={"status": status},
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None, "해당 오류 ID를 찾을 수 없습니다."
        return None, f"백엔드 오류: {exc.response.status_code}"


def fetch_search_term_status() -> tuple[dict[str, int | bool] | None, str | None]:
    try:
        response = httpx.get(f"{BASE_URL}/news/search-terms/status", timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def fetch_search_terms(active_only: bool | None = None) -> tuple[list[dict] | None, str | None]:
    try:
        params: dict[str, str | int] = {}
        if active_only is not None:
            params["active_only"] = str(active_only).lower()
        response = httpx.get(f"{BASE_URL}/news/search-terms", params=params, timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def create_search_term(query: str, provider: str, display: int, sort: str, is_active: bool) -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(
            f"{BASE_URL}/news/search-terms",
            json={"query": query, "provider": provider, "display": display, "sort": sort, "is_active": is_active},
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def patch_search_term(term_id: int, updates: dict) -> tuple[dict | None, str | None]:
    try:
        response = httpx.patch(
            f"{BASE_URL}/news/search-terms/{term_id}",
            json=updates,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None, "검색어를 찾을 수 없습니다."
        return None, f"백엔드 오류: {exc.response.status_code}"


def delete_search_term(term_id: int) -> tuple[bool, str | None]:
    try:
        response = httpx.delete(f"{BASE_URL}/news/search-terms/{term_id}", timeout=TIMEOUT_SECONDS, follow_redirects=True)
        if response.status_code == 204:
            return True, None
        return False, f"백엔드 오류: {response.status_code}"
    except httpx.RequestError:
        return False, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."


def render_home_page() -> None:
    st.title("Stock AI Lab")
    st.write("AI 뉴스 분석 및 모의투자 연구용 대시보드")
    st.markdown("### 현재 개발 단계")
    st.write("기본 환경과 대시보드 뼈대만 구성된 초기 단계입니다.")
    st.success("기본 환경이 정상적으로 실행되었습니다.")
    st.markdown("### 앞으로 구현할 기능 목록")
    st.write(
        "- 뉴스 수집\n"
        "- GPT 뉴스 분석\n"
        "- 테마 선정\n"
        "- 종목 및 ETF 분석\n"
        "- 모의투자\n"
        "- 머신러닝\n"
        "- Error Center"
    )


def render_error_center_page() -> None:
    st.title("Error Center")
    refresh = st.button("새로고침", key="error_refresh_button")
    demo_button = st.button("테스트 오류 생성", key="error_demo_button")

    if refresh:
        st.rerun()

    severity_filter = st.selectbox(
        "Severity 필터",
        ["", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        key="error_severity_filter",
    )
    status_filter = st.selectbox(
        "Status 필터",
        ["", "new", "investigating", "planned", "resolved", "ignored", "reopened"],
        key="error_status_filter",
    )
    ticker_filter = st.text_input("Ticker 필터", key="error_ticker_filter")

    summary_data, summary_error = fetch_summary()
    if summary_error:
        st.warning(summary_error)
    elif summary_data is None:
        st.warning("FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요.")
    else:
        cols = st.columns(6)
        cols[0].metric("전체 오류", summary_data.get("total", 0))
        cols[1].metric("미해결 오류", summary_data.get("unresolved", 0))
        cols[2].metric("CRITICAL", summary_data.get("critical", 0))
        cols[3].metric("WARNING", summary_data.get("warning", 0))
        cols[4].metric("ERROR", summary_data.get("error", 0))
        cols[5].metric("INFO", summary_data.get("info", 0))

    if demo_button:
        demo_data, demo_error = create_demo_error()
        if demo_error:
            st.error(demo_error)
        else:
            st.success("테스트 오류가 생성되었습니다.")
            st.json(demo_data)

    error_rows, errors_error = fetch_errors(
        limit=100,
        severity=severity_filter or None,
        component=None,
        status=status_filter or None,
        ticker=ticker_filter or None,
    )

    if errors_error:
        st.warning(errors_error)
    elif not error_rows:
        st.info("조회된 오류가 없습니다.")
    else:
        st.markdown("### 오류 목록")
        table_data = [
            {
                "id": item.get("id"),
                "occurred_at": item.get("occurred_at"),
                "severity": item.get("severity"),
                "component": item.get("component"),
                "error_type": item.get("error_type"),
                "message": item.get("message"),
                "status": item.get("status"),
                "ticker": item.get("ticker"),
                "retry_count": item.get("retry_count"),
            }
            for item in error_rows
        ]
        st.dataframe(table_data)

    st.markdown("### 오류 상태 변경")
    status_id = st.number_input("Error ID", min_value=1, step=1, key="error_status_id")
    new_status = st.selectbox(
        "새 상태 선택",
        ["new", "investigating", "planned", "resolved", "ignored", "reopened"],
        key="error_new_status",
    )
    if st.button("상태 변경", key="error_update_button"):
        updated_data, update_error_text = update_error_status(int(status_id), new_status)
        if update_error_text:
            st.error(update_error_text)
        else:
            st.success("오류 상태가 변경되었습니다.")
            st.json(updated_data)


def fetch_collection_status() -> tuple[dict | None, str | None]:
    try:
        response = httpx.get(f"{BASE_URL}/news/collection-status", timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def collect_all_now() -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(f"{BASE_URL}/news/collect-all", timeout=60.0, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def render_search_terms_page() -> None:
    st.title("수집 현황")
    st.caption("시스템 내부 수집 프로필 기반 자동 뉴스 수집 현황입니다.")
    refresh = st.button("상태 새로고침", key="collection_status_refresh")
    if refresh:
        st.rerun()

    status_data, status_error = fetch_collection_status()
    if status_error:
        st.warning(status_error)
    elif status_data is None:
        st.warning("수집 현황을 불러올 수 없습니다.")
    else:
        cols = st.columns(3)
        cols[0].metric("자동 수집", "활성" if status_data.get("enabled") else "비활성")
        cols[1].metric("수집 간격", f"{status_data.get('interval_minutes', 0)} 분")
        cols[2].metric("활성 프로필", status_data.get("active_profile_count", 0))

        cols = st.columns(3)
        cols[0].metric("전체 저장 뉴스", status_data.get("total_articles", 0))
        cols[1].metric("최근 24시간 뉴스", status_data.get("articles_last_24h", 0))
        cols[2].metric("중복 뉴스", status_data.get("duplicate_articles", 0))

        cols = st.columns(3)
        cols[0].metric("성공한 수집 실행", status_data.get("total_collection_runs", 0) - status_data.get("failed_collection_runs", 0))
        cols[1].metric("실패한 수집 실행", status_data.get("failed_collection_runs", 0))
        cols[2].metric("마지막 실행", status_data.get("last_run_at") or "-")

        st.markdown(f"**다음 실행 예정:** {status_data.get('next_run_at') or '-'}")

    if st.button("지금 뉴스 전체 수집", key="collection_now_button"):
        result, error = collect_all_now()
        if error:
            st.error(error)
        elif result:
            st.success("전체 수집이 실행되었습니다.")
            st.json(result)
        else:
            st.warning("전체 수집을 실행할 수 없습니다.")

    with st.expander("개발자용 내부 수집 프로필", expanded=False):
        term_rows, terms_error = fetch_search_terms(active_only=True)
        if terms_error:
            st.warning(terms_error)
        elif not term_rows:
            st.info("활성 내부 프로필이 없습니다.")
        else:
            st.dataframe(
                [
                    {
                        "query": item.get("query"),
                        "provider": item.get("provider"),
                        "display": item.get("display"),
                        "sort": item.get("sort"),
                        "is_active": item.get("is_active"),
                    }
                    for item in term_rows
                ]
            )


def render_news_page() -> None:
    st.title("뉴스")
    st.caption("자동으로 수집된 최신 시장 뉴스를 확인합니다.")
    refresh = st.button("새로고침", key="news_refresh_button")
    if refresh:
        st.rerun()

    status_data, status_error = fetch_collection_status()
    if status_error:
        st.warning(status_error)
    elif status_data is None:
        st.warning("FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요.")
    else:
        cols = st.columns(3)
        cols[0].metric("자동 수집", "활성" if status_data.get("enabled") else "비활성")
        cols[1].metric("수집 간격", f"{status_data.get('interval_minutes', 0)} 분")
        cols[2].metric("전체 저장 뉴스", status_data.get("total_articles", 0))

        cols = st.columns(3)
        cols[0].metric("최근 24시간 뉴스", status_data.get("articles_last_24h", 0))
        cols[1].metric("중복 뉴스", status_data.get("duplicate_articles", 0))
        cols[2].metric("성공한 수집 실행", status_data.get("total_collection_runs", 0) - status_data.get("failed_collection_runs", 0))

        cols = st.columns(3)
        cols[0].metric("실패한 수집 실행", status_data.get("failed_collection_runs", 0))
        cols[1].metric("마지막 자동 수집", status_data.get("last_run_at") or "-")
        cols[2].metric("다음 자동 수집", status_data.get("next_run_at") or "-")

    if st.button("지금 전체 수집", key="news_run_collection_button"):
        result, error = collect_all_now()
        if error:
            st.error(error)
        elif result:
            st.success("전체 수집이 실행되었습니다.")
            st.json(result)
        else:
            st.warning("전체 수집을 실행할 수 없습니다.")

    if st.button("중복/저관련 뉴스 정리", key="news_run_dedupe_button"):
        try:
            resp = httpx.post(f"{BASE_URL}/news/dedupe/run", timeout=60.0, follow_redirects=True)
            resp.raise_for_status()
            st.success("뉴스 중복 및 관련성 판정을 갱신했습니다.")
            st.json(resp.json())
            st.rerun()
        except httpx.RequestError:
            logging.exception("뉴스 품질 정리 요청 연결 오류")
            st.error("FastAPI 서버에 연결할 수 없습니다.")
        except httpx.HTTPStatusError as exc:
            logging.exception("뉴스 품질 정리 실패")
            st.error(f"뉴스 품질 정리 실패: {exc.response.status_code}")
        except Exception:
            logging.exception("뉴스 품질 정리 중 예기치 않은 오류")
            st.error("뉴스 품질 정리 중 오류가 발생했습니다.")

    st.markdown("---")
    st.subheader("자동 수집 뉴스")
    filter_cols = st.columns(3)
    show_duplicates = filter_cols[0].checkbox("중복 뉴스 표시", value=False, key="news_show_duplicates")
    show_noise = filter_cols[1].checkbox("저관련 뉴스 표시", value=False, key="news_show_noise")
    analysis_only = filter_cols[2].checkbox("분석 후보만 보기", value=True, key="news_analysis_only")
    try:
        params: dict[str, object] = {"limit": 100}
        if not show_duplicates:
            params["is_duplicate"] = False
        if analysis_only:
            params["is_analysis_candidate"] = True
        elif not show_noise:
            params["is_market_relevant"] = True
        resp = httpx.get(f"{BASE_URL}/news/", params=params, timeout=TIMEOUT_SECONDS, follow_redirects=True)
        resp.raise_for_status()
        articles = resp.json()
        if not articles:
            st.info("표시할 뉴스가 없습니다. 필터를 조정하거나 새 뉴스를 수집해 보세요.")
        else:
            table_data = [
                {
                    "게시 시각": a.get("published_at"),
                    "언론사": a.get("publisher") or "-",
                    "제목": a.get("title"),
                    "설명": a.get("description"),
                    "원문 링크": a.get("link"),
                    "수집 시각": a.get("collected_at"),
                    "Provider": a.get("provider"),
                    "수집 검색어": a.get("query"),
                    "시장 관련성 점수": a.get("market_relevance_score"),
                    "분석 후보": a.get("is_analysis_candidate"),
                    "중복 여부": a.get("is_duplicate"),
                    "중복 사유": a.get("duplicate_reason") or "-",
                }
                for a in articles[:100]
            ]
            st.dataframe(table_data)
    except httpx.RequestError:
        logging.exception("FastAPI 서버 연결 오류")
        st.warning("FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요.")
    except httpx.HTTPStatusError as exc:
        logging.exception("기사 목록 로드 실패")
        st.warning(f"뉴스 목록을 불러올 수 없습니다. 백엔드 오류: {exc.response.status_code}")
    except Exception:
        logging.exception("기사 목록 처리 중 예기치 않은 오류")
        st.warning("뉴스 목록을 불러오는 중 오류가 발생했습니다.")


def render_ai_analysis_page() -> None:
    st.title("AI 분석")
    st.caption("저장된 뉴스를 GPT로 분석하고 결과를 확인합니다.")
    refresh = st.button("분석 새로고침", key="ai_analysis_refresh_button")
    if refresh:
        st.rerun()

    col = st.columns(1)
    st.write("분석 실행")
    count = int(st.number_input("분석 개수", min_value=1, max_value=200, value=10, step=1, key="ai_analysis_count"))
    if st.button("1건 테스트 분석", key="ai_analysis_test_one_button"):
        try:
            resp = httpx.post(f"{BASE_URL}/news-analysis/test-one", timeout=60.0, follow_redirects=True)
            if resp.status_code == 200:
                render_ai_analysis_result(resp.json())
            else:
                detail = None
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = None
                st.error(detail or f"테스트 분석 실패: {resp.status_code}")
        except Exception:
            st.error("테스트 분석 요청 중 오류가 발생했습니다.")
    if st.button("미분석 뉴스 GPT 분석", key="ai_analysis_run_button"):
        try:
            resp = httpx.post(f"{BASE_URL}/news-analysis/run", params={"limit": count}, timeout=60.0, follow_redirects=True)
            if resp.status_code == 200:
                render_ai_analysis_result(resp.json())
            else:
                detail = None
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = None
                message = f"분석 실행 실패: {resp.status_code}"
                if detail:
                    message += f" - {detail}"
                st.error(message)
        except Exception:
            st.error("분석 요청 중 오류가 발생했습니다.")


def render_ai_analysis_result(result: dict) -> None:
    if result.get("requested", 0) == 0:
        st.info("현재 분석 가능한 미분석 뉴스가 없습니다.")
    elif result.get("failed", 0) > 0:
        messages = result.get("error_messages") or ["뉴스 분석 중 오류가 발생했습니다."]
        for message in messages:
            st.warning(message)
    else:
        st.success("분석 실행 요청 완료")
    st.json(result)


def fetch_security_summary() -> tuple[dict | None, str | None]:
    data, error, _cached = _security_get_json("summary", "종목 요약 조회", "/securities/summary", dict)
    return data, error


def fetch_securities(params: dict) -> tuple[list[dict] | None, str | None]:
    try:
        response = httpx.get(f"{BASE_URL}/securities", params=params, timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def sync_mock_securities() -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(f"{BASE_URL}/securities/sync/mock", timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"백엔드 오류: {exc.response.status_code}"


def sync_us_securities() -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(f"{BASE_URL}/securities/sync/us", timeout=120.0, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"백엔드 오류: {exc.response.status_code}"


def sync_kr_securities() -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(f"{BASE_URL}/securities/sync/kr", timeout=120.0, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다."
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"백엔드 오류: {exc.response.status_code}"


def fetch_security_data_quality() -> tuple[dict | None, str | None]:
    data, error, _cached = _security_get_json("data_quality", "데이터 품질 조회", "/securities/data-quality", dict)
    return data, error


def fetch_security_sync_runs() -> tuple[list[dict] | None, str | None]:
    data, error, _cached = _security_get_json(
        "sync_runs",
        "동기화 실행 기록 조회",
        "/securities/sync-runs",
        list,
        params={"country_code": "US", "limit": 10},
    )
    return data, error


def enrich_us_sec() -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(f"{BASE_URL}/securities/enrich/us/sec", timeout=120.0, follow_redirects=True)
        response.raise_for_status()
        for key in ("security_error_summary", "security_error_data_quality", "security_error_sync_runs"):
            st.session_state.pop(key, None)
        return response.json(), None
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"SEC CIK 보강 중 백엔드 오류 {exc.response.status_code}이 발생했습니다."
    except Exception as exc:
        return None, _security_error_message("SEC CIK 보강", exc)


def backfill_security_aliases_request() -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(f"{BASE_URL}/securities/aliases/backfill", timeout=120.0, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다."
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"백엔드 오류: {exc.response.status_code}"


def fetch_theme_security_candidates(theme_id: int) -> tuple[list[dict] | None, str | None]:
    try:
        response = httpx.get(f"{BASE_URL}/themes/{theme_id}/candidates", timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def run_theme_candidate_request(
    include_weak_industry_candidates: bool = False,
    include_watchlist_themes: bool = False,
    include_leveraged_inverse_etfs: bool = True,
    max_stock_candidates_per_theme: int = 15,
    max_etf_candidates_per_theme: int = 20,
) -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(
            f"{BASE_URL}/themes/candidates/run",
            json={
                "include_weak_industry_candidates": include_weak_industry_candidates,
                "include_watchlist_themes": include_watchlist_themes,
                "include_leveraged_inverse_etfs": include_leveraged_inverse_etfs,
                "max_stock_candidates_per_theme": max_stock_candidates_per_theme,
                "max_etf_candidates_per_theme": max_etf_candidates_per_theme,
            },
            timeout=120.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다."
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"백엔드 오류: {exc.response.status_code}"


def render_security_master_page() -> None:
    st.title("종목 기준정보")
    st.caption("한국·미국 상장 주식과 ETF 기준정보를 통합 관리합니다.")

    if st.button("새로고침", key="security_refresh_button"):
        st.rerun()
    if st.button("개발자용 Mock 동기화", key="security_mock_sync_button"):
        result, error = sync_mock_securities()
        if error:
            st.error(error)
        else:
            st.success("Mock 종목 기준정보 동기화가 완료되었습니다.")
            st.json(result)
    if st.button("미국 실제 종목 동기화", key="security_sync_us_button"):
        st.info("Nasdaq Trader 기준정보를 수집하고 SEC CIK 보강을 시도합니다.")
        result, error = sync_us_securities()
        if error:
            st.error(error)
        elif result:
            if result.get("error_message"):
                st.warning(result.get("error_message"))
            else:
                st.success("미국 종목 기준정보 동기화가 완료되었습니다.")
            st.json(result)
    if st.button("미국 SEC CIK 보강", key="security_enrich_us_sec_button"):
        result, error = enrich_us_sec()
        if error:
            st.error(error)
        elif result:
            st.success("미국 SEC CIK 보강이 완료되었습니다.")
            cols = st.columns(5)
            cols[0].metric("CIK 갱신", result.get("cik_updated_count", 0))
            cols[1].metric("매칭", result.get("matched_count", 0))
            cols[2].metric("미매칭", result.get("unmatched_count", 0))
            cols[3].metric("모호", result.get("ambiguous_count", 0))
            cols[4].metric("실행 ms", result.get("duration_ms", 0))
            st.json(result)
    if st.button("한국 실제 종목 동기화", key="security_sync_kr_button"):
        result, error = sync_kr_securities()
        if error:
            st.error(error)
        elif result:
            if result.get("error_message"):
                st.warning(result.get("error_message"))
            else:
                st.success("한국 종목 기준정보 동기화가 완료되었습니다.")
            st.json(result)
    if st.button("한국 동기화 상태 새로고침", key="security_kr_status_refresh_button"):
        st.rerun()
    alias_cols = st.columns(2)
    if alias_cols[0].button("종목 별칭 보강", key="security_alias_backfill_button"):
        result, error = backfill_security_aliases_request()
        st.error(error) if error else st.json(result)
    if alias_cols[1].button("별칭 매칭 진단", key="security_alias_diagnostic_button"):
        st.info("아래 회사명 또는 ticker 검색창에서 별칭 매칭 결과를 확인하세요.")

    summary, summary_error = fetch_security_summary()
    if summary_error:
        st.warning(summary_error)
        if summary:
            st.info("마지막으로 정상 조회한 데이터입니다.")
    elif summary:
        cols = st.columns(6)
        cols[0].metric("전체 종목", summary.get("total", 0))
        cols[1].metric("한국 주식", summary.get("kr_stock", 0))
        cols[2].metric("한국 ETF", summary.get("kr_etf", 0))
        cols[3].metric("미국 주식", summary.get("us_stock", 0))
        cols[4].metric("미국 ETF", summary.get("us_etf", 0))
        cols[5].metric("마지막 동기화", summary.get("last_sync_at") or "-")

    quality, quality_error = fetch_security_data_quality()
    if quality_error:
        st.warning(quality_error)
        if quality:
            st.info("마지막으로 정상 조회한 데이터입니다.")
    elif quality:
        cols = st.columns(6)
        cols[0].metric("미국 주식", quality.get("us_stock", 0))
        cols[1].metric("미국 ETF", quality.get("us_etf", 0))
        cols[2].metric("추천 가능", quality.get("recommendation_eligible_count", 0))
        cols[3].metric("제외 증권", quality.get("recommendation_excluded_count", 0))
        cols[4].metric("CIK 보강", quality.get("last_cik_enriched_count", 0))
        cols[5].metric("unknown 거래소", quality.get("unknown_exchange_count", 0))
        st.write(f"마지막 미국 동기화: {quality.get('last_successful_sync_at') or '-'}")
        kr_cols = st.columns(6)
        kr_cols[0].metric("한국 주식", quality.get("kr_stock", 0))
        kr_cols[1].metric("한국 ETF", quality.get("kr_etf", 0))
        kr_cols[2].metric("KOSPI", quality.get("kospi_stock_count", 0))
        kr_cols[3].metric("KOSDAQ", quality.get("kosdaq_stock_count", 0))
        kr_cols[4].metric("KONEX", quality.get("konex_stock_count", 0))
        kr_cols[5].metric("최근 기준일", quality.get("last_kr_snapshot_date") or "-")

    sync_runs, sync_runs_error = fetch_security_sync_runs()
    if sync_runs_error:
        st.warning(sync_runs_error)
        if sync_runs:
            st.info("마지막으로 정상 조회한 데이터입니다.")
    elif sync_runs:
        with st.expander("최근 미국 동기화 실행 기록", expanded=False):
            st.dataframe(sync_runs)

    keyword = st.text_input("회사명 또는 ticker", key="security_keyword")
    cols = st.columns(3)
    country = cols[0].selectbox("국가", ["", "KR", "US"], key="security_country")
    asset_type = cols[1].selectbox("자산 유형", ["", "stock", "etf"], key="security_asset")
    exchange = cols[2].text_input("거래소", key="security_exchange")
    params = {
        "keyword": keyword or None,
        "country_code": country or None,
        "asset_type": asset_type or None,
        "exchange_code": exchange or None,
        "limit": 100,
    }
    rows, rows_error = fetch_securities({k: v for k, v in params.items() if v is not None})
    if rows_error:
        st.warning(rows_error)
    elif not rows:
        st.info("조회된 종목 기준정보가 없습니다.")
    else:
        st.dataframe(
            [
                {
                    "국가": item.get("country_code"),
                    "종목명": item.get("name"),
                    "영문명": item.get("english_name"),
                    "ticker/종목코드": item.get("ticker"),
                    "거래소": item.get("exchange_name"),
                    "자산 유형": item.get("asset_type"),
                    "통화": item.get("currency"),
                    "산업": item.get("industry"),
                    "활성": item.get("is_active"),
                    "출처": item.get("source"),
                }
                for item in rows
            ]
        )


def run_theme_analysis_request(window_hours: int, max_sources: int, provider: str = "openai") -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(
            f"{BASE_URL}/themes/run",
            json={"window_hours": window_hours, "max_sources": max_sources, "provider": provider},
            timeout=120.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"백엔드 오류: {exc.response.status_code}"


def run_market_analysis_request(payload: dict) -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(
            f"{BASE_URL}/market-analysis/run",
            json=payload,
            timeout=240.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"백엔드 오류: {exc.response.status_code}"


def fetch_latest_recommendations() -> tuple[dict | None, str | None]:
    try:
        response = httpx.get(f"{BASE_URL}/recommendations/latest", timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def run_recommendations_request(payload: dict) -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(f"{BASE_URL}/recommendations/run", json=payload, timeout=120.0, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"백엔드 오류: {exc.response.status_code}"


def fetch_latest_themes() -> tuple[dict | None, str | None]:
    try:
        response = httpx.get(f"{BASE_URL}/themes/latest", timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인하세요."
    except httpx.HTTPStatusError as exc:
        return None, f"백엔드 오류: {exc.response.status_code}"


def test_theme_openai_request() -> tuple[dict | None, str | None]:
    try:
        response = httpx.post(f"{BASE_URL}/themes/test-openai", timeout=120.0, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.RequestError:
        return None, "FastAPI 서버에 연결할 수 없습니다."
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"백엔드 오류: {exc.response.status_code}"


def render_theme_analysis_page() -> None:
    st.title("단기 시장 테마")
    st.caption("최근 AI 뉴스 분석 결과를 종합해 시장 단기 테마를 선정합니다.")

    if st.button("새로고침", key="theme_refresh_button"):
        st.rerun()

    st.info("이 화면은 분석 기반 관심 후보 생성 단계이며 실제 투자 판단이나 거래 지시가 아닙니다.")
    st.subheader("통합 실행")
    st.caption("미분석 뉴스 AI 분석, 단기 테마 생성, 종목·ETF 후보 생성을 순서대로 실행합니다.")
    config_cols = st.columns(3)
    analysis_window_hours = int(
        config_cols[0].number_input(
            "테마 분석 기간(시간)",
            min_value=1,
            max_value=720,
            value=24,
            step=1,
            key="theme_window_hours",
        )
    )
    max_news_analysis_count = int(
        config_cols[1].number_input(
            "AI 뉴스 분석 건수",
            min_value=1,
            max_value=200,
            value=20,
            step=1,
            key="theme_news_analysis_count",
        )
    )
    max_sources = int(
        config_cols[2].number_input(
            "테마 근거 최대 건수",
            min_value=3,
            max_value=300,
            value=50,
            step=1,
            key="theme_max_sources",
        )
    )
    window_hours = analysis_window_hours
    option_cols = st.columns(5)
    force_reanalyze = option_cols[0].checkbox("완료 뉴스도 강제 재분석", value=False, key="market_force_reanalyze")
    run_candidates = option_cols[1].checkbox("후보 생성 포함", value=True, key="market_run_candidates")
    run_recommendation_items = option_cols[2].checkbox("최종 관심 후보까지 생성", value=True, key="market_run_recommendations")
    include_weak_integrated = option_cols[3].checkbox(
        "약한 산업 후보 포함",
        value=False,
        key="market_include_weak_candidates",
    )
    include_watchlist_integrated = option_cols[4].checkbox(
        "관찰 테마 후보 포함",
        value=False,
        key="market_include_watchlist_themes",
    )
    include_risky_integrated = st.checkbox(
        "레버리지/인버스 ETF 포함",
        value=False,
        key="market_include_risky_etfs",
    )
    candidate_limit_cols = st.columns(2)
    max_stock_candidates = int(
        candidate_limit_cols[0].number_input(
            "테마별 최대 주식 후보 수",
            min_value=1,
            max_value=100,
            value=15,
            step=1,
            key="market_max_stock_candidates",
        )
    )
    max_etf_candidates = int(
        candidate_limit_cols[1].number_input(
            "테마별 최대 ETF 후보 수",
            min_value=1,
            max_value=100,
            value=20,
            step=1,
            key="market_max_etf_candidates",
        )
    )
    market_payload = {
        "analysis_window_hours": analysis_window_hours,
        "max_news_analysis_count": max_news_analysis_count,
        "max_theme_source_count": max_sources,
        "force_reanalyze": force_reanalyze,
        "run_candidate_generation": run_candidates,
        "include_weak_industry_candidates": include_weak_integrated,
        "include_watchlist_themes": include_watchlist_integrated,
        "include_leveraged_inverse_etfs": include_risky_integrated,
        "max_stock_candidates_per_theme": max_stock_candidates,
        "max_etf_candidates_per_theme": max_etf_candidates,
        "run_recommendations": run_recommendation_items,
        "max_stocks_per_theme": min(max_stock_candidates, 3),
        "max_etfs_per_theme": min(max_etf_candidates, 2),
    }
    recommendation_cols = st.columns(2)
    market_payload["max_stocks_per_theme"] = int(
        recommendation_cols[0].number_input(
            "테마별 최종 주식 후보 수",
            min_value=1,
            max_value=10,
            value=3,
            step=1,
            key="market_final_stock_count",
        )
    )
    market_payload["max_etfs_per_theme"] = int(
        recommendation_cols[1].number_input(
            "테마별 최종 ETF 후보 수",
            min_value=1,
            max_value=10,
            value=2,
            step=1,
            key="market_final_etf_count",
        )
    )
    if st.button("시장 분석 실행", key="market_run_button"):
        result, error = run_market_analysis_request(market_payload)
        if error:
            st.error(error)
        elif result:
            status = result.get("status")
            if status == "completed":
                st.success("통합 분석이 완료되었습니다.")
            elif status == "insufficient_data":
                st.info("현재 통합 분석에 사용할 수 있는 데이터가 부족합니다.")
            else:
                st.warning("통합 분석이 일부만 완료되었거나 실패했습니다.")
                failed_stage = result.get("failed_stage")
                error_code = result.get("error_code")
                if not error_code:
                    stage_result = (result.get(failed_stage) or {}) if failed_stage else {}
                    error_codes = stage_result.get("error_codes") or []
                    error_code = stage_result.get("error_code") or (error_codes[0] if error_codes else None)
                if failed_stage or error_code:
                    stage_label = label_value(failed_stage, STAGE_LABELS)
                    st.warning(f"{stage_label} 단계에서 오류 코드 {error_code or 'UNKNOWN_ERROR'}가 발생했습니다.")
                    if error_code == "OPENAI_INVALID_REQUEST":
                        stage_result = (result.get(failed_stage) or {}) if failed_stage else {}
                        st.info("테마 분석 요청 형식 또는 Structured Outputs 스키마를 확인하세요.")
                        safe_fields = {
                            "model_name": stage_result.get("model_name"),
                            "original_param": stage_result.get("original_param"),
                            "schema_name": stage_result.get("schema_name"),
                        }
                        st.json({key: value for key, value in safe_fields.items() if value})
            summary_cols = st.columns(4)
            news_result = result.get("news_analysis") or {}
            theme_result = result.get("theme_analysis") or {}
            candidate_result = result.get("candidate_generation") or {}
            recommendation_result = result.get("recommendations") or {}
            summary_cols[0].metric("뉴스 분석 완료", news_result.get("completed", 0))
            summary_cols[1].metric("뉴스 분석 실패", news_result.get("failed", 0))
            summary_cols[2].metric("선정 테마", theme_result.get("selected_theme_count", 0))
            summary_cols[3].metric(
                "관심 후보",
                (recommendation_result.get("recommended_stock_count") or 0)
                + (recommendation_result.get("recommended_etf_count") or 0),
            )
            with st.expander("통합 실행 결과", expanded=False):
                st.json(result)
            news_selection = result.get("news_selection") or {}
            if news_selection:
                with st.expander("이번 분석에 사용한 뉴스 기준", expanded=False):
                    policy = news_selection.get("selection_policy") or {}
                    st.write(
                        "이번 분석은 단순 최신순이 아니라 시장 관련성, 주가 영향 가능성, "
                        "한국 상장 주식/ETF와의 연결 가능성, 중복/저관련 뉴스 제외 기준으로 뉴스를 선택했습니다. "
                        f"최근 {policy.get('window_hours')}시간 기준, 최대 {policy.get('max_theme_source_count')}개 AI 분석 결과를 사용합니다."
                    )
                    st.caption(policy.get("score_formula") or "")
                    metric_cols = st.columns(5)
                    metric_cols[0].metric("스캔 뉴스", news_selection.get("scanned_count", 0))
                    metric_cols[1].metric("중복 제외", news_selection.get("duplicate_excluded_count", 0))
                    metric_cols[2].metric("저관련 제외", news_selection.get("low_relevance_excluded_count", 0))
                    metric_cols[3].metric("완료 분석", news_selection.get("already_analyzed_count", 0))
                    metric_cols[4].metric("테마 사용", news_selection.get("selected_for_theme_count", 0))
                    selected_articles = news_selection.get("selected_articles") or []
                    if selected_articles:
                        st.dataframe(
                            [
                                {
                                    "article_id": item.get("article_id"),
                                    "제목": item.get("title"),
                                    "언론사": item.get("publisher"),
                                    "게시 시각": item.get("published_at"),
                                    "시장 관련성": item.get("market_relevance_score"),
                                    "주가 영향 가능성": item.get("price_impact_score"),
                                    "투자 가능 연결성": item.get("investable_link_score"),
                                    "최종 선택 점수": item.get("final_news_selection_score"),
                                    "선택 이유": item.get("selection_reason"),
                                }
                                for item in selected_articles
                            ]
                        )

    latest, latest_error = fetch_latest_themes()
    if latest_error:
        st.warning(latest_error)
        return
    if not latest or not latest.get("run"):
        st.info("테마를 선정할 만큼 분석된 뉴스가 충분하지 않습니다.")
        return

    run = latest["run"]
    themes = latest.get("themes", [])
    cols = st.columns(6)
    cols[0].metric("분석 뉴스", run.get("selected_source_count", 0))
    cols[1].metric("선정 테마", run.get("selected_theme_count", 0))
    cols[2].metric("분석 기간", f"{window_hours}시간")
    cols[3].metric("사용 토큰", run.get("total_tokens") or 0)
    cols[4].metric("마지막 실행", run.get("completed_at") or "-")
    cols[5].metric("상태", _status_label(run.get("status")))

    if run.get("status") == "insufficient_data" or not themes:
        st.info("테마를 선정할 만큼 분석된 뉴스가 충분하지 않습니다.")
        return

    section_order = {
        "investable_opportunity": 0,
        "watchlist": 1,
        "risk_alert": 2,
        "macro_background": 3,
        "low_actionability": 4,
    }
    section_titles = {
        "investable_opportunity": "분석 기반 관심 테마",
        "watchlist": "관찰 테마",
        "risk_alert": "위험 알림",
        "macro_background": "시장 배경",
        "low_actionability": "낮은 실행 가능성",
    }
    ordered_themes = sorted(
        themes,
        key=lambda item: (section_order.get(item.get("theme_bucket"), 9), item.get("rank", 0)),
    )
    current_section = None
    for theme in ordered_themes:
        bucket = theme.get("theme_bucket") or "low_actionability"
        if bucket != current_section:
            current_section = bucket
            st.markdown(f"### {section_titles.get(bucket, '기타 테마')}")
            if bucket == "investable_opportunity":
                st.caption("실제 상장 종목/ETF 후보와 연결 가능한 테마입니다.")
            elif bucket == "watchlist":
                st.caption("가격 영향 가능성은 있지만 후보 연결성이 상대적으로 약한 테마입니다.")
            elif bucket == "risk_alert":
                st.caption("규제, 단속, 논란, 배정 실패 등 위험 알림 성격의 테마입니다.")
        st.subheader(f"{theme.get('rank')}. {theme.get('theme_name')}")
        cols = st.columns(7)
        cols[0].metric("계산 점수", f"{theme.get('calculated_score', 0):.2f}")
        cols[1].metric("신뢰도", f"{theme.get('confidence_score', 0):.2f}")
        cols[2].metric("영향 방향", label_value(theme.get("impact_direction"), IMPACT_DIRECTION_LABELS))
        cols[3].metric("영향 기간", label_value(theme.get("time_horizon"), TIME_HORIZON_LABELS))
        cols[4].metric("가격 영향", f"{theme.get('price_impact_score', 0):.2f}")
        cols[5].metric("투자 연결", f"{theme.get('investable_link_score', 0):.2f}")
        cols[6].metric("후보 대상", "예" if theme.get("is_investable_theme") else "아니오")
        st.caption("분류: " + label_value(bucket, THEME_BUCKET_LABELS))
        if theme.get("theme_bucket_reason"):
            st.info(str(theme.get("theme_bucket_reason")))
        st.write(theme.get("theme_summary"))
        st.write(theme.get("why_now"))
        st.write("관련 산업: " + ", ".join(theme.get("related_industries_json") or ["-"]))
        st.write("관련 회사: " + ", ".join(theme.get("related_companies_json") or ["-"]))
        st.write("이슈 태그: " + ", ".join(theme.get("issue_tags_json") or ["-"]))
        st.write("직접 영향 산업: " + ", ".join(theme.get("direct_impact_industries_json") or ["-"]))
        entity_rows = theme.get("entity_business_industries_json") or []
        if entity_rows:
            entity_lines = []
            for row in entity_rows:
                if isinstance(row, dict):
                    industries = ", ".join(row.get("industries") or ["추정 없음"])
                    confidence = row.get("confidence")
                    suffix = " (추정)" if isinstance(confidence, (int, float)) and confidence < 0.7 else ""
                    entity_lines.append(f"{row.get('entity')}: {industries}{suffix}")
            st.write("언급 기업 본업 산업: " + "; ".join(entity_lines or ["-"]))
        st.write("시장 테마 태그: " + ", ".join(theme.get("market_theme_tags_json") or ["-"]))
        st.write("후보 검색 태그: " + ", ".join(theme.get("candidate_search_tags_json") or ["-"]))
        st.write(f"근거 뉴스 수: {theme.get('evidence_count', 0)} / 언론사 수: {theme.get('source_publisher_count', 0)}")
        risks = theme.get("risk_factors_json") or []
        if risks:
            st.write("위험 요인: " + ", ".join(risks))
        with st.expander("근거 뉴스 보기", expanded=False):
            for evidence in theme.get("evidence", []):
                st.markdown(f"**{evidence.get('title')}**")
                st.write(evidence.get("publisher") or "-")
                st.write(evidence.get("published_at") or "-")
                st.write(evidence.get("summary") or "-")
                st.write(f"테마 관련성 점수: {evidence.get('relevance_score', 0):.2f}")
                st.write(evidence.get("evidence_reason"))
        candidates, candidate_error = fetch_theme_security_candidates(theme.get("id"))
        if candidate_error:
            st.warning(candidate_error)
        else:
            with st.expander("종목 후보 보기", expanded=False):
                matched = [c for c in candidates if c.get("match_status") == "matched"]
                kr_stocks = [c for c in matched if c.get("country_code") == "KR" and c.get("asset_type") == "stock"]
                kr_etfs = [c for c in matched if c.get("country_code") == "KR" and c.get("asset_type") == "etf"]
                overseas = [c for c in matched if c.get("country_code") == "US"]
                ambiguous = [c for c in candidates if c.get("match_status") == "ambiguous"]
                unmatched = [c for c in candidates if c.get("match_status") == "unmatched"]

                st.write("국내 주식 관심 후보")
                if kr_stocks:
                    st.dataframe(_localized_candidate_rows(kr_stocks))
                else:
                    reason = "관련 후보가 있었지만 한국 상장 주식으로 검증된 후보가 없습니다." if candidates else "종목 마스터에서 검증된 후보를 찾지 못했습니다."
                    if overseas:
                        reason = "미국 주식 후보만 발견되어 해외 참고 후보로 분리했습니다."
                    elif kr_etfs:
                        reason = "개별 주식 후보는 없고 국내 ETF 후보만 발견되었습니다."
                    elif theme.get("impact_direction") == "negative":
                        reason = "부정적 영향 가능성이 있어 관심 후보 생성 대상에서 제외되었습니다."
                    st.info(reason)

                st.write("국내 ETF 후보")
                if kr_etfs:
                    st.dataframe(_localized_candidate_rows(kr_etfs))
                else:
                    st.caption("국내 ETF 후보가 없습니다.")

                st.write("해외 참고 후보")
                if overseas:
                    st.dataframe(_localized_candidate_rows(overseas))
                else:
                    st.caption("해외 참고 후보가 없습니다.")

                st.write("매칭 불확실 후보")
                if ambiguous:
                    st.dataframe(_localized_candidate_rows(ambiguous))
                else:
                    st.caption("매칭 불확실 후보가 없습니다.")

                st.write("미매칭 기업/키워드")
                if unmatched:
                    st.dataframe(_localized_candidate_rows(unmatched))
                else:
                    st.caption("미매칭 기업/키워드가 없습니다.")

    recommendations, recommendation_error = fetch_latest_recommendations()
    if recommendation_error:
        st.warning(recommendation_error)
    elif recommendations and recommendations.get("run"):
        st.markdown("---")
        st.subheader("분석 기반 관심 후보")
        st.caption("현재가, 가격 전망, 수익 전망은 포함하지 않습니다. 실제 투자 판단 전 추가 검증이 필요합니다.")
        for theme_result in recommendations.get("themes", []):
            st.write(f"**{theme_result.get('theme_name')}**")
            diagnostics = theme_result.get("candidate_diagnostics") or {}
            if diagnostics:
                cols = st.columns(5)
                cols[0].metric("전체 후보", diagnostics.get("total_candidate_count", 0))
                cols[1].metric("국내 주식 후보", diagnostics.get("domestic_stock_candidate_count", 0))
                cols[2].metric("국내 ETF 후보", diagnostics.get("domestic_etf_candidate_count", 0))
                cols[3].metric("해외 참고", diagnostics.get("overseas_reference_count", 0))
                cols[4].metric("제외/불확실", (diagnostics.get("ambiguous_candidate_count", 0) or 0) + (diagnostics.get("unmatched_candidate_count", 0) or 0))
            if theme_result.get("recommendation_summary"):
                st.write(theme_result.get("recommendation_summary"))
            if theme_result.get("risk_summary"):
                st.write("위험 요약: " + str(theme_result.get("risk_summary")))
            st.write("국내 주식 관심 후보")
            domestic_stocks = theme_result.get("domestic_stocks") or []
            if domestic_stocks:
                st.dataframe(_localized_recommendation_rows(domestic_stocks))
            else:
                st.info(_reason_text(diagnostics, "현재 기준을 통과한 국내 주식 관심 후보가 없습니다."))

            st.write("국내 ETF 후보")
            domestic_etfs = theme_result.get("domestic_etfs") or []
            if domestic_etfs:
                st.dataframe(_localized_recommendation_rows(domestic_etfs))
            else:
                st.caption("현재 기준을 통과한 국내 ETF 후보가 없습니다.")

            overseas_reference = theme_result.get("overseas_reference") or []
            if overseas_reference:
                with st.expander("해외 참고 후보", expanded=False):
                    st.dataframe(_localized_recommendation_rows(overseas_reference))
            excluded = theme_result.get("excluded") or []
            if excluded:
                with st.expander("제외 후보", expanded=False):
                    st.dataframe(_localized_recommendation_rows(excluded))


def _developer_request(method: str, path: str, json: dict | None = None, timeout: float = 30.0):
    try:
        response = httpx.request(method, f"{BASE_URL}{path}", json=json, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        return response.json(), None
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        return None, detail or f"백엔드 오류: {exc.response.status_code}"
    except Exception as exc:
        return None, _security_error_message("개발자 설정", exc)


def fetch_developer_settings_status():
    return _developer_request("GET", "/developer/settings/status")


def _render_developer_test_result(data: dict | None, error: str | None) -> None:
    if error:
        st.error(error)
        return
    if not data:
        st.error("연결 테스트 응답이 없습니다.")
        return
    message = str(data.get("message") or data.get("status") or "연결 테스트 결과를 확인하세요.")
    if data.get("success"):
        st.success(message)
    else:
        st.error(message)
    details = data.get("details")
    if details:
        st.json(details)


def _render_status_badge(label: str, configured: bool, source: str | None = None) -> None:
    text = "설정됨" if configured else "미설정"
    if source:
        text = f"{text} ({source})"
    st.metric(label, text)


DEVELOPER_NAVER_WIDGET_KEYS = ("developer_naver_client_id", "developer_naver_client_secret")
DEVELOPER_OPENAI_WIDGET_KEYS = ("developer_openai_api_key", "developer_openai_model")
DEVELOPER_SEC_WIDGET_KEYS = ("developer_sec_user_agent",)
DEVELOPER_KRX_WIDGET_KEYS = (
    "developer_krx_api_key",
    "developer_krx_base_url",
    "developer_krx_kospi_api_id",
    "developer_krx_kosdaq_api_id",
    "developer_krx_konex_api_id",
    "developer_krx_etf_api_id",
    "developer_krx_api_key_param",
    "developer_krx_api_id_param",
    "developer_krx_base_date_param",
)


def clear_widget_keys_before_render(flag_key: str, widget_keys: tuple[str, ...]) -> None:
    if st.session_state.pop(flag_key, False):
        for key in widget_keys:
            st.session_state.pop(key, None)


def render_api_settings_tab() -> None:
    clear_widget_keys_before_render("clear_developer_naver_form", DEVELOPER_NAVER_WIDGET_KEYS)
    clear_widget_keys_before_render("clear_developer_openai_form", DEVELOPER_OPENAI_WIDGET_KEYS)
    clear_widget_keys_before_render("clear_developer_sec_form", DEVELOPER_SEC_WIDGET_KEYS)
    clear_widget_keys_before_render("clear_developer_krx_form", DEVELOPER_KRX_WIDGET_KEYS)

    st.subheader("API 설정")
    status_data, status_error = fetch_developer_settings_status()
    if status_error:
        st.warning(status_error)
    status_data = status_data or {}

    with st.expander("네이버 뉴스 API", expanded=True):
        naver_status = status_data.get("naver", {})
        cols = st.columns(3)
        with cols[0]:
            _render_status_badge("Client ID", bool(naver_status.get("client_id_configured")), naver_status.get("source"))
        with cols[1]:
            _render_status_badge("Client Secret", bool(naver_status.get("client_secret_configured")), naver_status.get("source"))
        client_id = st.text_input("Client ID", type="password", key="developer_naver_client_id")
        client_secret = st.text_input("Client Secret", type="password", key="developer_naver_client_secret")
        if st.button("저장", key="developer_naver_save_button"):
            data, error = _developer_request("PUT", "/developer/settings/naver", {"client_id": client_id or None, "client_secret": client_secret or None})
            if error:
                st.error(error)
            else:
                st.session_state["clear_developer_naver_form"] = True
                st.rerun()
                st.success("네이버 API 설정을 저장했습니다.")
                st.json(data)
        if st.button("연결 테스트", key="developer_naver_test_button"):
            data, error = _developer_request("POST", "/developer/settings/test/naver", timeout=30.0)
            st.error(error) if error else st.json(data)
        if st.button("저장값 삭제", key="developer_naver_delete_button"):
            data, error = _developer_request("DELETE", "/developer/settings/naver")
            st.error(error) if error else st.success("네이버 API 저장값을 삭제했습니다.")

    with st.expander("OpenAI API", expanded=False):
        openai_status = status_data.get("openai", {})
        _render_status_badge("API Key", bool(openai_status.get("api_key_configured")), openai_status.get("source"))
        api_key = st.text_input("OpenAI API Key", type="password", key="developer_openai_api_key")
        model = st.text_input("모델명", value=str(openai_status.get("model") or ""), key="developer_openai_model")
        st.caption("OpenAI 연결 테스트에는 소량의 API 사용량이 발생할 수 있습니다.")
        if st.button("저장", key="developer_openai_save_button"):
            data, error = _developer_request("PUT", "/developer/settings/openai", {"api_key": api_key or None, "model": model or None})
            if error:
                st.error(error)
            else:
                st.session_state["clear_developer_openai_form"] = True
                st.rerun()
                st.success("OpenAI API 설정을 저장했습니다.")
                st.json(data)
        if st.button("연결 테스트", key="developer_openai_test_button"):
            data, error = _developer_request("POST", "/developer/settings/test/openai", timeout=30.0)
            st.error(error) if error else st.json(data)
        if st.button("저장값 삭제", key="developer_openai_delete_button"):
            data, error = _developer_request("DELETE", "/developer/settings/openai")
            st.error(error) if error else st.success("OpenAI API 저장값을 삭제했습니다.")

    with st.expander("SEC", expanded=False):
        sec_status = status_data.get("sec", {})
        _render_status_badge("User-Agent", bool(sec_status.get("user_agent_configured")), sec_status.get("source"))
        user_agent = st.text_input("SEC User-Agent", type="password", key="developer_sec_user_agent")
        if st.button("저장", key="developer_sec_save_button"):
            data, error = _developer_request("PUT", "/developer/settings/sec", {"user_agent": user_agent or None})
            if error:
                st.error(error)
            else:
                st.session_state["clear_developer_sec_form"] = True
                st.rerun()
                st.success("SEC 설정을 저장했습니다.")
                st.json(data)
        if st.button("연결 테스트", key="developer_sec_test_button"):
            data, error = _developer_request("POST", "/developer/settings/test/sec", timeout=30.0)
            st.error(error) if error else st.json(data)
        if st.button("삭제", key="developer_sec_delete_button"):
            data, error = _developer_request("DELETE", "/developer/settings/sec")
            st.error(error) if error else st.success("SEC 설정을 삭제했습니다.")

    with st.expander("KRX", expanded=False):
        krx_status = status_data.get("krx", {})
        kcols = st.columns(3)
        kcols[0].metric("API Key", "설정됨" if krx_status.get("api_key_configured") else "미설정")
        kcols[1].metric("Base URL", "설정됨" if krx_status.get("base_url_configured") else "미설정")
        kcols[2].metric("ETF API ID", "설정됨" if krx_status.get("etf_api_id_configured") else "미설정")
        krx_payload = {
            "api_key": st.text_input("KRX API Key", type="password", key="developer_krx_api_key") or None,
            "base_url": st.text_input("KRX API Base URL", key="developer_krx_base_url") or None,
            "kospi_api_id": st.text_input("KOSPI API ID", key="developer_krx_kospi_api_id") or None,
            "kosdaq_api_id": st.text_input("KOSDAQ API ID", key="developer_krx_kosdaq_api_id") or None,
            "konex_api_id": st.text_input("KONEX API ID", key="developer_krx_konex_api_id") or None,
            "etf_api_id": st.text_input("ETF API ID", key="developer_krx_etf_api_id") or None,
            "api_key_param": st.text_input("API Key 파라미터명", value="apiKey", key="developer_krx_api_key_param") or None,
            "api_id_param": st.text_input("API ID 파라미터명", value="serviceId", key="developer_krx_api_id_param") or None,
            "base_date_param": st.text_input("기준일 파라미터명", value="basDd", key="developer_krx_base_date_param") or None,
        }
        if st.button("저장", key="developer_krx_save_button"):
            data, error = _developer_request("PUT", "/developer/settings/krx", krx_payload)
            if error:
                st.error(error)
            else:
                st.session_state["clear_developer_krx_form"] = True
                st.rerun()
                st.success("KRX API 설정을 저장했습니다.")
                st.json(data)
        test_cols = st.columns(4)
        for service_name, button_key, col in [
            ("kospi", "developer_krx_kospi_test_button", test_cols[0]),
            ("kosdaq", "developer_krx_kosdaq_test_button", test_cols[1]),
            ("konex", "developer_krx_konex_test_button", test_cols[2]),
            ("etf", "developer_krx_etf_test_button", test_cols[3]),
        ]:
            if col.button(f"{service_name.upper()} 테스트", key=button_key):
                data, error = _developer_request("POST", f"/developer/settings/test/krx/{service_name}", timeout=40.0)
                _render_developer_test_result(data, error)
        if st.button("저장값 삭제", key="developer_krx_delete_button"):
            data, error = _developer_request("DELETE", "/developer/settings/krx")
            st.error(error) if error else st.success("KRX API 저장값을 삭제했습니다.")


def render_data_sync_page() -> None:
    st.subheader("데이터 동기화")
    cols = st.columns(4)
    if cols[0].button("미국 종목 동기화", key="developer_sync_us_button"):
        result, error = sync_us_securities()
        st.error(error) if error else st.json(result)
    if cols[1].button("미국 SEC CIK 보강", key="developer_enrich_us_sec_button"):
        result, error = enrich_us_sec()
        st.error(error) if error else st.json(result)
    if cols[2].button("한국 KRX 동기화", key="developer_sync_kr_button"):
        result, error = sync_kr_securities()
        st.error(error) if error else st.json(result)
    if cols[3].button("종목 별칭 보강", key="developer_security_alias_backfill_button"):
        result, error = backfill_security_aliases_request()
        st.error(error) if error else st.json(result)

    quality, quality_error = fetch_security_data_quality()
    if quality_error:
        st.warning(quality_error)
    elif quality:
        metric_cols = st.columns(4)
        metric_cols[0].metric("미국 주식", quality.get("us_stock", 0))
        metric_cols[1].metric("미국 ETF", quality.get("us_etf", 0))
        metric_cols[2].metric("한국 주식", quality.get("kr_stock", 0))
        metric_cols[3].metric("한국 ETF", quality.get("kr_etf", 0))

    sync_runs, sync_runs_error = fetch_security_sync_runs()
    if sync_runs_error:
        st.warning(sync_runs_error)
    elif sync_runs:
        st.dataframe(sync_runs)


def render_ai_analysis_admin_panel() -> None:
    render_ai_analysis_page()


def render_theme_analysis_admin_panel() -> None:
    st.subheader("테마 분석 관리")
    st.caption("테마 분석 단독 실행과 OpenAI 진단을 위한 개발자용 관리 화면입니다.")
    cols = st.columns(2)
    window_hours = int(
        cols[0].number_input(
            "분석 기간(시간)",
            min_value=1,
            max_value=720,
            value=24,
            step=1,
            key="theme_admin_window_hours",
        )
    )
    max_sources = int(
        cols[1].number_input(
            "테마 분석 근거 최대 건수",
            min_value=3,
            max_value=300,
            value=50,
            step=1,
            key="theme_admin_max_sources",
        )
    )
    if st.button("테마 OpenAI 테스트", key="theme_admin_openai_test_button"):
        result, error = test_theme_openai_request()
        st.error(error) if error else st.json(result)
    action_cols = st.columns(2)
    if action_cols[0].button("단기 테마 분석 단독 실행", key="theme_admin_run_button"):
        result, error = run_theme_analysis_request(window_hours, max_sources, provider="openai")
        st.error(error) if error else st.json(result)
    if action_cols[1].button("Mock 테마 분석 실행", key="theme_admin_mock_run_button"):
        result, error = run_theme_analysis_request(window_hours, max_sources, provider="mock")
        st.error(error) if error else st.json(result)
    st.markdown("---")
    st.subheader("테마 후보 생성 관리")
    candidate_cols = st.columns(5)
    include_weak = candidate_cols[0].checkbox("약한 산업 후보 포함", value=False, key="theme_admin_candidate_include_weak")
    include_watchlist = candidate_cols[1].checkbox("관찰 테마 후보 포함", value=False, key="theme_admin_candidate_include_watchlist")
    include_risky_etfs = candidate_cols[2].checkbox("레버리지/인버스 ETF 포함", value=True, key="theme_admin_candidate_include_risky_etf")
    max_stock = int(candidate_cols[3].number_input("테마별 최대 주식 후보", min_value=1, max_value=100, value=15, step=1, key="theme_admin_max_stock_candidates"))
    max_etf = int(candidate_cols[4].number_input("테마별 최대 ETF 후보", min_value=1, max_value=100, value=20, step=1, key="theme_admin_max_etf_candidates"))
    if st.button("종목·ETF 후보 생성 단독 실행", key="theme_admin_candidate_run_button"):
        result, error = run_theme_candidate_request(
            include_weak_industry_candidates=include_weak,
            include_watchlist_themes=include_watchlist,
            include_leveraged_inverse_etfs=include_risky_etfs,
            max_stock_candidates_per_theme=max_stock,
            max_etf_candidates_per_theme=max_etf,
        )
        st.error(error) if error else st.json(result)
    st.markdown("---")
    st.subheader("추천 엔진 관리")
    rec_cols = st.columns(4)
    max_stocks = int(rec_cols[0].number_input("테마별 최종 주식 후보", min_value=1, max_value=10, value=3, step=1, key="recommend_admin_max_stocks"))
    max_etfs = int(rec_cols[1].number_input("테마별 최종 ETF 후보", min_value=1, max_value=10, value=2, step=1, key="recommend_admin_max_etfs"))
    include_risky = rec_cols[2].checkbox("레버리지/인버스 ETF 포함", value=False, key="recommend_admin_include_risky")
    diversify = rec_cols[3].checkbox("국가 분산 보정", value=True, key="recommend_admin_diversify")
    if st.button("추천 엔진 단독 실행", key="recommend_admin_run_button"):
        result, error = run_recommendations_request(
            {
                "max_stocks_per_theme": max_stocks,
                "max_etfs_per_theme": max_etfs,
                "include_leveraged_inverse_etfs": include_risky,
                "diversify_country": diversify,
            }
        )
        st.error(error) if error else st.json(result)


def render_mock_investment_page() -> None:
    st.title("모의투자")
    st.info("모의투자 화면은 아직 준비 중입니다. 현재 단계에서는 실제 매매 기능을 제공하지 않습니다.")


def render_developer_tools_page() -> None:
    st.title("개발자 도구")
    selected_tab = st.tabs(["API 설정", "데이터 동기화", "종목 기준정보", "AI 분석 관리", "테마 분석 관리", "Error Center"])
    with selected_tab[0]:
        render_api_settings_tab()
    with selected_tab[1]:
        render_data_sync_page()
    with selected_tab[2]:
        render_security_master_page()
    with selected_tab[3]:
        render_ai_analysis_admin_panel()
    with selected_tab[4]:
        render_theme_analysis_admin_panel()
    with selected_tab[5]:
        render_error_center_page()


def main() -> None:
    st.set_page_config(page_title="Stock AI Lab", layout="wide")

    selected_page = st.selectbox(
        "페이지 선택",
        ["홈", "뉴스", "시장 분석", "모의투자", "개발자 도구"],
        index=0,
        key="main_page_select",
    )

    if selected_page == "홈":
        render_home_page()
    elif selected_page == "뉴스":
        render_news_page()
    elif selected_page == "시장 분석":
        render_theme_analysis_page()
    elif selected_page == "모의투자":
        render_mock_investment_page()
    elif selected_page == "개발자 도구":
        render_developer_tools_page()


if __name__ == "__main__":
    main()


