# Angular Quick Reference (Compact)

**Framework**: Angular 17+ | **아키텍처**: Standalone Components | **Core**: Signals + RxJS + DI
**TypeScript**: 필수 (`strict: true`, `strictTemplates: true`, `@app/*` 경로 별칭)

## 디렉토리 구조

> **FATAL RULE**: NgModule(`@NgModule`) 사용 절대 금지. 모든 컴포넌트는 `standalone: true`.
> **CWD 판단 후 init**: CWD에 `.claude/`있으면 → `ng new .` | 없으면 → `ng new [name]`
> **절대 금지**: `ng generate module`, `app.module.ts` 생성 ← standalone 아키텍처와 충돌!
> **검증 필수**: `angular.json` + `src/app/app.component.ts` 존재 + `app.module.ts` 없어야 정상

```
src/app/
├── app.component.ts       # 루트 컴포넌트 (standalone)
├── app.config.ts           # provideRouter, provideHttpClient 등
├── app.routes.ts           # 라우트 정의 (lazy loading)
├── core/                   # 싱글톤 서비스, 인터셉터, 가드, 리졸버
│   ├── services/           # AuthService, ApiService 등
│   ├── interceptors/       # HTTP 인터셉터 (functional)
│   └── guards/             # 라우트 가드 (functional)
├── shared/                 # 재사용 UI 컴포넌트, 파이프, 디렉티브
├── features/               # 기능별 (auth/, posts/, dashboard/)
│   └── [feature]/routes.ts # 기능별 라우트 (loadChildren)
├── layouts/                # 레이아웃 (MainLayout, AuthLayout)
└── models/                 # interface, type, enum
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `features/*/component.ts` | 페이지 조합, 인터랙션 | 직접 HTTP 호출, 복잡한 비즈니스 로직 |
| `core/services/` | 비즈니스 로직, API 통신 | DOM 접근, 컴포넌트 렌더링 |
| `core/interceptors/` | HTTP 요청/응답 가공 | 비즈니스 로직, 라우팅 |
| `core/guards/` | 라우트 접근 제어 | HTTP 호출, DOM 접근 |
| `shared/components/` | 재사용 UI (input/output) | 특정 서비스 주입, 비즈니스 로직 |
| `shared/pipes/` | 데이터 표시 변환 | HTTP 호출, 상태 변경 |
| `models/` | 타입 정의만 | 로직, 함수 구현 |

**데이터 흐름**: `core/services/(API)` → `features/component(조합)` → `shared/components/(UI)`

> **코드 생성 트리거**: 컴포넌트/서비스/가드/인터셉터 코드를 **작성**할 때는
> 반드시 `frameworks/angular.md`의 **코드 예시와 안티패턴**을 로드하여 참조할 것.
> compact 테이블만으로 코드 생성 금지.

## 핵심 패턴

```typescript
// Standalone Component + Signal
@Component({
  standalone: true, changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [PostCardComponent], template: `@for (p of posts(); track p.id) { <app-post-card [post]="p" /> }`
})
export class PostListComponent {
  private postService = inject(PostService);
  posts = this.postService.posts;
  ngOnInit() { this.postService.loadPosts(); }
}
// Signal Input/Output
post = input.required<Post>(); delete = output<string>();
// Functional Guard
export const authGuard: CanActivateFn = () => inject(AuthService).isAuthenticated() || inject(Router).createUrlTree(['/login']);
// Functional Interceptor
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const token = inject(AuthService).getToken();
  return next(token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req);
};
// Reactive Form
form = inject(FormBuilder).nonNullable.group({ title: ['', [Validators.required]] });
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| `@NgModule` 사용 | `standalone: true` 컴포넌트 사용 |
| `*ngIf`/`*ngFor` 사용 | `@if`/`@for` 제어 흐름 사용 |
| `@for`에서 `track` 누락 | `track item.id` 필수 |
| 구독 해제 미처리 | `takeUntilDestroyed(this.destroyRef)` |
| 중첩 subscribe | `switchMap`/`mergeMap` 파이프 사용 |
| 컴포넌트에서 직접 HTTP | 서비스로 분리 |
| `OnPush` 미사용 | `ChangeDetectionStrategy.OnPush` 설정 |
| 생성자 주입 | `inject()` 함수 사용 |
| Template-driven forms | Reactive Forms 사용 |

> **전체 가이드**: `frameworks/angular.md` 참조
