# TEAM-DETAILED.md - 베테랑 개발팀 상세 가이드 (인덱스)

> **빠른 참조**: `TEAM-QUICK.md` | **플래그 레퍼런스**: `commands/team/team.md`

## 모듈 구조

상세 가이드는 토큰 효율성을 위해 모듈화되어 있습니다. **필요한 모듈만 온디맨드로 로드**하세요.

| 모듈 | 파일 | 내용 | 로드 시점 |
|------|------|------|----------|
| **작업 흐름** | `detailed/workflow.md` | Phase 1-5 상세 흐름, 플러그인 트리거 | Phase 전환 시 |
| **협업** | `detailed/collaboration.md` | 협업 패턴, API 계약, 핸드오프 체크리스트, Phase 전환 프롬프트 | 핸드오프, API 계약 시 |
| **에러 복구** | `detailed/error-recovery.md` | 에러 분류, 역할별 복구 패턴, 자동 복구, 체크포인트 | 에러 발생 시만 |
| **역할 경계** | `detailed/role-boundaries.md` | 책임 분담, 비활성화 가이드, 역할 조합 프리셋 | --with/--without 사용 시 |
| **토큰 전략** | `detailed/token-strategy.md` | 스킬/모듈/플러그인 로딩 규칙, Phase별 토큰 예산 | 컨텍스트 관리 결정 시 |

## Phase별 권장 로딩

| Phase | 권장 모듈 | 이유 |
|-------|----------|------|
| **Phase 1** | (없음) | TEAM-QUICK.md로 충분 |
| **Phase 2** | `workflow` | Phase 전환 흐름 확인 |
| **Phase 3** | `collaboration` | 핸드오프, API 계약 |
| **Phase 3 에러** | `error-recovery` | 역할별 복구 패턴 |
| **Phase 4** | `collaboration` | 검증 프롬프트 체인 |
| **역할 조정** | `role-boundaries` | --with/--without 판단 |

> **원칙**: 에러 없는 세션에서는 인덱스만 로드 (~350 토큰). 기존 전체 로드(~5,700 토큰) 대비 **82% 절감**.
