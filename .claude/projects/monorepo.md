# PROJECT.md - Monorepo 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "monorepo"
```

---

## 📦 Monorepo 스택 설정

```yaml
monorepo:
  # 모노레포 도구 선택
  tool: "Turborepo"           # Turborepo | Nx | pnpm workspaces

  # Turborepo 설정
  turborepo:
    version: "2.x"
    remote_cache: false       # Vercel Remote Cache 사용
    daemon: true

  # 패키지 매니저
  package_manager: "pnpm"     # pnpm | npm | yarn | bun
  pnpm:
    version: "9.x"
    shamefully_hoist: false
    strict_peer_deps: true

  # 패키지 구조
  packages:
    # 애플리케이션
    - name: "web"
      type: "web"
      path: "apps/web"
      framework: "Nuxt.js"

    - name: "admin"
      type: "web"
      path: "apps/admin"
      framework: "Next.js"

    - name: "api"
      type: "backend"
      path: "apps/api"
      framework: "Hono"

    # 공유 라이브러리
    - name: "ui"
      type: "library"
      path: "packages/ui"
      description: "공유 UI 컴포넌트"

    - name: "shared"
      type: "library"
      path: "packages/shared"
      description: "공유 유틸리티, 타입"

    - name: "config"
      type: "config"
      path: "packages/config"
      description: "ESLint, TypeScript 공유 설정"

  # 공유 설정
  shared:
    eslint: true
    prettier: true
    typescript: true
    testing: "Vitest"
    e2e: "Playwright"
    commitlint: true
    changesets: true

  # 의존성 관리
  dependencies:
    sync_versions:
      - "typescript"
      - "react"
      - "vue"
    internal_protocol: "workspace:*"

  # CI/CD 설정
  ci:
    affected_only: true
    parallel: true
    cache: true
    pipeline:
      build:
        depends_on: ["^build"]
        outputs: ["dist/**", ".next/**", ".output/**"]
      test:
        depends_on: ["build"]
      lint:
        outputs: []
      deploy:
        depends_on: ["build", "test", "lint"]
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles: []
  auto_security_review: true
  default_mode: "hybrid"
```

## 프로젝트 컨벤션

```yaml
conventions:
  code_style:
    typescript:
      indent: 2
      quotes: "single"
      semicolon: false

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## Monorepo 요구사항

```yaml
monorepo_requirements:
  # 빌드
  build:
    incremental: true         # 변경된 패키지만 빌드
    cache: true               # 빌드 캐시 사용

  # 배포
  deployment:
    targets:
      web: "Vercel"
      admin: "Vercel"
      api: "Railway"

    environments:
      - development
      - staging
      - production

  # 버전 관리
  versioning:
    strategy: "changesets"    # changesets | lerna | manual
    independent: true         # 각 패키지 독립 버전
```

## 환경변수

```yaml
env_vars:
  # 루트 레벨 (공통)
  root:
    required:
      - NODE_ENV
    optional:
      - TURBO_TOKEN
      - TURBO_TEAM

  # 패키지별
  packages:
    web:
      - SUPABASE_URL
      - SUPABASE_KEY
    api:
      - DATABASE_URL
      - JWT_SECRET
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true           # per-package changelogs
    api_docs: true
  format:
    api: "OpenAPI"
    code: "TSDoc"
```
