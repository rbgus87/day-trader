# PROJECT.md - Fullstack 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "fullstack"
```

---

## 📦 Fullstack 스택 설정

```yaml
fullstack:
  # 프론트엔드
  frontend:
    framework: "Nuxt.js"      # Nuxt.js | Next.js | SvelteKit | Remix
    language: "TypeScript"
    styling: "TailwindCSS"
    ui_library: "shadcn-vue"  # shadcn-vue | shadcn/ui | Vuetify | MUI
    state: "Pinia"            # Pinia | Zustand | Redux | TanStack Query

  # 백엔드
  backend:
    framework: "Hono"         # Hono | Express | Fastify | NestJS | tRPC
    runtime: "Bun"            # Node.js | Bun | Deno
    language: "TypeScript"
    orm: "Drizzle"            # Drizzle | Prisma | TypeORM | Kysely
    validation: "Zod"         # Zod | Valibot | Yup

  # 데이터베이스
  database:
    primary: "PostgreSQL"     # PostgreSQL | MySQL | MongoDB
    host: "Supabase"          # Supabase | PlanetScale | Neon | Self-hosted
    cache: "none"             # Redis | none
    search: "none"            # Elasticsearch | Meilisearch | none

  # API 스타일
  api:
    style: "REST"             # REST | GraphQL | tRPC
    realtime: false           # WebSocket / SSE

  # 인프라
  infrastructure:
    frontend_hosting: "Vercel"    # Vercel | Netlify | Cloudflare
    backend_hosting: "Railway"    # Railway | Fly.io | Render | AWS
    ci_cd: "GitHub Actions"
    containerization: false
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

## Fullstack 요구사항

```yaml
fullstack_requirements:
  # 인증
  auth:
    providers: ["email"]      # email | google | github | apple | kakao
    mfa: false
    session: "jwt"            # jwt | session

  # 다국어
  i18n:
    enabled: false
    default_locale: "ko"
    locales: ["ko", "en"]

  # 접근성
  accessibility:
    wcag_level: "AA"

  # 성능 (프론트엔드)
  frontend_performance:
    lcp: 2500
    fid: 100
    cls: 0.1
    ttfb: 800

  # 성능 (백엔드)
  backend_performance:
    api_latency: 200          # ms (p95)
    throughput: 1000          # requests/second

  # SEO
  seo:
    sitemap: true
    robots: true
    structured_data: false
```

## 환경변수

```yaml
env_vars:
  required:
    - NODE_ENV
    - DATABASE_URL
  optional:
    - SUPABASE_URL
    - SUPABASE_KEY
    - REDIS_URL
    - SENTRY_DSN
    - LOG_LEVEL
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    api_docs: true
    changelog: true
  format:
    api: "OpenAPI"
    code: "TSDoc"
```
