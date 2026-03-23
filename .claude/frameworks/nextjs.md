# Next.js Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-10

Next.js App Router 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`npx create-next-app@latest`으로 항상 최신 버전을 설치합니다.

---

## 1. 디렉토리 구조

### FATAL RULE: App Router / Pages Router 혼용 방지

> **Next.js App Router가 기본입니다.**
> `npx create-next-app@latest` 실행 시 App Router를 선택합니다.
> **Pages Router(`pages/`)와 App Router(`app/`)를 절대 혼용하지 마세요.**

#### 절대 금지 사항

```bash
# ⛔ 아래 패턴은 어떤 상황에서도 절대 금지:
# App Router 프로젝트에 pages/ 디렉토리 생성
mkdir pages                           # → App Router와 충돌!
mkdir src/pages                       # → App Router와 충돌!

# app/ 내부에 page.js 없이 라우트 생성 시도
mkdir src/app/about                   # → page.tsx 없으면 라우트 아님!

# 'use client' 없이 클라이언트 기능 사용
# useState, useEffect 등을 Server Component에서 사용 금지!
```

#### 의사결정 트리 (반드시 이 순서대로)

```
STEP 1: CWD(현재 작업 디렉토리)를 확인한다

  CWD에 .claude/ 폴더, package.json, 또는 기존 프로젝트 파일이 있는가?
  ├── YES → CWD가 이미 프로젝트 루트
  │         → npx create-next-app@latest .
  │         (현재 폴더를 그대로 프로젝트 루트로 사용)
  │
  └── NO  → CWD는 프로젝트의 상위 폴더
            → npx create-next-app@latest [project-name]
            → cd [project-name]
            (새 서브폴더가 프로젝트 루트가 됨)

STEP 2: 구조 검증 (건너뛰기 금지)

  [PASS 조건 — 3개 모두 충족]
    ✅ next.config.ts (또는 .mjs) 가 CWD에 존재
    ✅ package.json 이 CWD에 존재
    ✅ src/app/layout.tsx 가 존재

  [FATAL 조건 — 하나라도 해당하면 수정 필수]
    ❌ src/pages/ 디렉토리가 존재 → App Router와 충돌!
    ❌ pages/ 디렉토리가 존재 → App Router와 충돌!
    ❌ app/과 pages/ 모두 존재 → 라우팅 혼란!
```

#### 검증 명령어

```bash
# 프로젝트 루트에서 확인
ls next.config.*     # ✅ 존재해야 함
ls package.json      # ✅ 존재해야 함
ls src/app/layout.tsx # ✅ 존재해야 함

# Pages Router 혼용 체크 (하나라도 존재하면 FATAL)
ls src/pages/         # ❌ 존재하면 안 됨
ls pages/             # ❌ 존재하면 안 됨
```

### Next.js 기본 구조

```
project-root/                       # ← next.config.ts, package.json이 여기에 위치
├── src/                            # src/ 컨벤션 (권장)
│   ├── app/                        # App Router (파일 기반 라우팅)
│   │   ├── layout.tsx              # 루트 레이아웃 (필수)
│   │   ├── page.tsx                # 홈 페이지 (/)
│   │   ├── loading.tsx             # 로딩 UI
│   │   ├── error.tsx               # 에러 UI ('use client' 필수)
│   │   ├── not-found.tsx           # 404 페이지
│   │   ├── global-error.tsx        # 전역 에러 ('use client' 필수)
│   │   ├── (auth)/                 # 라우트 그룹 (URL 미포함)
│   │   │   ├── login/page.tsx
│   │   │   └── register/page.tsx
│   │   ├── dashboard/
│   │   │   ├── layout.tsx          # 중첩 레이아웃
│   │   │   ├── page.tsx
│   │   │   └── loading.tsx
│   │   ├── posts/
│   │   │   ├── page.tsx            # /posts
│   │   │   └── [id]/
│   │   │       └── page.tsx        # /posts/:id
│   │   └── api/                    # Route Handlers
│   │       └── posts/
│   │           └── route.ts        # GET, POST, PUT, DELETE
│   ├── components/                 # React 컴포넌트
│   │   ├── ui/                     # 기본 UI (Button, Input, Card)
│   │   ├── features/               # 기능 단위 (LoginForm, UserCard)
│   │   └── layouts/                # 레이아웃 구성 (Header, Footer, Sidebar)
│   ├── lib/                        # 유틸리티, 헬퍼 함수
│   │   ├── utils.ts                # 순수 함수
│   │   ├── db.ts                   # DB 연결 (서버 전용)
│   │   └── auth.ts                 # 인증 로직
│   ├── actions/                    # Server Actions
│   │   ├── posts.ts                # 게시글 관련 액션
│   │   └── auth.ts                 # 인증 관련 액션
│   ├── hooks/                      # 커스텀 React Hooks (클라이언트)
│   │   ├── use-posts.ts
│   │   └── use-auth.ts
│   ├── types/                      # TypeScript 타입 정의
│   ├── styles/                     # 전역 스타일
│   └── middleware.ts               # Next.js 미들웨어 (src/ 루트)
├── public/                         # 정적 파일 (빌드 미처리)
├── next.config.ts                  # Next.js 설정 ← 반드시 프로젝트 루트에 위치
├── package.json                    # ← 반드시 프로젝트 루트에 위치
└── tsconfig.json                   # TypeScript 설정
```

### 레이어별 책임 분리 (Architecture Rules)

각 디렉토리는 명확한 책임 범위를 가집니다. **경계를 넘는 코드는 금지합니다.**

| 레이어 | 책임 | 허용 | 금지 |
|--------|------|------|------|
| `app/**/page.tsx` | 라우트 정의, 데이터 페칭 조합 | Server Component 데이터 페칭, 컴포넌트 배치, metadata export | 복잡한 비즈니스 로직, 클라이언트 상태 관리 |
| Server Components | 서버 데이터 표시 | `async/await`, DB 접근, 서버 전용 코드 | `useState`, `useEffect`, 이벤트 핸들러, 브라우저 API |
| Client Components | 인터랙티브 UI | `'use client'`, hooks, 이벤트 핸들러 | 직접 DB 접근, `fs` 모듈, 서버 전용 코드 |
| `actions/` | 서버 뮤테이션 | `'use server'`, DB 변경, `revalidatePath/Tag` | 클라이언트 상태, React hooks |
| `app/api/` (Route Handlers) | 외부 API, 웹훅 | `NextRequest`/`NextResponse`, 스트리밍 | React 컴포넌트, hooks |
| `components/` | UI 렌더링 | props, 슬롯(children), 스타일링 | 직접 DB 접근, Server Actions 정의 |
| `lib/` | 순수 함수, 설정 | 데이터 변환, 포맷팅, DB 커넥션 | React hooks, 상태, 컴포넌트 렌더링 |
| `hooks/` | 클라이언트 로직 재사용 | `use*` 커스텀 hooks, 상태 관리 | DB 접근, 서버 전용 코드 |

#### 데이터 흐름 (단방향)

```
Server Components  →  Client Components  →  Server Actions  →  revalidate
  (데이터 페칭)        (인터랙션 UI)          (뮤테이션)         (캐시 갱신)

Route Handlers ← 외부 서비스/웹훅
```

#### 올바른 분리 예시

```typescript
// ✅ lib/utils.ts - 순수 함수 (상태 없음)
export function formatDate(date: Date): string {
  return new Intl.DateTimeFormat('ko').format(date)
}

export function formatPrice(amount: number): string {
  return new Intl.NumberFormat('ko', { style: 'currency', currency: 'KRW' }).format(amount)
}
```

```typescript
// ✅ actions/posts.ts - Server Action (뮤테이션)
'use server'

import { revalidatePath } from 'next/cache'
import { db } from '@/lib/db'
import { z } from 'zod'

const CreatePostSchema = z.object({
  title: z.string().min(1).max(200),
  content: z.string().min(1),
})

export async function createPost(formData: FormData) {
  const validated = CreatePostSchema.safeParse({
    title: formData.get('title'),
    content: formData.get('content'),
  })

  if (!validated.success) {
    return { error: validated.error.flatten().fieldErrors }
  }

  await db.post.create({ data: validated.data })
  revalidatePath('/posts')
}
```

```tsx
// ✅ components/features/PostCard.tsx - UI만 담당 (Server Component)
interface PostCardProps {
  post: Post
}

export function PostCard({ post }: PostCardProps) {
  return (
    <article className="rounded-lg border p-4">
      <h2 className="text-lg font-semibold">{post.title}</h2>
      <p className="text-muted-foreground">{formatDate(post.createdAt)}</p>
      <p>{post.excerpt}</p>
    </article>
  )
}
```

```tsx
// ✅ components/features/DeleteButton.tsx - 인터랙티브 UI
'use client'

import { deletePost } from '@/actions/posts'

export function DeleteButton({ postId }: { postId: string }) {
  return (
    <button onClick={() => deletePost(postId)}>삭제</button>
  )
}
```

```tsx
// ✅ app/posts/page.tsx - 얇은 조합 레이어 (Server Component)
import { db } from '@/lib/db'
import { PostCard } from '@/components/features/PostCard'

export default async function PostsPage() {
  const posts = await db.post.findMany({ orderBy: { createdAt: 'desc' } })

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
// ❌ Server Component에서 클라이언트 기능 사용
export default function Page() {
  const [count, setCount] = useState(0)  // 'use client' 없이 useState 사용 불가!
  return <button onClick={() => setCount(count + 1)}>{count}</button>
}

// ❌ Client Component에서 직접 DB 접근
'use client'
import { db } from '@/lib/db'  // 클라이언트 번들에 DB 코드 포함됨!
export function UserList() {
  const users = await db.user.findMany()  // 브라우저에서 실행 불가!
}

// ❌ Server Action을 Client Component 파일 안에서 정의
'use client'
async function serverAction() {  // 'use server'를 inline에서만 사용 가능
  'use server'
  // ...이 패턴은 혼란을 초래
}
```

---

## 2. 데이터 페칭

### 도구 선택

| 도구 | 사용 시점 | SSR | 캐시 |
|------|----------|-----|------|
| Server Component `fetch` | 페이지/레이아웃 데이터 로딩 | ✅ | ✅ (자동) |
| Server Actions (`'use server'`) | 폼 제출, 데이터 변경 | ✅ | revalidate 트리거 |
| Route Handlers (`route.ts`) | 외부 API, 웹훅, 스트리밍 | ✅ | 설정 가능 |
| 클라이언트 `fetch` / SWR / TanStack Query | 실시간 데이터, 폴링 | ❌ | 라이브러리 제공 |

### Server Component 데이터 페칭 (권장)

```typescript
// app/posts/page.tsx - 서버에서 직접 데이터 접근
import { db } from '@/lib/db'

export default async function PostsPage() {
  // 서버에서 직접 DB 조회 (API 라우트 불필요)
  const posts = await db.post.findMany({
    orderBy: { createdAt: 'desc' },
    take: 20,
  })

  return <PostList posts={posts} />
}
```

```typescript
// 외부 API fetch (자동 캐시 + 중복 제거)
async function getPost(id: string) {
  const res = await fetch(`https://api.example.com/posts/${id}`, {
    next: { revalidate: 3600 }  // 1시간 ISR
  })
  if (!res.ok) throw new Error('Failed to fetch post')
  return res.json()
}

// 병렬 데이터 페칭
export default async function DashboardPage() {
  const [user, posts, stats] = await Promise.all([
    getUser(),
    getPosts(),
    getStats(),
  ])
  return <Dashboard user={user} posts={posts} stats={stats} />
}
```

### Server Actions (뮤테이션)

```typescript
// actions/posts.ts
'use server'

import { revalidatePath, revalidateTag } from 'next/cache'
import { redirect } from 'next/navigation'

export async function createPost(formData: FormData) {
  const post = await db.post.create({
    data: {
      title: formData.get('title') as string,
      content: formData.get('content') as string,
    }
  })
  revalidatePath('/posts')
  redirect(`/posts/${post.id}`)
}

export async function deletePost(id: string) {
  await db.post.delete({ where: { id } })
  revalidateTag('posts')
}
```

```tsx
// 폼에서 Server Action 사용
// app/posts/new/page.tsx
import { createPost } from '@/actions/posts'

export default function NewPostPage() {
  return (
    <form action={createPost}>
      <input name="title" required />
      <textarea name="content" required />
      <button type="submit">작성</button>
    </form>
  )
}
```

### Route Handlers

```typescript
// app/api/posts/route.ts
import { NextRequest, NextResponse } from 'next/server'

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams
  const page = Number(searchParams.get('page') ?? 1)

  const posts = await db.post.findMany({
    skip: (page - 1) * 20,
    take: 20,
  })

  return NextResponse.json({ data: posts, meta: { page } })
}

export async function POST(request: NextRequest) {
  const body = await request.json()
  const post = await db.post.create({ data: body })
  return NextResponse.json({ data: post }, { status: 201 })
}
```

### 안티패턴

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| Server Component에서 `useEffect`로 데이터 페칭 | 불필요한 클라이언트 워터폴 | `async` Server Component에서 직접 fetch |
| Client Component에서 DB 직접 접근 | 보안 위험 + 번들 비대화 | Server Action 또는 Route Handler 사용 |
| 불필요한 Route Handler 생성 | Server Component가 직접 접근 가능 | 외부 API/웹훅 전용으로만 사용 |
| `fetch`에 캐시 설정 누락 | 매 요청마다 재실행 | `next: { revalidate }` 또는 `cache` 옵션 설정 |
| Server Action에서 `redirect` 미사용 | 뮤테이션 후 수동 라우팅 필요 | `redirect()` 또는 `revalidatePath()` 사용 |

---

## 3. 레이아웃 & 라우팅

### 레이아웃 패턴

```tsx
// app/layout.tsx - 루트 레이아웃 (필수, <html>, <body> 포함)
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: { default: 'My App', template: '%s | My App' },
  description: '앱 설명',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  )
}

// app/dashboard/layout.tsx - 중첩 레이아웃
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1">{children}</main>
    </div>
  )
}
```

### 로딩 & 에러 상태

```tsx
// app/posts/loading.tsx - Suspense 자동 래핑
export default function Loading() {
  return <div className="animate-pulse">로딩 중...</div>
}

// app/posts/error.tsx - Error Boundary 자동 래핑 ('use client' 필수)
'use client'

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <div>
      <h2>오류가 발생했습니다</h2>
      <p>{error.message}</p>
      <button onClick={reset}>다시 시도</button>
    </div>
  )
}

// app/not-found.tsx - 404 페이지
export default function NotFound() {
  return (
    <div>
      <h2>페이지를 찾을 수 없습니다</h2>
    </div>
  )
}
```

### 라우트 그룹 & 동적 라우트

```
app/
├── (marketing)/           # 라우트 그룹 - URL에 미포함
│   ├── layout.tsx         # 마케팅 전용 레이아웃
│   ├── page.tsx           # /
│   └── about/page.tsx     # /about
├── (dashboard)/
│   ├── layout.tsx         # 대시보드 전용 레이아웃
│   └── dashboard/page.tsx # /dashboard
├── posts/
│   ├── [id]/page.tsx      # /posts/:id (동적)
│   └── [...slug]/page.tsx # /posts/a/b/c (Catch-all)
└── [[...slug]]/page.tsx   # 선택적 Catch-all
```

---

## 4. 미들웨어

```typescript
// src/middleware.ts (src/ 루트에 위치, 하나만 존재)
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export function middleware(request: NextRequest) {
  // 인증 체크
  const token = request.cookies.get('session')?.value

  if (request.nextUrl.pathname.startsWith('/dashboard') && !token) {
    return NextResponse.redirect(new URL('/login', request.url))
  }

  // 헤더 추가
  const response = NextResponse.next()
  response.headers.set('x-request-id', crypto.randomUUID())
  return response
}

// 미들웨어 적용 범위 설정
export const config = {
  matcher: [
    // 정적 파일, _next 제외
    '/((?!_next/static|_next/image|favicon.ico).*)',
  ],
}
```

**미들웨어 주의사항:**
- 프로젝트에 하나만 존재 가능 (`src/middleware.ts`)
- Edge Runtime에서 실행 (Node.js API 일부 사용 불가)
- `matcher`로 적용 범위를 제한하여 성능 최적화
- 무거운 로직 금지 (DB 접근, 복잡한 연산)

---

## 5. SEO & 메타데이터

### 정적 메타데이터

```typescript
// app/posts/page.tsx
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: '게시글 목록',
  description: '최신 게시글을 확인하세요',
  openGraph: {
    title: '게시글 목록',
    description: '최신 게시글을 확인하세요',
    images: ['/images/og-posts.png'],
  },
}
```

### 동적 메타데이터

```typescript
// app/posts/[id]/page.tsx
import type { Metadata } from 'next'

export async function generateMetadata(
  { params }: { params: Promise<{ id: string }> }
): Promise<Metadata> {
  const { id } = await params
  const post = await getPost(id)

  return {
    title: post.title,
    description: post.excerpt,
    openGraph: {
      title: post.title,
      images: [post.thumbnail],
    },
  }
}
```

---

## 6. Route Segment Config (렌더링 전략)

```typescript
// 페이지/레이아웃 파일에서 export로 설정

// 정적 생성 (빌드 시)
export const dynamic = 'force-static'

// 항상 동적 렌더링 (SSR)
export const dynamic = 'force-dynamic'

// ISR (Incremental Static Regeneration)
export const revalidate = 3600  // 1시간마다 재생성

// 런타임 설정
export const runtime = 'edge'       // Edge Runtime
export const runtime = 'nodejs'     // Node.js Runtime (기본값)

// 동적 파라미터 사전 생성
export async function generateStaticParams() {
  const posts = await db.post.findMany({ select: { id: true } })
  return posts.map((post) => ({ id: post.id }))
}
```

### next.config.ts 설정

```typescript
// next.config.ts
import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  // 이미지 최적화
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'cdn.example.com' },
    ],
  },

  // 리다이렉트
  async redirects() {
    return [
      { source: '/old-path', destination: '/new-path', permanent: true },
    ]
  },

  // 헤더
  async headers() {
    return [
      {
        source: '/api/:path*',
        headers: [
          { key: 'Access-Control-Allow-Origin', value: '*' },
        ],
      },
    ]
  },
}

export default nextConfig
```

---

## 7. 성능 최적화

### Server Component 우선 원칙

```tsx
// ✅ 기본은 Server Component (번들 크기 0)
export default async function Page() {
  const data = await getData()
  return <div>{data.title}</div>
}

// ✅ 인터랙션 필요한 부분만 Client Component로 분리
// components/features/LikeButton.tsx
'use client'
export function LikeButton({ postId }: { postId: string }) {
  const [liked, setLiked] = useState(false)
  return <button onClick={() => setLiked(!liked)}>좋아요</button>
}

// ✅ Server Component에서 Client Component를 children으로 조합
export default async function PostPage() {
  const post = await getPost(id)
  return (
    <article>
      <h1>{post.title}</h1>
      <p>{post.content}</p>
      <LikeButton postId={post.id} />  {/* 이 부분만 클라이언트 */}
    </article>
  )
}
```

### 이미지 최적화

```tsx
import Image from 'next/image'

// 자동 최적화 (WebP/AVIF, lazy loading, 크기 조정)
<Image src="/hero.jpg" alt="히어로" width={1200} height={600} priority />

// 반응형 이미지
<Image src="/photo.jpg" alt="사진" fill className="object-cover" sizes="(max-width: 768px) 100vw, 50vw" />
```

### 동적 import (코드 분할)

```tsx
import dynamic from 'next/dynamic'

// 클라이언트 전용 컴포넌트 지연 로딩
const Chart = dynamic(() => import('@/components/Chart'), {
  loading: () => <div className="animate-pulse h-64" />,
  ssr: false,
})
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

## 8. TypeScript

### 기본 설정

```json
// tsconfig.json (create-next-app이 자동 생성)
{
  "compilerOptions": {
    "strict": true,
    "paths": { "@/*": ["./src/*"] }
  }
}
```

### 주요 타입

```typescript
import type { Metadata } from 'next'
import type { NextRequest, NextResponse } from 'next/server'

// 페이지 props 타입
interface PageProps {
  params: Promise<{ id: string }>
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>
}

// 레이아웃 props 타입
interface LayoutProps {
  children: React.ReactNode
  params: Promise<{ id: string }>
}

// Server Action 반환 타입
type ActionResult = {
  error?: string
  success?: boolean
}
```

---

## 9. 테스팅

### Vitest + React Testing Library

```typescript
// __tests__/components/PostCard.test.tsx
import { render, screen } from '@testing-library/react'
import { PostCard } from '@/components/features/PostCard'

describe('PostCard', () => {
  it('제목을 렌더링한다', () => {
    render(<PostCard post={{ id: '1', title: '테스트 게시글' }} />)
    expect(screen.getByText('테스트 게시글')).toBeInTheDocument()
  })
})
```

### Playwright E2E

```typescript
// e2e/navigation.spec.ts
import { test, expect } from '@playwright/test'

test('페이지 네비게이션', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('h1')).toBeVisible()

  await page.getByRole('link', { name: '게시글' }).click()
  await expect(page).toHaveURL('/posts')
})
```

---

## 10. 흔한 실수 종합

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| `'use client'` 없이 `useState`/`useEffect` | Server Component에서 hooks 사용 불가 | `'use client'` 파일 최상단에 추가 |
| App Router + Pages Router 혼용 | 라우팅 충돌 | App Router만 사용 |
| Server Component에서 이벤트 핸들러 | `onClick` 등 서버에서 불가 | Client Component로 분리 |
| 불필요한 `'use client'` 남발 | 번들 크기 증가, SSR 이점 상실 | Server Component 기본, 필요한 부분만 Client |
| Client Component에서 `async` | Client Component는 async 불가 | `useEffect` 또는 서버에서 데이터 전달 |
| `fetch`에 `cache: 'no-store'` 과다 사용 | 성능 저하 | 적절한 `revalidate` 설정 |
| Server Action을 GET 요청 대용으로 사용 | 의도와 다른 사용법 | 데이터 조회는 Server Component에서 직접 |
| `layout.tsx`에서 `searchParams` 접근 | 레이아웃은 searchParams를 받지 않음 | `page.tsx`에서 접근 |
| 미들웨어에서 무거운 연산 | 모든 요청에 지연 발생 | `matcher`로 범위 제한, 경량 로직만 |
| `generateMetadata`에서 중복 fetch | 동일 데이터 이중 요청 | React `cache()` 또는 `fetch` 중복 제거 활용 |
