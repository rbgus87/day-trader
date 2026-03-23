# Remix Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-10

Remix 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`npx create-remix@latest`으로 항상 최신 버전을 설치합니다.

---

## 1. 디렉토리 구조

### FATAL RULE: loader/action 혼용 및 클라이언트 데이터 페칭 방지

> **Remix는 Web 표준 기반 풀스택 프레임워크입니다.**
> **데이터 로딩은 반드시 `loader`에서, 데이터 변경은 반드시 `action`에서 수행합니다.**
> **클라이언트에서 직접 `fetch`로 데이터를 가져오는 것은 금지합니다.**

#### 절대 금지 사항

```bash
# ⛔ 아래 패턴은 어떤 상황에서도 절대 금지:

# useEffect + fetch로 초기 데이터 로딩 (loader 사용!)
useEffect(() => { fetch('/api/posts').then(...) }, [])

# loader에서 데이터 변경 (action 사용!)
export async function loader() { await db.post.create(...) }

# action에서 JSON 직접 반환 (redirect 또는 데이터 반환!)
export async function action() { return new Response('ok') }

# 라우트 파일 외부에서 loader/action export
# loader/action은 반드시 app/routes/ 내 라우트 파일에서만 export
```

#### 의사결정 트리 (반드시 이 순서대로)

```
STEP 1: CWD(현재 작업 디렉토리)를 확인한다

  CWD에 .claude/ 폴더, package.json, 또는 기존 프로젝트 파일이 있는가?
  ├── YES → CWD가 이미 프로젝트 루트
  │         → npx create-remix@latest .
  │         (현재 폴더를 그대로 프로젝트 루트로 사용)
  │
  └── NO  → CWD는 프로젝트의 상위 폴더
            → npx create-remix@latest [project-name]
            → cd [project-name]
            (새 서브폴더가 프로젝트 루트가 됨)

STEP 2: 구조 검증 (건너뛰기 금지)

  [PASS 조건 — 3개 모두 충족]
    ✅ vite.config.ts (Remix Vite 플러그인 포함) 가 CWD에 존재
    ✅ package.json 이 CWD에 존재
    ✅ app/root.tsx 가 존재

  [FATAL 조건 — 하나라도 해당하면 수정 필수]
    ❌ app/routes/ 없이 pages/ 디렉토리 존재 → Remix는 routes/ 사용!
    ❌ remix.config.js 존재 (Vite 이전 레거시) → Vite 설정으로 마이그레이션!
    ❌ loader 안에서 DB write 수행 → action으로 이동!
```

#### 검증 명령어

```bash
# 프로젝트 루트에서 확인
ls vite.config.ts    # ✅ 존재해야 함
ls package.json      # ✅ 존재해야 함
ls app/root.tsx      # ✅ 존재해야 함

# 레거시 설정 체크 (존재하면 마이그레이션 필요)
ls remix.config.js   # ❌ 존재하면 안 됨 (Vite로 마이그레이션)
ls pages/            # ❌ 존재하면 안 됨 (routes/ 사용)
```

### Remix 기본 구조

```
project-root/                       # ← vite.config.ts, package.json이 여기에 위치
├── app/                            # 애플리케이션 코드
│   ├── root.tsx                    # 루트 레이아웃 (필수, <html>, <body>)
│   ├── entry.client.tsx            # 클라이언트 엔트리
│   ├── entry.server.tsx            # 서버 엔트리
│   ├── routes/                     # 파일 기반 라우팅
│   │   ├── _index.tsx              # 홈 페이지 (/)
│   │   ├── about.tsx               # /about
│   │   ├── posts._index.tsx        # /posts (목록)
│   │   ├── posts.$id.tsx           # /posts/:id (동적)
│   │   ├── posts.$id_.edit.tsx     # /posts/:id/edit (중첩 탈출)
│   │   ├── _auth.tsx               # 레이아웃 라우트 (URL 미포함)
│   │   ├── _auth.login.tsx         # /login (auth 레이아웃 적용)
│   │   ├── _auth.register.tsx      # /register (auth 레이아웃 적용)
│   │   ├── dashboard.tsx           # /dashboard 레이아웃
│   │   ├── dashboard._index.tsx    # /dashboard (인덱스)
│   │   ├── dashboard.settings.tsx  # /dashboard/settings
│   │   └── api.posts.ts            # Resource Route (JSON API)
│   ├── components/                 # React 컴포넌트
│   │   ├── ui/                     # 기본 UI (Button, Input, Card)
│   │   ├── features/               # 기능 단위 (LoginForm, PostCard)
│   │   └── layouts/                # 레이아웃 구성 (Header, Footer, Sidebar)
│   ├── lib/                        # 유틸리티, 헬퍼 함수
│   │   ├── utils.ts                # 순수 함수
│   │   ├── db.server.ts            # DB 연결 (서버 전용 — .server 접미사)
│   │   └── auth.server.ts          # 인증 로직 (서버 전용)
│   ├── models/                     # 데이터 모델 / DB 쿼리 함수
│   │   ├── post.server.ts          # 게시글 DB 쿼리
│   │   └── user.server.ts          # 사용자 DB 쿼리
│   ├── services/                   # 외부 API, 비즈니스 로직
│   │   └── email.server.ts         # 이메일 서비스 (서버 전용)
│   ├── hooks/                      # 커스텀 React Hooks
│   │   ├── use-debounce.ts
│   │   └── use-form-validation.ts
│   └── types/                      # TypeScript 타입 정의
├── public/                         # 정적 파일 (빌드 미처리)
├── vite.config.ts                  # Vite + Remix 플러그인 설정
├── package.json                    # ← 반드시 프로젝트 루트에 위치
└── tsconfig.json                   # TypeScript 설정
```

### 레이어별 책임 분리 (Architecture Rules)

각 디렉토리는 명확한 책임 범위를 가집니다. **경계를 넘는 코드는 금지합니다.**

| 레이어 | 책임 | 허용 | 금지 |
|--------|------|------|------|
| `routes/*.tsx` | 라우트 정의, loader/action, 컴포넌트 조합 | `loader`, `action`, `meta`, `ErrorBoundary`, 컴포넌트 배치 | 복잡한 비즈니스 로직, DB 직접 접근 (models 사용) |
| `components/` | UI 렌더링 | props, 이벤트 핸들러, hooks | loader/action 정의, 직접 DB 접근 |
| `models/*.server.ts` | DB 쿼리 함수 | DB 접근, 데이터 변환, 검증 | React hooks, 컴포넌트 렌더링, 클라이언트 코드 |
| `services/*.server.ts` | 외부 API, 비즈니스 로직 | 외부 서비스 호출, 복잡한 비즈니스 로직 | 클라이언트 상태, React hooks |
| `lib/` | 순수 함수, 설정 | 데이터 변환, 포맷팅, 공통 유틸 | React hooks, 상태, 컴포넌트 렌더링 |
| `lib/*.server.ts` | 서버 전용 유틸 | DB 커넥션, 인증, 세션 | 클라이언트 코드에서 import |
| `hooks/` | 클라이언트 로직 재사용 | `use*` 커스텀 hooks, 상태 관리 | DB 접근, 서버 전용 코드 |

#### 데이터 흐름 (단방향)

```
models/services (서버)  →  loader (데이터 로딩)  →  컴포넌트 (UI 렌더링)
                                                        │
                                                    Form 제출
                                                        │
                                                        ▼
                          action (데이터 변경)  →  자동 revalidation
```

#### `.server` 접미사 규칙

```typescript
// ✅ .server.ts 파일: 서버에서만 실행, 클라이언트 번들에서 자동 제외
// app/lib/db.server.ts
// app/models/post.server.ts
// app/services/email.server.ts

// ⛔ .server.ts 없이 DB/비밀키 사용 → 클라이언트 번들에 포함될 위험!
// app/lib/db.ts       ← 위험!
// app/models/post.ts  ← 위험!
```

#### 올바른 분리 예시

```typescript
// ✅ models/post.server.ts - DB 쿼리 함수 (서버 전용)
import { db } from '~/lib/db.server'

export async function getPosts(page: number = 1, limit: number = 20) {
  return db.post.findMany({
    skip: (page - 1) * limit,
    take: limit,
    orderBy: { createdAt: 'desc' },
  })
}

export async function getPost(id: string) {
  return db.post.findUnique({ where: { id } })
}

export async function createPost(data: { title: string; content: string; authorId: string }) {
  return db.post.create({ data })
}

export async function deletePost(id: string) {
  return db.post.delete({ where: { id } })
}
```

```typescript
// ✅ lib/utils.ts - 순수 함수 (상태 없음)
export function formatDate(date: Date): string {
  return new Intl.DateTimeFormat('ko').format(date)
}

export function formatPrice(amount: number): string {
  return new Intl.NumberFormat('ko', { style: 'currency', currency: 'KRW' }).format(amount)
}
```

```tsx
// ✅ components/features/PostCard.tsx - UI만 담당
interface PostCardProps {
  post: { id: string; title: string; excerpt: string; createdAt: string }
}

export function PostCard({ post }: PostCardProps) {
  return (
    <article className="rounded-lg border p-4">
      <h2 className="text-lg font-semibold">{post.title}</h2>
      <p className="text-muted-foreground">{formatDate(new Date(post.createdAt))}</p>
      <p>{post.excerpt}</p>
    </article>
  )
}
```

```tsx
// ✅ routes/posts._index.tsx - 얇은 조합 레이어
import type { LoaderFunctionArgs } from '@remix-run/node'
import { useLoaderData } from '@remix-run/react'
import { getPosts } from '~/models/post.server'
import { PostCard } from '~/components/features/PostCard'

export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url)
  const page = Number(url.searchParams.get('page') ?? '1')
  const posts = await getPosts(page)
  return { posts, page }
}

export default function PostsPage() {
  const { posts } = useLoaderData<typeof loader>()
  return (
    <div>
      {posts.map((post) => (
        <PostCard key={post.id} post={post} />
      ))}
    </div>
  )
}
```

#### 안티패턴: 경계 위반

```tsx
// ❌ 컴포넌트에서 직접 DB 접근
import { db } from '~/lib/db.server' // 컴포넌트에서 서버 모듈 import!
export function PostList() {
  const posts = db.post.findMany() // 불가능!
}

// ❌ useEffect로 초기 데이터 로딩 (loader 사용!)
export default function PostsPage() {
  const [posts, setPosts] = useState([])
  useEffect(() => {
    fetch('/api/posts').then(r => r.json()).then(setPosts)
  }, [])
}

// ❌ loader에서 데이터 변경 (action 사용!)
export async function loader() {
  await db.post.create({ data: { title: 'New Post' } })
  return { success: true }
}

// ❌ 라우트 파일에 비즈니스 로직 직접 작성 (models로 분리!)
export async function loader() {
  const posts = await db.post.findMany({
    where: { published: true },
    orderBy: { createdAt: 'desc' },
    include: { author: true, tags: true },
  }) // → models/post.server.ts로 이동
}
```

---

## 2. 데이터 페칭

### 도구 선택

| 도구 | 사용 시점 | SSR | 자동 revalidation |
|------|----------|-----|-------------------|
| `loader` + `useLoaderData` | 페이지 데이터 로딩 | ✅ | ✅ (action 후 자동) |
| `action` + Form | 폼 제출, 데이터 변경 | ✅ | ✅ |
| `useFetcher` | 폼 없이 데이터 변경, 검색 등 | ✅ | ✅ |
| Resource Route (`*.ts`) | JSON API, 파일 다운로드 | ✅ | ❌ |

### loader 패턴 (데이터 로딩)

```typescript
// routes/posts._index.tsx
import type { LoaderFunctionArgs } from '@remix-run/node'
import { json } from '@remix-run/node'
import { useLoaderData } from '@remix-run/react'
import { getPosts } from '~/models/post.server'

export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url)
  const page = Number(url.searchParams.get('page') ?? '1')
  const query = url.searchParams.get('q') ?? ''

  const posts = await getPosts({ page, query })
  return json({ posts, page, query })
}

export default function PostsPage() {
  const { posts, page, query } = useLoaderData<typeof loader>()
  return (
    <div>
      {posts.map((post) => <PostCard key={post.id} post={post} />)}
    </div>
  )
}
```

```typescript
// 동적 라우트: routes/posts.$id.tsx
export async function loader({ params }: LoaderFunctionArgs) {
  const post = await getPost(params.id!)
  if (!post) {
    throw new Response('게시글을 찾을 수 없습니다', { status: 404 })
  }
  return json({ post })
}
```

```typescript
// 병렬 데이터 페칭
export async function loader({ request }: LoaderFunctionArgs) {
  const [user, posts, stats] = await Promise.all([
    getUser(request),
    getPosts(),
    getStats(),
  ])
  return json({ user, posts, stats })
}
```

### action 패턴 (데이터 변경)

```typescript
// routes/posts.new.tsx
import type { ActionFunctionArgs } from '@remix-run/node'
import { json, redirect } from '@remix-run/node'
import { Form, useActionData } from '@remix-run/react'
import { z } from 'zod'
import { createPost } from '~/models/post.server'
import { requireUserId } from '~/lib/auth.server'

const CreatePostSchema = z.object({
  title: z.string().min(1, '제목은 필수입니다').max(200),
  content: z.string().min(1, '내용은 필수입니다'),
})

export async function action({ request }: ActionFunctionArgs) {
  const userId = await requireUserId(request)
  const formData = await request.formData()
  const raw = Object.fromEntries(formData)
  const result = CreatePostSchema.safeParse(raw)

  if (!result.success) {
    return json(
      { errors: result.error.flatten().fieldErrors },
      { status: 400 }
    )
  }

  const post = await createPost({ ...result.data, authorId: userId })
  return redirect(`/posts/${post.id}`)
}

export default function NewPostPage() {
  const actionData = useActionData<typeof action>()

  return (
    <Form method="post">
      <input name="title" />
      {actionData?.errors?.title && <span>{actionData.errors.title[0]}</span>}
      <textarea name="content" />
      {actionData?.errors?.content && <span>{actionData.errors.content[0]}</span>}
      <button type="submit">작성</button>
    </Form>
  )
}
```

### useFetcher 패턴 (인라인 인터랙션)

```tsx
// 폼 없이 데이터 변경 (좋아요, 삭제 등)
import { useFetcher } from '@remix-run/react'

export function DeleteButton({ postId }: { postId: string }) {
  const fetcher = useFetcher()
  const isDeleting = fetcher.state !== 'idle'

  return (
    <fetcher.Form method="post" action={`/posts/${postId}`}>
      <input type="hidden" name="intent" value="delete" />
      <button type="submit" disabled={isDeleting}>
        {isDeleting ? '삭제 중...' : '삭제'}
      </button>
    </fetcher.Form>
  )
}

// 검색 자동완성 (GET fetcher)
export function SearchBox() {
  const fetcher = useFetcher<typeof loader>()

  return (
    <div>
      <fetcher.Form method="get" action="/api/search">
        <input
          name="q"
          onChange={(e) => fetcher.submit(e.currentTarget.form)}
        />
      </fetcher.Form>
      {fetcher.data?.results.map((r) => <div key={r.id}>{r.title}</div>)}
    </div>
  )
}
```

### Resource Route (JSON API)

```typescript
// routes/api.posts.ts - loader만 export (UI 없음)
import type { LoaderFunctionArgs } from '@remix-run/node'
import { json } from '@remix-run/node'

export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url)
  const q = url.searchParams.get('q') ?? ''
  const posts = await searchPosts(q)
  return json({ results: posts })
}

// action도 가능 (웹훅 등)
export async function action({ request }: ActionFunctionArgs) {
  const body = await request.json()
  await processWebhook(body)
  return json({ ok: true })
}
```

### 안티패턴

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| `useEffect` + `fetch`로 데이터 로딩 | SSR 무시, SEO 손실, 워터폴 | `loader` + `useLoaderData` 사용 |
| `loader`에서 데이터 변경 | HTTP 의미 위반, GET에서 변경 | `action` 사용 (POST/PUT/DELETE) |
| `action`에서 `redirect` 누락 | 뮤테이션 후 stale 데이터 표시 | `redirect()` 또는 `json()` 반환 |
| 컴포넌트에서 `.server.ts` import | 서버 코드 클라이언트 번들 포함 | loader/action에서 호출 후 데이터 전달 |
| `fetch('/api/...')` 직접 사용 | Remix 자동 revalidation 우회 | `useFetcher` 사용 |

---

## 3. 중첩 라우트 & 레이아웃

### 파일 명명 규칙

```
app/routes/
├── _index.tsx                  # / (루트 인덱스)
├── about.tsx                   # /about
├── posts._index.tsx            # /posts
├── posts.$id.tsx               # /posts/:id (중첩 — dashboard의 Outlet에 렌더링)
├── posts.$id_.edit.tsx         # /posts/:id/edit (trailing _ = 중첩 탈출)
├── _auth.tsx                   # 레이아웃 라우트 (URL 미포함, _ prefix)
├── _auth.login.tsx             # /login (_auth 레이아웃 적용)
├── _auth.register.tsx          # /register (_auth 레이아웃 적용)
├── dashboard.tsx               # /dashboard 레이아웃 (Outlet 포함)
├── dashboard._index.tsx        # /dashboard
├── dashboard.settings.tsx      # /dashboard/settings
├── files.$.tsx                 # /files/* (Splat/Catch-all 라우트)
└── ($lang).about.tsx           # /about 또는 /ko/about (선택적 세그먼트)
```

### Outlet (중첩 렌더링)

```tsx
// routes/dashboard.tsx - 레이아웃 라우트
import { Outlet } from '@remix-run/react'

export default function DashboardLayout() {
  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1">
        <Outlet />  {/* 자식 라우트가 여기에 렌더링 */}
      </main>
    </div>
  )
}
```

### 라우트 그룹 (경로가 없는 레이아웃)

```tsx
// routes/_auth.tsx - URL에 _auth는 포함되지 않음
import { Outlet } from '@remix-run/react'

export default function AuthLayout() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="w-full max-w-md">
        <Outlet />
      </div>
    </div>
  )
}
```

---

## 4. 에러 처리

### ErrorBoundary

```tsx
// routes/posts.$id.tsx
import { isRouteErrorResponse, useRouteError } from '@remix-run/react'

export function ErrorBoundary() {
  const error = useRouteError()

  if (isRouteErrorResponse(error)) {
    return (
      <div>
        <h1>{error.status} {error.statusText}</h1>
        <p>{error.data}</p>
      </div>
    )
  }

  return (
    <div>
      <h1>오류가 발생했습니다</h1>
      <p>{error instanceof Error ? error.message : '알 수 없는 오류'}</p>
    </div>
  )
}
```

### 에러 던지기 패턴

```typescript
// loader에서 Response 던지기 (예상 에러)
export async function loader({ params }: LoaderFunctionArgs) {
  const post = await getPost(params.id!)
  if (!post) {
    throw new Response('게시글을 찾을 수 없습니다', { status: 404 })
  }
  return json({ post })
}

// json 헬퍼로 에러 데이터 전달
export async function action({ request }: ActionFunctionArgs) {
  try {
    await processPayment(request)
    return redirect('/success')
  } catch (error) {
    throw new Response('결제 처리 실패', { status: 500 })
  }
}
```

### 루트 ErrorBoundary

```tsx
// app/root.tsx
export function ErrorBoundary() {
  const error = useRouteError()

  return (
    <html lang="ko">
      <head>
        <title>오류</title>
        <Meta />
        <Links />
      </head>
      <body>
        <div className="error-container">
          <h1>문제가 발생했습니다</h1>
          {isRouteErrorResponse(error) ? (
            <p>{error.status}: {error.data}</p>
          ) : (
            <p>예상치 못한 오류가 발생했습니다</p>
          )}
          <a href="/">홈으로 돌아가기</a>
        </div>
        <Scripts />
      </body>
    </html>
  )
}
```

---

## 5. Meta & SEO

### meta 함수

```typescript
import type { MetaFunction } from '@remix-run/node'

// 정적 메타데이터
export const meta: MetaFunction = () => {
  return [
    { title: '게시글 목록 | My App' },
    { name: 'description', content: '최신 게시글을 확인하세요' },
    { property: 'og:title', content: '게시글 목록' },
    { property: 'og:description', content: '최신 게시글을 확인하세요' },
  ]
}

// 동적 메타데이터 (loader 데이터 활용)
export const meta: MetaFunction<typeof loader> = ({ data }) => {
  if (!data) {
    return [{ title: '게시글을 찾을 수 없습니다' }]
  }

  return [
    { title: `${data.post.title} | My App` },
    { name: 'description', content: data.post.excerpt },
    { property: 'og:title', content: data.post.title },
    { property: 'og:image', content: data.post.thumbnail },
  ]
}

// 부모 라우트 메타데이터 병합
export const meta: MetaFunction<typeof loader, { root: typeof rootLoader }> = ({
  data,
  matches,
}) => {
  const parentMeta = matches.flatMap((match) => match.meta ?? [])
  return [
    ...parentMeta.filter((m) => !('title' in m)),
    { title: `${data?.post.title} | My App` },
  ]
}
```

---

## 6. 스트리밍 & defer

```typescript
import { defer } from '@remix-run/node'
import { Await, useLoaderData } from '@remix-run/react'
import { Suspense } from 'react'

export async function loader({ params }: LoaderFunctionArgs) {
  // 중요 데이터는 await (SSR 포함)
  const post = await getPost(params.id!)

  // 비중요 데이터는 프로미스 직접 전달 (스트리밍)
  const commentsPromise = getComments(params.id!)
  const relatedPromise = getRelatedPosts(params.id!)

  return defer({
    post,                         // ✅ 즉시 렌더링
    comments: commentsPromise,     // ✅ 스트리밍 (await 없음!)
    related: relatedPromise,       // ✅ 스트리밍
  })
}

export default function PostPage() {
  const { post, comments, related } = useLoaderData<typeof loader>()

  return (
    <article>
      <h1>{post.title}</h1>
      <p>{post.content}</p>

      {/* 스트리밍 데이터 표시 */}
      <Suspense fallback={<div className="animate-pulse">댓글 로딩 중...</div>}>
        <Await resolve={comments}>
          {(comments) => (
            <div>{comments.map((c) => <Comment key={c.id} comment={c} />)}</div>
          )}
        </Await>
      </Suspense>

      <Suspense fallback={<div className="animate-pulse">관련 글 로딩 중...</div>}>
        <Await resolve={related} errorElement={<p>관련 글 로딩 실패</p>}>
          {(posts) => <RelatedPosts posts={posts} />}
        </Await>
      </Suspense>
    </article>
  )
}
```

---

## 7. 세션 & 인증

```typescript
// lib/session.server.ts
import { createCookieSessionStorage, redirect } from '@remix-run/node'

const sessionStorage = createCookieSessionStorage({
  cookie: {
    name: '__session',
    httpOnly: true,
    maxAge: 60 * 60 * 24 * 30, // 30일
    path: '/',
    sameSite: 'lax',
    secrets: [process.env.SESSION_SECRET!],
    secure: process.env.NODE_ENV === 'production',
  },
})

export async function getSession(request: Request) {
  return sessionStorage.getSession(request.headers.get('Cookie'))
}

export async function createUserSession(userId: string, redirectTo: string) {
  const session = await sessionStorage.getSession()
  session.set('userId', userId)
  return redirect(redirectTo, {
    headers: { 'Set-Cookie': await sessionStorage.commitSession(session) },
  })
}

export async function requireUserId(request: Request): Promise<string> {
  const session = await getSession(request)
  const userId = session.get('userId')
  if (!userId) throw redirect('/login')
  return userId
}

export async function logout(request: Request) {
  const session = await getSession(request)
  return redirect('/login', {
    headers: { 'Set-Cookie': await sessionStorage.destroySession(session) },
  })
}
```

---

## 8. 배포 어댑터

| 어댑터 | 환경 | 패키지 |
|--------|------|--------|
| Node.js | Express, Docker | `@remix-run/node` (기본) |
| Vercel | Vercel | `@remix-run/vercel` |
| Cloudflare Pages | Cloudflare | `@remix-run/cloudflare` |
| Cloudflare Workers | Workers | `@remix-run/cloudflare` |
| Deno | Deno Deploy | `@remix-run/deno` |
| Architect | AWS Lambda | `@remix-run/architect` |

### Vite 설정

```typescript
// vite.config.ts
import { vitePlugin as remix } from '@remix-run/dev'
import { defineConfig } from 'vite'
import tsconfigPaths from 'vite-tsconfig-paths'

export default defineConfig({
  plugins: [
    remix({
      // 기본 라우트 규칙 (파일 기반)
      // 커스텀 라우트 필요 시:
      // routes(defineRoutes) {
      //   return defineRoutes((route) => {
      //     route('/custom', 'routes/custom.tsx')
      //   })
      // },
    }),
    tsconfigPaths(),
  ],
})
```

---

## 9. 성능 최적화

### prefetch (링크 프리페치)

```tsx
import { Link } from '@remix-run/react'

// intent: 호버/포커스 시 프리페치 (기본값)
<Link to="/posts" prefetch="intent">게시글</Link>

// render: 렌더링 시 즉시 프리페치
<Link to="/about" prefetch="render">About</Link>

// viewport: 뷰포트에 보일 때 프리페치
<Link to="/posts/1" prefetch="viewport">Post 1</Link>

// none: 프리페치 비활성화
<Link to="/heavy-page" prefetch="none">Heavy</Link>
```

### 캐시 헤더

```typescript
export async function loader() {
  const posts = await getPosts()
  return json({ posts }, {
    headers: {
      'Cache-Control': 'public, max-age=300, s-maxage=3600',
    },
  })
}
```

### 성능 예산

```yaml
metrics:
  LCP: < 2.5s
  INP: < 200ms
  CLS: < 0.1
  TTFB: < 800ms
bundle:
  initial: < 200KB (gzip)
  chunk: < 50KB (gzip)
```

---

## 10. TypeScript

### 기본 설정

```json
// tsconfig.json
{
  "compilerOptions": {
    "strict": true,
    "paths": { "~/*": ["./app/*"] }
  }
}
```

### 주요 타입

```typescript
import type {
  LoaderFunctionArgs,
  ActionFunctionArgs,
  MetaFunction,
} from '@remix-run/node'
import type { useLoaderData } from '@remix-run/react'

// loader 반환 타입은 typeof loader로 추론
export async function loader({ request, params }: LoaderFunctionArgs) {
  return json({ post: await getPost(params.id!) })
}
// useLoaderData<typeof loader>() → 자동 타입 추론
```

---

## 11. 테스팅

### Vitest + React Testing Library

```typescript
// __tests__/components/PostCard.test.tsx
import { render, screen } from '@testing-library/react'
import { PostCard } from '~/components/features/PostCard'

describe('PostCard', () => {
  it('제목을 렌더링한다', () => {
    render(<PostCard post={{ id: '1', title: '테스트', excerpt: '', createdAt: '2026-01-01' }} />)
    expect(screen.getByText('테스트')).toBeInTheDocument()
  })
})
```

### Playwright E2E

```typescript
// e2e/navigation.spec.ts
import { test, expect } from '@playwright/test'

test('게시글 목록 페이지 네비게이션', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('h1')).toBeVisible()

  await page.getByRole('link', { name: '게시글' }).click()
  await expect(page).toHaveURL('/posts')
})

test('게시글 생성', async ({ page }) => {
  await page.goto('/posts/new')
  await page.fill('input[name="title"]', '새 게시글')
  await page.fill('textarea[name="content"]', '게시글 내용')
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL(/\/posts\//)
})
```

---

## 12. 흔한 실수 종합

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| `useEffect` + `fetch`로 데이터 로딩 | SSR 무시, SEO 손실 | `loader` + `useLoaderData` 사용 |
| `loader`에서 데이터 변경 (create/update/delete) | HTTP GET에서 부작용 발생 | `action` 사용 (POST/PUT/DELETE) |
| `.server.ts` 접미사 누락 | 서버 코드 클라이언트 번들 포함 | DB/인증 파일에 `.server.ts` 접미사 필수 |
| `action`에서 `redirect` 또는 `json` 미반환 | 예측 불가능한 동작 | 반드시 Response 반환 |
| `fetch('/api/...')` 직접 호출 | Remix revalidation 우회 | `useFetcher` 사용 |
| `Form` 대신 `form` 사용 | 프로그레시브 인핸스먼트 미적용 | Remix의 `Form` 컴포넌트 사용 |
| 중첩 라우트에서 `Outlet` 누락 | 자식 라우트 미렌더링 | 레이아웃 라우트에 `<Outlet />` 추가 |
| `defer` 데이터에 `await` 사용 | 스트리밍 비활성화 | 프로미스 직접 전달 (await 없이) |
| `meta` 함수에서 부모 메타 덮어쓰기 | 부모 메타데이터 소실 | `matches`로 병합 |
| 라우트 파일에 복잡한 비즈니스 로직 | 유지보수 어려움, 테스트 불가 | `models/`, `services/`로 분리 |
