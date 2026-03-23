# Templates 폴더 활용 가이드

이 폴더에는 AI 베테랑 개발팀이 작업 시 활용할 수 있는 템플릿들이 포함되어 있습니다.

## 폴더 구조

```
templates/
├── README.md                    # 이 파일
├── project-init/                # 프로젝트 초기화 템플릿
│   ├── env.example             # 환경변수 템플릿
│   └── gitignore.template      # .gitignore 템플릿
├── api-contract/               # API 계약 템플릿
│   ├── openapi.yaml            # OpenAPI 스펙 템플릿
│   └── endpoint.md             # 엔드포인트 문서 템플릿
├── component-spec/             # 컴포넌트 명세 템플릿
│   └── component.md            # 컴포넌트 명세서 템플릿
├── security/                   # 보안 관련 템플릿
│   ├── security-review.md      # 보안 검토 보고서 템플릿
│   └── checklist.md            # 보안 체크리스트 템플릿
├── testing/                    # 테스트 관련 템플릿
│   └── test-report.md          # 테스트 결과 보고서 템플릿
├── ci-cd/                     # CI/CD 파이프라인 템플릿
│   ├── github-actions.yml     # GitHub Actions 워크플로우
│   └── gitlab-ci.yml          # GitLab CI 파이프라인
└── deployment/                 # 배포 관련 템플릿
    └── deploy-checklist.md     # 배포 체크리스트 템플릿
```

## 활용 방법

### 역할별 템플릿 사용

| 역할 | 주로 사용하는 템플릿 |
|------|---------------------|
| Orchestrator | 모든 템플릿 참조 |
| Bootstrapper | `project-init/*` |
| Designer | `component-spec/*` |
| Frontend | `component-spec/*`, `api-contract/*` |
| Backend | `api-contract/*` |
| Security | `security/*` |
| DevOps | `deployment/*`, `ci-cd/*`, `project-init/*` |
| QA | `testing/*` |

### 템플릿 커스터마이징

프로젝트에 맞게 템플릿을 수정할 수 있습니다:

1. 필요한 템플릿을 복사
2. 프로젝트에 맞게 수정
3. `PROJECT.md`에 커스텀 템플릿 경로 지정

```yaml
# PROJECT.md 예시
templates:
  security_review: "templates/security/custom-review.md"
  api_contract: "templates/api-contract/custom-openapi.yaml"
```

## 템플릿 추가 가이드

새 템플릿을 추가할 때:

1. 적절한 하위 폴더에 파일 생성
2. 플레이스홀더는 `{{PLACEHOLDER}}` 형식 사용
3. 이 README.md의 폴더 구조 섹션 업데이트
4. 관련 SKILL.md에서 템플릿 참조 추가
