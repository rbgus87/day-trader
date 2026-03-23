# Flutter Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-11

Flutter + Dart 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`flutter create`으로 항상 최신 버전을 설치합니다.

---

## 1. 디렉토리 구조

### FATAL RULE: Widget 내부에 비즈니스 로직 배치 금지

> **Widget은 UI 렌더링에만 집중합니다.**
> **비즈니스 로직, 상태 관리, API 호출을 Widget 내부에 작성하지 마세요.**
> **Riverpod Provider를 통해 의존성을 주입하고, 상태는 Notifier/AsyncNotifier로 관리합니다.**

#### 절대 금지 사항

```dart
// ⛔ 아래 패턴은 어떤 상황에서도 절대 금지:

// Widget 내부에서 직접 HTTP 호출
class PostsScreen extends StatefulWidget { /* ... */ }
class _PostsScreenState extends State<PostsScreen> {
  List<Post> posts = [];
  @override
  void initState() {
    super.initState();
    http.get('/api/posts').then((r) => setState(() => posts = r));  // ⛔ FATAL!
  }
}

// StatefulWidget 남발 (Riverpod 사용 시 불필요)
class Counter extends StatefulWidget { /* ... */ }  // ⛔ ConsumerWidget 사용!

// BuildContext를 비동기 콜백에서 사용
onPressed: () async {
  await doSomething();
  Navigator.of(context).pop();  // ⛔ mounted 체크 없이 context 사용!
}
```

#### 의사결정 트리 (반드시 이 순서대로)

```
STEP 1: CWD(현재 작업 디렉토리)를 확인한다

  CWD에 .claude/ 폴더, pubspec.yaml, 또는 기존 프로젝트 파일이 있는가?
  ├── YES → CWD가 이미 프로젝트 루트
  │         → flutter create . --empty
  │         (현재 폴더를 그대로 프로젝트 루트로 사용)
  │
  └── NO  → CWD는 프로젝트의 상위 폴더
            → flutter create [project_name]
            → cd [project_name]
            (새 서브폴더가 프로젝트 루트가 됨)

STEP 2: 구조 검증 (건너뛰기 금지)

  [PASS 조건 — 3개 모두 충족]
    ✅ pubspec.yaml 이 CWD에 존재
    ✅ lib/main.dart 가 존재
    ✅ lib/ 디렉토리가 존재

  [FATAL 조건 — 하나라도 해당하면 수정 필수]
    ❌ lib/ 없이 src/ 사용 → Flutter 규칙 위반!
    ❌ pubspec.yaml이 lib/ 안에 존재 → 루트 위치 오류!
    ❌ Widget 클래스 안에 HTTP 호출 → 아키텍처 위반!
```

#### 검증 명령어

```bash
# 프로젝트 루트에서 확인
ls pubspec.yaml      # ✅ 존재해야 함
ls lib/main.dart     # ✅ 존재해야 함
flutter doctor        # ✅ 환경 점검

# 아키텍처 위반 체크
grep -r "http.get\|http.post\|Dio()" lib/features/*/presentation/  # ❌ Widget 레이어에 HTTP 호출
grep -r "StatefulWidget" lib/features/*/presentation/                # ⚠️ ConsumerWidget 사용 권장
```

### Flutter + Riverpod 기본 구조

```
project-root/                       # ← pubspec.yaml이 여기에 위치
├── lib/
│   ├── main.dart                   # 앱 엔트리포인트, ProviderScope
│   ├── app.dart                    # MaterialApp.router 설정
│   ├── core/                       # 공통 코어 모듈
│   │   ├── constants/              # 앱 상수 (colors, spacing, strings)
│   │   ├── extensions/             # Dart 확장 메서드
│   │   ├── router/                 # go_router 설정
│   │   │   └── app_router.dart
│   │   ├── theme/                  # ThemeData 설정
│   │   │   └── app_theme.dart
│   │   ├── network/                # HTTP 클라이언트 (Dio), 인터셉터
│   │   │   ├── api_client.dart
│   │   │   └── interceptors.dart
│   │   └── utils/                  # 순수 함수, 헬퍼
│   │       └── formatters.dart
│   ├── features/                   # 기능 단위 모듈 (Feature-First)
│   │   ├── auth/
│   │   │   ├── data/               # 데이터 레이어
│   │   │   │   ├── models/         # 데이터 모델 (freezed)
│   │   │   │   │   └── user.dart
│   │   │   │   ├── repositories/   # Repository 구현체
│   │   │   │   │   └── auth_repository.dart
│   │   │   │   └── datasources/    # 데이터 소스 (API, 로컬)
│   │   │   │       └── auth_api.dart
│   │   │   ├── domain/             # 도메인 레이어 (선택)
│   │   │   │   └── entities/       # 도메인 엔티티
│   │   │   └── presentation/       # UI 레이어
│   │   │       ├── providers/      # Riverpod 프로바이더
│   │   │       │   └── auth_provider.dart
│   │   │       ├── screens/        # 화면 위젯
│   │   │       │   └── login_screen.dart
│   │   │       └── widgets/        # 기능별 위젯
│   │   │           └── login_form.dart
│   │   └── posts/
│   │       ├── data/
│   │       │   ├── models/post.dart
│   │       │   └── repositories/post_repository.dart
│   │       └── presentation/
│   │           ├── providers/post_provider.dart
│   │           ├── screens/posts_screen.dart
│   │           └── widgets/post_card.dart
│   └── shared/                     # 공유 위젯, 유틸
│       ├── widgets/                # 공용 UI 위젯 (Button, Input, Card)
│       │   ├── app_button.dart
│       │   └── app_text_field.dart
│       └── providers/              # 공용 프로바이더
│           └── connectivity_provider.dart
├── test/                           # 단위 테스트 + 위젯 테스트
│   ├── features/
│   │   └── posts/
│   │       ├── post_repository_test.dart
│   │       └── posts_screen_test.dart
│   └── shared/
├── integration_test/               # 통합 테스트
│   └── app_test.dart
├── assets/                         # 이미지, 폰트, 기타 자원
│   ├── images/
│   └── fonts/
├── pubspec.yaml                    # 의존성 관리
├── analysis_options.yaml           # Dart 린트 설정
└── l10n/                           # 국제화 (선택)
    ├── app_ko.arb
    └── app_en.arb
```

### 레이어별 책임 분리 (Architecture Rules)

각 레이어는 명확한 책임 범위를 가집니다. **경계를 넘는 코드는 금지합니다.**

| 레이어 | 책임 | 허용 | 금지 |
|--------|------|------|------|
| `presentation/screens/` | 화면 조합, 라우팅 | Provider 구독, Widget 배치, 네비게이션 | HTTP 호출, DB 접근, 비즈니스 로직 |
| `presentation/widgets/` | UI 렌더링 | props, 콜백, 스타일링 | Provider 직접 생성, HTTP 호출 |
| `presentation/providers/` | 상태 관리, UI 로직 | Notifier/AsyncNotifier, Repository 호출 | 직접 HTTP/DB, Widget 렌더링 |
| `data/repositories/` | 데이터 접근 추상화 | DataSource 호출, 캐싱, 에러 매핑 | UI 코드, Provider, BuildContext |
| `data/datasources/` | 원시 데이터 접근 | HTTP 호출, 로컬 DB, SharedPreferences | UI 코드, 비즈니스 로직 |
| `data/models/` | 데이터 직렬화 | freezed, json_serializable, fromJson/toJson | 비즈니스 로직, UI |
| `core/` | 공통 인프라 | HTTP 클라이언트, 라우터, 테마, 상수 | Feature 의존, UI 컴포넌트 |
| `shared/widgets/` | 공용 UI 컴포넌트 | props, 스타일링, 테마 참조 | Feature 의존, HTTP 호출 |

#### 데이터 흐름 (단방향)

```
DataSource (API/DB)  →  Repository  →  Provider (Notifier)  →  Screen  →  Widget
   (원시 데이터)         (추상화)        (상태 관리)            (조합)      (UI)
```

#### 올바른 분리 예시

```dart
// ✅ core/utils/formatters.dart - 순수 함수 (상태 없음)
String formatDate(DateTime date) {
  return DateFormat('yyyy.MM.dd').format(date);
}

String formatPrice(int amount) {
  return NumberFormat.currency(locale: 'ko', symbol: '₩').format(amount);
}
```

```dart
// ✅ features/posts/data/models/post.dart - freezed 모델
import 'package:freezed_annotation/freezed_annotation.dart';

part 'post.freezed.dart';
part 'post.g.dart';

@freezed
class Post with _$Post {
  const factory Post({
    required String id,
    required String title,
    required String content,
    required DateTime createdAt,
    String? thumbnail,
  }) = _Post;

  factory Post.fromJson(Map<String, dynamic> json) => _$PostFromJson(json);
}
```

```dart
// ✅ features/posts/data/repositories/post_repository.dart
import 'package:riverpod_annotation/riverpod_annotation.dart';

part 'post_repository.g.dart';

class PostRepository {
  final ApiClient _client;
  PostRepository(this._client);

  Future<List<Post>> getPosts({int page = 1}) async {
    final response = await _client.get('/posts', queryParameters: {'page': page});
    return (response.data['data'] as List).map((e) => Post.fromJson(e)).toList();
  }

  Future<Post> createPost({required String title, required String content}) async {
    final response = await _client.post('/posts', data: {'title': title, 'content': content});
    return Post.fromJson(response.data['data']);
  }
}

@riverpod
PostRepository postRepository(PostRepositoryRef ref) {
  return PostRepository(ref.watch(apiClientProvider));
}
```

```dart
// ✅ features/posts/presentation/providers/post_provider.dart
import 'package:riverpod_annotation/riverpod_annotation.dart';

part 'post_provider.g.dart';

@riverpod
class PostsNotifier extends _$PostsNotifier {
  @override
  Future<List<Post>> build() async {
    return ref.watch(postRepositoryProvider).getPosts();
  }

  Future<void> createPost({required String title, required String content}) async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() async {
      await ref.read(postRepositoryProvider).createPost(title: title, content: content);
      return ref.read(postRepositoryProvider).getPosts();
    });
  }
}
```

```dart
// ✅ features/posts/presentation/widgets/post_card.dart - UI만 담당
class PostCard extends StatelessWidget {
  final Post post;
  final VoidCallback? onTap;

  const PostCard({super.key, required this.post, this.onTap});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(post.title, style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 4),
              Text(formatDate(post.createdAt), style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
        ),
      ),
    );
  }
}
```

```dart
// ✅ features/posts/presentation/screens/posts_screen.dart - 얇은 조합 레이어
class PostsScreen extends ConsumerWidget {
  const PostsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final postsAsync = ref.watch(postsNotifierProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('게시글')),
      body: postsAsync.when(
        data: (posts) => ListView.builder(
          itemCount: posts.length,
          itemBuilder: (context, index) => PostCard(
            post: posts[index],
            onTap: () => context.push('/posts/${posts[index].id}'),
          ),
        ),
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, stack) => Center(child: Text('오류: $error')),
      ),
    );
  }
}
```

#### 안티패턴: 경계 위반

```dart
// ❌ Widget에서 직접 HTTP 호출
class PostsScreen extends StatefulWidget { /* ... */ }
class _PostsScreenState extends State<PostsScreen> {
  List<Post> posts = [];
  void initState() {
    super.initState();
    Dio().get('/api/posts').then((r) => setState(() => posts = r.data));  // Provider 사용!
  }
}

// ❌ Provider에서 BuildContext 사용
@riverpod
class MyNotifier extends _$MyNotifier {
  void navigate(BuildContext context) {  // Provider에서 context 사용 금지!
    Navigator.of(context).push(/* ... */);
  }
}

// ❌ Repository에서 UI 코드 참조
class PostRepository {
  void showError() {
    showDialog(/* ... */);  // Repository는 데이터만 처리!
  }
}
```

---

## 2. 라우팅 (go_router)

### 라우터 설정

```dart
// core/router/app_router.dart
import 'package:go_router/go_router.dart';
import 'package:riverpod_annotation/riverpod_annotation.dart';

part 'app_router.g.dart';

@riverpod
GoRouter appRouter(AppRouterRef ref) {
  final authState = ref.watch(authNotifierProvider);

  return GoRouter(
    initialLocation: '/',
    redirect: (context, state) {
      final isLoggedIn = authState.valueOrNull != null;
      final isAuthRoute = state.matchedLocation.startsWith('/auth');

      if (!isLoggedIn && !isAuthRoute) return '/auth/login';
      if (isLoggedIn && isAuthRoute) return '/';
      return null;
    },
    routes: [
      ShellRoute(
        builder: (context, state, child) => ScaffoldWithBottomNav(child: child),
        routes: [
          GoRoute(path: '/', builder: (context, state) => const HomeScreen()),
          GoRoute(path: '/posts', builder: (context, state) => const PostsScreen()),
          GoRoute(
            path: '/posts/:id',
            builder: (context, state) => PostDetailScreen(id: state.pathParameters['id']!),
          ),
          GoRoute(path: '/settings', builder: (context, state) => const SettingsScreen()),
        ],
      ),
      GoRoute(path: '/auth/login', builder: (context, state) => const LoginScreen()),
      GoRoute(path: '/auth/register', builder: (context, state) => const RegisterScreen()),
    ],
  );
}
```

### 네비게이션 API

```dart
// 프로그래매틱 네비게이션
context.go('/posts');              // 스택 교체
context.push('/posts/1');          // 스택에 추가
context.pop();                     // 뒤로 가기
context.pushReplacement('/home');  // 현재 화면 교체

// 파라미터 전달
context.push('/posts/1');
// 수신: state.pathParameters['id']

// 쿼리 파라미터
context.push('/posts?page=2');
// 수신: state.uri.queryParameters['page']

// Extra 데이터 전달
context.push('/posts/detail', extra: post);
// 수신: state.extra as Post
```

---

## 3. 상태 관리 (Riverpod)

### Provider 종류 선택

| Provider | 사용 시점 | 비고 |
|----------|----------|------|
| `@riverpod` (함수) | 단순 값, 의존성 주입 | 자동 dispose |
| `@riverpod` (클래스, Notifier) | 동기 상태 + 메서드 | `build()` 반환 타입 |
| `@riverpod` (클래스, AsyncNotifier) | 비동기 상태 + 메서드 | `Future<T>` 반환 |
| `StreamProvider` | 실시간 데이터 | WebSocket, Firestore |
| `FutureProvider` | 일회성 비동기 값 | 설정 로드 등 |

### Riverpod 코드 생성 패턴 (권장)

```dart
// riverpod_annotation + build_runner 사용 (권장)
import 'package:riverpod_annotation/riverpod_annotation.dart';

part 'my_provider.g.dart';

// 단순 값 Provider
@riverpod
String greeting(GreetingRef ref) => '안녕하세요';

// AsyncNotifier (비동기 상태 관리)
@riverpod
class PostsNotifier extends _$PostsNotifier {
  @override
  Future<List<Post>> build() async {
    return ref.watch(postRepositoryProvider).getPosts();
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() => ref.read(postRepositoryProvider).getPosts());
  }

  Future<void> deletePost(String id) async {
    await ref.read(postRepositoryProvider).deletePost(id);
    ref.invalidateSelf();  // 자동 재빌드
  }
}
```

### ConsumerWidget 사용

```dart
class PostsScreen extends ConsumerWidget {
  const PostsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // 상태 구독 (자동 리빌드)
    final postsAsync = ref.watch(postsNotifierProvider);

    // 일회성 읽기 (콜백 내에서)
    // ref.read(postsNotifierProvider.notifier).refresh();

    return postsAsync.when(
      data: (posts) => PostsList(posts: posts),
      loading: () => const LoadingIndicator(),
      error: (e, st) => ErrorView(error: e, onRetry: () => ref.invalidate(postsNotifierProvider)),
    );
  }
}
```

### 안티패턴

```dart
// ❌ build 메서드 안에서 ref.read (watch 사용해야 함)
Widget build(BuildContext context, WidgetRef ref) {
  final posts = ref.read(postsProvider);  // 변경 감지 안 됨!
}

// ❌ ref.watch를 콜백 안에서 사용
onPressed: () {
  ref.watch(postsProvider);  // 콜백에서는 ref.read 사용!
}

// ❌ Provider 안에서 setState 호출
// Riverpod 사용 시 StatefulWidget/setState 불필요
```

---

## 4. 모델 & 직렬화

### freezed + json_serializable (권장)

```dart
// features/posts/data/models/post.dart
import 'package:freezed_annotation/freezed_annotation.dart';

part 'post.freezed.dart';
part 'post.g.dart';

@freezed
class Post with _$Post {
  const factory Post({
    required String id,
    required String title,
    required String content,
    required DateTime createdAt,
    @Default('') String excerpt,
    String? thumbnail,
  }) = _Post;

  factory Post.fromJson(Map<String, dynamic> json) => _$PostFromJson(json);
}
```

```bash
# 코드 생성
dart run build_runner build --delete-conflicting-outputs

# 감시 모드
dart run build_runner watch --delete-conflicting-outputs
```

---

## 5. 플랫폼별 코드

### Platform 분기

```dart
import 'dart:io' show Platform;

// 플랫폼별 분기
if (Platform.isIOS) {
  // iOS 전용 코드
} else if (Platform.isAndroid) {
  // Android 전용 코드
}

// 적응형 위젯 사용
showAdaptiveDialog(
  context: context,
  builder: (context) => AlertDialog.adaptive(
    title: const Text('확인'),
    content: const Text('삭제하시겠습니까?'),
    actions: [
      adaptiveAction(context: context, onPressed: () => Navigator.pop(context), child: const Text('취소')),
      adaptiveAction(context: context, onPressed: () { /* 삭제 */ }, child: const Text('삭제')),
    ],
  ),
);
```

### Method Channel (네이티브 코드 호출)

```dart
// Flutter → 네이티브 통신
const platform = MethodChannel('com.example.app/native');

Future<String> getNativeVersion() async {
  try {
    return await platform.invokeMethod('getVersion');
  } on PlatformException catch (e) {
    return 'Error: ${e.message}';
  }
}
```

---

## 6. 성능 최적화

### Widget 리빌드 최소화

```dart
// ✅ const 생성자 사용 (리빌드 방지)
const PostCard(post: post)  // ← 불변이면 const 사용

// ✅ 무거운 위젯은 별도 ConsumerWidget으로 분리
// (부모 리빌드 시 자식은 Provider가 변경된 경우에만 리빌드)

// ✅ select로 세밀한 구독
final title = ref.watch(postProvider.select((p) => p.title));
// title이 변경될 때만 리빌드 (다른 필드 변경 무시)
```

### ListView 최적화

```dart
// ✅ ListView.builder (필요한 아이템만 렌더링)
ListView.builder(
  itemCount: posts.length,
  itemBuilder: (context, index) => PostCard(post: posts[index]),
)

// ❌ ListView + children (전체 리스트 한 번에 렌더링)
ListView(children: posts.map((p) => PostCard(post: p)).toList())
```

### 이미지 최적화

```dart
// cached_network_image 사용
CachedNetworkImage(
  imageUrl: post.thumbnail ?? '',
  placeholder: (context, url) => const CircularProgressIndicator(),
  errorWidget: (context, url, error) => const Icon(Icons.error),
  memCacheWidth: 300,  // 메모리 캐시 크기 제한
)
```

### 성능 예산

```yaml
metrics:
  startup_time: < 2s (cold start)
  fps: >= 60 (일반), >= 120 (ProMotion)
  apk_size: < 20MB (release)
  ipa_size: < 30MB (release)
  memory: < 200MB (idle)
rules:
  - ListView.builder 필수 (children 사용 금지)
  - const 생성자 가능하면 반드시 사용
  - ref.watch의 select로 세밀한 구독
  - 이미지 캐싱 필수 (CachedNetworkImage)
```

---

## 7. 테마 & 스타일링

### ThemeData 설정

```dart
// core/theme/app_theme.dart
class AppTheme {
  static ThemeData light() => ThemeData(
    useMaterial3: true,
    colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
    textTheme: const TextTheme(
      titleLarge: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
      titleMedium: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
      bodyLarge: TextStyle(fontSize: 16),
      bodyMedium: TextStyle(fontSize: 14),
    ),
    inputDecorationTheme: InputDecorationTheme(
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
    ),
  );

  static ThemeData dark() => ThemeData(
    useMaterial3: true,
    colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue, brightness: Brightness.dark),
  );
}
```

### 스타일링 규칙

```dart
// ✅ Theme.of(context) 사용 (하드코딩 금지)
Text('제목', style: Theme.of(context).textTheme.titleMedium)

// ❌ 하드코딩 스타일
Text('제목', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600))

// ✅ 상수 Spacing 사용
const EdgeInsets.all(AppSpacing.md)

// ❌ 매직 넘버
const EdgeInsets.all(16)
```

---

## 8. TypeScript (Dart 타입 시스템)

### 린트 설정

```yaml
# analysis_options.yaml
include: package:flutter_lints/flutter.yaml

linter:
  rules:
    prefer_const_constructors: true
    prefer_const_declarations: true
    avoid_print: true
    require_trailing_commas: true
    prefer_final_locals: true
    avoid_unnecessary_containers: true
    sized_box_for_whitespace: true
```

### 타입 안전 패턴

```dart
// sealed class로 상태 모델링 (Dart 3+)
sealed class AuthState {}
class AuthInitial extends AuthState {}
class AuthLoading extends AuthState {}
class Authenticated extends AuthState {
  final User user;
  Authenticated(this.user);
}
class Unauthenticated extends AuthState {}

// 패턴 매칭
switch (state) {
  case AuthInitial():
    return const SplashScreen();
  case AuthLoading():
    return const LoadingScreen();
  case Authenticated(:final user):
    return HomeScreen(user: user);
  case Unauthenticated():
    return const LoginScreen();
}
```

---

## 9. 테스팅

### 단위 테스트

```dart
// test/features/posts/post_repository_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';

class MockApiClient extends Mock implements ApiClient {}

void main() {
  late PostRepository repository;
  late MockApiClient mockClient;

  setUp(() {
    mockClient = MockApiClient();
    repository = PostRepository(mockClient);
  });

  group('PostRepository', () {
    test('getPosts는 게시글 목록을 반환한다', () async {
      when(() => mockClient.get('/posts', queryParameters: any(named: 'queryParameters')))
          .thenAnswer((_) async => Response(data: {'data': [{'id': '1', 'title': '테스트'}]}));

      final posts = await repository.getPosts();

      expect(posts, isA<List<Post>>());
      expect(posts.first.title, '테스트');
    });
  });
}
```

### 위젯 테스트

```dart
// test/features/posts/posts_screen_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

void main() {
  testWidgets('PostsScreen은 게시글 목록을 표시한다', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          postsNotifierProvider.overrideWith(() => FakePostsNotifier()),
        ],
        child: const MaterialApp(home: PostsScreen()),
      ),
    );

    await tester.pumpAndSettle();
    expect(find.text('테스트 게시글'), findsOneWidget);
  });

  testWidgets('로딩 중 인디케이터 표시', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          postsNotifierProvider.overrideWith(() => LoadingPostsNotifier()),
        ],
        child: const MaterialApp(home: PostsScreen()),
      ),
    );

    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });
}
```

### 통합 테스트

```dart
// integration_test/app_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('게시글 목록에서 상세 화면으로 이동', (tester) async {
    await tester.pumpWidget(const MyApp());
    await tester.pumpAndSettle();

    expect(find.text('게시글'), findsOneWidget);
    await tester.tap(find.text('게시글').first);
    await tester.pumpAndSettle();

    expect(find.byType(PostDetailScreen), findsOneWidget);
  });
}
```

---

## 10. 빌드 & 배포

### 빌드 명령어

```bash
# 릴리스 빌드
flutter build apk --release                # Android APK
flutter build appbundle --release           # Android AAB (Play Store 권장)
flutter build ios --release                 # iOS
flutter build web --release                 # Web

# 코드 생성 (freezed, riverpod_generator)
dart run build_runner build --delete-conflicting-outputs

# 환경별 빌드
flutter run --dart-define=API_URL=https://api.example.com
```

### 필수 의존성

```yaml
# pubspec.yaml
dependencies:
  flutter:
    sdk: flutter
  flutter_riverpod: ^2.5.0
  riverpod_annotation: ^2.3.0
  go_router: ^14.0.0
  freezed_annotation: ^2.4.0
  json_annotation: ^4.9.0
  dio: ^5.4.0
  cached_network_image: ^3.3.0

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^4.0.0
  riverpod_generator: ^2.4.0
  build_runner: ^2.4.0
  freezed: ^2.5.0
  json_serializable: ^6.8.0
  mocktail: ^1.0.0
  integration_test:
    sdk: flutter
```

---

## 11. 흔한 실수 종합

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| Widget에서 직접 HTTP 호출 | 아키텍처 위반, 테스트 불가 | Repository + Provider 사용 |
| StatefulWidget + setState 남발 | Riverpod 사용 시 불필요, 상태 관리 혼란 | ConsumerWidget + Provider 사용 |
| `ref.read`를 `build` 안에서 사용 | 상태 변경 감지 불가 | `ref.watch` 사용 |
| `ref.watch`를 콜백 안에서 사용 | 의도치 않은 구독 | 콜백에서는 `ref.read` 사용 |
| `const` 생성자 미사용 | 불필요한 Widget 리빌드 | `const` 가능하면 반드시 사용 |
| ListView(children: [...]) | 전체 아이템 한 번에 빌드 | `ListView.builder` 사용 |
| 비동기 콜백에서 context 사용 | mounted 아닐 때 크래시 | `mounted` 체크 또는 `ref.read` 사용 |
| 하드코딩 스타일 | 테마 변경 시 일관성 깨짐 | `Theme.of(context)` 사용 |
| `build_runner` 미실행 | freezed/riverpod 코드 생성 누락 | `dart run build_runner build` 실행 |
| 이미지 캐싱 미사용 | 매번 네트워크 요청, 메모리 누수 | `CachedNetworkImage` 사용 |
