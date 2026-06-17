###### \# Korea Stock News Research Assistant

###### 

###### 한국 주식 뉴스 기사를 수집하고, 관련 종목 및 ETF 후보를 매칭하며, AI 기반 리서치 자동화를 돕는 오픈소스 프로젝트입니다.

###### 

###### 이 프로젝트는 특정 종목 매수를 유도하는 투자 조언 서비스가 아니라, 한국어 금융 뉴스와 종목 데이터를 연결하는 리서치 자동화 도구입니다.

###### 

###### \## Features

###### 

###### \- 한국어 뉴스 기사 수집

###### &#x20;   

###### \- 뉴스 중복 제거 및 정규화

###### &#x20;   

###### \- 종목명 및 별칭 기반 관련 종목 매칭

###### &#x20;   

###### \- ETF 후보 연결

###### &#x20;   

###### \- AI 기반 뉴스 요약 및 테마 분류

###### &#x20;   

###### \- FastAPI 기반 백엔드 API

###### &#x20;   

###### \- Streamlit 기반 대시보드

###### &#x20;   

###### \- pytest 기반 테스트 코드

###### &#x20;   

###### 

###### \## Tech Stack

###### 

###### \- Python

###### &#x20;   

###### \- FastAPI

###### &#x20;   

###### \- Streamlit

###### &#x20;   

###### \- SQLite

###### &#x20;   

###### \- OpenAI API

###### &#x20;   

###### \- pytest

###### &#x20;   

###### 

###### \## Project Purpose

###### 

###### 한국어 금융 뉴스는 종목명, 기업 별칭, ETF, 산업 테마가 복잡하게 연결되어 있습니다.

###### 

###### 이 프로젝트는 뉴스 기사와 관련 종목을 자동으로 연결하고, AI 분석 워크플로를 공개된 형태로 실험할 수 있도록 만드는 것을 목표로 합니다.  

###### 학생 개발자, 개인 연구자, 오픈소스 기여자가 한국어 금융 뉴스 분석 파이프라인을 학습하고 확장할 수 있는 기반 템플릿을 제공합니다.

###### 

###### \## Installation

###### 

###### ```bash

###### git clone https://github.com/wogjs0925/korea-stock-news-research-assistant.git

###### cd korea-stock-news-research-assistant

###### python -m venv .venv

###### ```

###### 

###### Windows PowerShell:

###### 

###### ```powershell

###### .venv\\Scripts\\activate

###### pip install -r requirements.txt

###### ```

###### 

###### \## Environment Variables

###### 

###### `.env.example` 파일을 참고하여 `.env` 파일을 생성하세요.

###### 

###### ```env

###### OPENAI\_API\_KEY=your\_openai\_api\_key\_here

###### KRX\_API\_KEY=your\_krx\_api\_key\_here

###### NAVER\_CLIENT\_ID=your\_naver\_client\_id\_here

###### NAVER\_CLIENT\_SECRET=your\_naver\_client\_secret\_here

###### DATABASE\_URL=sqlite:///./app.db

###### ```

###### 

###### 실제 API 키는 절대 GitHub에 업로드하지 마세요.

###### 

###### \## Run FastAPI Server

###### 

###### ```bash

###### uvicorn app.backend.main:app --reload

###### ```

###### 

###### \## Run Streamlit Dashboard

###### 

###### ```bash

###### streamlit run app/dashboard.py

###### ```

###### 

###### \## Run Tests

###### 

###### ```bash

###### pytest

###### ```

###### 

###### \## Repository Structure

###### 

###### ```text

###### app/

###### ├─ backend/

###### ├─ models/

###### ├─ providers/

###### ├─ repositories/

###### ├─ schemas/

###### ├─ services/

###### ├─ utils/

###### └─ dashboard.py

###### 

###### tests/

###### requirements.txt

###### README.md

###### LICENSE

###### .env.example

###### ```

###### 

###### \## Roadmap

###### 

###### \- 뉴스 수집 안정화

###### &#x20;   

###### \- 종목 별칭 매칭 정확도 개선

###### &#x20;   

###### \- ETF 매칭 로직 보강

###### &#x20;   

###### \- AI 분석 프롬프트 개선

###### &#x20;   

###### \- 샘플 데이터 추가

###### &#x20;   

###### \- 테스트 커버리지 확대

###### &#x20;   

###### \- 문서화 보강

###### &#x20;   

###### \- GitHub Issues 기반 기여 가이드 정리

###### &#x20;   

###### 

###### \## Disclaimer

###### 

###### 이 프로젝트는 투자 조언을 제공하지 않습니다.  

###### 모든 결과는 학습, 개발, 리서치 자동화를 위한 참고용입니다.  

###### 투자 판단과 책임은 사용자 본인에게 있습니다.

###### 

###### \## License

###### 

###### MIT License

