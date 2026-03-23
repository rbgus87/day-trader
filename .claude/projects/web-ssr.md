# PROJECT.md - Web SSR 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "web"
```

---

## 🌐 Web SSR 스택 설정

```yaml
web:
  type: "SSR"                 # Server-Side Rendering

  frontend:
    framework: "Nuxt.js"      # Nuxt.js | Next.js | SvelteKit | Remix
    language: "TypeScript"
    styling: "TailwindCSS"    # TailwindCSS | CSS Modules | Styled-Components
    ui_library: "shadcn-vue"  # shadcn-vue | shadcn/ui | Vuetify | MUI
    state: "Pinia"            # Pinia | Zustand | Redux | Jotai

  backend:
    type: "BaaS"              # BaaS | Custom | Serverless
    service: "Supabase"       # Supabase | Firebase | Appwrite
    runtime: "Node.js"        # Node.js | Bun | Deno
    database: "PostgreSQL"    # PostgreSQL | MySQL | MongoDB

  ssr_config:
    rendering: "hybrid"       # universal | hybrid | islands
    cache_strategy: "SWR"     # SWR | ISR | CDN | none
    edge_runtime: false       # Edge Function 사용 여부

  infrastructure:
    hosting: "Vercel"         # Vercel | Netlify | Cloudflare | AWS
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

## SSR 요구사항

```yaml
ssr_requirements:
  performance:
    ttfb: 200                 # Time to First Byte (ms)
    lcp: 2500                 # Largest Contentful Paint (ms)
    fid: 100                  # First Input Delay (ms)
    cls: 0.1                  # Cumulative Layout Shift

  seo:
    sitemap: true
    robots: true
    structured_data: true
    open_graph: true
    dynamic_meta: true        # 페이지별 동적 메타 태그

  caching:
    cdn: true
    stale_while_revalidate: true
    cache_ttl: 3600           # 초

  auth:
    providers: ["email"]
    session: "jwt"
    ssr_auth: true            # 서버 사이드 인증 처리

  i18n:
    enabled: false
    default_locale: "ko"
    locales: ["ko", "en"]
    seo_friendly: true        # hreflang, 언어별 URL
```

## 환경변수

```yaml
env_vars:
  required:
    - NODE_ENV
    - SUPABASE_URL
    - SUPABASE_KEY
  optional:
    - SENTRY_DSN
    - CDN_URL
    - CACHE_TTL
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
