# PROJECT.md - Web 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "web"
```

---

## 🌐 Web 스택 설정

```yaml
web:
  type: "SSR"                 # SPA | SSR | SSG | Hybrid

  frontend:
    framework: "Nuxt.js"      # Nuxt.js | Next.js | SvelteKit | Remix | Astro
    language: "TypeScript"
    styling: "TailwindCSS"    # TailwindCSS | CSS Modules | Styled-Components
    ui_library: "shadcn-vue"  # shadcn-vue | shadcn/ui | Vuetify | MUI | Radix
    state: "Pinia"            # Pinia | Zustand | Redux | Jotai | TanStack Query

  backend:
    type: "BaaS"              # BaaS | Custom | Serverless | Edge
    service: "Supabase"       # Supabase | Firebase | Appwrite | Convex
    runtime: "Node.js"        # Node.js | Bun | Deno
    database: "PostgreSQL"    # PostgreSQL | MySQL | MongoDB | PlanetScale

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
  # disabled_roles:
  #   - devops      # 서버리스/BaaS 사용 시
  auto_security_review: true
  default_mode: "hybrid"      # auto | step | hybrid
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
    format: "conventional"    # conventional | simple | gitmoji

  branching:
    strategy: "github-flow"   # github-flow | git-flow | trunk-based
    main: "main"
    feature: "feature/*"
```

## Web 요구사항

```yaml
web_requirements:
  auth:
    providers: ["email"]      # email | google | github | apple | kakao
    mfa: false
    session: "jwt"            # jwt | session | oauth2

  i18n:
    enabled: false
    default_locale: "ko"
    locales: ["ko", "en"]

  accessibility:
    wcag_level: "AA"          # A | AA | AAA

  performance:
    lcp: 2500                 # Largest Contentful Paint (ms)
    fid: 100                  # First Input Delay (ms)
    cls: 0.1                  # Cumulative Layout Shift
    ttfb: 800                 # Time to First Byte (ms)

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
    - SUPABASE_URL
    - SUPABASE_KEY
  optional:
    - SENTRY_DSN
    - LOG_LEVEL
    - GOOGLE_ANALYTICS_ID
```

## 문서화 설정

```yaml
documentation:
  language: "ko"              # ko | en | both
  auto_generate:
    readme: true
    api_docs: true
    changelog: true
  format:
    api: "OpenAPI"
    code: "TSDoc"
```
