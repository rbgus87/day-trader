# SvelteKit Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-10

SvelteKit 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`npx sv create`으로 항상 최신 버전을 설치합니다.

---

## 1. 디렉토리 구조

### FATAL RULE: load 함수 파일 혼동 방지

> **`+page.ts`는 서버+클라이언트 양쪽 실행. `+page.server.ts`는 서버에서만 실행.**
> **DB 접근, 비밀키는 반드시 `+page.server.ts` 또는 `+server.ts`에서만 수행.**

```typescript
// ⛔ +page.ts에서 DB 접근 (FATAL: 서버 코드가 클라이언트 번들에 포함!)
import { db } from '$lib/server/db';
export const load = async () => { return { posts: await db.post.findMany() } }

// ⛔ +page.svelte에서 직접 fetch (SSR 데이터 흐름 깨짐 — load 함수 사용!)
// ⛔ +server.ts에서 throw new Error (error() 함수 사용!)
```

#### 의사결정 트리

```
데이터 소스: DB, 비밀키, 서버 전용 라이브러리 필요?
├── YES → +page.server.ts (또는 +server.ts) — 서버 전용
└── NO  → +page.ts — universal load (public API만)

검증: ✅ svelte.config.js + src/routes/ + src/app.html 존재
FATAL: ❌ $lib/server/ 모듈을 +page.ts에서 import
```

### 기본 구조

```
project-root/
├── src/
│   ├── routes/                   # 파일 기반 라우팅
│   │   ├── +page.svelte          # 페이지 컴포넌트
│   │   ├── +page.server.ts       # server load + form actions
│   │   ├── +page.ts              # universal load (서버+클라이언트)
│   │   ├── +layout.svelte        # 레이아웃
│   │   ├── +error.svelte         # 에러 페이지
│   │   ├── +server.ts            # API 엔드포인트
│   │   └── api/.../+server.ts    # REST API
│   ├── lib/                      # $lib alias
│   │   ├── components/           # 재사용 컴포넌트 (ui/, features/)
│   │   ├── server/               # 서버 전용 ($lib/server)
│   │   ├── stores/               # Svelte stores
│   │   ├── utils/                # 유틸리티 함수
│   │   └── types/                # TypeScript 타입
│   ├── app.html / app.d.ts       # HTML 템플릿 / 타입 선언
│   └── hooks.server.ts           # 서버 훅
├── static/                       # 정적 파일
├── svelte.config.js              # SvelteKit 설정
└── vite.config.ts                # Vite 설정
```

### 레이어별 책임 분리

| 레이어 | 허용 | 금지 |
|--------|------|------|
| `+page.svelte` | `data` prop, 컴포넌트 조합, 이벤트 | DB 접근, 비즈니스 로직 |
| `+page.server.ts` | DB, 비밀키, 검증, form actions | 클라이언트 상태, DOM |
| `+page.ts` | public API 호출, 데이터 변환 | DB, 비밀키, `$lib/server` |
| `+server.ts` | JSON 응답, 인증, 검증 | 컴포넌트 렌더링 |
| `lib/components/` | props, events, 슬롯 | 직접 API 호출, 전역 상태 변경 |
| `lib/server/` | DB, 외부 API, 인증 | 클라이언트 코드 |
| `lib/stores/` | `writable`, `derived` | DB, 서버 로직 |
| `lib/utils/` | 데이터 변환, 포맷팅 | 상태, store |

**데이터 흐름**: `lib/server/` → `+page.server.ts` → `+page.svelte` → `lib/components/`

#### 올바른 분리 예시

```typescript
// ✅ lib/server/db.ts - 서버 전용
export const db = drizzle(/* ... */);

// ✅ routes/blog/+page.server.ts - 서버 load
import { db } from '$lib/server/db';
import type { PageServerLoad } from './$types';
export const load: PageServerLoad = async ({ url }) => {
  const page = Number(url.searchParams.get('page') ?? '1');
  return { posts: await db.post.findMany({ skip: (page - 1) * 20, take: 20 }), page };
};
```

```svelte
<!-- ✅ routes/blog/+page.svelte - 데이터 표시만 -->
<script lang="ts">
  import PostCard from '$lib/components/features/PostCard.svelte';
  let { data }: { data: import('./$types').PageData } = $props();
</script>
{#each data.posts as post (post.id)}<PostCard {post} />{/each}

<!-- ✅ lib/components/features/PostCard.svelte - UI만 -->
<script lang="ts">
  let { post, ondelete }: { post: Post; ondelete?: (id: string) => void } = $props();
</script>
<article><h2>{post.title}</h2></article>
```

#### 안티패턴

```typescript
// ❌ +page.ts에서 서버 모듈 import → +page.server.ts 사용
// ❌ +page.svelte에서 onMount+fetch → load 함수 사용
// ❌ 컴포넌트에서 직접 API 호출 → load → data prop으로 전달
```

---

## 2. 데이터 페칭

| 파일 | 실행 위치 | DB | 사용 시점 |
|------|----------|-----|----------|
| `+page.server.ts` | 서버 전용 | ✅ | DB, 비밀키, form actions |
| `+page.ts` | 서버+클라이언트 | ❌ | public API, 데이터 변환 |
| `+layout.server.ts` | 서버 전용 | ✅ | 공통 레이아웃 데이터 |

### API 엔드포인트 (+server.ts)

```typescript
import { json, error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url }) => {
  return json({ posts: await db.post.findMany() });
};
export const POST: RequestHandler = async ({ request, locals }) => {
  if (!locals.user) error(401, '인증 필요');
  const body = await request.json();
  return json({ post: await db.post.create({ data: body }) }, { status: 201 });
};
```

### 부모 데이터 & 무효화

```typescript
// 부모 레이아웃 데이터 접근
const { user } = await parent();

// 의존성 등록 → 나중에 invalidate('app:posts')로 새로고침
export const load: PageServerLoad = async ({ depends }) => {
  depends('app:posts');
  return { posts: await db.post.findMany() };
};
```

---

## 3. Form Actions

```typescript
// routes/posts/create/+page.server.ts
import { fail, redirect } from '@sveltejs/kit';
import { z } from 'zod';

const Schema = z.object({ title: z.string().min(1), content: z.string().min(1) });

export const actions: Actions = {
  default: async ({ request, locals }) => {
    if (!locals.user) return fail(401, { error: '인증 필요' });
    const validated = Schema.safeParse(Object.fromEntries(await request.formData()));
    if (!validated.success) return fail(400, { errors: validated.error.flatten().fieldErrors });
    const post = await db.post.create({ data: { ...validated.data, authorId: locals.user.id } });
    redirect(303, `/posts/${post.id}`);
  }
};
```

```svelte
<!-- +page.svelte: use:enhance로 JS 향상 -->
<script lang="ts">
  import { enhance } from '$app/forms';
  let { form }: { form: import('./$types').ActionData } = $props();
</script>
<form method="POST" use:enhance>
  <input name="title" value={form?.data?.title ?? ''} />
  {#if form?.errors?.title}<span class="error">{form.errors.title[0]}</span>{/if}
  <button type="submit">게시</button>
</form>

<!-- Named actions: action="?/update", action="?/delete" -->
```

---

## 4. 레이아웃 & 페이지 옵션

```svelte
<!-- routes/+layout.svelte -->
<script lang="ts">
  let { data, children }: { data: import('./$types').LayoutData; children: any } = $props();
</script>
<Header user={data.user} /><main>{@render children()}</main>
```

**그룹 레이아웃**: `(app)/` (인증 필요), `(auth)/` (인증 페이지) — URL에 미반영

```typescript
// 페이지 옵션 (+page.ts 또는 +layout.ts에서 export)
export const prerender = true;  // 정적 생성
export const ssr = false;       // CSR only (레이아웃 설정 시 하위 전체 적용)
```

---

## 5. Hooks

```typescript
// src/hooks.server.ts
export const handle: Handle = async ({ event, resolve }) => {
  const token = event.cookies.get('session');
  if (token) event.locals.user = await getUserFromToken(token);
  return resolve(event, {
    transformPageChunk: ({ html }) => html.replace('%lang%', 'ko')
  });
};

export const handleError: HandleServerError = async ({ error }) => {
  console.error('서버 에러:', error);
  return { message: '서버 에러', code: 'INTERNAL_ERROR' };
};

// src/hooks.client.ts
export const handleError: HandleClientError = async ({ error }) => {
  return { message: '오류 발생', code: 'CLIENT_ERROR' };
};
```

---

## 6. 에러 처리

```svelte
<!-- routes/+error.svelte -->
<script lang="ts">
  import { page } from '$app/state';
</script>
<h1>{page.status}</h1><p>{page.error?.message}</p><a href="/">홈으로</a>
```

```typescript
// 예상 에러: error() → +error.svelte로 라우팅
import { error } from '@sveltejs/kit';
error(404, '게시글을 찾을 수 없습니다');
// 예상치 못한 에러: handleError 훅에서 처리
```

---

## 7. 상태 관리

```svelte
<!-- Svelte 5 Runes (컴포넌트 내) -->
<script lang="ts">
  let count = $state(0);
  let doubled = $derived(count * 2);
</script>
```

```typescript
// Svelte Stores (전역 상태) — lib/stores/theme.ts
import { writable } from 'svelte/store';
import { browser } from '$app/environment';

function createThemeStore() {
  const { subscribe, update } = writable<'light' | 'dark'>(
    browser ? (localStorage.getItem('theme') as 'light' | 'dark') ?? 'light' : 'light'
  );
  return {
    subscribe,
    toggle: () => update(t => {
      const next = t === 'light' ? 'dark' : 'light';
      if (browser) localStorage.setItem('theme', next);
      return next;
    })
  };
}
export const theme = createThemeStore();
// ⚠️ browser 체크 없이 localStorage/window 접근 금지. 서버 상태는 event.locals 사용.
```

---

## 8. 어댑터 설정

| 어댑터 | 환경 |
|--------|------|
| `adapter-auto` | 자동 감지 (기본) |
| `adapter-node` | Node.js 서버 |
| `adapter-static` | 정적 사이트 (SPA/SSG) |
| `adapter-vercel` | Vercel |
| `adapter-cloudflare` | Cloudflare Pages |

```javascript
// svelte.config.js
import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
export default {
  preprocess: vitePreprocess(),
  kit: { adapter: adapter({ out: 'build', precompress: true }) }
};
```

---

## 9. 성능 최적화

```typescript
// 스트리밍: await 없이 프로미스 반환
export const load: PageServerLoad = async () => ({
  user: await getUser(),         // 즉시
  analytics: getAnalytics(),     // 스트리밍 (await 없음!)
});

// 프리렌더링 (동적 라우트)
export const entries: EntryGenerator = async () => {
  return (await db.post.findMany({ select: { slug: true } })).map(p => ({ slug: p.slug }));
};
export const prerender = true;
```

---

## 10. TypeScript

```typescript
// app.d.ts — 전역 타입 확장
declare global {
  namespace App {
    interface Locals { user: import('$lib/types').User | null; }
    interface Error { message: string; code?: string; }
  }
}
export {};
// $types는 자동 생성: import type { PageServerLoad, Actions } from './$types';
```

---

## 11. 테스팅

```typescript
// Vitest: src/lib/utils/format.test.ts
import { describe, it, expect } from 'vitest';
import { formatDate } from './format';
describe('formatDate', () => {
  it('한국어 형식', () => { expect(formatDate(new Date('2026-01-15'))).toContain('2026'); });
});

// Playwright E2E: e2e/navigation.spec.ts
import { test, expect } from '@playwright/test';
test('블로그', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: '블로그' }).click();
  await expect(page).toHaveURL('/blog');
});
```

---

## 12. 흔한 실수

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| `+page.ts`에서 `$lib/server` import | 서버 코드 클라이언트 노출 | `+page.server.ts` 사용 |
| `onMount` + fetch로 초기 데이터 | SSR 무시, SEO 손실 | load 함수 사용 |
| `throw new Error()` (서버) | HTTP 상태 없음 | `error(404, 'msg')` |
| store에서 `browser` 미체크 | SSR에서 `window` 에러 | `browser` import 후 체크 |
| `use:enhance` 미사용 | 전체 페이지 리로드 | `use:enhance` 추가 |
| 글로벌 `fetch` (load 내) | 서버 상대경로 실패 | load의 `fetch` 사용 |
| 모듈 레벨 상태 (서버) | 요청 간 오염 | `event.locals` 사용 |
| 스트리밍에서 `await` | 스트리밍 비활성화 | await 없이 프로미스 반환 |
| `depends()` 미호출 | `invalidate()` 안 됨 | `depends('app:key')` 등록 |
