# PLUGINS.md - 플러그인 통합 가이드

AI 베테랑 개발팀에서 활용하는 외부 플러그인 통합 가이드입니다.

---

## 사용 가능한 플러그인

### 1. Superpowers

개발 워크플로우를 강화하는 스킬 모음입니다.

| 스킬 | 용도 | 자동 트리거 조건 |
|-----|------|----------------|
| `brainstorming` | 요구사항 탐색, 접근법 도출 | 복잡한 요청, 모호한 요구사항 |
| `writing-plans` | 상세 실행 계획 수립 | Task 5개 이상, 복잡도 High |
| `executing-plans` | 단계별 계획 실행 | 계획 수립 후 실행 단계 |
| `dispatching-parallel-agents` | 병렬 에이전트 실행 | 독립적 작업 2개 이상 |
| `subagent-driven-development` | 현재 세션 내 서브에이전트 병렬 개발 | 실행 계획의 독립 태스크 병렬 수행 |
| `test-driven-development` | TDD 워크플로우 | Phase 3: 새 기능 구현 전 테스트 선행 작성 |
| `systematic-debugging` | 체계적 디버깅 | 버그 발견 시 |
| `verification-before-completion` | 완료 전 검증 | Phase 4 최종 검증 |
| `requesting-code-review` | 코드 리뷰 요청 | 코드 완료 후 |
| `receiving-code-review` | 코드 리뷰 응답 | 리뷰 피드백 수신 시 |
| `using-superpowers` | 대화 시작 시 스킬 활용법 확립 | 모든 대화 시작 시점 |
| `writing-skills` | 새 스킬 작성 및 검증 | 커스텀 스킬 생성/수정 시 |

### 2. Context7 MCP Server

라이브러리/프레임워크의 최신 문서를 실시간 조회합니다.

**사용 방법:**
```
1. resolve-library-id: 라이브러리 ID 조회
2. query-docs: 문서 쿼리
```

**주요 라이브러리 ID:**

| 라이브러리 | ID | 조회 예시 |
|-----------|-----|----------|
| Nuxt.js | `/nuxt/nuxt` | "nuxt.config.ts modules setup" |
| Next.js | `/vercel/next.js` | "app router server components" |
| Supabase | `/supabase/supabase` | "RLS policies authentication" |
| TailwindCSS | `/tailwindlabs/tailwindcss` | "utility classes responsive" |
| Prisma | `/prisma/prisma` | "schema model relations" |
| React | `/facebook/react` | "hooks useEffect useState" |

### 3. Playwright MCP Server

브라우저 자동화 및 E2E 테스트를 지원합니다.

**주요 기능:**

| 기능 | 용도 | 활용 역할 |
|-----|------|----------|
| `browser_navigate` | 페이지 이동 | 전체 |
| `browser_click` | 요소 클릭 | QA, Designer |
| `browser_fill_form` | 폼 입력 | QA |
| `browser_type` | 텍스트 입력 | QA |
| `browser_snapshot` | 접근성 스냅샷 (요소 참조용) | Accessibility, QA |
| `browser_take_screenshot` | 스크린샷 캡처 | Designer, QA |
| `browser_evaluate` | JavaScript 실행 | Performance, QA |
| `browser_console_messages` | 콘솔 메시지 조회 | QA (에러 감지) |
| `browser_network_requests` | 네트워크 요청 조회 | Performance, QA |
| `browser_press_key` | 키보드 입력 | Accessibility (키보드 내비게이션) |
| `browser_select_option` | 드롭다운 선택 | QA |
| `browser_hover` | 요소 호버 | QA, Accessibility |
| `browser_wait_for` | 텍스트/상태 대기 | QA (비동기 테스트) |
| `browser_tabs` | 탭 관리 (열기/닫기/전환) | QA (멀티탭 테스트) |
| `browser_resize` | 브라우저 크기 조절 | Designer (반응형 검증) |

**E2E 테스트 흐름 예시:**
```
1. browser_navigate → 테스트 페이지 이동
2. browser_snapshot → 페이지 구조 파악
3. browser_fill_form → 폼 입력
4. browser_click → 제출 버튼 클릭
5. browser_snapshot → 결과 확인
6. browser_take_screenshot → 증거 캡처
```

### 4. Feature Dev

가이드 기반 기능 개발을 지원합니다.

| 스킬 | 용도 | 활용 시점 |
|-----|------|----------|
| `feature-dev` | 가이드 기반 기능 개발 (전체 워크플로우) | 새 기능 구현 시 코드베이스 이해 → 아키텍처 설계 → 구현 |
| `code-architect` | 아키텍처 설계 가이드 | 기존 패턴 분석 후 구현 설계도 제공 |
| `code-explorer` | 코드베이스 심층 탐색 | 실행 경로 추적, 아키텍처 레이어 매핑 |
| `code-reviewer` | 코드 리뷰 (보안, 품질, 컨벤션) | 주요 구현 완료 후 검증 |

**feature-dev vs 개별 스킬:**
```
feature-dev         → 기능 개발 전체 라이프사이클 (탐색 + 설계 + 구현 가이드)
code-architect      → 아키텍처 설계만 (기존 코드 패턴 분석 → 설계도)
code-explorer       → 코드 탐색만 (의존성 맵핑, 실행 경로 분석)
code-reviewer       → 코드 리뷰만 (버그, 보안, 품질)
```

### 5. Frontend Design

고품질 프론트엔드 인터페이스를 생성합니다.

**활용 시점:**
- 와이어프레임에서 상세 UI로 전환 시
- 컴포넌트 비주얼 설계 시
- 반응형 레이아웃 설계 시

---

## 역할별 플러그인 매핑

| 역할 | 주요 플러그인 | 활용 목적 |
|------|-------------|----------|
| **Orchestrator** | superpowers (brainstorming, writing-plans, dispatching-parallel-agents, subagent-driven-development) | 복잡한 요청 분석, 계획 수립, 병렬 작업 실행 |
| **Bootstrapper** | context7, feature-dev:code-explorer | 프레임워크 최신 문서 조회, 기존 코드 구조 파악 |
| **Product Designer** | frontend-design, playwright | 고품질 UI 설계, 디자인 검증 |
| **Frontend Architect** | context7, frontend-design, playwright, feature-dev:feature-dev | 라이브러리 문서, UI 구현, E2E, 기능 개발 가이드 |
| **Backend Architect** | context7, feature-dev:feature-dev, feature-dev:code-architect | API 문서, 기능 개발 가이드, 아키텍처 설계 |
| **Performance Architect** | context7, playwright | 성능 라이브러리 문서, Web Vitals 측정 |
| **Accessibility Architect** | playwright, frontend-design | 접근성 감사, 접근성 고려 UI |
| **Security Engineer** | feature-dev:code-reviewer, superpowers:verification | 보안 코드 리뷰, 검증 |
| **DevOps Engineer** | context7 | CI/CD 도구 문서 조회 |
| **QA Engineer** | playwright, superpowers (TDD, debugging, verification), feature-dev:code-reviewer | Phase 3: TDD 가이드 / Phase 4: E2E, 회귀 테스트, 디버깅, 검증 |

---

## Phase별 자동 트리거

### Phase 1: 분석 (Orchestrator)

```
요청 수신
    │
    ├── 대화 시작 시
    │   └── @superpowers:using-superpowers
    │       - 사용 가능한 스킬 확인
    │       - 적절한 스킬 활용 전략 수립
    │
    ├── 복잡한 요청 감지
    │   └── @superpowers:brainstorming
    │       - 요구사항 탐색
    │       - 가능한 접근법 도출
    │
    ├── 멀티스텝 작업 필요 (Task 5개+)
    │   └── @superpowers:writing-plans
    │       - 상세 실행 계획 수립
    │
    └── 독립적 작업 2개 이상
        ├── @superpowers:dispatching-parallel-agents
        │   - 병렬 에이전트 실행 (독립 세션)
        └── @superpowers:subagent-driven-development
            - 현재 세션 내 서브에이전트 병렬 실행
```

### Phase 2: 초기 설정 (Bootstrapper)

```
프로젝트 초기화
    │
    └── 프레임워크/라이브러리 설정
        └── @context7
            - resolve-library-id로 ID 조회
            - query-docs로 최신 설정 조회
            - 버전별 차이점 확인
```

### Phase 3: 병렬 개발

```
Orchestrator 실행 전략 결정
    ├── 독립 태스크 2개+ → @superpowers:subagent-driven-development
    │   - 서브에이전트가 Frontend/Backend 병렬 수행
    └── 순차 실행 → 기본 Phase 3 흐름

Designer 활성화
    ├── UI 설계 → @frontend-design
    └── 디자인 검증 → @playwright:browser_snapshot

Frontend 활성화
    ├── 기능 개발 가이드 → @feature-dev:feature-dev
    ├── 라이브러리 API → @context7
    ├── UI 구현 → @frontend-design
    └── E2E 테스트 → @playwright

Backend 활성화
    ├── 기능 개발 가이드 → @feature-dev:feature-dev
    ├── API 아키텍처 → @feature-dev:code-architect
    └── 라이브러리 사용 → @context7

Performance 활성화
    ├── 성능 라이브러리 → @context7
    └── Web Vitals 측정 → @playwright:browser_evaluate

Accessibility 활성화
    ├── 접근성 트리 검증 → @playwright:browser_snapshot
    └── 접근성 고려 UI → @frontend-design

Security 활성화 (병렬)
    └── 코드 리뷰 → @feature-dev:code-reviewer

QA 활성화 (TDD 가이드)
    ├── 새 기능 구현 전 테스트 선행 작성 → @superpowers:test-driven-development
    └── Frontend/Backend에 테스트 스펙 제공
```

### Phase 4: 검증 및 완료

```
QA 활성화
    ├── 전체 테스트 스위트 실행
    ├── 회귀 테스트 실행
    ├── E2E 테스트 → @playwright
    ├── 커버리지 분석 및 리포팅
    └── 버그 발견 → @superpowers:systematic-debugging

Security 최종 검증
    └── @superpowers:verification-before-completion

Orchestrator 완료
    └── @superpowers:requesting-code-review
```

### Phase 5: 지속적 개선 (ralph-loop)

> ⚠️ **반복 제한 필수**: `/ralph-loop` 실행 시 `--max-iterations`를 반드시 포함하세요.
> 미설정 시 무제한 반복으로 비용이 폭증할 수 있습니다. 기본 권장값: **10회**.

```
/ralph-loop 자동 실행 (--max-iterations 10 기본 포함)
    │
    │  예시: /ralph-loop "UI 개선해줘" --max-iterations 10
    │  예시: /ralph-loop "성능 최적화" --max-iterations 10 --completion-promise 'All optimized'
    │
    ├── 역할 관점 개선 요청
    │   ├── "UI/UX 개선해줘" → Designer 활성화
    │   │   └── @frontend-design, @playwright
    │   │
    │   ├── "성능 최적화해줘" → Performance Architect 활성화
    │   │   └── @context7, @playwright (Web Vitals)
    │   │
    │   ├── "접근성 검토해줘" → Accessibility Architect 활성화
    │   │   └── @playwright:browser_snapshot
    │   │
    │   ├── "보안 점검해줘" → Security Engineer 활성화
    │   │   └── @feature-dev:code-reviewer
    │   │
    │   └── "테스트 보강해줘" → QA Engineer 활성화
    │       └── @superpowers:test-driven-development
    │
    ├── 반복 개선 (--max-iterations 범위 내)
    │
    ├── 자동 종료: max-iterations 도달 또는 completion-promise 충족
    └── 수동 조기 종료: /cancel-ralph
```

**ralph-loop 안전 설정:**

| 옵션 | 권장값 | 설명 |
|------|--------|------|
| `--max-iterations` | **10** (필수 권장) | 최대 반복 횟수, 미설정 시 무제한 (위험) |
| `--completion-promise` | 선택 | Claude가 조건 충족 시 자동 종료 |

**ralph-loop 활용 시나리오:**

| 개선 요청 | 활성화 역할 | 트리거 플러그인 |
|----------|------------|----------------|
| "UI가 밋밋해" | Designer | frontend-design |
| "로딩이 느려" | Performance Architect | playwright (Lighthouse) |
| "키보드로 조작 안돼" | Accessibility Architect | playwright (a11y snapshot) |
| "보안 취약점 없어?" | Security Engineer | code-reviewer |
| "테스트 커버리지 높여줘" | QA Engineer | TDD superpowers |
| "기능 추가해줘" | Frontend/Backend | feature-dev:feature-dev |
| "코드 구조 개선해줘" | Orchestrator | feature-dev:code-architect |
| "여러 개선 동시 진행해줘" | Orchestrator | subagent-driven-development |

---

## 수동 호출 방법

플러그인을 명시적으로 호출하려면:

```bash
# Context7 문서 조회
"Supabase RLS 정책에 대해 @context7로 조회해줘"

# Playwright E2E 테스트
"@playwright로 로그인 페이지 E2E 테스트 실행해줘"

# Frontend Design UI 생성
"@frontend-design으로 대시보드 UI 만들어줘"

# Feature Dev 기능 개발
"@feature-dev로 사용자 인증 기능 개발 가이드해줘"
"@feature-dev:code-explorer로 현재 코드 구조 분석해줘"

# Superpowers 스킬
"@superpowers:brainstorming으로 접근법 탐색해줘"
"@superpowers:systematic-debugging으로 이 버그 분석해줘"
"@superpowers:subagent-driven-development로 병렬 개발해줘"
```

---

## 플러그인 활용 체크리스트

### Phase 1 시작 시
- [ ] 사용 가능 스킬 확인 → using-superpowers 활용
- [ ] 요청 복잡도 평가 → brainstorming 필요 여부 판단
- [ ] Task 개수 추정 → writing-plans 필요 여부 판단
- [ ] 병렬 작업 가능성 → dispatching-parallel-agents 또는 subagent-driven-development 고려

### Phase 2 시작 시
- [ ] 사용할 프레임워크 식별 → context7 라이브러리 ID 조회
- [ ] 최신 설정 가이드 조회 → query-docs 실행

### Phase 3 진행 시
- [ ] 새 기능 구현 시 → feature-dev:feature-dev로 가이드 기반 개발
- [ ] 기존 코드 이해 필요 시 → feature-dev:code-explorer로 코드 구조 분석
- [ ] UI 설계 필요 시 → frontend-design 활용
- [ ] 라이브러리 API 불확실 시 → context7 조회
- [ ] 코드 리뷰 시점 → code-reviewer 활용
- [ ] 새 기능 구현 전 → test-driven-development로 테스트 선행 작성

### Phase 4 시작 시
- [ ] 전체 테스트 스위트 실행 → 모든 테스트 통과 확인
- [ ] 회귀 테스트 → 기존 기능 영향 없음 확인
- [ ] E2E 테스트 실행 → playwright 활용
- [ ] 버그 발견 시 → systematic-debugging 적용
- [ ] 최종 검증 → verification-before-completion 실행

---

## 자동 트리거 조건 상세

### 복잡도 판단 기준

```yaml
complexity_detection:
  # "복잡한 요청" 감지 조건 (OR 조건)
  triggers_brainstorming:
    - keyword_count: 3  # "인증", "결제", "알림" 등 주요 기능 키워드 3개 이상
    - domain_count: 2   # frontend + backend, web + mobile 등 복수 도메인
    - ambiguous_terms: ["적절한", "좋은 방법", "어떻게", "추천"]
    - question_marks: 2  # "?"가 2개 이상

  # 점수 기반 판단 (100점 만점)
  complexity_score:
    multiple_features: +30  # 기능 2개 이상 요청
    cross_domain: +25       # 프론트+백엔드 동시 작업
    external_integration: +20  # 외부 API/서비스 연동
    security_sensitive: +15    # 인증, 결제, 민감정보
    performance_critical: +10  # 성능 요구사항 명시

  thresholds:
    brainstorming: 50   # 50점 이상 → brainstorming 트리거
    writing_plans: 70   # 70점 이상 → writing-plans 트리거
```

### Task 개수 판단 기준

```yaml
task_counting:
  # Task 단위 정의
  task_unit:
    - 하나의 파일 생성/수정
    - 하나의 API 엔드포인트
    - 하나의 컴포넌트
    - 하나의 테스트 스위트

  # 예시
  examples:
    "로그인 페이지 만들어줘":
      tasks:
        - 로그인 폼 컴포넌트
        - 유효성 검사 로직
        - API 연동
        - 에러 처리 UI
        - 성공 시 리다이렉트
      count: 5
      triggers: [writing-plans]

    "버튼 색상 변경해줘":
      tasks:
        - CSS 수정
      count: 1
      triggers: []  # 플러그인 불필요
```

### 병렬 작업 판단 기준

```yaml
parallel_detection:
  # 독립적 작업 조건
  independent_tasks:
    - 서로 다른 파일 수정
    - 의존성 없는 기능
    - 순서 무관한 작업

  # 병렬 실행 방식 선택
  strategy_selection:
    dispatching-parallel-agents:
      when: "완전히 독립된 작업, 별도 세션 실행"
      pros: "격리된 실행, 충돌 없음"
      cons: "컨텍스트 공유 불가"
    subagent-driven-development:
      when: "실행 계획 내 독립 태스크, 현재 세션에서 병렬"
      pros: "컨텍스트 공유, 리뷰 체크포인트"
      cons: "동일 파일 수정 시 충돌 가능"

  # 예시
  examples:
    parallel_ok:
      - "메인 페이지와 설정 페이지 동시 개발"
      - "프론트엔드 테스트와 백엔드 테스트"
      - "버그 A 수정과 버그 B 수정 (관련 없음)"

    parallel_not_ok:
      - "API 만들고 그걸 사용하는 UI" # 의존성 있음
      - "DB 스키마 수정 후 마이그레이션" # 순서 있음
```

### Security 개입 조건

```yaml
security_trigger:
  # 자동 개입 키워드 (하나라도 포함 시)
  always_trigger:
    - "인증", "로그인", "auth", "login"
    - "결제", "payment", "billing"
    - "비밀번호", "password", "credential"
    - "토큰", "token", "JWT", "session"
    - "암호화", "encrypt", "hash"
    - "권한", "permission", "role", "admin"
    - "민감", "sensitive", "private"
    - "API 키", "secret"

  # 파일 패턴으로 감지
  file_patterns:
    - "**/auth/**"
    - "**/security/**"
    - "**/.env*"
    - "**/middleware/**"

  # 개입 수준
  intervention_levels:
    minimal:
      scope: "의존성 감사만"
      when: "--security minimal"
    standard:
      scope: "의존성 + 주요 엔드포인트"
      when: "기본값"
    strict:
      scope: "모든 코드 라인 검토"
      when: "--security strict 또는 금융/의료 도메인"
```

---

## 플러그인 권한 설정

### 필요한 권한 목록

```json
// settings.local.json에 추가 권장
{
  "permissions": {
    "allow": [
      // Context7 MCP
      "mcp__plugin_context7_context7__resolve-library-id",
      "mcp__plugin_context7_context7__query-docs",

      // Playwright MCP (기본)
      "mcp__plugin_playwright_playwright__browser_navigate",
      "mcp__plugin_playwright_playwright__browser_snapshot",
      "mcp__plugin_playwright_playwright__browser_click",
      "mcp__plugin_playwright_playwright__browser_type",
      "mcp__plugin_playwright_playwright__browser_fill_form",
      "mcp__plugin_playwright_playwright__browser_take_screenshot",
      "mcp__plugin_playwright_playwright__browser_evaluate",
      "mcp__plugin_playwright_playwright__browser_install",

      // Playwright MCP (확장 - 디버깅/성능/접근성)
      "mcp__plugin_playwright_playwright__browser_console_messages",
      "mcp__plugin_playwright_playwright__browser_network_requests",
      "mcp__plugin_playwright_playwright__browser_press_key",
      "mcp__plugin_playwright_playwright__browser_select_option",
      "mcp__plugin_playwright_playwright__browser_hover",
      "mcp__plugin_playwright_playwright__browser_wait_for",
      "mcp__plugin_playwright_playwright__browser_tabs",
      "mcp__plugin_playwright_playwright__browser_resize"
    ]
  }
}
```

### 플러그인별 제한사항

| 플러그인 | 제한 | 이유 |
|---------|------|------|
| Context7 | 질문당 3회 | 토큰/비용 절약 |
| Playwright | 브라우저 1개 | 리소스 관리 |
| Superpowers | 동시 1개 스킬 | 컨텍스트 혼란 방지 |

---

## 주의사항

1. **Context7 호출 제한**: 질문당 최대 3회 호출 권장
2. **Playwright 브라우저**: 미설치 시 `browser_install` 먼저 실행
3. **플러그인 우선순위**: 역할의 핵심 책임 > 플러그인 의존
4. **수동 호출**: 자동 트리거 조건에 맞지 않아도 필요 시 명시적 호출 가능
5. **권한 설정**: MCP 플러그인 사용 시 settings.local.json에 권한 명시 권장

---

## 플러그인 충돌 감지 규칙

### 동시 사용 금지 조합

| 조합 | 이유 | 대안 |
|------|------|------|
| `brainstorming` + `writing-plans` | 분석과 계획을 동시에 하면 컨텍스트 혼란 | brainstorming 완료 → writing-plans 순차 |
| `test-driven-development` + `systematic-debugging` | TDD(신규 작성)와 디버깅(기존 수정)은 목적 상충 | 하나 완료 후 다른 것 실행 |
| `dispatching-parallel-agents` + `subagent-driven-development` | 두 병렬화 전략 동시 사용 불가 | 세션 간 격리 필요 → dispatching, 세션 내 → subagent |
| `feature-dev:feature-dev` + `feature-dev:code-architect` | feature-dev가 code-architect를 내부 포함 | feature-dev만 사용 |

### 순서 의존성

| 선행 플러그인 | 후행 플러그인 | 이유 |
|-------------|-------------|------|
| `using-superpowers` | 모든 superpowers 스킬 | 사용 가능 스킬 확인이 선행 |
| `brainstorming` | `writing-plans` | 요구사항 탐색 후 계획 수립 |
| `writing-plans` | `executing-plans` | 계획 수립 후 실행 |
| `test-driven-development` | `verification-before-completion` | TDD 후 최종 검증 |
| `context7:resolve-library-id` | `context7:query-docs` | 라이브러리 ID 조회 후 문서 쿼리 |
| `playwright:browser_navigate` | `playwright:browser_snapshot` | 페이지 이동 후 스냅샷 |

### 폴백 전략

| 플러그인 실패 | 폴백 액션 |
|-------------|----------|
| **Context7 응답 없음** | 로컬 프레임워크 가이드(`frameworks/`) 참조 |
| **Playwright 브라우저 미설치** | `browser_install` 실행 → 재시도 |
| **Playwright 브라우저 크래시** | `browser_close` → `browser_navigate` 재시작 |
| **Superpowers 스킬 미발견** | 해당 스킬 없이 역할의 기본 역량으로 진행 |
| **Feature-dev 탐색 실패** | Glob/Grep 직접 사용하여 코드 탐색 |
