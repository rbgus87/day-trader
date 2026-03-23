# Astro Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-10

Astro 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`npm create astro@latest`으로 항상 최신 버전을 설치합니다.

---

## 1. 디렉토리 구조

### FATAL RULE: Astro 컴포넌트에서 클라이언트 JS 오용 방지

> **Astro 컴포넌트(`.astro`)는 서버에서만 실행되며, 클라이언트 JS를 포함하지 않습니다.**
> **인터랙티브 UI가 필요하면 반드시 Island(React/Vue/Svelte) + `client:*` 디렉티브를 사용합니다.**

```astro
---
// ⛔ frontmatter에서 브라우저 API 접근 (서버에서만 실행!)
const width = window.innerWidth;          // → window는 서버에 없음!
document.getElementById('app');           // → document는 서버에 없음!
---
<!-- ⛔ client: 없이 프레임워크 컴포넌트 → 정적 HTML만, JS 없음! -->
<ReactCounter />
<!-- ⛔ 존재하지 않는 디렉티브 -->
<ReactCounter client:hover />             // → client:hover는 없음!
<!-- ⛔ .astro에 client: 디렉티브 → .astro는 Island이 아님! -->
<MyAstroComponent client:load />
```

#### 의사결정 트리

```
CWD에 .claude/, package.json 등 프로젝트 파일이 있는가?
├── YES → npm create astro@latest -- --template minimal .
└── NO  → npm create astro@latest [project-name] && cd [project-name]

검증: ✅ astro.config.mjs + package.json + src/pages/ 존재
FATAL: ❌ .astro에 client: 적용 | ❌ client: 없이 프레임워크 컴포넌트 JS 기대
```

### Astro 기본 구조

```
project-root/
├── src/
│   ├── pages/                      # 파일 기반 라우팅 (.astro, .md, .mdx)
│   │   ├── index.astro             # 홈 (/)
│   │   ├── blog/
│   │   │   ├── index.astro         # /blog
│   │   │   └── [slug].astro        # /blog/:slug
│   │   ├── [...slug].astro         # Catch-all
│   │   ├── 404.astro               # 404 페이지
│   │   └── rss.xml.ts              # RSS 엔드포인트
│   ├── layouts/                    # 페이지 레이아웃 (<html>, <body>)
│   ├── components/                 # 재사용 컴포넌트
│   │   ├── Header.astro            # Astro 컴포넌트 (정적 UI)
│   │   └── islands/                # 프레임워크 Island 컴포넌트
│   │       ├── Counter.tsx         # React Island
│   │       └── Search.vue          # Vue Island
│   ├── content/                    # Content Collections
│   │   ├── config.ts              # 컬렉션 스키마 정의
│   │   └── blog/                   # 블로그 컬렉션 (.md/.mdx)
│   ├── styles/                     # 전역 스타일
│   └── utils/                      # 유틸리티 함수
├── public/                         # 정적 파일
├── astro.config.mjs                # Astro 설정
└── tsconfig.json                   # TypeScript 설정
```

### 레이어별 책임 분리

| 레이어 | 허용 | 금지 |
|--------|------|------|
| `pages/` | frontmatter 데이터 로드, 컴포넌트 배치 | 복잡한 비즈니스 로직, 브라우저 API |
| `layouts/` | `<slot />`, head 메타, 전역 스타일 | 데이터 페칭, 비즈니스 로직 |
| `components/*.astro` | props, `<slot />`, 조건부 렌더링 | 브라우저 API, 클라이언트 상태 |
| `components/islands/*` | 상태 관리, 이벤트, `client:*` 필수 | 직접 DB 접근, Astro API |
| `content/` | 스키마 정의, 타입 안전 쿼리 | 런타임 코드, 컴포넌트 로직 |
| `utils/` | 순수 함수, 데이터 변환 | 상태, 사이드이펙트 |
| `pages/api/*.ts` | `Request`/`Response` (SSR 모드) | 컴포넌트 렌더링 |

**데이터 흐름**: `content/` → `pages/` (frontmatter) → `layouts/` → `components/` → `islands/` (client:*)

#### 올바른 분리 예시

```astro
---
// ✅ pages/blog/[slug].astro - 데이터 페칭 + 레이아웃 조합
import { getCollection } from 'astro:content';
import BlogLayout from '../../layouts/BlogLayout.astro';
import TOC from '../../components/islands/TOC.tsx';
export async function getStaticPaths() {
  const posts = await getCollection('blog');
  return posts.map((post) => ({ params: { slug: post.slug }, props: { post } }));
}
const { post } = Astro.props;
const { Content, headings } = await post.render();
---
<BlogLayout title={post.data.title}>
  <TOC headings={headings} client:idle />
  <Content />
</BlogLayout>
```

```tsx
// ✅ components/islands/TOC.tsx - 인터랙티브 Island
import { useState } from 'react';
export default function TOC({ headings }: { headings: { slug: string; text: string }[] }) {
  const [active, setActive] = useState('');
  return (
    <nav>
      {headings.map((h) => (
        <a key={h.slug} href={`#${h.slug}`} className={active === h.slug ? 'active' : ''}
           onClick={() => setActive(h.slug)}>{h.text}</a>
      ))}
    </nav>
  );
}
```

---

## 2. Island Architecture

### client:* 디렉티브

| 디렉티브 | 동작 | 사용 시점 |
|----------|------|----------|
| `client:load` | 즉시 하이드레이션 | 내비게이션, 모달 (즉시 필요) |
| `client:idle` | 브라우저 idle 시 | TOC, 사이드바 (기본 선택) |
| `client:visible` | 뷰포트 진입 시 | 댓글, 차트 (스크롤 아래) |
| `client:media="(query)"` | 미디어 쿼리 매치 시 | 모바일 전용 UI |
| `client:only="react"` | SSR 건너뜀, 클라이언트만 | Canvas, WebGL |
| *(없음)* | 정적 HTML, JS 0바이트 | 카드, 푸터, 정적 콘텐츠 |

```
인터랙션 필요? → NO → 디렉티브 없음 (JS 0바이트)
  └─ YES → 즉시? → YES → client:load
     └─ NO → 뷰포트? → YES → client:visible
        └─ NO → 미디어 쿼리? → YES → client:media="..."
           └─ NO → client:idle (기본)
SSR 불가? → client:only="react|vue|svelte"
```

### 프레임워크 통합

```javascript
// astro.config.mjs
import react from '@astrojs/react';
import vue from '@astrojs/vue';
import svelte from '@astrojs/svelte';
export default defineConfig({
  integrations: [react(), vue(), svelte()],
});
// ⚠️ 여러 JSX 프레임워크 혼용 시 include/exclude 필수
// react({ include: ['**/react/*'] }), preact({ include: ['**/preact/*'] })
```

```astro
---
// 여러 프레임워크 Island을 하나의 페이지에서 혼용 가능
import ReactSearch from '../components/islands/Search.tsx';
import VueComments from '../components/islands/Comments.vue';
import SvelteToggle from '../components/islands/Toggle.svelte';
---
<ReactSearch client:load />
<VueComments postId={post.id} client:visible />
<SvelteToggle client:idle />
```

---

## 3. Content Collections

### 스키마 정의

```typescript
// src/content/config.ts
import { defineCollection, z } from 'astro:content';
const blog = defineCollection({
  type: 'content',  // Markdown/MDX
  schema: z.object({
    title: z.string(),
    description: z.string(),
    pubDate: z.coerce.date(),
    updatedDate: z.coerce.date().optional(),
    tags: z.array(z.string()).default([]),
    draft: z.boolean().default(false),
    heroImage: z.string().optional(),
  }),
});
const authors = defineCollection({
  type: 'data',  // JSON/YAML
  schema: z.object({ name: z.string(), email: z.string().email(), bio: z.string() }),
});
export const collections = { blog, authors };
```

### 콘텐츠 쿼리

```astro
---
import { getCollection, getEntry } from 'astro:content';
// 필터링 + 정렬
const posts = (await getCollection('blog', ({ data }) => !data.draft))
  .sort((a, b) => b.data.pubDate.valueOf() - a.data.pubDate.valueOf());
// 단일 항목 + 렌더링
const post = await getEntry('blog', 'first-post');
const { Content, headings } = await post.render();
---
```

---

## 4. 동적 라우트 & 페이지네이션

```astro
---
// src/pages/blog/[slug].astro - 정적 동적 라우트
import { getCollection } from 'astro:content';
export async function getStaticPaths() {
  const posts = await getCollection('blog');
  return posts.map((post) => ({ params: { slug: post.slug }, props: { post } }));
}
const { post } = Astro.props;
const { Content } = await post.render();
---
<Content />
```

```astro
---
// src/pages/blog/[...page].astro - 페이지네이션
import type { GetStaticPaths } from 'astro';
import { getCollection } from 'astro:content';
export const getStaticPaths = (async ({ paginate }) => {
  const posts = (await getCollection('blog')).sort((a, b) => b.data.pubDate.valueOf() - a.data.pubDate.valueOf());
  return paginate(posts, { pageSize: 10 });
}) satisfies GetStaticPaths;
const { page } = Astro.props;
---
{page.data.map((post) => <a href={`/blog/${post.slug}`}>{post.data.title}</a>)}
{page.url.prev && <a href={page.url.prev}>이전</a>}
{page.url.next && <a href={page.url.next}>다음</a>}
```

### RSS & 사이트맵

```typescript
// src/pages/rss.xml.ts
import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';
import type { APIContext } from 'astro';
export async function GET(context: APIContext) {
  const posts = await getCollection('blog');
  return rss({
    title: '내 블로그', description: '블로그 설명', site: context.site!,
    items: posts.map((post) => ({
      title: post.data.title, pubDate: post.data.pubDate,
      description: post.data.description, link: `/blog/${post.slug}/`,
    })),
  });
}
```

```javascript
// astro.config.mjs - 사이트맵
import sitemap from '@astrojs/sitemap';
export default defineConfig({ site: 'https://example.com', integrations: [sitemap()] });
```

---

## 5. 어댑터 설정

| 어댑터 | 환경 | 모드 |
|--------|------|------|
| *(없음, 기본)* | 정적 호스팅 | `output: 'static'` |
| `@astrojs/node` | Node.js 서버 | `'server'` / `'hybrid'` |
| `@astrojs/vercel` | Vercel | `'server'` / `'hybrid'` |
| `@astrojs/cloudflare` | Cloudflare Pages | `'server'` / `'hybrid'` |
| `@astrojs/netlify` | Netlify | `'server'` / `'hybrid'` |

```
대부분 정적? → output: 'static' (기본, 어댑터 불필요)
  일부 동적? → output: 'hybrid' + 어댑터 (동적 페이지에 export const prerender = false)
대부분 동적? → output: 'server' + 어댑터 (정적 페이지에 export const prerender = true)
```

```javascript
// Node.js 서버 배포
import node from '@astrojs/node';
export default defineConfig({ output: 'server', adapter: node({ mode: 'standalone' }) });
// Hybrid 모드 (Vercel)
import vercel from '@astrojs/vercel';
export default defineConfig({ output: 'hybrid', adapter: vercel() });
```

---

## 6. API 엔드포인트 & SEO

```typescript
// src/pages/api/posts.ts (SSR 모드 필수)
import type { APIRoute } from 'astro';
export const GET: APIRoute = async ({ url }) => {
  const posts = await db.post.findMany();
  return new Response(JSON.stringify({ data: posts }), {
    headers: { 'Content-Type': 'application/json' },
  });
};
```

```astro
---
// layouts/BaseLayout.astro - SEO 메타데이터
const { title, description = '기본 설명', image = '/og.png' } = Astro.props;
const canonical = new URL(Astro.url.pathname, Astro.site);
---
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <meta name="description" content={description} />
  <link rel="canonical" href={canonical} />
  <meta property="og:title" content={title} />
  <meta property="og:image" content={new URL(image, Astro.url)} />
</head>
<body><slot /></body>
</html>
```

---

## 7. 이미지 & 스타일링

```astro
---
import { Image } from 'astro:assets';
import hero from '../assets/hero.jpg';
---
<Image src={hero} alt="히어로" width={1200} height={600} />

<style>
  /* 자동 스코프 (이 컴포넌트에만 적용) */
  h1 { color: navy; }
</style>
<style is:global>
  /* 전역 스타일 */
  body { margin: 0; }
</style>
```

---

## 8. 흔한 실수 종합

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| `.astro` frontmatter에서 `window`/`document` | 서버 실행 → 브라우저 API 없음 | `<script>` 또는 Island 컴포넌트 |
| `client:` 없이 프레임워크 컴포넌트 | 정적 HTML만, JS 없음 | `client:load`/`idle`/`visible` 추가 |
| `.astro`에 `client:` 디렉티브 | 무시됨, .astro는 서버 전용 | React/Vue/Svelte 컴포넌트로 작성 |
| `client:hover`/`client:click` 사용 | 존재하지 않는 디렉티브 | `load`/`idle`/`visible`/`media`/`only`만 유효 |
| `client:load` 과다 사용 | 번들 크기 증가, 성능 저하 | `client:idle` 또는 `client:visible` |
| Content Collection 스키마 누락 | 타입 안전성 없음 | `src/content/config.ts`에 정의 |
| `getStaticPaths` 미반환 | SSG 빌드 실패 | 동적 라우트에서 모든 경로 반환 |
| SSR 모드에서 어댑터 미설정 | 빌드 실패 | `output: 'server'` + 어댑터 |
| `Astro.glob()` 사용 | deprecated | Content Collections 또는 `import.meta.glob()` |
| Island 간 상태 공유 시도 | Island은 독립 실행 | nanostores, 이벤트, URL 파라미터 |
