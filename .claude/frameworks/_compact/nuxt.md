# Nuxt.js Quick Reference (Compact)

**Framework**: Nuxt.js | **srcDir**: `app/` | **Core**: Vue 3 + Nitro + Vite
**TypeScript**: 필수 (`noUncheckedIndexedAccess` 기본값)

## 디렉토리 구조

> **FATAL RULE**: `nuxi init`은 자동으로 `app/` srcDir를 생성.
> **CWD 판단 후 init**: CWD에 `.claude/`있으면 → `nuxi init .` | 없으면 → `nuxi init [name]`
> **절대 금지**: `nuxi init app`, `nuxi init frontend`, `nuxi init src`, `mkdir app` ← `app/app/` 이중 중첩!
> **검증 필수**: `nuxt.config.ts`가 CWD에 존재 + `app/app/` 디렉토리 없어야 정상

```
app/                    # srcDir (Nuxt 기본)
├── components/         # 자동 임포트 (Vue 컴포넌트)
├── composables/        # 자동 임포트 (use* 함수)
├── layouts/            # 페이지 레이아웃
├── middleware/         # 라우트 미들웨어
├── pages/              # 파일 기반 라우팅
├── plugins/            # Vue 플러그인
├── utils/              # 자동 임포트 (유틸리티)
├── app.vue             # 루트 컴포넌트
└── error.vue           # 에러 페이지
server/                 # 서버 코드 (rootDir)
├── api/                # API 라우트 (filename.METHOD.ts)
├── middleware/         # 서버 미들웨어 (자동 실행)
└── utils/              # 서버 유틸리티 (자동 임포트)
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `pages/` | 라우트 + 컴포넌트 조합 (얇게) | 비즈니스 로직, 직접 API 호출 |
| `components/` | UI 렌더링만 (props/emit) | 직접 API 호출, 전역 상태 변경 |
| `composables/` | 비즈니스 로직 + 데이터 페칭 | DOM 접근, 스타일링 |
| `utils/` | 순수 함수 (상태 없음) | `ref`, `reactive`, API 호출 |
| `stores/` (Pinia) | 복잡한 전역 상태 (3+ 컴포넌트 공유) | UI 로직 |
| `server/api/` | DB, 검증, 외부 API | 클라이언트 상태/Vue API |

**데이터 흐름**: `server/api/` → `composables/` → `pages/` → `components/`

**원칙**: composable 우선 → 3+ 컴포넌트 공유 시 Pinia로 승격

> **코드 생성 트리거**: 컴포넌트/composable/page/store 코드를 **작성**할 때는
> 반드시 `frameworks/nuxt.md` Section 1 "레이어별 책임 분리"의 **코드 예시와 안티패턴**을 로드하여 참조할 것.
> compact 테이블만으로 코드 생성 금지.

## 데이터 페칭

| 도구 | 사용 시점 | SSR | 비고 |
|------|----------|-----|------|
| `useFetch(url)` | 컴포넌트 셋업 | ✅ | 단순 API 호출 |
| `useAsyncData(key, fn)` | 복잡 로직 | ✅ | 명시적 키 필수 |
| `$fetch()` | 이벤트 핸들러 | ❌ | 클릭/서버 내부용 |

## 핵심 패턴

```typescript
// 컴포저블 (SSR 안전 상태)
export const useUser = () => useState<User | null>('user', () => null)

// 서버 라우트 (파일명으로 HTTP 메서드 지정)
// server/api/posts.get.ts
export default defineEventHandler(async (event) => {
  return { data: await db.post.findMany() }
})

// 라우트 미들웨어
// app/middleware/auth.ts
export default defineNuxtRouteMiddleware((to) => {
  if (!useUser().value) return navigateTo('/login')
})
```

## routeRules

`routeRules`: `prerender: true` (SSG), `swr: 3600` (ISR), `ssr: false` (SPA) — 라우트별 하이브리드 렌더링

## 흔한 실수

| 실수 | 해결 |
|------|------|
| 컴포넌트에서 직접 `useFetch` | `composables/`로 이동 |
| 페이지에 비즈니스 로직 | `composables/`로 분리 |
| 모듈 레벨 `ref()` 사용 | `useState()` 로 SSR 안전하게 |
| 이벤트 핸들러에서 `useFetch` | `$fetch()` 사용 |
| `data.value`가 deep ref라 가정 | **shallow ref** 사용됨 |
| `pending` 으로 로딩 판단 | `status === 'pending'` 사용 |
| `pick`/`transform` 미사용 | 페이로드 비대화 → SEO 성능 저하 |

> **전체 가이드**: `frameworks/nuxt.md` 참조
