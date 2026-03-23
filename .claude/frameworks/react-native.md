# React Native Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-11

React Native + Expo 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`npx create-expo-app@latest`으로 항상 최신 버전을 설치합니다.

---

## 1. 디렉토리 구조

### FATAL RULE: Expo Router 파일 기반 라우팅 혼동 방지

> **Expo Router가 기본입니다.**
> `npx create-expo-app@latest` 실행 시 Expo Router 기반 프로젝트가 생성됩니다.
> **React Navigation 수동 설정과 Expo Router를 절대 혼용하지 마세요.**

#### 절대 금지 사항

```bash
# ⛔ 아래 패턴은 어떤 상황에서도 절대 금지:
# Expo Router 프로젝트에서 React Navigation 수동 설정 병행
npm install @react-navigation/native    # → Expo Router가 내부적으로 포함!

# app/ 내부에 index.tsx 없이 라우트 생성 시도
mkdir app/profile                       # → _layout.tsx 또는 index.tsx 없으면 라우트 아님!

# 웹 전용 CSS 직접 사용
import './styles.css'                   # → React Native에서 CSS 파일 import 금지!
```

#### 의사결정 트리 (반드시 이 순서대로)

```
STEP 1: CWD(현재 작업 디렉토리)를 확인한다

  CWD에 .claude/ 폴더, package.json, 또는 기존 프로젝트 파일이 있는가?
  ├── YES → CWD가 이미 프로젝트 루트
  │         → npx create-expo-app@latest .
  │         (현재 폴더를 그대로 프로젝트 루트로 사용)
  │
  └── NO  → CWD는 프로젝트의 상위 폴더
            → npx create-expo-app@latest [project-name]
            → cd [project-name]
            (새 서브폴더가 프로젝트 루트가 됨)

STEP 2: 구조 검증 (건너뛰기 금지)

  [PASS 조건 — 3개 모두 충족]
    ✅ app.json (또는 app.config.ts) 가 CWD에 존재
    ✅ package.json 이 CWD에 존재
    ✅ app/_layout.tsx 가 존재

  [FATAL 조건 — 하나라도 해당하면 수정 필수]
    ❌ NavigationContainer를 직접 사용 → Expo Router와 충돌!
    ❌ app/ 디렉토리 없이 src/screens/ 사용 → Expo Router 미사용!
    ❌ CSS 파일 import → StyleSheet API 사용!
```

#### 검증 명령어

```bash
# 프로젝트 루트에서 확인
ls app.json          # ✅ 존재해야 함 (또는 app.config.ts)
ls package.json      # ✅ 존재해야 함
ls app/_layout.tsx   # ✅ 존재해야 함

# 혼용 체크 (하나라도 존재하면 FATAL)
grep -r "NavigationContainer" app/   # ❌ 존재하면 안 됨
ls src/screens/                       # ❌ Expo Router 사용 시 불필요
```

### React Native + Expo 기본 구조

```
project-root/                       # ← app.json, package.json이 여기에 위치
├── app/                            # Expo Router (파일 기반 라우팅)
│   ├── _layout.tsx                 # 루트 레이아웃 (필수)
│   ├── index.tsx                   # 홈 화면 (/)
│   ├── +not-found.tsx              # 404 화면
│   ├── (tabs)/                     # 탭 네비게이션 그룹
│   │   ├── _layout.tsx             # 탭 레이아웃 (Tab.Navigator)
│   │   ├── index.tsx               # 첫 번째 탭
│   │   ├── explore.tsx             # 두 번째 탭
│   │   └── settings.tsx            # 세 번째 탭
│   ├── (auth)/                     # 인증 라우트 그룹
│   │   ├── _layout.tsx             # 인증 레이아웃 (Stack)
│   │   ├── login.tsx               # /login
│   │   └── register.tsx            # /register
│   ├── posts/
│   │   ├── index.tsx               # /posts
│   │   └── [id].tsx                # /posts/:id (동적 라우트)
│   └── modal.tsx                   # 모달 화면
├── components/                     # 재사용 컴포넌트
│   ├── ui/                         # 기본 UI (Button, Input, Card)
│   ├── features/                   # 기능 단위 (LoginForm, PostCard)
│   └── layouts/                    # 레이아웃 구성 (Header, TabBar)
├── hooks/                          # 커스텀 React Hooks
│   ├── useAuth.ts
│   └── usePosts.ts
├── lib/                            # 유틸리티, 헬퍼 함수
│   ├── utils.ts                    # 순수 함수
│   ├── api.ts                      # API 클라이언트 설정
│   └── storage.ts                  # AsyncStorage 래퍼
├── stores/                         # 상태 관리 (Zustand 또는 Jotai)
│   ├── authStore.ts
│   └── settingsStore.ts
├── types/                          # TypeScript 타입 정의
├── constants/                      # 상수 (Colors, Spacing, Fonts)
│   ├── Colors.ts
│   └── Layout.ts
├── assets/                         # 이미지, 폰트 등 정적 자원
├── app.json                        # Expo 설정
├── app.config.ts                   # 동적 Expo 설정 (선택)
├── package.json                    # 의존성
├── tsconfig.json                   # TypeScript 설정
├── babel.config.js                 # Babel 설정
└── eas.json                        # EAS Build/Submit 설정
```

### 레이어별 책임 분리 (Architecture Rules)

각 디렉토리는 명확한 책임 범위를 가집니다. **경계를 넘는 코드는 금지합니다.**

| 레이어 | 책임 | 허용 | 금지 |
|--------|------|------|------|
| `app/**/*.tsx` | 라우트 정의, 화면 조합 | hooks 호출, 컴포넌트 배치, 네비게이션 | 복잡한 비즈니스 로직, 직접 API 호출 |
| `components/` | UI 렌더링 | props, 스타일링(StyleSheet), 이벤트 콜백 | 직접 API 호출, 전역 상태 변경, 네비게이션 |
| `hooks/` | 비즈니스 로직, 데이터 페칭 | `use*` 패턴, 상태 관리, API 호출 | 직접 UI 렌더링, StyleSheet 정의 |
| `lib/` | 순수 함수, 설정 | 데이터 변환, 포맷팅, API 클라이언트 | React hooks, 상태, 컴포넌트 렌더링 |
| `stores/` | 전역 상태 관리 | Zustand/Jotai 스토어, 상태 전이 | UI 로직, 직접 렌더링 |
| `constants/` | 앱 상수 | 색상, 간격, 폰트, 설정값 | 상태, 로직, 사이드이펙트 |

#### 데이터 흐름 (단방향)

```
API Server  →  hooks/  →  app/ (화면)  →  components/
  (외부)       (비즈니스 로직)  (조합)        (UI 렌더링)
               stores/ (전역 상태)
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
// ✅ lib/api.ts - API 클라이언트 설정
const BASE_URL = process.env.EXPO_PUBLIC_API_URL

export async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) throw new Error(`API Error: ${res.status}`)
  return res.json()
}
```

```typescript
// ✅ hooks/usePosts.ts - 비즈니스 로직 + 데이터 페칭
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchApi } from '@/lib/api'
import type { Post } from '@/types'

export function usePosts() {
  return useQuery({
    queryKey: ['posts'],
    queryFn: () => fetchApi<Post[]>('/posts'),
  })
}

export function useCreatePost() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: { title: string; content: string }) =>
      fetchApi<Post>('/posts', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['posts'] }),
  })
}
```

```tsx
// ✅ components/features/PostCard.tsx - UI만 담당
import { StyleSheet, View, Text, Pressable } from 'react-native'
import { formatDate } from '@/lib/utils'
import type { Post } from '@/types'

interface PostCardProps {
  post: Post
  onPress?: (id: string) => void
}

export function PostCard({ post, onPress }: PostCardProps) {
  return (
    <Pressable style={styles.card} onPress={() => onPress?.(post.id)}>
      <Text style={styles.title}>{post.title}</Text>
      <Text style={styles.date}>{formatDate(post.createdAt)}</Text>
      <Text style={styles.excerpt}>{post.excerpt}</Text>
    </Pressable>
  )
}

const styles = StyleSheet.create({
  card: { borderRadius: 8, borderWidth: 1, borderColor: '#e0e0e0', padding: 16, marginBottom: 12 },
  title: { fontSize: 18, fontWeight: '600' },
  date: { fontSize: 12, color: '#888', marginTop: 4 },
  excerpt: { fontSize: 14, marginTop: 8 },
})
```

```tsx
// ✅ app/posts/index.tsx - 얇은 조합 레이어
import { FlatList, ActivityIndicator, View, Text } from 'react-native'
import { useRouter } from 'expo-router'
import { usePosts } from '@/hooks/usePosts'
import { PostCard } from '@/components/features/PostCard'

export default function PostsScreen() {
  const { data: posts, isLoading, error } = usePosts()
  const router = useRouter()

  if (isLoading) return <ActivityIndicator size="large" />
  if (error) return <Text>오류가 발생했습니다</Text>

  return (
    <FlatList
      data={posts}
      keyExtractor={(item) => item.id}
      renderItem={({ item }) => (
        <PostCard post={item} onPress={(id) => router.push(`/posts/${id}`)} />
      )}
    />
  )
}
```

#### 안티패턴: 경계 위반

```tsx
// ❌ 화면에서 직접 API 호출
export default function PostsScreen() {
  const [posts, setPosts] = useState([])
  useEffect(() => {
    fetch('/api/posts').then(r => r.json()).then(setPosts)  // hooks/로 이동해야 함
  }, [])
}

// ❌ 컴포넌트에서 네비게이션
export function PostCard({ post }) {
  const router = useRouter()  // 컴포넌트에서 네비게이션 금지! onPress 콜백 사용
  return <Pressable onPress={() => router.push(`/posts/${post.id}`)} />
}

// ❌ CSS 파일 사용
import './styles.css'  // React Native에서 CSS 파일 import 불가! StyleSheet 사용
```

---

## 2. 네비게이션 (Expo Router)

### 라우트 패턴

```
app/
├── _layout.tsx              # 루트 레이아웃 (Stack 기본)
├── index.tsx                # / (홈)
├── (tabs)/                  # 탭 네비게이션 그룹 (URL 미포함)
│   ├── _layout.tsx          # Tabs 레이아웃
│   ├── index.tsx            # 첫 번째 탭
│   └── settings.tsx         # 설정 탭
├── (auth)/                  # 인증 그룹
│   ├── _layout.tsx          # Stack 레이아웃
│   ├── login.tsx            # /login
│   └── register.tsx         # /register
├── posts/
│   ├── index.tsx            # /posts
│   └── [id].tsx             # /posts/:id (동적)
├── [...missing].tsx         # Catch-all (404)
└── modal.tsx                # 모달
```

### 레이아웃 설정

```tsx
// app/_layout.tsx - 루트 레이아웃
import { Stack } from 'expo-router'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'

const queryClient = new QueryClient()

export default function RootLayout() {
  return (
    <QueryClientProvider client={queryClient}>
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="(auth)" options={{ headerShown: false }} />
        <Stack.Screen name="modal" options={{ presentation: 'modal' }} />
      </Stack>
    </QueryClientProvider>
  )
}

// app/(tabs)/_layout.tsx - 탭 레이아웃
import { Tabs } from 'expo-router'
import { TabBarIcon } from '@/components/ui/TabBarIcon'

export default function TabLayout() {
  return (
    <Tabs screenOptions={{ tabBarActiveTintColor: '#007AFF' }}>
      <Tabs.Screen
        name="index"
        options={{
          title: '홈',
          tabBarIcon: ({ color }) => <TabBarIcon name="home" color={color} />,
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: '설정',
          tabBarIcon: ({ color }) => <TabBarIcon name="cog" color={color} />,
        }}
      />
    </Tabs>
  )
}
```

### 네비게이션 API

```tsx
import { useRouter, useLocalSearchParams, Link, Redirect } from 'expo-router'

// 프로그래매틱 네비게이션
const router = useRouter()
router.push('/posts/1')           // 스택에 추가
router.replace('/home')           // 현재 화면 교체
router.back()                     // 뒤로 가기
router.dismiss()                  // 모달 닫기

// 동적 파라미터
const { id } = useLocalSearchParams<{ id: string }>()

// 선언적 링크
<Link href="/posts/1">게시글 보기</Link>

// 조건부 리다이렉트
if (!isAuthenticated) return <Redirect href="/login" />
```

---

## 3. 상태 관리

### 도구 선택

| 도구 | 사용 시점 | 비고 |
|------|----------|------|
| `useState` / `useReducer` | 단일 컴포넌트 로컬 상태 | React 기본 |
| TanStack Query | 서버 상태 (API 데이터) | 캐싱, 재검증, 페이지네이션 |
| Zustand | 전역 클라이언트 상태 | 간단한 API, 작은 번들 |
| Jotai | 원자적 상태 관리 | 세밀한 리렌더링 제어 |
| Context API | 테마, 인증 등 저빈도 변경 | 고빈도 변경 시 성능 저하 |

### Zustand 패턴

```typescript
// stores/authStore.ts
import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import AsyncStorage from '@react-native-async-storage/async-storage'

interface AuthState {
  user: User | null
  token: string | null
  login: (user: User, token: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      login: (user, token) => set({ user, token }),
      logout: () => set({ user: null, token: null }),
    }),
    {
      name: 'auth-storage',
      storage: createJSONStorage(() => AsyncStorage),
    }
  )
)
```

### TanStack Query 설정

```typescript
// lib/queryClient.ts
import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,     // 5분
      gcTime: 10 * 60 * 1000,       // 10분
      retry: 2,
      refetchOnWindowFocus: false,    // 모바일에서는 비활성화 권장
    },
  },
})
```

---

## 4. 스타일링

### StyleSheet 패턴 (권장)

```tsx
import { StyleSheet, View, Text, useColorScheme } from 'react-native'
import { Colors } from '@/constants/Colors'

export function Card({ title, children }: { title: string; children: React.ReactNode }) {
  const colorScheme = useColorScheme()
  const colors = Colors[colorScheme ?? 'light']

  return (
    <View style={[styles.card, { backgroundColor: colors.cardBackground }]}>
      <Text style={[styles.title, { color: colors.text }]}>{title}</Text>
      {children}
    </View>
  )
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 12,
    padding: 16,
    marginVertical: 8,
    marginHorizontal: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,  // Android 그림자
  },
  title: {
    fontSize: 18,
    fontWeight: '600',
    marginBottom: 8,
  },
})
```

### 반응형 레이아웃

```typescript
import { Dimensions, useWindowDimensions } from 'react-native'

export function useResponsive() {
  const { width, height } = useWindowDimensions()

  return {
    isSmall: width < 375,
    isMedium: width >= 375 && width < 768,
    isLarge: width >= 768,
    width,
    height,
  }
}
```

### 스타일링 안티패턴

```tsx
// ❌ 인라인 스타일 남발 → 매 렌더마다 새 객체 생성
<View style={{ padding: 16, margin: 8, backgroundColor: '#fff' }}>

// ✅ StyleSheet 사용 → 한 번만 생성, 네이티브 최적화
<View style={styles.container}>

// ❌ 조건부 스타일에서 새 객체 생성
<View style={active ? { backgroundColor: 'blue' } : { backgroundColor: 'gray' }}>

// ✅ 배열 스프레드로 조건부 스타일 적용
<View style={[styles.base, active && styles.active]}>
```

---

## 5. 네이티브 모듈 & 플랫폼별 코드

### Expo 모듈 사용

```typescript
import * as ImagePicker from 'expo-image-picker'
import * as Location from 'expo-location'
import * as Notifications from 'expo-notifications'

// 권한 요청 패턴
async function pickImage() {
  const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync()
  if (status !== 'granted') {
    Alert.alert('권한 필요', '이미지를 선택하려면 갤러리 접근 권한이 필요합니다.')
    return
  }
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ['images'],
    quality: 0.8,
    allowsEditing: true,
  })
  if (!result.canceled) return result.assets[0]
}
```

### 플랫폼별 코드

```typescript
import { Platform } from 'react-native'

// Platform.select 사용
const styles = StyleSheet.create({
  shadow: Platform.select({
    ios: {
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.1,
      shadowRadius: 4,
    },
    android: {
      elevation: 3,
    },
    default: {},
  }),
})

// 플랫폼별 파일 분리 (자동 선택)
// components/ui/Button.ios.tsx    → iOS에서 자동 사용
// components/ui/Button.android.tsx → Android에서 자동 사용
// components/ui/Button.tsx         → 기본 (웹 등)
```

---

## 6. 성능 최적화

### FlatList 최적화

```tsx
<FlatList
  data={posts}
  keyExtractor={(item) => item.id}
  renderItem={renderItem}
  // 성능 최적화 옵션
  removeClippedSubviews={true}          // 뷰포트 밖 언마운트
  maxToRenderPerBatch={10}              // 배치당 최대 렌더 수
  windowSize={5}                        // 렌더 윈도우 크기
  initialNumToRender={10}               // 초기 렌더 수
  getItemLayout={(data, index) => ({    // 고정 높이 시 레이아웃 계산 스킵
    length: ITEM_HEIGHT,
    offset: ITEM_HEIGHT * index,
    index,
  })}
/>

// renderItem은 반드시 useCallback으로 메모이제이션
const renderItem = useCallback(({ item }: { item: Post }) => (
  <PostCard post={item} onPress={handlePress} />
), [handlePress])
```

### 메모이제이션

```tsx
// React.memo: props 변경 없으면 리렌더 방지
export const PostCard = React.memo(function PostCard({ post, onPress }: PostCardProps) {
  return (/* ... */)
})

// useMemo: 비용 높은 계산 캐시
const sortedPosts = useMemo(
  () => posts.sort((a, b) => b.createdAt - a.createdAt),
  [posts]
)

// useCallback: 함수 참조 안정화 (FlatList 등에 필수)
const handlePress = useCallback((id: string) => {
  router.push(`/posts/${id}`)
}, [router])
```

### 이미지 최적화

```tsx
import { Image } from 'expo-image'

// expo-image: 캐싱, 블러 해시, 트랜지션 지원
<Image
  source={{ uri: post.thumbnail }}
  style={styles.image}
  placeholder={{ blurhash: post.blurhash }}
  contentFit="cover"
  transition={200}
  cachePolicy="memory-disk"
/>
```

### 성능 예산

```yaml
metrics:
  startup_time: < 2s (cold start)
  fps: >= 60 (일반), >= 120 (ProMotion)
  js_bundle: < 5MB (Hermes 바이트코드)
  memory: < 200MB (idle)
rules:
  - FlatList에 keyExtractor 필수
  - 인라인 스타일 금지 (StyleSheet 사용)
  - useCallback 없이 FlatList renderItem 금지
  - 이미지 리사이징 필수 (원본 전송 금지)
```

---

## 7. TypeScript

### 기본 설정

```json
// tsconfig.json (create-expo-app이 자동 생성)
{
  "extends": "expo/tsconfig.base",
  "compilerOptions": {
    "strict": true,
    "paths": { "@/*": ["./*"] }
  }
}
```

### 주요 타입

```typescript
// 네비게이션 파라미터 타입
import { useLocalSearchParams } from 'expo-router'

// 동적 라우트 파라미터
const { id } = useLocalSearchParams<{ id: string }>()

// 컴포넌트 Props 타입
interface PostCardProps {
  post: Post
  onPress?: (id: string) => void
  onDelete?: (id: string) => void
}

// API 응답 타입
interface ApiResponse<T> {
  data: T
  meta?: { page: number; total: number }
  error?: string
}

// Zustand 스토어 타입
interface AuthState {
  user: User | null
  token: string | null
  login: (user: User, token: string) => void
  logout: () => void
}
```

---

## 8. 환경변수

```bash
# .env (Expo는 EXPO_PUBLIC_ 접두사 필수)
EXPO_PUBLIC_API_URL=https://api.example.com
EXPO_PUBLIC_SENTRY_DSN=https://xxx@sentry.io/xxx

# 비공개 키 (서버에서만 사용, 앱 번들에 포함 안 됨)
# ⚠️ EXPO_PUBLIC_ 없는 변수는 EAS Build 환경에서만 접근 가능
API_SECRET_KEY=secret_xxx
```

```typescript
// 사용
const apiUrl = process.env.EXPO_PUBLIC_API_URL

// ⚠️ EXPO_PUBLIC_ 없는 변수를 클라이언트에서 사용하면 undefined
// console.log(process.env.API_SECRET_KEY)  // → undefined (앱에서)
```

---

## 9. 테스팅

### Jest + React Native Testing Library

```typescript
// __tests__/components/PostCard.test.tsx
import { render, screen, fireEvent } from '@testing-library/react-native'
import { PostCard } from '@/components/features/PostCard'

describe('PostCard', () => {
  const mockPost = { id: '1', title: '테스트 게시글', excerpt: '요약', createdAt: new Date() }

  it('제목을 렌더링한다', () => {
    render(<PostCard post={mockPost} />)
    expect(screen.getByText('테스트 게시글')).toBeTruthy()
  })

  it('클릭 시 onPress 호출', () => {
    const onPress = jest.fn()
    render(<PostCard post={mockPost} onPress={onPress} />)
    fireEvent.press(screen.getByText('테스트 게시글'))
    expect(onPress).toHaveBeenCalledWith('1')
  })
})
```

### Hook 테스트

```typescript
// __tests__/hooks/usePosts.test.tsx
import { renderHook, waitFor } from '@testing-library/react-native'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { usePosts } from '@/hooks/usePosts'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
)

describe('usePosts', () => {
  it('게시글 목록을 반환한다', async () => {
    const { result } = renderHook(() => usePosts(), { wrapper })
    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(result.current.data).toBeInstanceOf(Array)
  })
})
```

### Detox E2E (선택)

```typescript
// e2e/navigation.test.ts
describe('네비게이션', () => {
  beforeAll(async () => {
    await device.launchApp()
  })

  it('홈 화면에서 게시글 목록으로 이동', async () => {
    await expect(element(by.text('홈'))).toBeVisible()
    await element(by.text('게시글')).tap()
    await expect(element(by.id('posts-list'))).toBeVisible()
  })
})
```

---

## 10. 빌드 & 배포 (EAS)

### EAS 설정

```json
// eas.json
{
  "build": {
    "development": {
      "developmentClient": true,
      "distribution": "internal"
    },
    "preview": {
      "distribution": "internal"
    },
    "production": {}
  },
  "submit": {
    "production": {
      "ios": { "appleId": "your@email.com" },
      "android": { "serviceAccountKeyPath": "./google-services.json" }
    }
  }
}
```

```bash
# 개발 빌드
eas build --profile development --platform ios
eas build --profile development --platform android

# 프로덕션 빌드 및 제출
eas build --profile production --platform all
eas submit --platform all
```

---

## 11. 흔한 실수 종합

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| CSS 파일 import | React Native에서 CSS 미지원 | `StyleSheet.create()` 사용 |
| 인라인 스타일 남발 | 매 렌더마다 새 객체 생성 → GC 부하 | `StyleSheet.create()` 사용 |
| NavigationContainer 직접 사용 | Expo Router와 충돌 | Expo Router 파일 기반 라우팅 사용 |
| FlatList에 keyExtractor 누락 | 리렌더링 성능 저하 | 고유 `keyExtractor` 설정 |
| useCallback 없이 FlatList renderItem | 매 렌더마다 함수 재생성 | `useCallback`으로 메모이제이션 |
| 컴포넌트에서 직접 API 호출 | 관심사 분리 위반, 재사용 불가 | `hooks/`로 비즈니스 로직 분리 |
| 큰 이미지 원본 사용 | 메모리 과다 사용, 느린 로딩 | 리사이징 + `expo-image` 캐싱 |
| `EXPO_PUBLIC_` 없이 환경변수 | 앱에서 `undefined` | `EXPO_PUBLIC_` 접두사 사용 |
| Platform.OS 분기 과다 | 코드 복잡도 증가 | 플랫폼별 파일 분리 (`.ios.tsx`, `.android.tsx`) |
| 모달을 일반 화면으로 구현 | 네이티브 전환 효과 미적용 | `presentation: 'modal'` 옵션 사용 |
