# Vue.js Quick Reference (Compact)

**Framework**: Vue 3 | **API**: Composition API + `<script setup>` | **Core**: Vite + Pinia + Vue Router 4
**TypeScript**: 필수 (`strict: true`, `@/*` 경로 별칭)

## 디렉토리 구조

> **FATAL RULE**: Options API(`data/methods/computed`), Vuex, Mixins 사용 절대 금지.
> **CWD 판단 후 init**: CWD에 `.claude/`있으면 → `npm create vue@latest .` | 없으면 → `npm create vue@latest [name]`
> **절대 금지**: `npm install vuex`, Options API, Mixins ← Composition API + Pinia 사용!
> **검증 필수**: `vite.config.ts` + `src/App.vue` 존재 + Vuex/Options API 없어야 정상

```
src/
├── App.vue                 # 루트 컴포넌트
├── main.ts                 # 엔트리 (createApp)
├── router/index.ts         # Vue Router 설정
├── stores/                 # Pinia 스토어 (전역 상태)
├── views/                  # 페이지 컴포넌트 (라우트 매핑)
├── components/             # 재사용 컴포넌트 (ui/, features/, layouts/)
├── composables/            # 비즈니스 로직 재사용 (use* 함수)
├── services/               # API 통신 (axios/fetch)
├── utils/                  # 순수 함수 (포맷팅, 검증)
└── types/                  # TypeScript 타입 정의
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `views/` | 페이지 조합 (얇게) | 복잡한 비즈니스 로직, 직접 API 호출 |
| `components/` | UI 렌더링 (props/emits/slots) | 직접 API 호출, store 직접 접근 |
| `composables/` | 비즈니스 로직 (`ref`, `computed`, 서비스 호출) | DOM 접근, 스타일링 |
| `stores/` (Pinia) | 전역 상태 (3+ 컴포넌트 공유) | DOM 접근, UI 로직 |
| `services/` | API 통신 (axios/fetch) | Vue 반응형 (`ref`/`reactive`), 상태 관리 |
| `utils/` | 순수 함수 (상태 없음) | `ref`, `reactive`, API 호출 |

**데이터 흐름**: `services/(API)` → `composables/(로직)` → `views/(조합)` → `components/(UI)`
**Pinia vs Composable**: composable 기본 → 3+ 컴포넌트 공유 시 Pinia로 승격

> **코드 생성 트리거**: 컴포넌트/composable/store 코드를 **작성**할 때는
> 반드시 `frameworks/vue.md`의 **코드 예시와 안티패턴**을 로드하여 참조할 것.
> compact 테이블만으로 코드 생성 금지.

## 핵심 패턴

```typescript
// Composable
export function usePosts() {
  const posts = ref<Post[]>([])
  const loading = ref(false)
  async function load() { loading.value = true; posts.value = await postService.getAll(); loading.value = false }
  return { posts, loading, load }
}
// Pinia Store (Setup 패턴 권장)
export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const isAuthenticated = computed(() => !!user.value)
  async function login(dto: LoginDto) { user.value = (await authService.login(dto)).user }
  return { user, isAuthenticated, login }
})
// Props/Emits
defineProps<{ post: Post }>()
defineEmits<{ delete: [id: string] }>()
// v-model
const model = defineModel<string>({ required: true })
// Router Guard
router.beforeEach((to) => {
  if (to.meta.requiresAuth && !useAuthStore().isAuthenticated) return { name: 'login' }
})
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| Options API 사용 | `<script setup>` + Composition API |
| Vuex 사용 | Pinia `defineStore` |
| Mixins 사용 | Composable 함수로 대체 |
| `reactive` 구조분해 → 반응성 손실 | `toRefs()` 사용 또는 `ref()` 사용 |
| 컴포넌트에서 직접 API 호출 | `composables/` 또는 `services/`로 분리 |
| `v-for`에서 `:key` 누락 | 고유한 `:key` 바인딩 필수 |
| Pinia store를 setup 밖에서 사용 | 컴포넌트 `setup` 안에서 호출 |
| 대용량 데이터에 `ref` | `shallowRef()` 사용 |
| `provide/inject` 타입 미정의 | `InjectionKey<T>` 사용 |

> **전체 가이드**: `frameworks/vue.md` 참조
