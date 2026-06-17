from datetime import datetime, timedelta, timezone

class MockNewsProvider:
    name = "mock"

    async def search(self, query: str, display: int, sort: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        items = []
        for i in range(3):
            items.append(
                {
                    "title": f"[테스트] {query} 관련 산업 동향 발표 #{i+1}",
                    "description": f"{query}에 대한 테스트 설명 #{i+1}",
                    "link": f"https://example.com/{query.replace(' ', '_')}/{i+1}",
                    "original_link": f"https://original.example.com/{query.replace(' ', '_')}/{i+1}" if i == 0 else None,
                    "published_at": (now - timedelta(hours=i)).isoformat(),
                    "publisher": f"MockNews{i+1}",
                    "raw_data": {"provider": "mock", "idx": i+1},
                }
            )
        return items
