# 멀티 에이전트 세션 프로토콜

Claude Code의 서브에이전트를 활용한 병렬 개발 가이드.

## 개요

`@superpowers:subagent-driven-development`와 `@superpowers:dispatching-parallel-agents`를 활용하여
여러 역할이 동시에 독립적인 작업을 수행합니다.

## 병렬 실행 조건

```yaml
parallel_execution:
  # 병렬 가능 조건 (모두 충족 시)
  conditions:
    - 작업 간 파일 의존성 없음
    - 공유 상태(DB 스키마 등)가 확정된 상태
    - API 계약이 합의된 상태

  # 병렬 불가 (순차 실행 필수)
  sequential_only:
    - 같은 파일을 수정하는 작업
    - DB 마이그레이션이 필요한 작업
    - 빌드 설정 변경 작업
```

## Phase별 병렬화 전략

### Phase 3: 최대 병렬화

```
Orchestrator 작업 분배
    │
    ├── Agent A: Frontend (페이지 구현)
    │   └── 독립 파일: components/, pages/
    │
    ├── Agent B: Backend (API 구현)
    │   └── 독립 파일: server/, api/
    │
    ├── Agent C: Security (병렬 검토)
    │   └── 읽기 전용: 코드 리뷰
    │
    └── Agent D: QA (TDD 테스트 작성)
        └── 독립 파일: tests/
```

### Phase 5 (ralph-loop): 개선 병렬화

```
/ralph-loop "UI 개선 + 성능 최적화"
    │
    ├── Agent A: Designer + Frontend (UI 개선)
    │   └── 컴포넌트 수정
    │
    └── Agent B: Performance (최적화)
        └── 번들/쿼리 최적화
```

## 충돌 방지 규칙

| 규칙 | 설명 |
|------|------|
| **파일 잠금** | 하나의 에이전트만 파일 수정 가능 |
| **API 계약 동결** | Phase 3 시작 시 API 스펙 확정, 변경 시 Orchestrator 승인 |
| **브랜치 분리** | `@superpowers:using-git-worktrees`로 독립 브랜치 작업 |
| **머지 순서** | Backend → Frontend → QA 순서로 머지 |

## 에이전트 간 통신

```yaml
communication:
  # 에이전트 간 직접 통신 없음 — 파일 기반 계약
  method: "file-based contract"

  contracts:
    api: "templates/api-contract/endpoint.md"
    component: "templates/component-spec/component.md"

  # 충돌 발생 시
  on_conflict:
    1: "작업 중단"
    2: "Orchestrator에 보고"
    3: "Orchestrator가 우선순위 결정"
    4: "순차 실행으로 전환"
```

## 권장 병렬 조합

| 조합 | 시나리오 | 에이전트 수 |
|------|---------|------------|
| Frontend + Backend | 풀스택 기능 구현 | 2 |
| Frontend + Backend + QA | TDD 기반 풀스택 | 3 |
| Security + Performance | 비파괴적 분석 | 2 |
| Designer + Backend | UI 설계 + API 구현 동시 | 2 |

## 비용 고려

```yaml
cost_awareness:
  # 서브에이전트 = 독립 컨텍스트 → 추가 토큰 소비
  guidelines:
    - 단순 작업은 순차 실행 (에이전트 오버헤드 회피)
    - 30분 이상 걸리는 독립 작업만 병렬화
    - 최대 동시 에이전트: 3개 권장 (비용 vs 속도 균형)
```
