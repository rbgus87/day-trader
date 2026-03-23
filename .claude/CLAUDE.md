# 프로젝트 Claude 설정

이 프로젝트는 AI 베테랑 개발팀 설정이 적용되어 있습니다.

## 팀 구성

@TEAM-QUICK.md

## 프로젝트 설정

@PROJECT.md

### PROJECT.md 로드 규칙

```yaml
project_md_loading:
  # 파일 위치 (우선순위 순)
  locations:
    1: "PROJECT.md"           # 프로젝트 루트
    2: ".claude/PROJECT.md"   # .claude 폴더 내

  # 필수 필드
  required_fields:
    - project_name
    - platforms        # [web, mobile, desktop, cli, embedded, game, ml, blockchain]

  # 선택 필드
  optional_fields:
    - tech_stack       # frontend, backend, infrastructure 설정
    - team_config      # disabled_roles, auto_security_review
    - conventions      # code_style, commit, branching

  # PROJECT.md 없을 때 동작
  fallback:
    message: "PROJECT.md가 없습니다. 기본 설정으로 진행합니다."
    defaults:
      platforms: [web]
      team_config:
        disabled_roles: []
        auto_security_review: true
        default_mode: hybrid

  # 검증 규칙
  validation:
    - platforms는 지원 도메인 목록에 있어야 함
    - disabled_roles에 orchestrator 포함 불가
    - tech_stack 미지정 시 Orchestrator가 분석 후 결정
```

## 사용 가능한 커맨드

| 커맨드 | 설명 |
|--------|------|
| `/team [요청]` | AI 개발팀 전체 가동 |
| `/team [요청] --with [역할]` | 특정 전문가만 활성화 |
| `/team [요청] --without [역할]` | 특정 전문가 제외 |
| `/team [요청] --mode auto\|step\|hybrid` | 실행 모드 지정 |
| `/init-project` | 인터랙티브 PROJECT.md 생성 (도메인/프레임워크 선택) |
| `/ralph-loop` | 지속적 개선 루프 진입 (**`--max-iterations 10` 기본 포함 필수**) |
| `/cancel-ralph` | 개선 루프 수동 조기 종료 |

> **상세 플래그 문서**: `commands/team/team.md` 참조

## 빠른 시작

```bash
# 새 기능 개발
/team "사용자 인증 기능 만들어줘"

# 특정 전문가만 활용
/team "API 보안 검토해줘" --with security,backend

# 단계별 확인 모드
/team "결제 시스템 구현해줘" --mode step

# 빠른 프로토타입 (테스트/배포 제외)
/team "대시보드 만들어줘" --mode auto --without qa,devops
```

---

## 역할별 활성화 조건 (SSOT)

> 이 섹션은 모든 역할의 활성화 조건에 대한 **공식 문서(Single Source of Truth)**입니다.

### 자동 활성화 규칙

| 역할 | 활성화 조건 | 활성화 키워드 |
|------|------------|---------------|
| **Orchestrator** | 항상 (제외 불가) | `/team` 호출 시 자동 |
| **Bootstrapper** | 새 프로젝트 또는 설정 변경 | "새 프로젝트", "초기화", "설정", "환경 구축", "의존성" |
| **Designer** | UI/UX 관련 작업 | "디자인", "UI", "UX", "화면", "레이아웃", "와이어프레임", "컴포넌트 설계" |
| **Frontend** | 클라이언트 측 개발 | "프론트", "컴포넌트", "페이지", "화면 구현", "상태 관리", "UI 구현" |
| **Backend** | 서버 측 개발 | "API", "백엔드", "데이터베이스", "DB", "서버", "엔드포인트", "비즈니스 로직" |
| **Performance** | 성능 관련 작업 | "성능", "최적화", "속도", "LCP", "번들", "렌더링", "캐싱", "느림" |
| **Accessibility** | 접근성 관련 작업 | "접근성", "a11y", "WCAG", "스크린 리더", "키보드", "대비", "포커스" |
| **Security** | 보안 관련 작업 + 모든 단계 검토 | "보안", "인증", "취약점", "XSS", "CSRF", "권한", "암호화" |
| **DevOps** | 인프라/배포 관련 작업 | "배포", "Docker", "CI/CD", "인프라", "모니터링", "GitHub Actions" |
| **QA** | 테스트/품질 관련 작업 | "테스트", "QA", "검증", "E2E", "품질", "커버리지" |

### 도메인별 자동 활성화

| 도메인 | 자동 활성화 역할 |
|--------|------------------|
| **Web** | Orchestrator, Bootstrapper, Designer, Frontend, Backend, Performance, Accessibility, Security, DevOps, QA |
| **Mobile** | Orchestrator, Bootstrapper, Designer, Frontend, Backend, Performance, Accessibility, Security, QA |
| **Desktop** | Orchestrator, Bootstrapper, Designer, Frontend, Backend, Performance, Security, DevOps |
| **CLI/System** | Orchestrator, Bootstrapper, Backend, Performance, Security, DevOps, QA |
| **Embedded** | Orchestrator, Bootstrapper, Backend, Performance, Security, DevOps, QA |
| **Game** | Orchestrator, Bootstrapper, Designer, Frontend, Backend, Performance, QA |
| **ML/AI** | Orchestrator, Bootstrapper, Backend, Performance, DevOps, QA |
| **Blockchain** | Orchestrator, Bootstrapper, Frontend, Backend, Security, QA |

### Security 개입 시점

Security Engineer는 다른 역할과 **병렬로** 각 단계에서 검토를 수행합니다:

| 단계 | Security 검토 내용 |
|------|-------------------|
| Bootstrapper 완료 후 | 의존성 보안 감사 (npm audit, cargo audit 등) |
| Backend 작업 중 | API 보안 검토, 인증/인가 검증 |
| Frontend 작업 중 | XSS 방지, 입력 검증 검토 |
| DevOps 설정 시 | 인프라 보안 검토, 환경변수 관리 |
| 최종 단계 | 전체 보안 점검, OWASP 체크리스트 |

### 역할 간 의존성

```
Orchestrator (Phase 1)
    │
    ▼
Bootstrapper (Phase 2) ─────────────────────────────┐
    │                                               │
    │ 완료 후                                        │
    ▼                                               ▼
┌─────────────────────────────────────────────────────┐
│                    Phase 3: 병렬 개발                │
│                                                     │
│  Designer ───▶ Frontend ◀───▶ Backend              │
│      │            │              │                  │
│      │            ├──────────────┤                  │
│      │            ▼              ▼                  │
│      │      Performance    Performance              │
│      │      (번들 최적화)   (쿼리 최적화)            │
│      │            │              │                  │
│      └────▶ Accessibility ◀──────┘                  │
│                   │                                 │
│           QA (TDD 가이드, 테스트 선행 작성)           │
│              Security (병렬 검토)                    │
│                   │                                 │
│                DevOps                               │
└─────────────────────────────────────────────────────┘
    │
    ▼
QA (전체 검증) + Security (Phase 4: 최종 검증)
    │
    ▼
Orchestrator (완료 보고)
    │
    ▼ (자동 실행 시 --max-iterations 10 기본 포함)
/ralph-loop (Phase 5: 지속적 개선)
    └── ⚠️ --max-iterations 없이 실행 금지
```

---

## 참조 문서

| 문서 | 경로 | 설명 |
|------|------|------|
| 팀 설정 (빠른 참조) | `TEAM-QUICK.md` | 팀 구조, 실행 모드, 작업 흐름 개요 |
| 팀 설정 (상세) | `TEAM-DETAILED.md` | 인덱스 → `detailed/` 모듈별 온디맨드 로드 (협업, 에러 복구, 역할 경계, 토큰 전략) |
| 프로젝트 설정 | `PROJECT.md` | 프로젝트별 기술 스택, 설정 |
| **플러그인 빠른 참조** | `PLUGINS-QUICK.md` | **플러그인 요약, 역할 매핑, Phase 트리거 (기본 로드)** |
| 플러그인 상세 가이드 | `PLUGINS.md` | 상세 트리거, 복잡도 점수, 권한 JSON, 충돌 규칙 (온디맨드) |
| 커맨드 정의 | `commands/team/team.md` | `/team` 커맨드 상세, 플래그 레퍼런스 |
| 역할별 상세 | `skills/team/[역할]/SKILL.md` | 각 전문가의 상세 역량 |
| 프레임워크 가이드 | `frameworks/` | 프레임워크별 상세 베스트 프랙티스 |
| 템플릿 | `templates/` | API 계약, 컴포넌트 명세, 보안 검토 등 템플릿 |
| 멀티 에이전트 프로토콜 | `guides/multi-agent-protocol.md` | 병렬 에이전트 실행 가이드 |
| 버전 관리 전략 | `guides/versioning.md` | 템플릿 + 프로젝트 버전 관리 |
| 성능 측정 프레임워크 | `guides/metrics.md` | 팀 실행 메트릭, Web Vitals |
| 커스텀 역할 확장 | `guides/custom-roles.md` | 새 역할 추가 방법 |

---

## 템플릿 활용

`templates/` 폴더에는 각 역할이 산출물 생성 시 활용할 수 있는 템플릿이 포함되어 있습니다.

| 템플릿 | 경로 | 활용 역할 |
|--------|------|----------|
| 환경변수 | `templates/project-init/env.example` | Bootstrapper, DevOps |
| .gitignore | `templates/project-init/gitignore.template` | Bootstrapper, DevOps |
| API 엔드포인트 명세 | `templates/api-contract/endpoint.md` | Backend, Frontend |
| OpenAPI 스펙 | `templates/api-contract/openapi.yaml` | Backend, Frontend |
| 컴포넌트 명세서 | `templates/component-spec/component.md` | Designer, Frontend |
| 보안 검토 보고서 | `templates/security/security-review.md` | Security |
| 보안 체크리스트 | `templates/security/checklist.md` | Security |
| 테스트 결과 보고서 | `templates/testing/test-report.md` | QA |
| CI/CD (GitHub Actions) | `templates/ci-cd/github-actions.yml` | DevOps |
| CI/CD (GitLab CI) | `templates/ci-cd/gitlab-ci.yml` | DevOps |
| 배포 체크리스트 | `templates/deployment/deploy-checklist.md` | DevOps |

> **상세 가이드**: `templates/README.md` 참조
