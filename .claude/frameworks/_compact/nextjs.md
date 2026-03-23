# Next.js Quick Reference (Compact)

**Framework**: Next.js App Router | **src/**: `src/app/` | **Core**: React 19 + Server Components + Turbopack
**TypeScript**: 필수 (`strict: true`, `@/*` 경로 별칭)

## 디렉토리 구조

> **FATAL RULE**: App Router와 Pages Router를 절대 혼용 금지.
> **CWD 판단 후 init**: CWD에 `.claude/`있으면 → `create-next-app .` | 없으면 → `create-next-app [name]`
> **절대 금지**: `mkdir pages`, `mkdir src/pages` ← App Router와 충돌!
> **검증 필수**: `next.config.ts` + `src/app/layout.tsx` 존재 + `pages/` 없어야 정상

```
src/app/                    # App Router (파일 기반 라우팅)
├── layout.tsx              # 루트 레이아웃 (필수, html/body)
├── page.tsx, loading.tsx   # 홈 / Suspense 로딩 UI
├── error.tsx, not-found.tsx # Error Boundary ('use client' 필수) / 404
├── (group)/                # 라우트 그룹 (URL 미포함)
├── posts/[id]/page.tsx     # 동적 라우트
└── api/posts/route.ts      # Route Handlers (GET, POST, ...)
src/components/             # React 컴포넌트 (ui/, features/, layouts/)
src/lib/                    # 유틸리티, DB 커넥션, 헬퍼
src/actions/                # Server Actions ('use server')
src/hooks/                  # 커스텀 React Hooks (클라이언트 전용)
src/middleware.ts           # Next.js 미들웨어 (하나만 존재)
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `app/**/page.tsx` | 라우트 + 데이터 페칭 조합 (얇게) | 복잡한 비즈니스 로직, 클라이언트 상태 |
| Server Components | 서버 데이터 표시 (`async/await`) | `useState`, `useEffect`, 이벤트 핸들러 |
| Client Components (`'use client'`) | 인터랙티브 UI, hooks | 직접 DB 접근, 서버 전용 코드 |
| `actions/` (`'use server'`) | 뮤테이션, `revalidatePath/Tag` | React hooks, 클라이언트 상태 |
| `lib/` | 순수 함수, DB, 설정 | React hooks, 컴포넌트 렌더링 |
| `hooks/` | 클라이언트 로직 재사용 | DB 접근, 서버 전용 코드 |
| `app/api/` Route Handlers | 외부 API, 웹훅 전용 | React 컴포넌트, hooks |

**데이터 흐름**: `Server Components(fetch)` → `Client Components(UI)` → `Server Actions(mutate)` → `revalidate`

> **코드 생성 시**: 반드시 `frameworks/nextjs.md`의 레이어별 책임 분리 **코드 예시와 안티패턴**을 로드하여 참조할 것.

## 데이터 페칭

| 도구 | 사용 시점 | SSR | 비고 |
|------|----------|-----|------|
| Server Component 직접 fetch/DB | 페이지 데이터 로딩 | ✅ | 기본 권장 |
| Server Actions (`'use server'`) | 폼 제출, 데이터 변경 | ✅ | `revalidatePath` |
| Route Handlers (`route.ts`) | 외부 API, 웹훅 | ✅ | 내부는 SC 사용 |
| SWR / TanStack Query | 실시간, 폴링 | ❌ | CC 전용 |

## 핵심 패턴

```tsx
// Server Component 데이터 페칭
export default async function PostsPage() {
  const posts = await db.post.findMany()
  return <PostList posts={posts} />
}
// Server Action (actions/posts.ts)
'use server'
export async function createPost(formData: FormData) {
  await db.post.create({ data: { title: formData.get('title') as string } })
  revalidatePath('/posts')
}
// Route Segment Config
export const revalidate = 3600          // ISR
export const dynamic = 'force-dynamic'  // SSR
export const runtime = 'edge'           // Edge Runtime
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| `'use client'` 없이 `useState`/`useEffect` | 파일 최상단에 `'use client'` 추가 |
| App Router + Pages Router 혼용 | App Router만 사용, `pages/` 삭제 |
| 불필요한 `'use client'` 남발 | Server Component 기본, 인터랙션만 Client |
| Client Component에서 DB 접근 | Server Action 또는 Route Handler 사용 |
| `layout.tsx`에서 `searchParams` 접근 | `page.tsx`에서만 접근 가능 |
| 불필요한 Route Handler 생성 | Server Component가 직접 접근 가능 |
| Server Action을 GET 대용으로 사용 | 조회는 Server Component에서 직접 |

> **전체 가이드**: `frameworks/nextjs.md` 참조
