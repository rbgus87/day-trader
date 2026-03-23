# TEAM-QUICK.md - 베테랑 개발팀 빠른 참조

1인 개발을 위한 AI 개발팀 구성. 모든 멤버는 30년 이상의 경력을 가진 베테랑 전문가.

## 팀 구조

```
═════════════════════════════════════════════════════════════════════════════════
                              🎯 ORCHESTRATOR
                    (분석, 기술 스택 결정, 전체 조율)
                                     │
                                     ▼
                          ┌─────────────────┐
                          │   BOOTSTRAPPER  │
                          │  (프로젝트 설정)│
                          └────────┬────────┘
                                   │
┌─────────┬─────────┬─────────┬────┴────┬─────────┬─────────┬─────────┬─────────┐
▼         ▼         ▼         ▼         ▼         ▼         ▼         ▼         ▼
Designer  Frontend  Backend   Perf      A11y    Security   DevOps      QA
          Architect Architect Architect Architect Engineer  Engineer  Engineer
═════════════════════════════════════════════════════════════════════════════════
```

## 역할 요약

| 역할 | Skill 경로 | 핵심 책임 |
|------|-----------|----------|
| Orchestrator | `skills/team/orchestrator/` | 요청 분석, 기술 결정, 작업 분배, 전체 조율 |
| Bootstrapper | `skills/team/bootstrapper/` | 프로젝트 초기화, 의존성 설정, 환경 검증 |
| Product Designer | `skills/team/product-designer/` | UX/UI 설계, 와이어프레임, 컴포넌트 명세 |
| Frontend Architect | `skills/team/frontend-architect/` | 웹/모바일 UI, 상태 관리, 프론트 아키텍처 |
| Backend Architect | `skills/team/backend-architect/` | API 설계, DB 모델링, 비즈니스 로직 |
| **Performance Architect** | `skills/team/performance-architect/` | **성능 최적화, Web Vitals, 번들/쿼리 튜닝** |
| **Accessibility Architect** | `skills/team/accessibility-architect/` | **접근성, WCAG 준수, 스크린 리더 호환** |
| Security Engineer | `skills/team/security-engineer/` | 보안 검토, 취약점 점검, 보안 아키텍처 |
| DevOps Engineer | `skills/team/devops-engineer/` | 인프라, CI/CD, 배포, 모니터링 |
| QA Engineer | `skills/team/qa-engineer/` | 테스트 전략, E2E 테스트, 품질 검증 |

## 실행 모드

| 모드 | 동작 | 적합한 상황 |
|------|------|------------|
| `auto` | 끝까지 자동 실행 | 간단한 작업, 신뢰도 높은 패턴 |
| `step` | 매 Phase 승인 요청 | 복잡한 작업, 첫 사용 |
| `hybrid` | 중요 결정만 확인 | 일반적인 사용 (기본값) |

## 작업 흐름 개요

> **플러그인 통합**: 각 Phase에서 조건에 따라 플러그인이 자동 트리거됩니다. 빠른 참조는 `PLUGINS-QUICK.md`, 상세 가이드는 `PLUGINS.md` 참조.

| Phase | 담당 | 핵심 활동 | 주요 플러그인 |
|-------|------|----------|--------------|
| **1. 분석** | Orchestrator | 요청 분석, 기술 스택 결정, 작업 분해 | brainstorming, writing-plans |
| **2. 초기 설정** | Bootstrapper | 프로젝트 초기화, 의존성, 검증 | context7 |
| **3. 병렬 개발** | 전문가 팀 | UI/API 구현, 보안/성능/접근성 검토, **TDD 가이드** | feature-dev, context7, **test-driven-development** |
| **4. 검증** | QA + Security | 전체 테스트 실행, 회귀/E2E, 최종 보안 점검 | playwright, systematic-debugging |
| **5. 지속적 개선** | 전체 | ralph-loop (--max-iterations 10 필수) | 역할별 플러그인 |

## 모델 라우팅 (권장)

| 작업 유형 | 권장 모델 | 이유 |
|----------|----------|------|
| Phase 1 분석, 아키텍처 결정 | Opus | 복잡한 추론 |
| Phase 2 프로젝트 초기화 | Sonnet | 표준 패턴 |
| Phase 3 코드 구현 | Sonnet | 속도-품질 균형 |
| Phase 3 보안/아키텍처 검토 | Opus | 심층 분석 |
| Phase 4 테스트 실행 | Sonnet | 패턴 기반 |
| 간단한 수정, 포맷팅 | Haiku | 비용 절감 |

## 커맨드

| 커맨드 | 설명 |
|--------|------|
| `/team [요청]` | 팀 전체 가동, 요청 분석 및 실행 |
| `/team [요청] --with [역할]` | 특정 전문가만 활성화하여 요청 |
| `/ralph-loop` | Phase 4 완료 후 지속적 개선 루프 진입 (**`--max-iterations 10` 기본 포함 필수**) |
| `/cancel-ralph` | 개선 루프 수동 조기 종료 |

> **상세 가이드**: 협업 프로토콜, 핸드오프 체크리스트, 에러 복구, 역할 경계, 토큰 전략 → `TEAM-DETAILED.md` (인덱스 → `detailed/` 모듈별 온디맨드 로드)
