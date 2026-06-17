from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.market_theme import MarketTheme
from app.models.news_analysis import NewsAnalysis
from app.models.security import Security
from app.models.theme_analysis_run import ThemeAnalysisRun
from app.models.theme_news_link import ThemeNewsLink
from app.schemas.theme_analysis import SelectedThemeCandidate, ThemeEvidence
from app.services.theme_candidate_service import generate_theme_candidates
from app.services.theme_actionability_service import score_news_selection, score_theme_actionability
from app.services.theme_tag_enrichment_service import build_tag_confidence, enrich_theme_tags
from app.utils.security_names import generate_security_key, normalize_company_name


def test_theme_prompt_contains_separated_tag_instructions() -> None:
    from app.providers.ai.openai_news_analyzer import SYSTEM_PROMPT as NEWS_PROMPT
    from app.providers.ai.openai_theme_analyzer import SYSTEM_PROMPT as THEME_PROMPT

    assert "issue_tags" in THEME_PROMPT
    assert "direct_impact_industries" in THEME_PROMPT
    assert "entity_business_industries" in THEME_PROMPT
    assert "candidate_search_tags" in THEME_PROMPT
    assert "listed stock or ETF prices" in NEWS_PROMPT
    assert "price impact and investable linkage" in THEME_PROMPT
    assert "candidate_themes에는 단순 회사명만 넣지 말고" in NEWS_PROMPT


def test_theme_openai_schema_uses_simple_tag_types() -> None:
    from app.schemas.theme_analysis import SelectedThemeCandidate, ThemeSelectionOutput

    selected_schema = SelectedThemeCandidate.model_json_schema()
    theme_schema = ThemeSelectionOutput.model_json_schema()

    assert "tag_confidence" not in selected_schema.get("properties", {})
    assert selected_schema["properties"]["issue_tags"]["items"]["type"] == "string"
    assert selected_schema["properties"]["direct_impact_industries"]["items"]["type"] == "string"
    assert selected_schema["properties"]["market_theme_tags"]["items"]["type"] == "string"
    assert selected_schema["properties"]["candidate_search_tags"]["items"]["type"] == "string"
    assert "EntityBusinessIndustryItem" in theme_schema.get("$defs", {})
    entity_schema = theme_schema["$defs"]["EntityBusinessIndustryItem"]
    assert set(entity_schema["properties"]) == {"entity", "industries", "confidence", "reason"}
    assert entity_schema["properties"]["industries"]["items"]["type"] == "string"


def test_actionability_scores_investable_price_impact_news() -> None:
    oil = score_theme_actionability(
        theme_name="중동 공급 차질과 원유 가격 변동",
        theme_summary="원유 공급 차질로 정유와 에너지 업종 비용과 마진 영향이 예상됩니다.",
        why_now="유가 변동성이 커졌습니다.",
        impact_direction="mixed",
        issue_tags=["공급 차질", "유가"],
        direct_impact_industries=["에너지", "정유"],
        market_theme_tags=["원유"],
        candidate_search_tags=["에너지", "정유", "oil"],
        related_companies=[],
        evidence_count=3,
        source_scores=[{"price_impact_score": 0.8, "investable_link_score": 0.7}],
    )
    semiconductor = score_theme_actionability(
        theme_name="AI 투자와 반도체 수출 확대",
        theme_summary="AI 데이터센터 투자가 반도체 수출과 장비 수요에 영향을 줄 수 있습니다.",
        why_now="대형 클라우드 기업의 투자가 확대됐습니다.",
        impact_direction="positive",
        issue_tags=["AI 투자"],
        direct_impact_industries=["반도체", "데이터센터"],
        market_theme_tags=["AI 인프라"],
        candidate_search_tags=["반도체", "AI", "데이터센터"],
        related_companies=["삼성전자"],
        evidence_count=3,
        source_scores=[{"price_impact_score": 0.75, "investable_link_score": 0.8}],
    )

    assert oil["theme_bucket"] == "investable_opportunity"
    assert semiconductor["theme_bucket"] == "investable_opportunity"


def test_actionability_separates_low_actionability_finance_news() -> None:
    allocation = score_theme_actionability(
        theme_name="공모주 배정 실패 논란",
        theme_summary="투자자 배정 논란과 시장 공정성 이슈가 제기됐습니다.",
        why_now="공모주 청약 과정에서 불만이 커졌습니다.",
        impact_direction="neutral",
        issue_tags=["공모주 배정 실패", "시장 공정성"],
        direct_impact_industries=["금융"],
        market_theme_tags=["IPO"],
        candidate_search_tags=["증권"],
        related_companies=[],
        evidence_count=2,
        source_scores=[{"price_impact_score": 0.2, "investable_link_score": 0.2}],
    )
    enforcement = score_theme_actionability(
        theme_name="불공정거래 단속 강화",
        theme_summary="금감원 조사와 불공정거래 단속이 강화됐습니다.",
        why_now="시장 감시 정책이 발표됐습니다.",
        impact_direction="negative",
        issue_tags=["불공정거래", "단속", "조사"],
        direct_impact_industries=["금융"],
        market_theme_tags=["규제 리스크"],
        candidate_search_tags=["증권"],
        related_companies=[],
        evidence_count=2,
        source_scores=[{"price_impact_score": 0.15, "investable_link_score": 0.15}],
    )
    private_company = score_theme_actionability(
        theme_name="비상장 우주 기업 IPO 기대",
        theme_summary="비상장 기업 중심 뉴스로 국내 상장 후보 연결이 약합니다.",
        why_now="IPO 기대가 커졌습니다.",
        impact_direction="neutral",
        issue_tags=["IPO"],
        direct_impact_industries=[],
        market_theme_tags=["우주항공"],
        candidate_search_tags=[],
        related_companies=["SpaceX"],
        evidence_count=2,
        source_scores=[{"price_impact_score": 0.25, "investable_link_score": 0.1}],
    )

    assert allocation["theme_bucket"] in {"risk_alert", "watchlist"}
    assert enforcement["theme_bucket"] == "risk_alert"
    assert private_company["is_investable_theme"] is False


def test_news_selection_score_weights_price_and_investable_link() -> None:
    investable = score_news_selection(
        {
            "title": "반도체 수출 증가와 AI 데이터센터 투자 확대",
            "summary": "매출과 수주 증가가 반도체 업종에 영향을 줄 수 있습니다.",
            "market_relevance_score": 0.7,
            "impact_direction": "positive",
            "event_type": "investment",
            "candidate_themes": ["AI 인프라"],
            "companies": ["삼성전자"],
        }
    )
    controversy = score_news_selection(
        {
            "title": "공모주 배정 실패와 시장 공정성 논란",
            "summary": "투자자 배정 논란이 이어졌습니다.",
            "market_relevance_score": 0.9,
            "impact_direction": "neutral",
            "event_type": "policy",
            "candidate_themes": ["IPO"],
            "companies": [],
        }
    )

    assert investable["price_impact_score"] > controversy["price_impact_score"]
    assert investable["investable_link_score"] > controversy["investable_link_score"]
    assert investable["final_news_selection_score"] > controversy["final_news_selection_score"]


def test_enrichment_separates_issue_industry_entity_and_search_tags() -> None:
    theme = SelectedThemeCandidate(
        theme_name="해외 비상장 투자와 공모주 시장 관심",
        theme_summary="스페이스X IPO 가능성과 증권사 투자은행 수익 기대가 함께 언급됐습니다.",
        why_now="공모주와 IPO 뉴스가 단기 관심을 만들었습니다.",
        impact_direction="positive",
        confidence_score=0.8,
        time_horizon="short_term",
        related_industries=["증권"],
        related_companies=["스페이스X", "미래에셋증권"],
        evidence=[ThemeEvidence(news_analysis_id=1, relevance_score=0.9, reason="IPO 근거")],
        risk_factors=[],
    )
    enriched = enrich_theme_tags(
        theme,
        [
            {
                "news_analysis_id": 1,
                "title": "스페이스X IPO 기대에 증권사 공모주 시장 관심",
                "summary": "비상장 기업 투자와 공모주 시장이 함께 부각됐습니다.",
                "candidate_themes": ["우주항공", "공모주"],
                "companies": ["스페이스X", "미래에셋증권"],
            }
        ],
    )

    assert "IPO" in enriched.issue_tags
    assert "증권" in enriched.direct_impact_industries
    assert "우주항공" in enriched.market_theme_tags
    entity_map = {row.entity: row.industries for row in enriched.entity_business_industries}
    assert "항공우주" in entity_map["스페이스X"]
    assert "금융투자" in entity_map["미래에셋증권"]
    assert {"space", "satellite", "IPO"} & set(enriched.candidate_search_tags)
    assert build_tag_confidence(enriched)


def test_candidate_search_tags_are_used_for_etf_candidates(sqlite_session_local) -> None:
    now = datetime.now(timezone.utc)
    with sqlite_session_local() as db:
        run = ThemeAnalysisRun(
            run_id="THEME-TAGS",
            model_name="mock",
            prompt_version="theme-v1",
            window_start=now - timedelta(hours=1),
            window_end=now,
            requested_source_count=3,
            selected_source_count=3,
            selected_theme_count=1,
            status="completed",
            started_at=now,
            completed_at=now,
        )
        db.add(run)
        db.commit()
        theme = MarketTheme(
            theme_run_id=run.id,
            rank=1,
            theme_name="우주항공 투자 관심",
            normalized_theme_name="우주항공 투자 관심",
            theme_summary="우주항공과 위성 관련 관심이 높아졌습니다.",
            why_now="IPO와 위성 발사 이슈가 함께 나타났습니다.",
            impact_direction="positive",
            confidence_score=0.8,
            calculated_score=0.8,
            actionability_score=0.85,
            price_impact_score=0.8,
            investable_link_score=0.8,
            is_investable_theme=True,
            theme_bucket="investable_opportunity",
            time_horizon="short_term",
            related_industries_json=[],
            related_companies_json=["스페이스X"],
            risk_factors_json=[],
            issue_tags_json=["IPO"],
            direct_impact_industries_json=["증권"],
            entity_business_industries_json=[{"entity": "스페이스X", "industries": ["항공우주", "위성"], "confidence": 0.85}],
            market_theme_tags_json=["우주항공"],
            candidate_search_tags_json=["space", "satellite", "항공우주"],
            tag_confidence_json={"space": 0.6},
            evidence_count=2,
            source_publisher_count=2,
        )
        db.add(theme)
        db.commit()
        analysis = NewsAnalysis(
            news_article_id=1,
            analysis_run_id=1,
            model_name="mock",
            prompt_version="news-v1",
            status="completed",
            summary="summary",
            event_type="ipo",
            impact_direction="positive",
            sentiment_score=0.5,
            importance_score=0.8,
            novelty_score=0.7,
            market_relevance_score=0.8,
            confidence_score=0.8,
            time_horizon="short_term",
            candidate_themes_json=["우주항공"],
            companies_json=[{"company_name": "스페이스X"}],
            evidence_points_json=[],
            risk_factors_json=[],
            is_investment_relevant=True,
        )
        db.add(analysis)
        db.commit()
        db.add(ThemeNewsLink(market_theme_id=theme.id, news_analysis_id=analysis.id, relevance_score=0.9, evidence_reason="tag"))
        security = Security(
            security_key=generate_security_key("KR", "XKRX", "SPACE"),
            country_code="KR",
            asset_type="etf",
            exchange_code="XKRX",
            exchange_name="XKRX",
            ticker="SPACE",
            name="우주항공 ETF",
            english_name="Korea Space Satellite ETF",
            normalized_name=normalize_company_name("우주항공 ETF"),
            issuer_name="Test",
            sector="우주항공",
            industry="위성",
            currency="KRW",
            is_active=True,
            is_recommendation_eligible=True,
            source="test",
        )
        db.add(security)
        db.commit()

        result = generate_theme_candidates(db, theme_run_id=run.id)

    assert result["status"] == "completed"
    assert result["etf_candidate_count"] >= 1


def test_negative_theme_skips_candidate_generation(sqlite_session_local) -> None:
    now = datetime.now(timezone.utc)
    with sqlite_session_local() as db:
        run = ThemeAnalysisRun(
            run_id="THEME-NEG",
            model_name="mock",
            prompt_version="theme-v1",
            window_start=now - timedelta(hours=1),
            window_end=now,
            requested_source_count=3,
            selected_source_count=3,
            selected_theme_count=1,
            status="completed",
            started_at=now,
            completed_at=now,
        )
        db.add(run)
        db.commit()
        db.add(
            MarketTheme(
                theme_run_id=run.id,
                rank=1,
                theme_name="중동 리스크와 에너지 가격 변동",
                normalized_theme_name="중동 리스크와 에너지 가격 변동",
                theme_summary="지정학 리스크가 커졌습니다.",
                why_now="위험 요인이 부각됐습니다.",
                impact_direction="negative",
                confidence_score=0.8,
                calculated_score=0.8,
                time_horizon="short_term",
                related_industries_json=["에너지"],
                related_companies_json=[],
                risk_factors_json=["유가 변동"],
                candidate_search_tags_json=["에너지"],
                evidence_count=2,
                source_publisher_count=2,
            )
        )
        db.commit()

        result = generate_theme_candidates(db, theme_run_id=run.id)

    assert result["stock_candidate_count"] == 0
    assert result["etf_candidate_count"] == 0


def test_theme_tag_backfill_api_runs_without_openai(client, sqlite_session_local) -> None:
    now = datetime.now(timezone.utc)
    with sqlite_session_local() as db:
        run = ThemeAnalysisRun(
            run_id="THEME-BACKFILL",
            model_name="mock",
            prompt_version="theme-v1",
            window_start=now - timedelta(hours=1),
            window_end=now,
            requested_source_count=3,
            selected_source_count=3,
            selected_theme_count=1,
            status="completed",
            started_at=now,
            completed_at=now,
        )
        db.add(run)
        db.commit()
        db.add(
            MarketTheme(
                theme_run_id=run.id,
                rank=1,
                theme_name="AI 반도체 투자 확대",
                normalized_theme_name="ai 반도체 투자 확대",
                theme_summary="AI 반도체와 데이터센터 투자 관심이 커졌습니다.",
                why_now="GPU 수요가 증가했습니다.",
                impact_direction="positive",
                confidence_score=0.8,
                calculated_score=0.8,
                time_horizon="short_term",
                related_industries_json=["반도체"],
                related_companies_json=["엔비디아"],
                risk_factors_json=[],
                evidence_count=2,
                source_publisher_count=2,
            )
        )
        db.commit()

    response = client.post("/themes/tags/backfill")

    assert response.status_code == 200
    data = response.json()
    assert data["scanned_count"] == 1
    assert data["updated_count"] == 1
    with sqlite_session_local() as db:
        theme = db.query(MarketTheme).one()
        assert theme.issue_tags_json or theme.direct_impact_industries_json
        assert theme.candidate_search_tags_json
        assert theme.tag_confidence_json
