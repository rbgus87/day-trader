# 버전 관리 전략

Team-Init 템플릿 자체와 프로젝트의 버전 관리 가이드.

## 템플릿 버전 관리

### 시맨틱 버저닝

```yaml
versioning:
  format: "MAJOR.MINOR.PATCH"
  current: "1.0.0"

  rules:
    major: "역할 추가/제거, Phase 구조 변경, 호환되지 않는 SKILL 형식 변경"
    minor: "새 프레임워크 가이드 추가, 프로젝트 템플릿 추가, 기능 확장"
    patch: "버그 수정, 오타 수정, 기존 가이드 개선"
```

### 변경 로그 형식

```markdown
## [1.1.0] - 2025-XX-XX

### Added
- Remix, Astro 프레임워크 가이드
- 모바일/백엔드 프레임워크 가이드
- CI/CD 파이프라인 템플릿
- 프레임워크 자동 감지 기능

### Changed
- TDD를 Phase 4 → Phase 3으로 이동 (버그 수정)
- TEAM.md를 TEAM-QUICK.md + TEAM-DETAILED.md로 분리

### Fixed
- templates/README.md 누락 파일 참조 수정
```

## 프로젝트 버전 관리

### Git 브랜치 전략

```
main (프로덕션)
  │
  ├── develop (통합)
  │   ├── feature/auth       ← 기능 브랜치
  │   ├── feature/dashboard
  │   └── fix/login-bug      ← 버그 수정
  │
  └── hotfix/critical-fix    ← 긴급 수정 (main에서 분기)
```

### 커밋 컨벤션

```yaml
commit_convention:
  format: "<type>(<scope>): <description>"

  types:
    feat: "새 기능"
    fix: "버그 수정"
    docs: "문서 수정"
    style: "코드 포맷팅 (기능 변화 없음)"
    refactor: "리팩토링"
    test: "테스트 추가/수정"
    chore: "빌드, 설정 변경"
    perf: "성능 개선"

  examples:
    - "feat(auth): 소셜 로그인 추가"
    - "fix(api): 토큰 만료 시 자동 갱신"
    - "docs(readme): 설치 가이드 업데이트"
```

### 릴리즈 프로세스

```
1. develop에서 release 브랜치 생성
   git checkout -b release/v1.2.0 develop

2. 버전 번호 업데이트 + 최종 테스트

3. main에 머지 + 태그
   git checkout main
   git merge release/v1.2.0
   git tag v1.2.0

4. develop에 역머지
   git checkout develop
   git merge release/v1.2.0

5. release 브랜치 삭제
```

## 템플릿 업데이트 적용

기존 프로젝트에 새 버전의 템플릿을 적용하는 방법:

```yaml
update_strategy:
  # 안전한 파일 (덮어쓰기 가능)
  safe_overwrite:
    - "skills/team/*/SKILL.md"
    - "skills/team/_compact/*.md"
    - "frameworks/*.md"
    - "templates/*.md"

  # 머지 필요 (사용자 커스텀 가능성)
  merge_required:
    - "CLAUDE.md"
    - "TEAM-QUICK.md"
    - "PLUGINS.md"
    - "PROJECT.md"

  # 건드리지 않음
  never_touch:
    - "PROJECT.md"  # 프로젝트별 고유 설정
```
