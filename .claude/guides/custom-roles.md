# 커스텀 역할 확장 가이드

기본 10개 역할 외에 프로젝트별 전문 역할을 추가하는 방법.

## 커스텀 역할 생성

### 1. SKILL 파일 구조

```
skills/team/{{role-name}}/
├── SKILL.md        # 전체 역할 정의 (~300-800줄)
└── (참조 파일)      # 필요 시 추가

skills/team/_compact/
└── {{role-name}}.md  # 압축 버전 (~50-60줄)
```

### 2. SKILL.md 필수 섹션

```markdown
# {{Role Name}} ({{영문 제목}})

> **version**: 1.0.0 | **updated**: {{날짜}}

{{한 줄 설명}}

## Identity

role: {{역할명}}
experience: {{경력}}
philosophy: "{{핵심 철학}}"

## Priority Hierarchy

1. {{최우선 원칙}}
2. {{차우선 원칙}}

## Core Responsibilities

### 1. {{책임 1}}
### 2. {{책임 2}}

## Technical Expertise

(역할별 기술 전문성)

## Plugin Integration

(활용 플러그인 목록)

## Error Recovery

(에러 유형별 복구 패턴)

## Checklists

(역할별 체크리스트)
```

### 3. Compact 버전 필수 섹션

```markdown
# {{Role Name}} Quick Reference (Compact)

**역할**: {{한 줄 설명}} | **Phase**: {{활성화 Phase}}

## 핵심 책임
(5줄 이내 요약)

## 플러그인
(사용 플러그인 목록)

## 체크리스트
(핵심 항목만)

> **전체 가이드**: `skills/team/{{role-name}}/SKILL.md` 참조
```

## 등록 절차

### Step 1: 파일 생성

위 구조에 맞춰 SKILL.md와 compact 버전을 생성합니다.

### Step 2: TEAM-QUICK.md에 등록

역할 요약 테이블에 새 역할을 추가합니다:

```markdown
| {{Role Name}} | `skills/team/{{role-name}}/` | {{핵심 책임}} |
```

### Step 3: CLAUDE.md 활성화 조건 추가

자동 활성화 규칙 테이블에 새 역할을 추가합니다:

```markdown
| **{{Role Name}}** | {{활성화 조건}} | {{활성화 키워드}} |
```

### Step 4: PLUGINS.md 매핑 추가 (필요 시)

역할이 외부 플러그인을 사용하는 경우 매핑을 추가합니다.

### Step 5: 검증

```bash
# SKILL 파일 존재 확인
ls skills/team/{{role-name}}/SKILL.md
ls skills/team/_compact/{{role-name}}.md

# TEAM-QUICK.md 등록 확인
grep "{{role-name}}" TEAM-QUICK.md

# CLAUDE.md 활성화 조건 확인
grep "{{Role Name}}" CLAUDE.md
```

## 커스텀 역할 예시

### 예시 1: Data Engineer

```yaml
use_case: "데이터 파이프라인이 핵심인 프로젝트"
responsibilities:
  - ETL/ELT 파이프라인 설계
  - 데이터 모델링 (Star Schema, Snowflake)
  - 배치/스트리밍 처리
  - 데이터 품질 관리
plugins:
  - context7 (Spark/Airflow 문서 조회)
activation: "데이터", "ETL", "파이프라인", "데이터 웨어하우스"
```

### 예시 2: ML Engineer

```yaml
use_case: "ML 모델 학습/서빙이 포함된 프로젝트"
responsibilities:
  - 모델 학습 파이프라인 구축
  - 피처 엔지니어링
  - 모델 서빙 (FastAPI + ONNX)
  - 실험 추적 (MLflow/W&B)
plugins:
  - context7 (PyTorch/TensorFlow 문서)
  - feature-dev:code-architect (ML 아키텍처)
activation: "모델", "학습", "추론", "ML", "딥러닝", "파인튜닝"
```

### 예시 3: Technical Writer

```yaml
use_case: "API 문서, 사용자 가이드가 중요한 프로젝트"
responsibilities:
  - API 문서 자동 생성 (OpenAPI → docs)
  - 사용자 가이드 작성
  - 코드 주석 표준화
  - README 관리
plugins:
  - context7 (문서 도구 조회)
activation: "문서", "가이드", "README", "API 문서", "JSDoc"
```

## 역할 비활성화

기본 역할 중 불필요한 역할을 비활성화하려면:

```yaml
# PROJECT.md
team_config:
  disabled_roles:
    - accessibility-architect  # 접근성 불필요 시
    - performance-architect    # 성능 최적화 불필요 시
```

> **주의**: `orchestrator`는 비활성화할 수 없습니다.

## 역할 수 권장사항

| 프로젝트 규모 | 활성 역할 수 | 비고 |
|-------------|------------|------|
| 소규모 (1주 이내) | 4-5개 | Orchestrator + 핵심 3-4개 |
| 중규모 (1-4주) | 6-8개 | 기본 역할 대부분 |
| 대규모 (1개월+) | 8-12개 | 기본 + 커스텀 역할 |

> 역할이 많을수록 토큰 소비 증가 → 필요한 역할만 활성화하세요.
