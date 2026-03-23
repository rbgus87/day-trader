# Flutter Quick Reference (Compact)

**Framework**: Flutter + Dart | **라우팅**: go_router | **Core**: Material 3 + Riverpod
**Dart**: `strict` 린트 (flutter_lints), `analysis_options.yaml` 필수

## 디렉토리 구조

> **FATAL RULE**: Widget 내부에 비즈니스 로직(HTTP 호출, DB 접근) 절대 금지.
> **CWD 판단 후 init**: CWD에 `.claude/`있으면 → `flutter create . --empty` | 없으면 → `flutter create [name]`
> **절대 금지**: Widget에서 직접 `Dio().get()`, `StatefulWidget` + `setState` 남발, `ref.read`를 `build`에서 사용
> **검증 필수**: `pubspec.yaml` + `lib/main.dart` 존재 + `lib/` 디렉토리 구조 정상

```
lib/
├── main.dart               # 엔트리포인트 (ProviderScope)
├── app.dart                # MaterialApp.router 설정
├── core/                   # 공통 인프라
│   ├── router/             # go_router 설정
│   ├── theme/              # ThemeData 설정
│   ├── network/            # Dio HTTP 클라이언트
│   └── utils/              # 순수 함수, 헬퍼
├── features/               # 기능 단위 모듈 (Feature-First)
│   └── [feature]/
│       ├── data/           # models/ (freezed), repositories/, datasources/
│       └── presentation/   # providers/ (Riverpod), screens/, widgets/
└── shared/                 # 공용 위젯, 프로바이더
    ├── widgets/            # AppButton, AppTextField 등
    └── providers/          # 공용 프로바이더
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `presentation/screens/` | 화면 조합, Provider 구독 | HTTP 호출, DB 접근, 비즈니스 로직 |
| `presentation/widgets/` | UI 렌더링 (props/콜백) | Provider 직접 생성, HTTP 호출 |
| `presentation/providers/` | 상태 관리 (Notifier) | 직접 HTTP/DB, BuildContext 사용 |
| `data/repositories/` | 데이터 접근 추상화 | UI 코드, Provider, BuildContext |
| `data/datasources/` | 원시 데이터 접근 (HTTP, DB) | UI 코드, 비즈니스 로직 |
| `data/models/` | freezed 모델, JSON 직렬화 | 비즈니스 로직, UI |
| `core/` | HTTP, 라우터, 테마, 상수 | Feature 의존, UI 컴포넌트 |

**데이터 흐름**: `DataSource` → `Repository` → `Provider (Notifier)` → `Screen` → `Widget`

> **코드 생성 트리거**: Widget/Provider/Repository 코드를 **작성**할 때는
> 반드시 `frameworks/flutter.md`의 **코드 예시와 안티패턴**을 로드하여 참조할 것.
> compact 테이블만으로 코드 생성 금지.

## 상태 관리 (Riverpod)

| Provider 타입 | 사용 시점 | 비고 |
|--------------|----------|------|
| `@riverpod` 함수 | 단순 값, DI | 자동 dispose |
| Notifier 클래스 | 동기 상태 + 메서드 | `build()` 반환 |
| AsyncNotifier 클래스 | 비동기 상태 + 메서드 | `Future<T>` 반환 |
| StreamProvider | 실시간 데이터 | WebSocket 등 |

## 핵심 패턴

```dart
// Riverpod AsyncNotifier (providers/)
@riverpod
class PostsNotifier extends _$PostsNotifier {
  @override
  Future<List<Post>> build() async => ref.watch(postRepositoryProvider).getPosts();
}
// ConsumerWidget (screens/)
class PostsScreen extends ConsumerWidget {
  Widget build(BuildContext context, WidgetRef ref) {
    final postsAsync = ref.watch(postsNotifierProvider);
    return postsAsync.when(data: (p) => ListView.builder(...), loading: ...);
  }
}
// freezed 모델 (models/)
@freezed class Post with _$Post { const factory Post({required String id, ...}) = _Post; }
// go_router 네비게이션
context.push('/posts/${post.id}');
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| Widget에서 직접 HTTP 호출 | Repository + Provider 사용 |
| `setState` 남발 | ConsumerWidget + Provider 사용 |
| `ref.read`를 `build`에서 사용 | `ref.watch` 사용 |
| `ref.watch`를 콜백에서 사용 | 콜백에서는 `ref.read` 사용 |
| `const` 생성자 미사용 | 가능하면 반드시 `const` 사용 |
| `ListView(children: [...])` | `ListView.builder` 사용 |
| 비동기 콜백에서 context 사용 | `mounted` 체크 필수 |
| `build_runner` 미실행 | `dart run build_runner build` 실행 |

> **전체 가이드**: `frameworks/flutter.md` 참조
