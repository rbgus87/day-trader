# React Native Quick Reference (Compact)

**Framework**: React Native + Expo | **라우팅**: `app/` (Expo Router) | **Core**: React + Hermes
**TypeScript**: 필수 (`strict: true`, `@/*` 경로 별칭)

## 디렉토리 구조

> **FATAL RULE**: Expo Router와 React Navigation 수동 설정을 절대 혼용 금지.
> **CWD 판단 후 init**: CWD에 `.claude/`있으면 → `create-expo-app .` | 없으면 → `create-expo-app [name]`
> **절대 금지**: `NavigationContainer` 직접 사용, CSS 파일 import, 인라인 스타일 남발
> **검증 필수**: `app.json` + `app/_layout.tsx` 존재 + `src/screens/` 없어야 정상

```
app/                        # Expo Router (파일 기반 라우팅)
├── _layout.tsx             # 루트 레이아웃 (필수, Stack/Tabs)
├── index.tsx               # 홈 화면 (/)
├── +not-found.tsx          # 404 화면
├── (tabs)/_layout.tsx      # 탭 네비게이션 그룹
├── (auth)/login.tsx        # 인증 라우트 그룹
└── posts/[id].tsx          # 동적 라우트
components/                 # 재사용 컴포넌트 (ui/, features/, layouts/)
hooks/                      # 커스텀 React Hooks (비즈니스 로직, 데이터 페칭)
lib/                        # 유틸리티, API 클라이언트, 헬퍼 함수
stores/                     # 전역 상태 (Zustand/Jotai)
constants/                  # 상수 (Colors, Layout, Fonts)
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `app/**/*.tsx` | 라우트 + 화면 조합 (얇게) | 복잡한 비즈니스 로직, 직접 API 호출 |
| `components/` | UI 렌더링 (props/콜백) | 직접 API 호출, 네비게이션, 전역 상태 변경 |
| `hooks/` | 비즈니스 로직, 데이터 페칭 (TanStack Query) | UI 렌더링, StyleSheet 정의 |
| `lib/` | 순수 함수, API 클라이언트 | React hooks, 상태, 컴포넌트 |
| `stores/` | 전역 클라이언트 상태 (Zustand) | UI 로직, 직접 렌더링 |
| `constants/` | 앱 상수 | 상태, 로직, 사이드이펙트 |

**데이터 흐름**: `API Server` → `hooks/` → `app/ (화면)` → `components/`

> **코드 생성 트리거**: 컴포넌트/hooks/화면 코드를 **작성**할 때는
> 반드시 `frameworks/react-native.md`의 **코드 예시와 안티패턴**을 로드하여 참조할 것.
> compact 테이블만으로 코드 생성 금지.

## 상태 관리 도구 선택

| 도구 | 사용 시점 | 비고 |
|------|----------|------|
| `useState`/`useReducer` | 단일 컴포넌트 로컬 상태 | React 기본 |
| TanStack Query | 서버 상태 (API 데이터) | 캐싱, 재검증 |
| Zustand | 전역 클라이언트 상태 | 간단한 API |
| Context API | 테마, 인증 등 저빈도 변경 | 고빈도 시 성능 저하 |

## 핵심 패턴

```tsx
// Expo Router 레이아웃
export default function RootLayout() {
  return <Stack><Stack.Screen name="(tabs)" options={{ headerShown: false }} /></Stack>
}
// TanStack Query 데이터 페칭 (hooks/)
export function usePosts() {
  return useQuery({ queryKey: ['posts'], queryFn: () => fetchApi<Post[]>('/posts') })
}
// 스타일링 (StyleSheet 필수, 인라인 금지)
const styles = StyleSheet.create({ card: { borderRadius: 12, padding: 16 } })
// 환경변수 (EXPO_PUBLIC_ 접두사 필수)
const apiUrl = process.env.EXPO_PUBLIC_API_URL
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| CSS 파일 import | `StyleSheet.create()` 사용 |
| 인라인 스타일 남발 | `StyleSheet.create()` 정적 정의 |
| `NavigationContainer` 직접 사용 | Expo Router 파일 기반 라우팅 사용 |
| FlatList에 `keyExtractor` 누락 | 고유 key 설정 필수 |
| `useCallback` 없이 FlatList renderItem | 메모이제이션 필수 |
| 컴포넌트에서 직접 API 호출 | `hooks/`로 비즈니스 로직 분리 |
| `EXPO_PUBLIC_` 없이 환경변수 | 접두사 추가 필수 |
| 큰 이미지 원본 사용 | 리사이징 + `expo-image` 사용 |

> **전체 가이드**: `frameworks/react-native.md` 참조
