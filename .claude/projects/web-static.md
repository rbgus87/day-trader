# PROJECT.md - Web Static/SSG 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "web"
```

---

## 🌐 Web Static (SSG) 스택 설정

```yaml
web:
  type: "SSG"                 # Static Site Generation

  frontend:
    framework: "Astro"        # Astro | Hugo | Gatsby | Nuxt (generate) | Next (export)
    language: "TypeScript"
    styling: "TailwindCSS"    # TailwindCSS | CSS Modules | Sass
    ui_library: "none"        # shadcn | DaisyUI | none
    islands: true             # Astro Islands / Partial Hydration

  content:
    source: "markdown"        # markdown | mdx | cms | api
    cms: "none"               # Contentlayer | Sanity | Strapi | none
    collections: true         # 콘텐츠 컬렉션 사용

  infrastructure:
    hosting: "Cloudflare Pages"  # Cloudflare Pages | Netlify | Vercel | GitHub Pages
    ci_cd: "GitHub Actions"
    cdn: true
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles:
    - backend                 # 정적 사이트, 서버 불필요 (CMS 사용 시 활성화)
    - devops                  # 정적 호스팅, 복잡한 인프라 불필요
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

## SSG 요구사항

```yaml
ssg_requirements:
  performance:
    lighthouse_score: 95      # 목표 Lighthouse 점수
    max_page_size: 200        # KB (HTML + Critical CSS)
    max_bundle_size: 100      # KB (JavaScript)

  seo:
    sitemap: true
    robots: true
    structured_data: true
    open_graph: true
    rss_feed: true            # 블로그/뉴스 사이트

  build:
    incremental: true         # 증분 빌드
    image_optimization: true  # 이미지 자동 최적화
    prefetch: true            # 링크 프리페칭

  content:
    search: false             # 클라이언트 사이드 검색 (Pagefind 등)
    pagination: true
    toc: true                 # Table of Contents 자동 생성

  accessibility:
    wcag_level: "AA"
```

## 환경변수

```yaml
env_vars:
  optional:
    - SITE_URL
    - ANALYTICS_ID
    - CMS_API_KEY
    - SEARCH_API_KEY
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
```
