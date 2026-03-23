# PLUGINS-QUICK.md - 플러그인 빠른 참조

## 플러그인 요약

| 플러그인 | 용도 | 주요 스킬/기능 |
|---------|------|--------------|
| **Superpowers** | 개발 워크플로우 강화 | brainstorming, writing-plans, subagent-driven-development, TDD, debugging, verification |
| **Context7** | 라이브러리 최신 문서 조회 | resolve-library-id → query-docs (질문당 **3회 제한**) |
| **Playwright** | 브라우저 자동화, E2E 테스트 | navigate, snapshot, click, fill_form, screenshot, evaluate |
| **Feature Dev** | 가이드 기반 기능 개발 | feature-dev (전체), code-architect, code-explorer, code-reviewer |
| **Frontend Design** | 고품질 프론트엔드 UI 생성 | 와이어프레임 → 상세 UI, 반응형 레이아웃 |

## 역할별 플러그인 매핑

| 역할 | 주요 플러그인 |
|------|-------------|
| **Orchestrator** | superpowers (brainstorming, writing-plans, dispatching, subagent) |
| **Bootstrapper** | context7, feature-dev:code-explorer |
| **Designer** | frontend-design, playwright |
| **Frontend** | context7, frontend-design, playwright, feature-dev:feature-dev |
| **Backend** | context7, feature-dev:feature-dev, feature-dev:code-architect |
| **Performance** | context7, playwright (Web Vitals) |
| **Accessibility** | playwright (a11y snapshot), frontend-design |
| **Security** | feature-dev:code-reviewer, superpowers:verification |
| **DevOps** | context7 |
| **QA** | playwright, superpowers (TDD, debugging, verification), feature-dev:code-reviewer |

## Phase별 트리거 요약

| Phase | 자동 트리거 | 조건 |
|-------|-----------|------|
| **1. 분석** | using-superpowers | 대화 시작 시 |
| | brainstorming | 복잡한 요청 (키워드 3개+, 복수 도메인) |
| | writing-plans | Task 5개+, 복잡도 70점+ |
| | dispatching / subagent | 독립 작업 2개+ |
| **2. 설정** | context7 | 프레임워크/라이브러리 설정 시 |
| **3. 개발** | subagent-driven-development | 독립 태스크 2개+ |
| | feature-dev, context7 | 기능 구현, API 조회 |
| | test-driven-development | 새 기능 구현 전 |
| | code-reviewer | Security 병렬 검토 |
| **4. 검증** | playwright | E2E 테스트 실행 |
| | systematic-debugging | 버그 발견 시 |
| | verification-before-completion | 최종 검증 |
| **5. 개선** | 역할별 플러그인 | ralph-loop 개선 요청에 따라 |

## 핵심 규칙

1. **Context7**: 질문당 최대 **3회** 호출 (resolve-library-id → query-docs 순서)
2. **동시 사용 금지**: brainstorming + writing-plans, TDD + debugging, dispatching + subagent
3. **순서 의존**: using-superpowers → 다른 superpowers, brainstorming → writing-plans → executing-plans
4. **폴백**: Context7 실패 → `frameworks/` 참조, Playwright 미설치 → browser_install 실행

## 수동 호출

```bash
"@context7로 Supabase RLS 정책 조회해줘"
"@playwright로 로그인 페이지 E2E 테스트해줘"
"@frontend-design으로 대시보드 UI 만들어줘"
"@feature-dev로 사용자 인증 기능 개발해줘"
"@superpowers:brainstorming으로 접근법 탐색해줘"
```

> **전체 가이드**: 상세 트리거 조건, 복잡도 점수, 권한 JSON, 충돌 규칙 → `PLUGINS.md` 참조
