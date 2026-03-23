# Remix Quick Reference (Compact)

**Framework**: Remix | **라우팅**: `app/routes/` | **Core**: React + Vite + Web Standards
**TypeScript**: 권장 (`strict: true`, `~/*` 경로 별칭)

## 디렉토리 구조

> **FATAL RULE**: 데이터 로딩은 반드시 `loader`, 데이터 변경은 반드시 `action`에서 수행.
> **CWD 판단 후 init**: CWD에 `.claude/`있으면 → `create-remix .` | 없으면 → `create-remix [name]`
> **절대 금지**: `useEffect+fetch`로 초기 데이터 로딩, `loader`에서 DB write, `.server.ts` 접미사 누락
> **검증 필수**: `vite.config.ts` + `app/root.tsx` 존재 + `remix.config.js` 없어야 정상

```
app/
├── root.tsx                # 루트 레이아웃 (필수, html/body)
├── routes/                 # 파일 기반 라우팅
│   ├── _index.tsx          # / (홈)
│   ├── posts._index.tsx    # /posts (목록)
│   ├── posts.$id.tsx       # /posts/:id (동적)
│   ├── _auth.tsx           # 레이아웃 라우트 (URL 미포함)
│   ├── _auth.login.tsx     # /login (_auth 레이아웃 적용)
│   ├── dashboard.tsx       # /dashboard 레이아웃 (Outlet)
│   └── api.posts.ts        # Resource Route (JSON API)
├── components/             # React 컴포넌트 (ui/, features/, layouts/)
├── models/*.server.ts      # DB 쿼리 함수 (서버 전용)
├── lib/                    # 유틸리티, 헬퍼
│   ├── utils.ts            # 순수 함수
│   └── db.server.ts        # DB 연결 (서버 전용)
└── hooks/                  # 커스텀 React Hooks
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `routes/*.tsx` | loader/action, 컴포넌트 조합 (얇게) | 복잡한 비즈니스 로직, 직접 DB 접근 |
| `components/` | UI 렌더링 (props/hooks) | loader/action 정의, 직접 DB 접근 |
| `models/*.server.ts` | DB 쿼리 함수 | React hooks, 클라이언트 코드 |
| `lib/*.server.ts` | 서버 전용 유틸 (DB, 인증) | 클라이언트에서 import |
| `lib/` | 순수 함수, 포맷팅 | React hooks, 상태 관리 |
| `hooks/` | 클라이언트 로직 재사용 | DB 접근, 서버 전용 코드 |

**데이터 흐름**: `models/services(서버)` → `loader(로딩)` → `컴포넌트(UI)` → `Form(제출)` → `action(변경)` → `자동 revalidation`

> **코드 생성 트리거**: loader/action/컴포넌트 코드를 **작성**할 때는
> 반드시 `frameworks/remix.md`의 **코드 예시와 안티패턴**을 로드하여 참조할 것.
> compact 테이블만으로 코드 생성 금지.

## 데이터 페칭

| 도구 | 사용 시점 | SSR | 비고 |
|------|----------|-----|------|
| `loader` + `useLoaderData` | 페이지 데이터 로딩 | ✅ | 기본 권장 |
| `action` + `Form` | 폼 제출, 데이터 변경 | ✅ | 자동 revalidation |
| `useFetcher` | 폼 없는 변경, 검색 | ✅ | 인라인 인터랙션 |
| Resource Route (`.ts`) | JSON API, 웹훅 | ✅ | UI 없는 라우트 |

## 핵심 패턴

```tsx
// loader + useLoaderData
export async function loader({ params }: LoaderFunctionArgs) {
  const post = await getPost(params.id!)
  if (!post) throw new Response('Not Found', { status: 404 })
  return json({ post })
}
export default function PostPage() {
  const { post } = useLoaderData<typeof loader>()
  return <article>{post.title}</article>
}

// action + Form → redirect (자동 revalidation)
export async function action({ request }: ActionFunctionArgs) {
  const formData = await request.formData()
  await createPost({ title: formData.get('title') as string })
  return redirect('/posts')
}
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| `useEffect+fetch`로 데이터 로딩 | `loader` + `useLoaderData` 사용 |
| `loader`에서 DB write | `action` 사용 (POST/PUT/DELETE) |
| `.server.ts` 접미사 누락 | DB/인증 파일에 `.server.ts` 필수 |
| `Form` 대신 `form` 사용 | Remix `Form` 컴포넌트 사용 |
| `fetch('/api/...')` 직접 호출 | `useFetcher` 사용 |
| 중첩 라우트에서 `Outlet` 누락 | 레이아웃 라우트에 `<Outlet />` 추가 |
| `defer` 데이터에 `await` 사용 | 프로미스 직접 전달 (await 없이) |
| 라우트에 비즈니스 로직 직접 작성 | `models/`, `services/`로 분리 |

> **전체 가이드**: `frameworks/remix.md` 참조
