# 작업 흐름 상세

> **출처**: TEAM-DETAILED.md > workflow 모듈

## Phase별 작업 흐름

> **플러그인 통합**: 각 Phase에서 조건에 따라 플러그인이 자동 트리거됩니다. 상세 가이드는 `PLUGINS.md` 참조.

```
PHASE 1: 분석 (Orchestrator)
├── [대화 시작] → @superpowers:using-superpowers
├── 요청 의도 파악
│   └── [복잡한 요청] → @superpowers:brainstorming
├── 기술 스택 결정
├── 작업 분해 (Task Breakdown)
│   └── [Task 5개+] → @superpowers:writing-plans
├── 역할 배분 및 실행 전략
│   ├── [독립 작업 2개+, 별도 세션] → @superpowers:dispatching-parallel-agents
│   └── [독립 작업 2개+, 현재 세션] → @superpowers:subagent-driven-development
└── 실행 계획 확정

PHASE 2: 초기 설정 (Bootstrapper)
├── 프로젝트 초기화
├── 의존성 설치
│   └── [프레임워크 설정] → @context7 (최신 문서 조회)
├── 설정 파일 구성
└── 검증 (npm run dev 성공 확인)

PHASE 3: 병렬 개발
├── [독립 태스크 2개+] → @superpowers:subagent-driven-development
│   └── 서브에이전트가 Frontend/Backend 병렬 수행
├── Designer: UI/UX 설계
│   └── @frontend-design, @playwright (디자인 검증)
├── Frontend: UI 구현
│   └── @feature-dev:feature-dev, @context7, @frontend-design, @playwright (E2E)
├── Backend: API/DB 구현
│   └── @feature-dev:feature-dev, @context7, @feature-dev:code-architect
├── Performance: 성능 최적화
│   └── @context7, @playwright (Web Vitals 측정)
├── Accessibility: 접근성 검토
│   └── @playwright (접근성 감사)
├── Security: 보안 검토 (각 단계 개입)
│   └── @feature-dev:code-reviewer
├── DevOps: 인프라 설정
│   └── @context7
└── QA: TDD 가이드 (테스트 선행 작성)
    └── @superpowers:test-driven-development

PHASE 4: 검증 및 완료
├── QA: 전체 테스트 실행 및 검증
│   ├── 회귀 테스트 + 전체 테스트 스위트 실행
│   ├── @playwright (E2E 테스트)
│   ├── 커버리지 분석 및 리포팅
│   └── [버그 발견] → @superpowers:systematic-debugging
├── Security: 최종 보안 점검
│   └── @superpowers:verification-before-completion
└── Orchestrator: 완료 보고
    └── @superpowers:requesting-code-review

PHASE 5: 지속적 개선 (자동 실행 시 반복 제한 필수)
├── /ralph-loop 자동 실행 (--max-iterations 10 기본 포함)
│   ⚠️ --max-iterations 없이 실행 금지 (무제한 반복 방지)
│   └── 안전 설정:
│       ├── --max-iterations N (기본: 10, 최대 권장: 30)
│       └── --completion-promise 'TEXT' (선택, 자동 종료 조건)
├── 점진적 개선 요청
│   ├── "UI/UX 개선해줘"
│   ├── "Performance Architect 관점에서 최적화해줘"
│   ├── "Accessibility Architect 관점에서 검토해줘"
│   ├── "Security Engineer 관점에서 취약점 점검해줘"
│   └── ... (max-iterations 범위 내에서 반복)
├── 자동 종료: max-iterations 도달 또는 completion-promise 충족
└── /cancel-ralph (수동 조기 종료)
```

> **인덱스**: `TEAM-DETAILED.md` | **빠른 참조**: `TEAM-QUICK.md`
