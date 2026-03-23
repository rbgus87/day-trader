# 에러 처리 및 복구

> **출처**: TEAM-DETAILED.md > error-recovery 모듈

## 에러 분류 및 대응 매트릭스

| 에러 유형 | 원인 | 대응 순서 | 롤백 방법 |
|----------|------|----------|----------|
| **npm install 실패** | 네트워크, 버전 충돌, peer 의존성 | 1. `--legacy-peer-deps` 재시도 → 2. node 버전 확인 → 3. 캐시 삭제 후 재시도 → 4. 사용자에게 에스컬레이션 | `rm -rf node_modules package-lock.json` |
| **npm run dev 실패** | 포트 사용 중, 환경변수 누락, 문법 오류 | 1. 에러 메시지 분석 → 2. 알려진 패턴이면 자동 수정 → 3. 사용자 입력 요청 | `git stash` 또는 마지막 체크포인트 |
| **API 스펙 불일치** | Frontend/Backend 계약 위반 | Backend 스펙이 정본 → Frontend가 적응 → 타입 정의 업데이트 | 해당 없음 (협의 해결) |
| **테스트 실패** | 로직 오류, 환경 문제 | 1. 실패 원인 분류 → 2. 담당 역할 재배정 → 3. 수정 후 재테스트 | 해당 없음 |
| **빌드 실패** | 타입 에러, 누락된 모듈 | 1. 에러 위치 파악 → 2. 해당 역할이 수정 → 3. 전체 빌드 재검증 | `git checkout .` |

## 역할별 에러 복구 패턴

### Frontend Architect

| 에러 유형 | 대응 순서 | 롤백 방법 |
|----------|----------|----------|
| **컴포넌트 렌더링 실패** | 1. 콘솔 에러 확인 → 2. Props/State 검증 → 3. 생명주기 분석 | `git stash` + 이전 커밋 참조 |
| **번들 사이즈 초과** | 1. 번들 분석 (webpack-analyzer) → 2. 동적 임포트 적용 → 3. 의존성 교체 | 변경 전 번들 스냅샷 |
| **SSR 하이드레이션 불일치** | 1. 서버/클라이언트 차이 식별 → 2. `ClientOnly` 래핑 → 3. `useState` 전환 | 해당 없음 |
| **스타일 충돌** | 1. CSS 스코핑 확인 → 2. 유틸리티 클래스 전환 → 3. 레이어 분리 | 해당 없음 |

### Backend Architect

| 에러 유형 | 대응 순서 | 롤백 방법 |
|----------|----------|----------|
| **DB 마이그레이션 실패** | 1. 마이그레이션 상태 확인 → 2. 롤백 마이그레이션 실행 → 3. 스키마 수동 정합 | 마이그레이션 롤백 |
| **API 응답 타임아웃** | 1. 쿼리 실행 계획 분석 → 2. 인덱스 추가 → 3. 페이지네이션 적용 | 해당 없음 |
| **인증 토큰 오류** | 1. 토큰 만료/유효성 확인 → 2. 리프레시 로직 검증 → 3. 시크릿 키 확인 | 해당 없음 |
| **외부 API 장애** | 1. 서킷 브레이커 패턴 적용 → 2. 폴백 응답 → 3. 재시도 큐 | 해당 없음 |

### QA Engineer

| 에러 유형 | 대응 순서 | 롤백 방법 |
|----------|----------|----------|
| **테스트 환경 불안정** | 1. 테스트 격리 확인 → 2. 시드 데이터 재설정 → 3. 환경 재구축 | 테스트 DB 리셋 |
| **플레이키 테스트** | 1. 비동기 대기 추가 → 2. 테스트 격리 강화 → 3. 재시도 정책 적용 | 해당 없음 |
| **커버리지 미달** | 1. 미커버 라인 식별 → 2. 엣지 케이스 테스트 추가 → 3. 목표 재조정 | 해당 없음 |

### Security Engineer

| 에러 유형 | 대응 순서 | 롤백 방법 |
|----------|----------|----------|
| **취약 의존성 발견** | 1. 심각도 평가 → 2. 패치 버전 확인 → 3. 대체 패키지 검토 | 이전 lock 파일 |
| **인증 우회 발견** | 1. 즉시 차단 → 2. 미들웨어 검증 → 3. 전체 엔드포인트 감사 | 해당 없음 (즉시 수정) |
| **민감 데이터 노출** | 1. 노출 범위 확인 → 2. 환경변수 분리 → 3. 시크릿 로테이션 | 시크릿 즉시 변경 |

### DevOps Engineer

| 에러 유형 | 대응 순서 | 롤백 방법 |
|----------|----------|----------|
| **CI/CD 파이프라인 실패** | 1. 로그 분석 → 2. 환경변수/캐시 확인 → 3. 러너 재시작 | 이전 워크플로우 버전 |
| **배포 실패** | 1. 헬스체크 확인 → 2. 이전 버전 롤백 → 3. 환경 차이 분석 | 이전 배포 버전 |
| **컨테이너 빌드 실패** | 1. Dockerfile 레이어별 검증 → 2. 베이스 이미지 확인 → 3. 멀티스테이지 분리 | 이전 이미지 태그 |

## 자동 복구 패턴

```yaml
# npm install 실패 시
npm_install_recovery:
  step_1:
    command: "npm install --legacy-peer-deps"
    on_fail: step_2
  step_2:
    check: "node -v와 .nvmrc/.node-version 비교"
    action: "nvm use 또는 버전 안내"
    on_fail: step_3
  step_3:
    command: "npm cache clean --force && rm -rf node_modules && npm install"
    on_fail: escalate_to_user

# 포트 충돌 시 (Windows)
port_conflict_recovery:
  windows: "netstat -ano | findstr :[PORT] → taskkill /PID [PID] /F"
  unix: "lsof -i :[PORT] → kill -9 [PID]"

# 환경변수 누락 시
env_missing_recovery:
  - ".env.example 확인"
  - "누락된 변수 목록 사용자에게 안내"
  - ".env 파일 생성 가이드"
```

## 체크포인트 시스템

```yaml
checkpoints:
  phase_1_complete:
    marker: "분석 완료, 기술 스택 확정"
    save: "analysis_result.md"
    rollback_command: "재분석 요청"

  phase_2_complete:
    marker: "프로젝트 구조 생성, npm run dev 성공"
    save: "git commit (초기 설정)"
    rollback_command: "git reset --soft HEAD~1"

  phase_3_component:
    marker: "개별 컴포넌트/API 완성"
    save: "기능별 커밋"
    rollback_command: "git revert [commit]"

  phase_4_verified:
    marker: "모든 테스트 통과, 보안 검증 완료"
    save: "최종 커밋"
    rollback_command: "git reset --soft HEAD~1"
```

> **인덱스**: `TEAM-DETAILED.md` | **빠른 참조**: `TEAM-QUICK.md`
