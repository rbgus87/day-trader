# Vue.js Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-11

Vue 3 Composition API 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`npm create vue@latest`으로 항상 최신 버전을 설치합니다. (Options API 기반 레거시 패턴 사용 금지)

---

## 1. 디렉토리 구조

### FATAL RULE: Options API / Composition API 혼용 방지

> **Vue 3 Composition API + `<script setup>`이 기본입니다.**
> `npm create vue@latest` 실행 시 TypeScript, Router, Pinia를 선택합니다.
> **Options API(`data()`, `methods`, `computed`)를 새로 작성하지 마세요.**

#### 절대 금지 사항

```bash
# ⛔ 아래 패턴은 어떤 상황에서도 절대 금지:
# Options API 사용
export default {
  data() { return { count: 0 } },     # → Composition API 사용!
  methods: { increment() { ... } },    # → <script setup> 사용!
  computed: { doubled() { ... } },     # → computed() 함수 사용!
}

# Vuex 사용
npm install vuex                       # → Pinia 사용!
import { createStore } from 'vuex'     # → Pinia의 defineStore 사용!

# Mixins 사용
export const myMixin = { ... }         # → Composable 함수로 대체!
```

#### 의사결정 트리 (반드시 이 순서대로)

```
STEP 1: CWD(현재 작업 디렉토리)를 확인한다

  CWD에 .claude/ 폴더, package.json, 또는 기존 프로젝트 파일이 있는가?
  ├── YES → CWD가 이미 프로젝트 루트
  │         → npm create vue@latest .
  │         (현재 폴더를 그대로 프로젝트 루트로 사용)
  │
  └── NO  → CWD는 프로젝트의 상위 폴더
            → npm create vue@latest [project-name]
            → cd [project-name]
            (새 서브폴더가 프로젝트 루트가 됨)

STEP 2: 구조 검증 (건너뛰기 금지)

  [PASS 조건 — 3개 모두 충족]
    ✅ vite.config.ts 가 CWD에 존재
    ✅ package.json 이 CWD에 존재
    ✅ src/App.vue 가 존재

  [FATAL 조건 — 하나라도 해당하면 수정 필수]
    ❌ src/store/index.ts 에 Vuex 사용 → Pinia로 마이그레이션!
    ❌ Options API (data/methods/computed) 사용 → Composition API로 전환!
    ❌ Mixins 사용 → Composable 함수로 대체!
```

#### 검증 명령어

```bash
# 프로젝트 루트에서 확인
ls vite.config.ts       # ✅ 존재해야 함
ls package.json         # ✅ 존재해야 함
ls src/App.vue          # ✅ 존재해야 함

# 레거시 패턴 체크 (하나라도 존재하면 FATAL)
grep -r "createStore" src/   # ❌ Vuex 사용 → Pinia로 전환
grep -r "data()" src/        # ❌ Options API 사용 의심
grep -r "mixins:" src/       # ❌ Mixin 사용 → Composable로 전환
```

### Vue 3 기본 구조

```
project-root/                          # ← vite.config.ts, package.json이 여기에 위치
├── src/
│   ├── App.vue                        # 루트 컴포넌트
│   ├── main.ts                        # 엔트리 포인트 (createApp)
│   ├── router/                        # Vue Router 설정
│   │   └── index.ts                   # 라우트 정의
│   ├── stores/                        # Pinia 스토어
│   │   ├── auth.ts                    # 인증 스토어
│   │   └── posts.ts                   # 게시글 스토어
│   ├── views/                         # 페이지 컴포넌트 (라우트 매핑)
│   │   ├── HomeView.vue
│   │   ├── posts/
│   │   │   ├── PostListView.vue
│   │   │   └── PostDetailView.vue
│   │   └── auth/
│   │       ├── LoginView.vue
│   │       └── RegisterView.vue
│   ├── components/                    # 재사용 컴포넌트
│   │   ├── ui/                        # 기본 UI (BaseButton, BaseInput, BaseCard)
│   │   ├── features/                  # 기능 단위 (PostCard, LoginForm)
│   │   └── layouts/                   # 레이아웃 구성 (AppHeader, AppFooter, AppSidebar)
│   ├── composables/                   # 컴포저블 (비즈니스 로직 재사용)
│   │   ├── usePosts.ts
│   │   ├── useAuth.ts
│   │   └── useForm.ts
│   ├── services/                      # API 통신 서비스
│   │   ├── api.ts                     # Axios/fetch 인스턴스
│   │   ├── postService.ts
│   │   └── authService.ts
│   ├── utils/                         # 순수 유틸리티 함수
│   │   ├── format.ts
│   │   └── validation.ts
│   ├── types/                         # TypeScript 타입 정의
│   │   └── index.ts
│   ├── assets/                        # 빌드 도구가 처리하는 정적 자원
│   └── styles/                        # 전역 스타일
├── public/                            # 정적 파일 (빌드 미처리)
├── vite.config.ts                     # Vite 설정 ← 반드시 프로젝트 루트에 위치
├── package.json                       # ← 반드시 프로젝트 루트에 위치
└── tsconfig.json                      # TypeScript 설정
```

### 레이어별 책임 분리 (Architecture Rules)

각 디렉토리는 명확한 책임 범위를 가집니다. **경계를 넘는 코드는 금지합니다.**

| 레이어 | 책임 | 허용 | 금지 |
|--------|------|------|------|
| `views/` | 페이지 조합, 라우트 매핑 | composable 호출, 컴포넌트 배치, `defineProps` 사용 | 복잡한 비즈니스 로직, 직접 API 호출 |
| `components/` | UI 렌더링 | props, emits, slots, 스타일링 | 직접 API 호출, store 직접 접근, 라우팅 로직 |
| `composables/` | 비즈니스 로직 재사용 | `ref`, `computed`, `watch`, 서비스 호출 | DOM 접근, 스타일링, 컴포넌트 렌더링 |
| `stores/` (Pinia) | 전역 상태 관리 | `ref`, `computed`, `actions`, 서비스 호출 | DOM 접근, UI 로직 |
| `services/` | API 통신 | `axios`/`fetch`, 요청/응답 변환 | Vue 반응형 API (`ref`, `reactive`), 상태 관리 |
| `utils/` | 순수 함수 | 데이터 변환, 포맷팅, 검증 | 상태, 사이드이펙트, API 호출, `ref`/`reactive` |
| `types/` | 타입 정의 | `interface`, `type`, `enum` | 로직, 함수 구현 |

#### 데이터 흐름 (단방향)

```
services/  →  composables/  →  views/  →  components/
  (API)       (비즈니스 로직)    (조합)      (UI 렌더링)
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
// ✅ services/postService.ts - API 통신만 담당
import api from './api'
import type { Post, CreatePostDto } from '@/types'

export const postService = {
  getAll(page = 1): Promise<Post[]> {
    return api.get('/posts', { params: { page } }).then(res => res.data)
  },

  getById(id: string): Promise<Post> {
    return api.get(`/posts/${id}`).then(res => res.data)
  },

  create(dto: CreatePostDto): Promise<Post> {
    return api.post('/posts', dto).then(res => res.data)
  },

  delete(id: string): Promise<void> {
    return api.delete(`/posts/${id}`)
  },
}
```

```typescript
// ✅ composables/usePosts.ts - 비즈니스 로직 + 반응형 상태
import { ref, computed } from 'vue'
import { postService } from '@/services/postService'
import type { Post } from '@/types'

export function usePosts() {
  const posts = ref<Post[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  const postCount = computed(() => posts.value.length)

  async function loadPosts() {
    loading.value = true
    error.value = null
    try {
      posts.value = await postService.getAll()
    } catch (e) {
      error.value = '게시글 로딩 실패'
    } finally {
      loading.value = false
    }
  }

  async function removePost(id: string) {
    await postService.delete(id)
    posts.value = posts.value.filter(p => p.id !== id)
  }

  return { posts, loading, error, postCount, loadPosts, removePost }
}
```

```vue
<!-- ✅ components/features/PostCard.vue - UI만 담당 -->
<script setup lang="ts">
import type { Post } from '@/types'
import { formatDate } from '@/utils/format'

defineProps<{
  post: Post
}>()

const emit = defineEmits<{
  delete: [id: string]
}>()
</script>

<template>
  <article class="rounded-lg border p-4">
    <h2 class="text-lg font-semibold">{{ post.title }}</h2>
    <p class="text-muted">{{ formatDate(post.createdAt) }}</p>
    <p>{{ post.excerpt }}</p>
    <button @click="emit('delete', post.id)">삭제</button>
  </article>
</template>
```

```vue
<!-- ✅ views/posts/PostListView.vue - 얇은 조합 레이어 -->
<script setup lang="ts">
import { onMounted } from 'vue'
import { usePosts } from '@/composables/usePosts'
import PostCard from '@/components/features/PostCard.vue'

const { posts, loading, error, loadPosts, removePost } = usePosts()

onMounted(() => {
  loadPosts()
})
</script>

<template>
  <div>
    <div v-if="loading">로딩 중...</div>
    <div v-else-if="error">{{ error }}</div>
    <template v-else>
      <PostCard
        v-for="post in posts"
        :key="post.id"
        :post="post"
        @delete="removePost"
      />
    </template>
  </div>
</template>
```

#### 안티패턴: 경계 위반

```vue
<!-- ❌ 컴포넌트에서 직접 API 호출 -->
<script setup>
import axios from 'axios'
const { data } = await axios.get('/api/posts')  // composable로 이동!
</script>

<!-- ❌ 컴포넌트에서 비즈니스 로직 -->
<script setup>
const price = ref(1000)
const tax = computed(() => price.value * 0.1)        // composable 또는 utils로 이동
const total = computed(() => price.value + tax.value) // composable 또는 utils로 이동
</script>

<!-- ❌ view에서 복잡한 상태 관리 -->
<script setup>
const posts = ref([])
const loading = ref(false)
const page = ref(1)
const hasMore = computed(() => posts.value.length >= 20 * page.value)
// ^^^ 이 모든 로직을 composable로 분리해야 함
</script>

<!-- ❌ Options API 사용 -->
<script>
export default {
  data() { return { count: 0 } },
  methods: { increment() { this.count++ } },
}
</script>
```

#### Pinia vs Composable 선택 기준

| 기준 | Composable (`use*`) | Pinia Store |
|------|---------------------|-------------|
| 상태 범위 | 단일 기능/페이지 | 여러 페이지/기능 공유 |
| 복잡도 | 단순~중간 | 복잡한 상태 전이 |
| 예시 | `usePosts`, `useForm` | `useAuthStore`, `useCartStore` |
| DevTools | 없음 | Vue DevTools 연동 |
| 지속성 | 컴포넌트 언마운트 시 초기화 | 앱 전역 유지 |
| SSR | 주의 필요 | 내장 SSR 지원 |

**원칙**: 단순한 것부터 시작하고, 필요할 때만 Pinia로 승격합니다.
- 상태가 1~2개 컴포넌트에서만 사용 → `composable`
- 상태가 3개 이상 무관한 컴포넌트에서 공유 → `Pinia store`
- 상태 전이가 복잡하거나 DevTools 필요 → `Pinia store`

---

## 2. Reactivity (반응형 시스템)

### ref vs reactive

```typescript
import { ref, reactive, computed, watch, watchEffect } from 'vue'

// ref - 원시값 + 객체 모두 가능 (권장)
const count = ref(0)
count.value++  // .value 필요

const user = ref<User | null>(null)
user.value = { id: '1', name: 'Kim' }

// reactive - 객체 전용 (구조분해 시 반응성 손실 주의)
const form = reactive({
  title: '',
  content: '',
})
form.title = '새 제목'  // .value 불필요

// computed - 파생 상태 (자동 캐시)
const doubled = computed(() => count.value * 2)
const fullName = computed(() => `${user.value?.firstName} ${user.value?.lastName}`)

// watch - 특정 값 감시
watch(count, (newVal, oldVal) => {
  console.log(`${oldVal} → ${newVal}`)
})

// watch 여러 소스
watch([count, () => user.value?.name], ([newCount, newName]) => {
  console.log(newCount, newName)
})

// watchEffect - 의존성 자동 추적
watchEffect(() => {
  console.log(`현재 값: ${count.value}`)
})
```

### 반응형 주의사항

```typescript
// ❌ reactive 객체 구조분해 → 반응성 손실
const state = reactive({ count: 0 })
const { count } = state  // 반응성 없음!

// ✅ toRefs로 구조분해
import { toRefs } from 'vue'
const { count } = toRefs(state)  // count는 Ref<number>

// ❌ ref를 .value 없이 재할당
const items = ref([1, 2, 3])
items = ref([4, 5, 6])  // 반응성 끊김!

// ✅ .value로 값 변경
items.value = [4, 5, 6]

// ❌ reactive에 원시값 할당
const data = reactive(null)  // 동작하지 않음!

// ✅ ref 사용
const data = ref<Data | null>(null)
```

---

## 3. Composables (로직 재사용)

### 기본 Composable 패턴

```typescript
// composables/useCounter.ts
import { ref, computed } from 'vue'

export function useCounter(initialValue = 0) {
  const count = ref(initialValue)
  const doubled = computed(() => count.value * 2)

  function increment() { count.value++ }
  function decrement() { count.value-- }
  function reset() { count.value = initialValue }

  return { count, doubled, increment, decrement, reset }
}
```

### 비동기 Composable

```typescript
// composables/useFetch.ts
import { ref, watchEffect, type Ref } from 'vue'

export function useFetch<T>(url: string | Ref<string>) {
  const data = ref<T | null>(null) as Ref<T | null>
  const error = ref<string | null>(null)
  const loading = ref(false)

  async function execute() {
    loading.value = true
    error.value = null
    try {
      const urlValue = typeof url === 'string' ? url : url.value
      const response = await fetch(urlValue)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      data.value = await response.json()
    } catch (e) {
      error.value = e instanceof Error ? e.message : '알 수 없는 오류'
    } finally {
      loading.value = false
    }
  }

  watchEffect(() => {
    execute()
  })

  return { data, error, loading, refresh: execute }
}
```

### 인증 Composable

```typescript
// composables/useAuth.ts
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useRouter } from 'vue-router'

export function useAuth() {
  const store = useAuthStore()
  const router = useRouter()

  const isLoggedIn = computed(() => store.isAuthenticated)
  const currentUser = computed(() => store.user)

  async function login(email: string, password: string) {
    await store.login({ email, password })
    router.push('/')
  }

  async function logout() {
    store.logout()
    router.push('/auth/login')
  }

  return { isLoggedIn, currentUser, login, logout }
}
```

### Composable 규칙

```typescript
// ✅ 좋은 Composable: 상태 캡슐화, 명확한 반환
export function usePagination(total: Ref<number>, perPage = 20) {
  const currentPage = ref(1)
  const totalPages = computed(() => Math.ceil(total.value / perPage))
  const hasNext = computed(() => currentPage.value < totalPages.value)
  const hasPrev = computed(() => currentPage.value > 1)

  function next() { if (hasNext.value) currentPage.value++ }
  function prev() { if (hasPrev.value) currentPage.value-- }
  function goTo(page: number) { currentPage.value = Math.max(1, Math.min(page, totalPages.value)) }

  return { currentPage, totalPages, hasNext, hasPrev, next, prev, goTo }
}

// ❌ 나쁜 Composable: DOM 접근, 전역 상태 오염
export function useBadComposable() {
  document.title = 'Bad'  // DOM 직접 접근!
  window.myGlobal = true  // 전역 오염!
}
```

---

## 4. Vue Router 4

### 라우터 설정

```typescript
// router/index.ts
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      component: () => import('@/components/layouts/MainLayout.vue'),
      children: [
        {
          path: '',
          name: 'home',
          component: () => import('@/views/HomeView.vue'),
        },
        {
          path: 'posts',
          name: 'posts',
          component: () => import('@/views/posts/PostListView.vue'),
        },
        {
          path: 'posts/:id',
          name: 'post-detail',
          component: () => import('@/views/posts/PostDetailView.vue'),
          props: true,  // route.params를 props로 전달
        },
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
          meta: { requiresAuth: true },
        },
      ],
    },
    {
      path: '/auth',
      component: () => import('@/components/layouts/AuthLayout.vue'),
      children: [
        { path: 'login', name: 'login', component: () => import('@/views/auth/LoginView.vue') },
        { path: 'register', name: 'register', component: () => import('@/views/auth/RegisterView.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
  ],
})

export default router
```

### 네비게이션 가드

```typescript
// router/index.ts (또는 별도 파일)
import { useAuthStore } from '@/stores/auth'

// 글로벌 가드
router.beforeEach((to, from) => {
  const authStore = useAuthStore()

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (to.name === 'login' && authStore.isAuthenticated) {
    return { name: 'home' }
  }
})

// 라우트별 가드
{
  path: 'admin',
  beforeEnter: (to, from) => {
    const authStore = useAuthStore()
    if (!authStore.isAdmin) return { name: 'home' }
  },
}
```

### 컴포넌트 내 라우터 사용

```vue
<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()

// 현재 라우트 파라미터
const postId = computed(() => route.params.id as string)

// 프로그래밍 방식 네비게이션
function goToPost(id: string) {
  router.push({ name: 'post-detail', params: { id } })
}

function goBack() {
  router.back()
}
</script>
```

---

## 5. Pinia (상태 관리)

### Setup Store (권장)

```typescript
// stores/auth.ts
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authService } from '@/services/authService'
import type { User, LoginDto } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  // State
  const user = ref<User | null>(null)
  const token = ref<string | null>(localStorage.getItem('token'))

  // Getters
  const isAuthenticated = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')

  // Actions
  async function login(dto: LoginDto) {
    const result = await authService.login(dto)
    user.value = result.user
    token.value = result.token
    localStorage.setItem('token', result.token)
  }

  function logout() {
    user.value = null
    token.value = null
    localStorage.removeItem('token')
  }

  async function fetchUser() {
    if (!token.value) return
    user.value = await authService.getMe()
  }

  return { user, token, isAuthenticated, isAdmin, login, logout, fetchUser }
})
```

### Store에서 다른 Store 사용

```typescript
// stores/posts.ts
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useAuthStore } from './auth'
import { postService } from '@/services/postService'
import type { Post } from '@/types'

export const usePostsStore = defineStore('posts', () => {
  const authStore = useAuthStore()

  const posts = ref<Post[]>([])
  const loading = ref(false)

  const myPosts = computed(() =>
    posts.value.filter(p => p.authorId === authStore.user?.id)
  )

  async function loadPosts() {
    loading.value = true
    try {
      posts.value = await postService.getAll()
    } finally {
      loading.value = false
    }
  }

  async function createPost(title: string, content: string) {
    const post = await postService.create({ title, content })
    posts.value.unshift(post)
  }

  return { posts, loading, myPosts, loadPosts, createPost }
})
```

### Store 안티패턴

```typescript
// ❌ Options API 스타일 Store (레거시)
export const useCounterStore = defineStore('counter', {
  state: () => ({ count: 0 }),
  getters: { doubled: (state) => state.count * 2 },
  actions: { increment() { this.count++ } },
})

// ✅ Setup Store (권장)
export const useCounterStore = defineStore('counter', () => {
  const count = ref(0)
  const doubled = computed(() => count.value * 2)
  function increment() { count.value++ }
  return { count, doubled, increment }
})

// ❌ 컴포넌트 외부에서 Store 직접 접근
const store = useAuthStore()  // createPinia() 전에 호출 불가!

// ✅ 컴포넌트 setup 또는 다른 store 안에서 접근
```

---

## 6. 컴포넌트 패턴

### Props & Emits (TypeScript)

```vue
<script setup lang="ts">
import type { Post } from '@/types'

// Props 정의 (타입 기반)
const props = defineProps<{
  post: Post
  showActions?: boolean
}>()

// Props 기본값
const props = withDefaults(defineProps<{
  title: string
  size?: 'sm' | 'md' | 'lg'
}>(), {
  size: 'md',
})

// Emits 정의 (타입 기반)
const emit = defineEmits<{
  delete: [id: string]
  update: [post: Post]
}>()

// Emit 호출
function handleDelete() {
  emit('delete', props.post.id)
}
</script>
```

### Slots

```vue
<!-- BaseCard.vue -->
<template>
  <div class="card">
    <div v-if="$slots.header" class="card-header">
      <slot name="header" />
    </div>
    <div class="card-body">
      <slot />
    </div>
    <div v-if="$slots.footer" class="card-footer">
      <slot name="footer" />
    </div>
  </div>
</template>

<!-- 사용 -->
<BaseCard>
  <template #header>
    <h2>제목</h2>
  </template>
  <p>본문 내용</p>
  <template #footer>
    <button>확인</button>
  </template>
</BaseCard>
```

### Provide / Inject

```typescript
// 제공 (상위 컴포넌트)
import { provide, ref } from 'vue'
import type { InjectionKey, Ref } from 'vue'

// 타입 안전한 키 정의
export const ThemeKey: InjectionKey<Ref<'light' | 'dark'>> = Symbol('theme')

// 부모 컴포넌트
const theme = ref<'light' | 'dark'>('light')
provide(ThemeKey, theme)

// 자식 컴포넌트 (깊이 무관)
import { inject } from 'vue'
import { ThemeKey } from '@/types/injection-keys'

const theme = inject(ThemeKey)  // Ref<'light' | 'dark'> | undefined
const theme = inject(ThemeKey, ref('light'))  // 기본값 포함
```

### v-model (양방향 바인딩)

```vue
<!-- BaseInput.vue -->
<script setup lang="ts">
const model = defineModel<string>({ required: true })
</script>

<template>
  <input :value="model" @input="model = ($event.target as HTMLInputElement).value" />
</template>

<!-- 사용 -->
<BaseInput v-model="searchQuery" />

<!-- 여러 v-model -->
<script setup lang="ts">
const firstName = defineModel<string>('firstName')
const lastName = defineModel<string>('lastName')
</script>

<!-- 사용 -->
<UserForm v-model:firstName="first" v-model:lastName="last" />
```

---

## 7. 에러 처리

### 전역 에러 핸들러

```typescript
// main.ts
const app = createApp(App)

app.config.errorHandler = (err, instance, info) => {
  console.error('전역 에러:', err)
  console.error('컴포넌트:', instance)
  console.error('정보:', info)
  // 에러 리포팅 서비스에 전송
}
```

### 컴포넌트 에러 바운더리

```vue
<!-- components/ErrorBoundary.vue -->
<script setup lang="ts">
import { onErrorCaptured, ref } from 'vue'

const error = ref<Error | null>(null)

onErrorCaptured((err) => {
  error.value = err instanceof Error ? err : new Error(String(err))
  return false  // 전파 중단
})

function retry() {
  error.value = null
}
</script>

<template>
  <div v-if="error" class="error-boundary">
    <p>오류가 발생했습니다: {{ error.message }}</p>
    <button @click="retry">다시 시도</button>
  </div>
  <slot v-else />
</template>
```

### API 에러 처리

```typescript
// services/api.ts
import axios from 'axios'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  timeout: 10000,
})

// 요청 인터셉터
api.interceptors.request.use((config) => {
  const authStore = useAuthStore()
  if (authStore.token) {
    config.headers.Authorization = `Bearer ${authStore.token}`
  }
  return config
})

// 응답 인터셉터
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const authStore = useAuthStore()
      authStore.logout()
      router.push({ name: 'login' })
    }
    return Promise.reject(error)
  },
)

export default api
```

---

## 8. SEO & 메타

### useHead (via @vueuse/head 또는 @unhead/vue)

```typescript
// views/posts/PostDetailView.vue
import { useHead } from '@unhead/vue'

const { data: post } = useFetch<Post>(`/api/posts/${route.params.id}`)

useHead({
  title: computed(() => post.value?.title ?? '로딩 중...'),
  meta: [
    { name: 'description', content: computed(() => post.value?.excerpt ?? '') },
    { property: 'og:title', content: computed(() => post.value?.title ?? '') },
    { property: 'og:image', content: computed(() => post.value?.thumbnail ?? '') },
  ],
})
```

---

## 9. 성능 최적화

### 컴포넌트 지연 로딩

```typescript
import { defineAsyncComponent } from 'vue'

const HeavyChart = defineAsyncComponent({
  loader: () => import('@/components/features/HeavyChart.vue'),
  loadingComponent: LoadingSpinner,
  errorComponent: ErrorDisplay,
  delay: 200,
  timeout: 10000,
})
```

### v-once, v-memo

```vue
<!-- 한 번만 렌더링 (정적 콘텐츠) -->
<div v-once>
  <h1>{{ staticTitle }}</h1>
</div>

<!-- 조건부 재렌더링 -->
<div v-memo="[post.id, post.updatedAt]">
  <PostCard :post="post" />
</div>
```

### shallowRef (대용량 데이터)

```typescript
import { shallowRef, triggerRef } from 'vue'

// 대용량 배열/객체에 사용 (내부 변경 감지 안 함)
const largeList = shallowRef<Item[]>([])

// 값을 통째로 교체
largeList.value = [...largeList.value, newItem]

// 수동으로 트리거 (내부 변경 시)
largeList.value.push(newItem)
triggerRef(largeList)
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
    "paths": { "@/*": ["./src/*"] }
  }
}
```

### 주요 타입 패턴

```typescript
// types/index.ts
export interface User {
  id: string
  name: string
  email: string
  role: 'admin' | 'member'
}

export interface Post {
  id: string
  title: string
  content: string
  excerpt: string
  authorId: string
  createdAt: Date
  updatedAt: Date
}

export interface CreatePostDto {
  title: string
  content: string
}

export interface PaginatedResponse<T> {
  data: T[]
  meta: {
    page: number
    total: number
    totalPages: number
  }
}

// 컴포넌트 Props 타입 (별도 정의 시)
export interface PostCardProps {
  post: Post
  showActions?: boolean
}
```

---

## 11. 테스팅

### Vitest + Vue Test Utils

```typescript
// components/features/PostCard.spec.ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PostCard from './PostCard.vue'

describe('PostCard', () => {
  const mockPost = {
    id: '1',
    title: '테스트 게시글',
    content: '내용',
    excerpt: '발췌',
    authorId: 'u1',
    createdAt: new Date(),
    updatedAt: new Date(),
  }

  it('제목을 렌더링한다', () => {
    const wrapper = mount(PostCard, {
      props: { post: mockPost },
    })
    expect(wrapper.text()).toContain('테스트 게시글')
  })

  it('삭제 버튼 클릭 시 delete 이벤트를 발행한다', async () => {
    const wrapper = mount(PostCard, {
      props: { post: mockPost },
    })

    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('delete')).toBeTruthy()
    expect(wrapper.emitted('delete')![0]).toEqual(['1'])
  })
})
```

### Pinia Store 테스트

```typescript
// stores/auth.spec.ts
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from './auth'

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('초기 상태는 비인증이다', () => {
    const store = useAuthStore()
    expect(store.isAuthenticated).toBe(false)
    expect(store.user).toBeNull()
  })

  it('로그인 시 사용자 정보가 설정된다', async () => {
    const store = useAuthStore()
    vi.spyOn(authService, 'login').mockResolvedValue({
      user: { id: '1', name: 'Kim' },
      token: 'test-token',
    })

    await store.login({ email: 'test@test.com', password: 'pass' })

    expect(store.isAuthenticated).toBe(true)
    expect(store.user?.name).toBe('Kim')
  })
})
```

### Composable 테스트

```typescript
// composables/useCounter.spec.ts
import { describe, it, expect } from 'vitest'
import { useCounter } from './useCounter'

describe('useCounter', () => {
  it('초기값을 설정할 수 있다', () => {
    const { count } = useCounter(10)
    expect(count.value).toBe(10)
  })

  it('increment/decrement가 동작한다', () => {
    const { count, increment, decrement } = useCounter()
    increment()
    expect(count.value).toBe(1)
    decrement()
    expect(count.value).toBe(0)
  })

  it('doubled가 올바르게 계산된다', () => {
    const { count, doubled, increment } = useCounter(5)
    expect(doubled.value).toBe(10)
    increment()
    expect(doubled.value).toBe(12)
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

test('로그인 흐름', async ({ page }) => {
  await page.goto('/auth/login')
  await page.getByLabel('이메일').fill('test@example.com')
  await page.getByLabel('비밀번호').fill('password123')
  await page.getByRole('button', { name: '로그인' }).click()
  await expect(page).toHaveURL('/')
})
```

---

## 12. 흔한 실수 종합

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| Options API 사용 | 레거시, Composition API와 혼동 | `<script setup>` + Composition API |
| Vuex 사용 | 레거시, 타입 안전 부족 | Pinia `defineStore` 사용 |
| Mixins 사용 | 이름 충돌, 출처 불명확 | Composable 함수로 대체 |
| `reactive` 구조분해 | 반응성 손실 | `toRefs()` 사용 또는 `ref()` 사용 |
| `ref` 없이 원시값 상태 | 반응성 없음 | `ref()` 래핑 |
| 컴포넌트에서 직접 API 호출 | 책임 분리 위반 | `composables/` 또는 `services/`로 분리 |
| view에 비즈니스 로직 | 재사용 불가, 테스트 어려움 | `composables/`로 추출 |
| `watch`에서 즉시 실행 미설정 | 초기값 감시 안 됨 | `{ immediate: true }` 옵션 사용 |
| `v-for`에서 `:key` 누락 | 불필요한 DOM 재생성 | 고유한 `:key` 바인딩 필수 |
| Pinia store를 setup 밖에서 사용 | `createPinia()` 전에 호출 불가 | 컴포넌트 `setup` 안에서 호출 |
| `provide/inject` 타입 미정의 | 타입 안전 없음 | `InjectionKey<T>` 사용 |
| 대용량 데이터에 `ref` 사용 | 불필요한 깊은 반응성 추적 | `shallowRef()` 사용 |
| Template refs 타입 누락 | `null` 참조 에러 | `ref<HTMLElement \| null>(null)` |
