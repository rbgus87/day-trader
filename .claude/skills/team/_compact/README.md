# Compact SKILL 버전

각 역할의 핵심만 담은 압축 버전입니다.
토큰 효율성을 위해 기본적으로 이 버전이 로드되며, 에러 발생 시 전체 SKILL.md가 로드됩니다.

## 사용 규칙

```yaml
loading_rules:
  default: compact  # 기본적으로 압축 버전 사용
  expand_when:
    - 에러 발생 시
    - 상세 가이드 필요 시
    - 사용자 명시적 요청 시
```

## 파일 목록

| 파일 | 역할 | 원본 줄수 | 압축 줄수 |
|------|------|----------|----------|
| orchestrator.md | CTO/PM | ~570줄 | ~50줄 |
| bootstrapper.md | 프로젝트 설정 | ~730줄 | ~50줄 |
| product-designer.md | UI/UX 설계 | ~490줄 | ~40줄 |
| frontend-architect.md | 프론트엔드 | ~760줄 | ~50줄 |
| backend-architect.md | 백엔드 | ~1050줄 | ~50줄 |
| performance-architect.md | 성능 최적화 | ~800줄 | ~50줄 |
| accessibility-architect.md | 접근성 | ~820줄 | ~50줄 |
| security-engineer.md | 보안 | ~770줄 | ~50줄 |
| devops-engineer.md | 인프라/배포 | ~920줄 | ~50줄 |
| qa-engineer.md | 테스트/품질 | ~880줄 | ~50줄 |

## Compact → Full 전환 의사결정 트리

역할 SKILL을 로드할 때, compact 버전과 full 버전 중 어떤 것을 사용할지 판단합니다.

```
STEP 1: 현재 Phase 확인
│
├── Phase 1 (분석) → Compact 사용 (Orchestrator만 필요)
├── Phase 2 (초기 설정) → Compact 사용 (Bootstrapper 기본)
│   └── 프레임워크 에러 발생? → Full 전환
├── Phase 3 (개발)
│   ├── 단순 기능 구현? → Compact 유지
│   ├── 프레임워크 특화 패턴 필요? → Full 전환
│   ├── 에러/트러블슈팅 필요? → Full 전환
│   └── 복잡한 아키텍처 결정? → Full 전환
├── Phase 4 (검증) → Compact 사용 (QA, Security)
│   └── 테스트 실패 디버깅? → Full 전환
└── Phase 5 (개선) → Compact 유지
    └── 특정 역할 심층 분석? → Full 전환

STEP 2: 작업 복잡도 확인
│
├── Low (단일 파일 수정, 표준 패턴)
│   → Compact 유지
├── Medium (다중 파일, 일반 패턴)
│   → Compact 유지, 필요 시 Full
└── High (아키텍처 결정, 에러 복구, 신규 패턴)
    → Full 전환

STEP 3: 에러 상태 확인
│
├── 에러 없음 → Compact 유지
├── 알려진 패턴의 에러 → Compact의 트러블슈팅 참조
└── 미지의 에러 → Full 전환 (상세 트러블슈팅 필요)
```

### 전환 판단 요약

| 조건 | Compact | Full |
|------|---------|------|
| 표준 패턴 작업 | ✅ | |
| 프레임워크 특화 에러 | | ✅ |
| 상세 코드 예제 필요 | | ✅ |
| 역할 간 핸드오프 체크 | | ✅ |
| 사용자 명시적 요청 | | ✅ |
| 단순 CRUD 구현 | ✅ | |
| 보안 심층 분석 | | ✅ |
| 성능 프로파일링 | | ✅ |

### 토큰 절약 효과

- Compact: 평균 ~50줄 (~2K 토큰)
- Full: 평균 ~800줄 (~30K 토큰)
- **절약률**: Compact 사용 시 역할당 ~93% 토큰 절약
