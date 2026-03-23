# Nuxt.js Development Guide

> **version**: 1.1.0 | **updated**: 2026-02-06

Nuxt.js 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`npx nuxi@latest init`으로 항상 최신 버전을 설치합니다.

---

## 1. 디렉토리 구조

### FATAL RULE: `app/app/` 이중 중첩 방지 — 의사결정 트리

> **Nuxt는 `srcDir` 기본값이 `app/`입니다.**
> `npx nuxi@latest init` 실행 시 프로젝트 루트 아래에 **자동으로 `app/` 디렉토리를 생성**합니다.
> `app/` 디렉토리를 수동으로 만들지 마세요.

#### 절대 금지 명령어

```bash
# ⛔ 아래 명령어는 어떤 상황에서도 절대 실행 금지:
npx nuxi@latest init app        # → app/app/ 이중 중첩 발생!
npx nuxi@latest init frontend   # → 불필요한 서브폴더 생성!
npx nuxi@latest init src        # → 불필요한 서브폴더 생성!
mkdir app                        # → Nuxt가 자동 생성하므로 수동 생성 금지!
```

#### 의사결정 트리 (반드시 이 순서대로)

```
STEP 1: CWD(현재 작업 디렉토리)를 확인한다

  CWD에 .claude/ 폴더, package.json, 또는 기존 프로젝트 파일이 있는가?
  ├── YES → CWD가 이미 프로젝트 루트
  │         → npx nuxi@latest init .
  │         (현재 폴더를 그대로 프로젝트 루트로 사용)
  │
  └── NO  → CWD는 프로젝트의 상위 폴더
            → npx nuxi@latest init [project-name]
            → cd [project-name]
            (새 서브폴더가 프로젝트 루트가 됨)

STEP 2: 구조 검증 (건너뛰기 금지)

  [PASS 조건 — 3개 모두 충족]
    ✅ nuxt.config.ts 가 CWD에 존재
    ✅ package.json 이 CWD에 존재
    ✅ app/app.vue 가 존재

  [FATAL 조건 — 하나라도 해당하면 삭제 후 STEP 1 재시작]
    ❌ app/app/ 디렉토리가 존재 → 이중 중첩!
    ❌ app/nuxt.config.ts 존재 → 프로젝트 루트 위치 오류
    ❌ app/package.json 존재 → 프로젝트 루트 위치 오류
```

#### 검증 명령어

```bash
# 프로젝트 루트에서 확인
ls nuxt.config.ts    # ✅ 존재해야 함
ls package.json      # ✅ 존재해야 함
ls app/app.vue       # ✅ 존재해야 함

# 이중 중첩 체크 (하나라도 존재하면 FATAL)
ls app/app/           # ❌ 존재하면 안 됨
ls app/nuxt.config.ts # ❌ 존재하면 안 됨
ls app/package.json   # ❌ 존재하면 안 됨
```

### Nuxt 기본 구조

```
project-root/                   # ← nuxt.config.ts, package.json이 여기에 위치
├── app/                        # srcDir (Nuxt 기본값, 자동 생성)
│   ├── assets/                 # 빌드 도구가 처리하는 정적 자원
│   ├── components/             # Vue 컴포넌트 (자동 임포트)
│   │   ├── ui/                 # 기본 UI (Button, Input, Card)
│   │   ├── features/           # 기능 단위 (LoginForm, UserCard)
│   │   └── layouts/            # 레이아웃 구성 (Header, Footer)
│   ├── composables/            # 컴포저블 (자동 임포트)
│   ├── layouts/                # 페이지 레이아웃
│   ├── middleware/             # 라우트 미들웨어
│   ├── pages/                  # 파일 기반 라우팅
│   ├── plugins/                # Vue 플러그인
│   ├── utils/                  # 유틸리티 함수 (자동 임포트)
│   ├── app.vue                 # 루트 컴포넌트
│   ├── app.config.ts           # 반응형 앱 설정
│   └── error.vue               # 에러 페이지
├── server/                     # 서버 코드 (rootDir 기준)
│   ├── api/                    # API 라우트 (자동 임포트)
│   ├── routes/                 # 서버 라우트
│   ├── middleware/             # 서버 미들웨어
│   ├── plugins/                # 서버 플러그인
│   └── utils/                  # 서버 유틸리티 (자동 임포트)
├── shared/                     # app + server 공유 코드
├── public/                     # 정적 파일 (빌드 미처리)
├── nuxt.config.ts              # Nuxt 설정 ← 반드시 프로젝트 루트에 위치
├── package.json                # ← 반드시 프로젝트 루트에 위치
└── .nuxtignore                 # 빌드 제외 파일
```

### Nuxt 경로 변경 (v3 → v4+)

| 항목 | Nuxt 3 (레거시) | Nuxt 현재 |
|------|--------|--------|
| 컴포넌트 | `components/` | `app/components/` |
| 페이지 | `pages/` | `app/pages/` |
| 컴포저블 | `composables/` | `app/composables/` |
| 레이아웃 | `layouts/` | `app/layouts/` |
| 미들웨어 | `middleware/` | `app/middleware/` |
| 플러그인 | `plugins/` | `app/plugins/` |
| app.vue | `app.vue` | `app/app.vue` |
| 서버 | `server/` | `server/` (변경 없음) |

```bash
# 자동 마이그레이션 codemod
npx codemod@latest nuxt/4/file-structure
```

### 레이어별 책임 분리 (Architecture Rules)

각 디렉토리는 명확한 책임 범위를 가집니다. **경계를 넘는 코드는 금지합니다.**

| 레이어 | 책임 | 허용 | 금지 |
|--------|------|------|------|
| `pages/` | 라우트 정의, 컴포넌트 조합 | `definePageMeta`, 컴포저블 호출, 컴포넌트 배치 | 비즈니스 로직, 직접 API 호출, 복잡한 상태 관리 |
| `components/` | UI 렌더링 | props/emit, 슬롯, 스타일링, 컴포저블 호출 | 직접 API 호출, 라우팅 로직, 전역 상태 변경 |
| `composables/` | 비즈니스 로직, 데이터 페칭 | `useFetch`, `useState`, 로직 캡슐화, 상태 관리 | DOM 접근, 스타일링, 직접 컴포넌트 렌더링 |
| `utils/` | 순수 함수 | 데이터 변환, 포맷팅, 검증 헬퍼 | 상태, 사이드이펙트, API 호출, `ref`/`reactive` |
| `stores/` (Pinia) | 복잡한 전역 상태 | 여러 컴포저블이 공유하는 상태, 복잡한 비즈니스 로직 | UI 로직, DOM 접근 |
| `server/api/` | 서버 로직 | DB 접근, 외부 API, 검증, 인증 | 클라이언트 상태, Vue API |
| `server/utils/` | 서버 헬퍼 | DB 커넥션, 공통 인증 로직, 서버 전용 유틸 | 클라이언트 코드 참조 |

#### 데이터 흐름 (단방향)

```
server/api/  →  composables/  →  pages/  →  components/
  (DB/API)      (비즈니스 로직)    (조합)      (UI 렌더링)
                stores/ (Pinia)
                  (전역 상태)
```

#### 올바른 분리 예시

```typescript
// ✅ utils/format.ts - 순수 함수 (상태 없음)
export function formatDate(date: Date): string {
  return new Intl.DateTimeFormat('ko').format(date)
}

export function formatPrice(amount: number): string {
  return new Intl.NumberFormat('ko', { style: 'currency', currency: 'KRW' }).format(amount)
}
```

```typescript
// ✅ composables/usePosts.ts - 비즈니스 로직 + 데이터 페칭
export const usePosts = () => {
  const page = ref(1)
  const { data, error, status, refresh } = useFetch('/api/posts', {
    query: { page },
    watch: [page]
  })

  const nextPage = () => page.value++
  const prevPage = () => { if (page.value > 1) page.value-- }

  return { posts: data, error, status, refresh, page, nextPage, prevPage }
}
```

```typescript
// ✅ composables/useAuth.ts - 인증 비즈니스 로직
export const useAuth = () => {
  const user = useState<User | null>('auth-user', () => null)
  const isLoggedIn = computed(() => !!user.value)

  async function login(credentials: LoginInput) {
    const result = await $fetch('/api/auth/login', {
      method: 'POST',
      body: credentials
    })
    user.value = result.user
  }

  async function logout() {
    await $fetch('/api/auth/logout', { method: 'POST' })
    user.value = null
    await navigateTo('/login')
  }

  return { user, isLoggedIn, login, logout }
}
```

```vue
<!-- ✅ components/features/PostCard.vue - UI만 담당 -->
<script setup lang="ts">
interface Props {
  post: Post
}
defineProps<Props>()
defineEmits<{ delete: [id: string] }>()
</script>

<template>
  <article class="rounded-lg border p-4">
    <h2 class="text-lg font-semibold">{{ post.title }}</h2>
    <p class="text-muted-foreground">{{ formatDate(post.createdAt) }}</p>
    <p>{{ post.excerpt }}</p>
    <button @click="$emit('delete', post.id)">삭제</button>
  </article>
</template>
```

```vue
<!-- ✅ pages/posts/index.vue - 얇은 조합 레이어 -->
<script setup lang="ts">
definePageMeta({ middleware: 'auth' })

const { posts, status, nextPage, prevPage, page } = usePosts()
const { isLoggedIn } = useAuth()

async function handleDelete(id: string) {
  await $fetch(`/api/posts/${id}`, { method: 'DELETE' })
  refreshNuxtData('posts')
}
</script>

<template>
  <div>
    <FeaturesPostCard
      v-for="post in posts"
      :key="post.id"
      :post="post"
      @delete="handleDelete"
    />
    <UiPagination :page="page" @prev="prevPage" @next="nextPage" />
  </div>
</template>
```

#### 안티패턴: 경계 위반

```vue
<!-- ❌ 컴포넌트에서 직접 API 호출 -->
<script setup>
const { data } = await useFetch('/api/posts')  // composable로 이동해야 함
</script>

<!-- ❌ 컴포넌트에서 비즈니스 로직 -->
<script setup>
const price = ref(1000)
const tax = computed(() => price.value * 0.1)        // composable 또는 utils로 이동
const total = computed(() => price.value + tax.value) // composable 또는 utils로 이동
</script>

<!-- ❌ 페이지에서 복잡한 로직 -->
<script setup>
const posts = ref([])
const loading = ref(false)
async function fetchPosts() {     // composable로 이동해야 함
  loading.value = true
  posts.value = await $fetch('/api/posts')
  loading.value = false
}
onMounted(fetchPosts)
</script>
```

#### Pinia vs Composable 선택 기준

| 기준 | Composable (`use*`) | Pinia Store |
|------|---------------------|-------------|
| 상태 범위 | 단일 기능/페이지 | 여러 페이지/기능 공유 |
| 복잡도 | 단순~중간 | 복잡한 상태 전이 |
| 예시 | `usePosts`, `useForm` | `useAuthStore`, `useCartStore` |
| DevTools | 없음 | Pinia DevTools 지원 |
| 지속성 | 페이지 이동 시 초기화 가능 | 앱 전역 유지 |

**원칙**: 단순한 것부터 시작하고, 필요할 때만 Pinia로 승격합니다.
- 상태가 1~2개 컴포넌트에서만 사용 → `composable`
- 상태가 3개 이상 무관한 컴포넌트에서 공유 → `Pinia store`
- 상태 전이가 복잡하거나 DevTools 필요 → `Pinia store`

---

## 2. 데이터 페칭

### 도구 선택

| 도구 | 사용 시점 | SSR | 중복 방지 |
|------|----------|-----|----------|
| `useFetch(url)` | 단순 API 호출 | ✅ | ✅ |
| `useAsyncData(key, fn)` | 복잡한 데이터 로직 | ✅ | ✅ |
| `$fetch()` | 이벤트 핸들러, 서버 내부 호출 | ❌ | ❌ |

### useFetch 패턴

```typescript
// 기본 사용 - SSR 안전, 자동 중복 방지
const { data, error, status, refresh } = await useFetch('/api/posts', {
  query: { page: 1, limit: 20 }
})

// 타입 안전 + 페이로드 최소화
const { data: users } = await useFetch<User[]>('/api/users', {
  pick: ['id', 'name', 'email'],  // 필요한 필드만 선택
  transform: (data) => data.filter(u => u.active)
})

// 지연 로딩 (SEO 불필요 시)
const { data } = await useFetch('/api/dashboard', {
  lazy: true
})

// 반응형 파라미터
const page = ref(1)
const { data } = await useFetch('/api/posts', {
  query: { page },  // page 변경 시 자동 재요청
  watch: [page]
})
```

### useAsyncData 패턴

```typescript
// 커스텀 키 + 복잡한 로직
const { data: post } = await useAsyncData(
  `post-${route.params.id}`,  // 명시적 키 (중복 방지)
  () => $fetch(`/api/posts/${route.params.id}`)
)

// 병렬 데이터 페칭
const [{ data: user }, { data: posts }] = await Promise.all([
  useAsyncData('user', () => $fetch('/api/user')),
  useAsyncData('posts', () => $fetch('/api/posts'))
])

// 캐시된 데이터 활용
const { data } = await useAsyncData('posts', () => $fetch('/api/posts'), {
  getCachedData: (key, nuxtApp) => nuxtApp.payload.data[key]
})
```

### $fetch 사용 (클라이언트 이벤트 전용)

```typescript
// 이벤트 핸들러에서 사용 (useFetch 사용 금지)
async function createPost() {
  const result = await $fetch('/api/posts', {
    method: 'POST',
    body: { title, content }
  })
  await refreshNuxtData('posts')  // 관련 캐시 갱신
}
```

### 안티패턴

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| 이벤트 핸들러에서 `useFetch` | 컴포넌트 셋업 외부 호출 불가 | `$fetch` 사용 |
| `useAsyncData` 키 누락 | 중복 요청 방지 실패 | 명시적 키 지정 |
| `useFetch` + `watch: false` 없이 반응형 URL | 의도치 않은 재요청 | `watch` 옵션으로 제어 |
| 서버 라우트에서 `useFetch` | 순환 호출 위험 | 직접 DB/서비스 호출 |
| `transform` 미사용 | HTML 페이로드 비대화 | `pick`/`transform` 으로 축소 |

---

## 3. 서버 라우트

### 파일 명명 규칙

```
server/api/
├── posts.get.ts              # GET  /api/posts
├── posts.post.ts             # POST /api/posts
├── posts/
│   ├── [id].get.ts           # GET  /api/posts/:id
│   ├── [id].put.ts           # PUT  /api/posts/:id
│   └── [id].delete.ts        # DELETE /api/posts/:id
├── auth/
│   ├── login.post.ts         # POST /api/auth/login
│   └── [...].ts              # Catch-all /api/auth/*
server/routes/
└── sitemap.xml.ts            # GET /sitemap.xml (비-API 라우트)
```

### defineEventHandler 패턴

```typescript
// server/api/posts.get.ts - 조회
export default defineEventHandler(async (event) => {
  const query = getQuery(event)
  const { page = 1, limit = 20 } = query

  const posts = await db.post.findMany({
    skip: (Number(page) - 1) * Number(limit),
    take: Number(limit)
  })

  return { data: posts, meta: { page, limit } }
})

// server/api/posts.post.ts - 생성 + 유효성 검증
import { z } from 'zod'

const CreatePostSchema = z.object({
  title: z.string().min(1).max(200),
  content: z.string().min(1),
})

export default defineEventHandler(async (event) => {
  const body = await readBody(event)
  const validated = CreatePostSchema.safeParse(body)

  if (!validated.success) {
    throw createError({
      statusCode: 400,
      message: 'Validation failed',
      data: validated.error.issues
    })
  }

  const post = await db.post.create({ data: validated.data })
  return { data: post }
})
```

### 서버 미들웨어

```typescript
// server/middleware/auth.ts - 모든 요청에 자동 실행
export default defineEventHandler((event) => {
  const token = getHeader(event, 'authorization')
  if (event.path.startsWith('/api/admin') && !token) {
    throw createError({ statusCode: 401, message: 'Unauthorized' })
  }
})
```

### 서버 유틸리티 (자동 임포트)

```typescript
// server/utils/db.ts - server/ 내 어디서든 자동 사용 가능
export const db = drizzle(...)

// server/utils/auth.ts
export function requireAuth(event: H3Event) {
  const session = await getUserSession(event)
  if (!session) throw createError({ statusCode: 401 })
  return session
}
```

### 런타임 설정 접근

```typescript
// server/api/config.get.ts
export default defineEventHandler((event) => {
  const config = useRuntimeConfig(event)
  // config.apiSecret (서버 전용)
  // config.public.apiBase (클라이언트 공개)
})
```

---

## 4. 컴포저블 & 상태 관리

### useState (SSR 안전 상태)

```typescript
// app/composables/useCounter.ts
export const useCounter = () => {
  // useState: SSR 안전, 키 기반 중복 방지
  const count = useState<number>('counter', () => 0)
  const increment = () => count.value++
  const decrement = () => count.value--
  return { count, increment, decrement }
}

// app/composables/useUser.ts
export const useUser = () => {
  const user = useState<User | null>('user', () => null)
  const isLoggedIn = computed(() => !!user.value)
  return { user, isLoggedIn }
}
```

**useState 핵심 규칙:**
- 데이터는 반드시 JSON 직렬화 가능 (클래스, 함수, Symbol 불가)
- 대용량 객체는 `shallowRef`와 함께 사용
- 키 문자열은 앱 전체에서 고유해야 함

### 안티패턴: 모듈 레벨 상태

```typescript
// ❌ 위험 - SSR에서 요청 간 상태 오염, 메모리 누수
export const globalState = ref({ user: null })

// ✅ 안전 - useState로 감싸기
export const useGlobalState = () => useState('global', () => ({ user: null }))
```

### Pinia 통합

```typescript
// app/stores/auth.ts
export const useAuthStore = defineStore('auth', () => {
  // setup 스토어 패턴 (Nuxt 권장)
  const user = ref<User | null>(null)
  const token = ref<string | null>(null)

  const isAuthenticated = computed(() => !!token.value)

  async function login(credentials: LoginInput) {
    const result = await $fetch('/api/auth/login', {
      method: 'POST',
      body: credentials
    })
    user.value = result.user
    token.value = result.token
  }

  function logout() {
    user.value = null
    token.value = null
    navigateTo('/login')
  }

  return { user, token, isAuthenticated, login, logout }
})
```

### 커스텀 컴포저블 설계

```typescript
// app/composables/usePosts.ts - API 컴포저블
export const usePosts = (options?: { page?: Ref<number> }) => {
  const page = options?.page ?? ref(1)

  const { data, error, status, refresh } = useFetch('/api/posts', {
    query: { page },
    watch: [page]
  })

  return { posts: data, error, status, refresh, page }
}
```

---

## 5. 컴포넌트 패턴

### 자동 임포트 규칙

| 디렉토리 | 임포트 방식 | 네이밍 |
|----------|------------|--------|
| `app/components/` | 자동 (태그로 사용) | `PascalCase.vue` |
| `app/composables/` | 자동 (함수 호출) | `useXxx.ts` |
| `app/utils/` | 자동 (함수 호출) | `camelCase.ts` |

중첩 디렉토리의 컴포넌트는 경로가 이름에 포함:
- `components/ui/Button.vue` → `<UiButton />`
- `components/features/LoginForm.vue` → `<FeaturesLoginForm />`

### ClientOnly & Lazy

```vue
<!-- 브라우저 전용 컴포넌트 -->
<ClientOnly>
  <ChartComponent />
  <template #fallback>
    <div class="animate-pulse h-64" />
  </template>
</ClientOnly>

<!-- 지연 로딩 (뷰포트 진입 시 로드) -->
<LazyFeaturesCommentSection v-if="showComments" />
```

### NuxtLink

```vue
<!-- 내부 링크 - 자동 프리페칭 -->
<NuxtLink to="/about">About</NuxtLink>

<!-- 프리페칭 비활성화 -->
<NuxtLink to="/heavy-page" :prefetch="false">Heavy Page</NuxtLink>

<!-- 외부 링크 - 자동 감지 -->
<NuxtLink to="https://example.com" external>External</NuxtLink>
```

---

## 6. 에러 처리

### error.vue (전역 에러 페이지)

```vue
<!-- app/error.vue -->
<script setup lang="ts">
import type { NuxtError } from '#app'

const props = defineProps<{ error: NuxtError }>()

const handleClear = () => clearError({ redirect: '/' })
</script>

<template>
  <div class="error-page">
    <h1>{{ error.statusCode }}</h1>
    <p>{{ error.message }}</p>
    <button @click="handleClear">홈으로</button>
  </div>
</template>
```

### NuxtErrorBoundary (컴포넌트 레벨)

```vue
<NuxtErrorBoundary>
  <DangerousComponent />
  <template #error="{ error, clearError }">
    <p>에러 발생: {{ error.message }}</p>
    <button @click="clearError">다시 시도</button>
  </template>
</NuxtErrorBoundary>
```

### createError (서버/클라이언트)

```typescript
// 서버 라우트에서
throw createError({
  statusCode: 404,
  statusMessage: 'Not Found',
  message: '게시글을 찾을 수 없습니다',
  data: { postId: id }
})

// 페이지/컴포넌트에서
if (!post.value) {
  throw showError({
    statusCode: 404,
    statusMessage: 'Page Not Found'
  })
}
```

---

## 7. 미들웨어

### 라우트 미들웨어

```typescript
// app/middleware/auth.ts (named)
export default defineNuxtRouteMiddleware((to, from) => {
  const { isAuthenticated } = useAuthStore()

  if (!isAuthenticated) {
    return navigateTo('/login')
  }
})

// 페이지에서 사용
definePageMeta({
  middleware: 'auth'
})
```

### 글로벌 미들웨어

```typescript
// app/middleware/analytics.global.ts (.global 접미사)
export default defineNuxtRouteMiddleware((to, from) => {
  // 모든 라우트 변경 시 자동 실행
  trackPageView(to.fullPath)
})
```

### 인라인 미들웨어

```typescript
definePageMeta({
  middleware: [
    function (to, from) {
      if (to.params.id === '1') {
        return abortNavigation()
      }
    }
  ]
})
```

**주의사항:**
- 미들웨어 내에서 `useRoute()` 대신 `to`, `from` 파라미터 사용
- `navigateTo()`로 리다이렉트, `abortNavigation()`으로 네비게이션 차단
- 에러 페이지 렌더링 시에도 미들웨어가 다시 실행됨

---

## 8. SEO & 메타

### useSeoMeta (권장)

```typescript
// TypeScript 안전, XSS 방지
useSeoMeta({
  title: '게시글 제목',
  description: '게시글 설명입니다',
  ogTitle: '게시글 제목',
  ogDescription: '게시글 설명입니다',
  ogImage: '/images/og-image.png',
  twitterCard: 'summary_large_image'
})

// 서버 전용 (클라이언트에서 변경 불필요 시)
useServerSeoMeta({
  robots: 'index, follow'
})
```

### useHead & titleTemplate

```typescript
// app/app.vue - 전역 설정
useHead({
  titleTemplate: (title) => title ? `${title} - My App` : 'My App',
  htmlAttrs: { lang: 'ko' },
  link: [{ rel: 'icon', href: '/favicon.ico' }]
})
```

### 페이지별 메타

```typescript
// app/pages/posts/[id].vue
const { data: post } = await useFetch(`/api/posts/${route.params.id}`)

useSeoMeta({
  title: () => post.value?.title,
  description: () => post.value?.excerpt,
  ogImage: () => post.value?.thumbnail
})
```

---

## 9. 성능 최적화

### routeRules (하이브리드 렌더링)

```typescript
// nuxt.config.ts
export default defineNuxtConfig({
  routeRules: {
    // 정적 프리렌더
    '/': { prerender: true },
    '/blog/**': { prerender: true },

    // SWR (Stale-While-Revalidate) - 1시간
    '/products/**': { swr: 3600 },

    // ISR (Incremental Static Regeneration)
    '/catalog/**': { isr: 600 },

    // CSR only (SSR 비활성화)
    '/dashboard/**': { ssr: false },

    // CORS 헤더
    '/api/**': { cors: true },

    // 캐시 헤더
    '/assets/**': {
      headers: { 'cache-control': 'public, max-age=31536000, immutable' }
    }
  }
})
```

### 지연 하이드레이션

```vue
<!-- 뷰포트에 보일 때 하이드레이션 -->
<LazyFeaturesComments hydrate-on-visible />

<!-- 유휴 시 하이드레이션 -->
<LazyFeaturesRecommendations hydrate-on-idle />
```

### 페이로드 최적화

```typescript
// pick으로 필요한 필드만 전송
const { data } = await useFetch('/api/posts', {
  pick: ['id', 'title', 'slug']  // HTML 페이로드 최소화
})

// transform으로 데이터 가공 후 전송
const { data } = await useFetch('/api/analytics', {
  transform: (raw) => ({
    totalViews: raw.reduce((sum, r) => sum + r.views, 0),
    topPages: raw.slice(0, 10)
  })
})
```

### 번들 분석

```bash
npx nuxi analyze    # 번들 크기 시각화
```

**성능 예산:**
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

```typescript
// nuxt.config.ts
export default defineNuxtConfig({
  typescript: {
    strict: true,
    // 기본값: noUncheckedIndexedAccess: true
    // 배열/객체 인덱스 접근 시 undefined 체크 필수
  }
})
```

### 자동 생성 타입

```
.nuxt/types/
├── components.d.ts   # 컴포넌트 타입
├── imports.d.ts       # 자동 임포트 타입
├── schema.d.ts        # 설정 스키마
└── app.config.d.ts    # 앱 설정 타입
```

### 타입 확장

```typescript
// app.config.ts 타입 정의
declare module 'nuxt/schema' {
  interface AppConfigInput {
    theme?: {
      primaryColor?: string
    }
  }
}
```

---

## 11. 테스팅

### Vitest + @nuxt/test-utils

```typescript
// tests/composables/useCounter.test.ts
import { mountSuspended } from '@nuxt/test-utils/runtime'

describe('useCounter', () => {
  it('should increment', async () => {
    const component = await mountSuspended(CounterComponent)
    await component.find('button').trigger('click')
    expect(component.text()).toContain('1')
  })
})
```

### 서버 라우트 테스트

```typescript
// tests/api/posts.test.ts
import { $fetch, setup } from '@nuxt/test-utils/e2e'

describe('/api/posts', async () => {
  await setup({ /* nuxt config */ })

  it('GET returns posts', async () => {
    const result = await $fetch('/api/posts')
    expect(result.data).toBeInstanceOf(Array)
  })

  it('POST validates input', async () => {
    const result = await $fetch('/api/posts', {
      method: 'POST',
      body: {}
    }).catch(e => e.data)
    expect(result.statusCode).toBe(400)
  })
})
```

### Playwright E2E

```typescript
// e2e/navigation.spec.ts
import { test, expect } from '@playwright/test'

test('페이지 네비게이션', async ({ page }) => {
  await page.goto('/')
  // SSR 컨텐츠 확인
  await expect(page.locator('h1')).toBeVisible()

  await page.getByRole('link', { name: '블로그' }).click()
  await expect(page).toHaveURL('/blog')
})
```

---

## 12. Nuxt 3 레거시 마이그레이션

> 기존 Nuxt 3 프로젝트를 현재 버전으로 마이그레이션할 때 참조합니다.

### 주요 변경사항 (v3 → v4+)

| 항목 | Nuxt 3 (레거시) | Nuxt 현재 |
|------|----------------|----------|
| srcDir | 프로젝트 루트 | `app/` |
| data.value | deep ref | **shallow ref** |
| `pending` | `!data && !error` | `status === 'pending'` |
| TypeScript | 선택 | `noUncheckedIndexedAccess` 기본 |
| `generate` 옵션 | `nuxt.config` 최상위 | `nitro.prerender` |

### 호환 모드 (점진적 마이그레이션)

```typescript
// nuxt.config.ts - Nuxt 3 프로젝트에서 최신 동작을 미리 적용
export default defineNuxtConfig({
  future: {
    compatibilityVersion: 4
  }
})
```

### 마이그레이션 체크리스트

- [ ] Node.js 18.20+ 확인
- [ ] `npx codemod@latest nuxt/4/file-structure` 실행
- [ ] `app/` 디렉토리로 파일 이동 확인
- [ ] `data.value` 접근하는 코드에서 shallow ref 동작 확인
- [ ] `pending` 사용 코드를 `status === 'pending'`으로 변경
- [ ] `generate` 옵션을 `nitro.prerender`으로 이동
- [ ] 모든 모듈/레이어 호환성 확인
- [ ] `npm run dev` 및 `npm run build` 성공 확인
