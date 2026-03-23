# 역할 간 협업 메커니즘

> **출처**: TEAM-DETAILED.md > collaboration 모듈

## 협업 패턴

| 협업 관계 | 협업 방식 | 인터페이스 |
|----------|----------|-----------|
| **Designer → Frontend** | 디자인 명세 전달 | 와이어프레임, 컴포넌트 명세서, 디자인 토큰 |
| **Frontend ↔ Backend** | API 계약 합의 | OpenAPI 스펙, 타입 정의, 엔드포인트 목록 |
| **Backend → DevOps** | 배포 요구사항 전달 | 환경변수 목록, 리소스 요구사항, 헬스체크 엔드포인트 |
| **Performance → Frontend/Backend** | 성능 최적화 가이드 | Web Vitals 리포트, 번들 분석, 쿼리 최적화 권장 |
| **Accessibility → Designer/Frontend** | 접근성 검토 피드백 | WCAG 감사 결과, 수정 가이드, ARIA 패턴 |
| **Security → 모든 역할** | 보안 검토 피드백 | 보안 체크리스트, 취약점 리포트, 수정 가이드 |
| **QA ↔ 모든 역할** | 테스트 결과 공유 | 테스트 리포트, 버그 목록, 커버리지 리포트 |

## API 계약 프로토콜 (Frontend ↔ Backend)

```yaml
# API 계약 예시
endpoint: "/api/users"
method: POST
request:
  body:
    email: string (required)
    password: string (required, min: 8)
response:
  201:
    user: { id, email, createdAt }
    token: string
  400:
    error: { code, message }

# Frontend는 이 스펙에 맞춰 구현
# Backend는 이 스펙을 구현
```

## 충돌 해결 프로토콜

| 충돌 유형 | 해결 주체 | 해결 방법 |
|----------|----------|----------|
| 기술 스택 의견 차이 | Orchestrator | PROJECT.md 설정 기준, 미설정 시 권장 스택 적용 |
| API 스펙 불일치 | Backend | Backend가 스펙 확정, Frontend가 적응 |
| 보안 vs 개발 속도 | Security | 보안 권고 우선, 예외는 명시적 승인 필요 |
| 범위 확장 요청 | Orchestrator | 영향 분석 후 사용자 확인 |
| 테스트 실패 처리 | QA + 해당 역할 | 원인 분석 후 수정, 재테스트 |

## 핸드오프 체크리스트

### Bootstrapper → 개발 팀

- [ ] 프로젝트 구조 생성 완료
- [ ] 의존성 설치 완료 (lock 파일 포함)
- [ ] 개발 서버 정상 실행 확인
- [ ] TypeScript/린터 설정 완료
- [ ] 환경변수 템플릿 (.env.example) 생성

### Designer → Frontend

- [ ] 와이어프레임/목업 완료
- [ ] 컴포넌트 명세서 작성
- [ ] 디자인 토큰 정의 (색상, 타이포, 스페이싱)
- [ ] 반응형 브레이크포인트 정의
- [ ] 접근성 요구사항 명시

### Frontend/Backend → QA

- [ ] 기능 구현 완료
- [ ] 단위 테스트 통과
- [ ] API 문서 최신화
- [ ] 알려진 제한사항 문서화

### Frontend/Backend → Performance Architect

- [ ] 주요 기능 구현 완료
- [ ] 성능 측정 필요 페이지 목록
- [ ] 현재 번들 사이즈/쿼리 정보

### Designer/Frontend → Accessibility Architect

- [ ] UI 컴포넌트 구현 완료
- [ ] 시맨틱 HTML 사용 여부
- [ ] 키보드 내비게이션 초기 구현

### 개발 팀 → DevOps

- [ ] 빌드 스크립트 정상 동작
- [ ] 환경변수 목록 전달
- [ ] 헬스체크 엔드포인트 구현
- [ ] 리소스 요구사항 명시

## Phase 전환 프롬프트 체인

각 Phase 전환 시 역할 간 정보를 전달하는 프롬프트 패턴입니다.

### Phase 1→2: Orchestrator → Bootstrapper

```
[Orchestrator 분석 완료]

프로젝트: {{project_name}}
도메인: {{domain}}
기술 스택:
  - Frontend: {{frontend_framework}} + {{styling}}
  - Backend: {{backend_service}}
  - DB: {{database}}
  - 배포: {{hosting}}

작업 분해:
  Epic 1: {{epic_description}}
    - Story 1.1: {{story}}
    - Story 1.2: {{story}}

→ Bootstrapper: 위 스택으로 프로젝트 초기화를 진행하세요.
  CWD 판단 후 적절한 init 명령을 실행하고, 구조 검증까지 완료하세요.
```

### Phase 2→3: Bootstrapper → 개발 팀

```
[Bootstrapper 설정 완료]

프로젝트 구조: ✅ 검증 완료
개발 서버: ✅ 정상 실행 (port {{port}})
의존성: ✅ {{dependency_count}}개 설치, 취약점 0건
설정 파일: ✅ TypeScript strict, 린터, 포맷터

→ Designer: 컴포넌트 설계를 시작하세요.
→ Frontend: Designer 명세에 따라 UI를 구현하세요.
→ Backend: API 엔드포인트를 설계하고 구현하세요.
→ QA (TDD): 새 기능 구현 전 테스트를 먼저 작성하세요.
→ Security: 각 단계에서 병렬로 보안 검토를 진행하세요.
```

### Phase 3→4: 개발 팀 → QA + Security

```
[Phase 3 개발 완료]

구현 완료 기능:
  - {{feature_1}}: ✅ (단위 테스트 통과)
  - {{feature_2}}: ✅ (단위 테스트 통과)

알려진 제한사항:
  - {{limitation}}

→ QA: 전체 테스트 스위트 실행, E2E 테스트, 회귀 테스트를 진행하세요.
→ Security: OWASP Top 10 기준 최종 보안 점검을 수행하세요.
→ 버그 발견 시 → @superpowers:systematic-debugging 활용
```

### Phase 4→5: QA → ralph-loop

```
[Phase 4 검증 완료]

테스트 결과:
  - Unit: {{pass}}/{{total}} 통과
  - E2E: {{pass}}/{{total}} 통과
  - 커버리지: {{coverage}}%

보안 점검: ✅ 취약점 0건
빌드: ✅ 프로덕션 빌드 성공

→ /ralph-loop "{{improvement_request}}" --max-iterations 10
  필요 시 개선 루프를 시작합니다.
```

## Security 검토 시점 및 산출물

| 단계 | 검토 대상 | 산출물 |
|------|----------|--------|
| Bootstrapper 완료 | 의존성 보안 | `npm audit` / `cargo audit` 결과, 취약 패키지 목록 |
| Backend 개발 중 | API 보안 | 인증 로직 검토, SQL Injection 체크, Rate Limiting 확인 |
| Frontend 개발 중 | 클라이언트 보안 | XSS 방지 검토, CSRF 토큰 사용, 민감정보 노출 체크 |
| DevOps 설정 시 | 인프라 보안 | 환경변수 관리, HTTPS 설정, CORS 정책 |
| 최종 검증 | 전체 보안 | OWASP Top 10 체크리스트, 침투 테스트 권고 |

## 프로젝트별 설정

`PROJECT.md` 파일에서 프로젝트별 설정을 관리:

```yaml
# PROJECT.md 예시
project_name: "My SaaS App"
platforms: [web, mobile-ios, mobile-android]
tech_stack:
  frontend: [Nuxt.js, TypeScript, TailwindCSS, shadcn-vue]
  backend: [Supabase]
  mobile: [React Native]
team_config:
  disabled_roles: []           # 비활성화할 역할
  auto_security_review: true   # 자동 보안 검토
```

> **인덱스**: `TEAM-DETAILED.md` | **빠른 참조**: `TEAM-QUICK.md`
