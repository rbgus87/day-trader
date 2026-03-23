# Astro Quick Reference (Compact)

**Framework**: Astro | **라우팅**: `src/pages/` | **Core**: Island Architecture + Vite
**TypeScript**: 권장 (`astro/tsconfigs/strict` 확장)

## 디렉토리 구조

> **FATAL RULE**: `.astro` 컴포넌트는 서버 전용 — frontmatter에서 `window`/`document` 접근 금지.
> **절대 금지**: `client:` 없이 프레임워크 컴포넌트 JS 동작 기대 ← 정적 HTML만 출력!
> **절대 금지**: `.astro` 컴포넌트에 `client:` 디렉티브 ← .astro는 Island이 아님!
> **검증 필수**: `astro.config.mjs` + `src/pages/` 존재

```
src/
├── pages/              # 파일 기반 라우팅 (.astro, .md, .mdx, .ts)
├── layouts/            # 페이지 레이아웃 (<html>, <body>, <slot />)
├── components/         # Astro 컴포넌트 (정적 UI)
│   └── islands/        # 프레임워크 Island (React/Vue/Svelte)
├── content/            # Content Collections (config.ts + Markdown/MDX/JSON)
├── styles/             # 전역 스타일
└── utils/              # 순수 함수
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `pages/` | 라우트 + 데이터 페칭 + 레이아웃 조합 | 비즈니스 로직, 브라우저 API |
| `layouts/` | 공통 HTML 구조 (`<slot />`) | 데이터 페칭, 비즈니스 로직 |
| `components/*.astro` | 정적 UI (props, `<slot />`) | 브라우저 API, 클라이언트 상태 |
| `components/islands/*` | 인터랙티브 UI (`client:*` 필수) | 직접 DB 접근, Astro API |
| `content/` | 구조화 콘텐츠 (스키마 + 쿼리) | 런타임 코드 |
| `utils/` | 순수 함수 | 상태, 사이드이펙트 |

**데이터 흐름**: `content/` → `pages/` → `layouts/` → `components/` → `islands/`

> **코드 생성 시**: 반드시 `frameworks/astro.md`의 **코드 예시와 안티패턴**을 로드하여 참조할 것.

## Content Collections & Island 디렉티브

```typescript
// src/content/config.ts
const blog = defineCollection({ type: 'content', schema: z.object({ title: z.string(), pubDate: z.coerce.date() }) });
// 쿼리: const posts = await getCollection('blog', ({ data }) => !data.draft);
```

| 디렉티브 | 시점 | 용도 |
|----------|------|------|
| `client:load` | 즉시 | 내비게이션, 모달 |
| `client:idle` | idle 시 | TOC, 사이드바 (기본) |
| `client:visible` | 뷰포트 | 댓글, 차트 |
| `client:media` | 쿼리 매치 | 모바일 전용 |
| `client:only` | 클라이언트만 | Canvas, WebGL |
| *(없음)* | JS 0바이트 | 정적 콘텐츠 |

## 핵심 패턴

```astro
---
// 동적 라우트 + Content Collection
export async function getStaticPaths() {
  return (await getCollection('blog')).map((p) => ({ params: { slug: p.slug }, props: { post: p } }));
}
const { Content } = await Astro.props.post.render();
---
<Content />
```

```javascript
// 어댑터: 'static' (기본) | 'hybrid' (일부 SSR) | 'server' (전체 SSR)
export default defineConfig({ output: 'server', adapter: node({ mode: 'standalone' }) });
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| frontmatter에서 `window`/`document` | `<script>` 또는 Island 사용 |
| `client:` 없이 프레임워크 컴포넌트 | `client:load`/`idle`/`visible` 추가 |
| `.astro`에 `client:` 디렉티브 | React/Vue/Svelte 컴포넌트로 작성 |
| `client:load` 과다 사용 | `client:idle`/`visible`로 변경 |
| Content Collection 스키마 누락 | `content/config.ts`에 정의 |
| SSR에서 어댑터 미설정 | `output: 'server'` + 어댑터 |
| Island 간 상태 공유 | nanostores 또는 이벤트 사용 |

> **전체 가이드**: `frameworks/astro.md` 참조
