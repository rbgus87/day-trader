# Angular Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-11

Angular 17+ Standalone 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`ng new`로 항상 최신 버전을 설치합니다. (NgModule 기반 레거시 아키텍처 사용 금지)

---

## 1. 디렉토리 구조

### FATAL RULE: NgModule 기반 아키텍처 사용 금지

> **Angular 17+ Standalone Components가 기본입니다.**
> `ng new` 실행 시 standalone 프로젝트가 생성됩니다.
> **NgModule(`@NgModule`)을 새로 작성하지 마세요. 모든 컴포넌트는 `standalone: true`입니다.**

#### 절대 금지 사항

```bash
# ⛔ 아래 패턴은 어떤 상황에서도 절대 금지:
# NgModule 기반 컴포넌트 생성
ng generate module users                  # → standalone 아키텍처와 충돌!
ng generate component users --module=app  # → NgModule 의존성 생성!

# app.module.ts 수동 생성
touch src/app/app.module.ts               # → standalone 프로젝트에 불필요!

# 'use client' 같은 React 패턴 사용
# Angular에는 Server/Client Component 구분이 없음!
```

#### 의사결정 트리 (반드시 이 순서대로)

```
STEP 1: CWD(현재 작업 디렉토리)를 확인한다

  CWD에 .claude/ 폴더, package.json, 또는 기존 프로젝트 파일이 있는가?
  ├── YES → CWD가 이미 프로젝트 루트
  │         → ng new . --style=scss --ssr=false
  │         (현재 폴더를 그대로 프로젝트 루트로 사용)
  │
  └── NO  → CWD는 프로젝트의 상위 폴더
            → ng new [project-name] --style=scss --ssr=false
            → cd [project-name]
            (새 서브폴더가 프로젝트 루트가 됨)

STEP 2: 구조 검증 (건너뛰기 금지)

  [PASS 조건 — 3개 모두 충족]
    ✅ angular.json 이 CWD에 존재
    ✅ package.json 이 CWD에 존재
    ✅ src/app/app.component.ts 가 존재 (standalone)

  [FATAL 조건 — 하나라도 해당하면 수정 필수]
    ❌ src/app/app.module.ts 존재 → NgModule 기반 프로젝트!
    ❌ @NgModule 데코레이터 사용 → standalone으로 마이그레이션 필수!
    ❌ declarations 배열에 컴포넌트 등록 → standalone: true 사용!
```

#### 검증 명령어

```bash
# 프로젝트 루트에서 확인
ls angular.json          # ✅ 존재해야 함
ls package.json          # ✅ 존재해야 함
ls src/app/app.component.ts  # ✅ 존재해야 함

# NgModule 혼용 체크 (하나라도 존재하면 FATAL)
ls src/app/app.module.ts     # ❌ 존재하면 안 됨
grep -r "@NgModule" src/     # ❌ 결과가 있으면 안 됨
```

### Angular 기본 구조

```
project-root/                          # ← angular.json, package.json이 여기에 위치
├── src/
│   ├── app/
│   │   ├── app.component.ts           # 루트 컴포넌트 (standalone)
│   │   ├── app.config.ts              # 애플리케이션 설정 (provideRouter 등)
│   │   ├── app.routes.ts              # 라우트 정의
│   │   ├── core/                      # 싱글톤 서비스, 인터셉터, 가드
│   │   │   ├── services/              # 전역 서비스 (AuthService, ApiService)
│   │   │   ├── interceptors/          # HTTP 인터셉터
│   │   │   ├── guards/                # 라우트 가드
│   │   │   └── resolvers/             # 라우트 리졸버
│   │   ├── shared/                    # 공유 컴포넌트, 디렉티브, 파이프
│   │   │   ├── components/            # 재사용 UI (Button, Input, Card)
│   │   │   ├── directives/            # 커스텀 디렉티브
│   │   │   └── pipes/                 # 커스텀 파이프
│   │   ├── features/                  # 기능 단위 모듈 (lazy loading 단위)
│   │   │   ├── auth/                  # 인증 기능
│   │   │   │   ├── login/             # 로그인 페이지
│   │   │   │   │   └── login.component.ts
│   │   │   │   ├── register/          # 회원가입 페이지
│   │   │   │   ├── services/          # 인증 관련 서비스
│   │   │   │   └── auth.routes.ts     # 인증 라우트
│   │   │   ├── dashboard/
│   │   │   │   ├── dashboard.component.ts
│   │   │   │   ├── widgets/           # 대시보드 위젯 컴포넌트
│   │   │   │   └── dashboard.routes.ts
│   │   │   └── posts/
│   │   │       ├── post-list/
│   │   │       ├── post-detail/
│   │   │       ├── services/
│   │   │       └── posts.routes.ts
│   │   ├── layouts/                   # 레이아웃 컴포넌트
│   │   │   ├── main-layout/
│   │   │   └── auth-layout/
│   │   └── models/                    # 타입, 인터페이스, enum
│   ├── environments/                  # 환경 설정
│   │   ├── environment.ts
│   │   └── environment.prod.ts
│   ├── assets/                        # 정적 자원
│   ├── styles/                        # 전역 스타일
│   ├── index.html                     # 엔트리 HTML
│   └── main.ts                        # 부트스트랩 (bootstrapApplication)
├── angular.json                       # Angular CLI 설정 ← 반드시 프로젝트 루트에 위치
├── package.json                       # ← 반드시 프로젝트 루트에 위치
└── tsconfig.json                      # TypeScript 설정
```

### 레이어별 책임 분리 (Architecture Rules)

각 디렉토리는 명확한 책임 범위를 가집니다. **경계를 넘는 코드는 금지합니다.**

| 레이어 | 책임 | 허용 | 금지 |
|--------|------|------|------|
| `features/*/component.ts` | 페이지 조합, 사용자 인터랙션 | 서비스 주입, 템플릿 바인딩, 시그널 | 직접 HTTP 호출, 복잡한 비즈니스 로직 |
| `core/services/` | 비즈니스 로직, API 통신 | `HttpClient`, `inject()`, RxJS, 시그널 | DOM 접근, 컴포넌트 렌더링 |
| `core/interceptors/` | HTTP 요청/응답 가공 | 헤더 추가, 에러 변환, 토큰 갱신 | 비즈니스 로직, 라우팅 |
| `core/guards/` | 라우트 접근 제어 | 인증 체크, 권한 검증, 리다이렉트 | HTTP 호출, DOM 접근 |
| `shared/components/` | 재사용 UI 렌더링 | `input()`, `output()`, 템플릿, 스타일 | 직접 서비스 주입 (표시용 파이프 제외) |
| `shared/pipes/` | 데이터 변환 (표시용) | 순수 함수, 포맷팅 | HTTP 호출, 상태 변경 |
| `shared/directives/` | DOM 동작 확장 | `ElementRef`, `Renderer2` | HTTP 호출, 비즈니스 로직 |
| `models/` | 타입 정의 | `interface`, `type`, `enum` | 로직, 함수 구현 |
| `layouts/` | 페이지 레이아웃 구조 | `<router-outlet>`, 네비게이션 | 비즈니스 로직 |

#### 데이터 흐름 (단방향)

```
core/services/  →  features/component.ts  →  shared/components/
  (API/비즈니스)      (조합, 상태 관리)           (UI 렌더링)

core/interceptors/ → HttpClient 요청/응답 가공
core/guards/ → Router 접근 제어
```

#### 올바른 분리 예시

```typescript
// ✅ models/post.model.ts - 타입 정의만
export interface Post {
  id: string;
  title: string;
  content: string;
  createdAt: Date;
}

export interface CreatePostDto {
  title: string;
  content: string;
}
```

```typescript
// ✅ core/services/post.service.ts - 비즈니스 로직 + API
import { Injectable, inject, signal, computed } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { toSignal } from '@angular/core/rxjs-interop';
import { Post, CreatePostDto } from '../../models/post.model';

@Injectable({ providedIn: 'root' })
export class PostService {
  private http = inject(HttpClient);

  private postsSignal = signal<Post[]>([]);
  readonly posts = this.postsSignal.asReadonly();
  readonly postCount = computed(() => this.postsSignal().length);

  loadPosts(): void {
    this.http.get<Post[]>('/api/posts').subscribe({
      next: (posts) => this.postsSignal.set(posts),
      error: (err) => console.error('게시글 로딩 실패:', err),
    });
  }

  createPost(dto: CreatePostDto) {
    return this.http.post<Post>('/api/posts', dto);
  }

  deletePost(id: string) {
    return this.http.delete(`/api/posts/${id}`);
  }
}
```

```typescript
// ✅ shared/components/post-card/post-card.component.ts - UI만 담당
import { Component, input, output } from '@angular/core';
import { DatePipe } from '@angular/common';
import { Post } from '../../../models/post.model';

@Component({
  selector: 'app-post-card',
  standalone: true,
  imports: [DatePipe],
  template: `
    <article class="rounded-lg border p-4">
      <h2 class="text-lg font-semibold">{{ post().title }}</h2>
      <p class="text-muted">{{ post().createdAt | date:'yyyy-MM-dd' }}</p>
      <button (click)="delete.emit(post().id)">삭제</button>
    </article>
  `,
})
export class PostCardComponent {
  post = input.required<Post>();
  delete = output<string>();
}
```

```typescript
// ✅ features/posts/post-list/post-list.component.ts - 조합 레이어
import { Component, inject, OnInit } from '@angular/core';
import { PostCardComponent } from '../../../shared/components/post-card/post-card.component';
import { PostService } from '../../../core/services/post.service';

@Component({
  selector: 'app-post-list',
  standalone: true,
  imports: [PostCardComponent],
  template: `
    @for (post of postService.posts(); track post.id) {
      <app-post-card [post]="post" (delete)="onDelete($event)" />
    }
  `,
})
export class PostListComponent implements OnInit {
  protected postService = inject(PostService);

  ngOnInit(): void {
    this.postService.loadPosts();
  }

  onDelete(id: string): void {
    this.postService.deletePost(id).subscribe(() => {
      this.postService.loadPosts();
    });
  }
}
```

#### 안티패턴: 경계 위반

```typescript
// ❌ 컴포넌트에서 직접 HttpClient 사용
@Component({ ... })
export class PostListComponent {
  private http = inject(HttpClient);  // 서비스로 분리해야 함!
  posts = this.http.get<Post[]>('/api/posts');
}

// ❌ 서비스에서 DOM 접근
@Injectable({ providedIn: 'root' })
export class ThemeService {
  constructor() {
    document.body.classList.add('dark');  // 디렉티브 또는 컴포넌트에서 처리!
  }
}

// ❌ shared 컴포넌트에서 비즈니스 서비스 주입
@Component({ selector: 'app-button', ... })
export class ButtonComponent {
  private postService = inject(PostService);  // shared는 특정 서비스에 의존 금지!
}

// ❌ NgModule 사용
@NgModule({
  declarations: [PostListComponent],  // standalone: true 사용!
  imports: [CommonModule],
})
export class PostsModule {}
```

---

## 2. 부트스트랩 및 설정

### bootstrapApplication (Standalone)

```typescript
// main.ts
import { bootstrapApplication } from '@angular/platform-browser';
import { AppComponent } from './app/app.component';
import { appConfig } from './app/app.config';

bootstrapApplication(AppComponent, appConfig)
  .catch((err) => console.error(err));
```

```typescript
// app.config.ts - 애플리케이션 설정
import { ApplicationConfig, provideZoneChangeDetection } from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { routes } from './app.routes';
import { authInterceptor } from './core/interceptors/auth.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withInterceptors([authInterceptor])),
  ],
};
```

---

## 3. 라우팅

### Standalone Routes

```typescript
// app.routes.ts
import { Routes } from '@angular/router';
import { authGuard } from './core/guards/auth.guard';
import { MainLayoutComponent } from './layouts/main-layout/main-layout.component';

export const routes: Routes = [
  {
    path: '',
    component: MainLayoutComponent,
    children: [
      { path: '', loadComponent: () => import('./features/home/home.component').then(m => m.HomeComponent) },
      {
        path: 'posts',
        loadChildren: () => import('./features/posts/posts.routes').then(m => m.POSTS_ROUTES),
      },
      {
        path: 'dashboard',
        canActivate: [authGuard],
        loadChildren: () => import('./features/dashboard/dashboard.routes').then(m => m.DASHBOARD_ROUTES),
      },
    ],
  },
  {
    path: 'auth',
    loadChildren: () => import('./features/auth/auth.routes').then(m => m.AUTH_ROUTES),
  },
  { path: '**', redirectTo: '' },
];
```

```typescript
// features/posts/posts.routes.ts - 기능별 라우트
import { Routes } from '@angular/router';

export const POSTS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () => import('./post-list/post-list.component').then(m => m.PostListComponent),
  },
  {
    path: ':id',
    loadComponent: () => import('./post-detail/post-detail.component').then(m => m.PostDetailComponent),
  },
];
```

### 라우트 가드 (Functional)

```typescript
// core/guards/auth.guard.ts
import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from '../services/auth.service';

export const authGuard: CanActivateFn = () => {
  const authService = inject(AuthService);
  const router = inject(Router);

  if (authService.isAuthenticated()) {
    return true;
  }

  return router.createUrlTree(['/auth/login']);
};
```

### 라우트 리졸버 (Functional)

```typescript
// core/resolvers/post.resolver.ts
import { inject } from '@angular/core';
import { ResolveFn } from '@angular/router';
import { PostService } from '../services/post.service';
import { Post } from '../../models/post.model';

export const postResolver: ResolveFn<Post> = (route) => {
  const postService = inject(PostService);
  return postService.getPost(route.paramMap.get('id')!);
};

// 라우트에서 사용
{
  path: ':id',
  loadComponent: () => import('./post-detail/post-detail.component').then(m => m.PostDetailComponent),
  resolve: { post: postResolver },
}

// 컴포넌트에서 resolve 데이터 접근
@Component({ ... })
export class PostDetailComponent {
  post = input.required<Post>();  // withComponentInputBinding() 사용 시 자동 바인딩
}
```

---

## 4. Signals (반응형 상태 관리)

### 기본 Signal 패턴

```typescript
import { signal, computed, effect } from '@angular/core';

// 쓰기 가능한 시그널
const count = signal(0);
count.set(10);             // 값 설정
count.update(v => v + 1);  // 이전 값 기반 업데이트

// 파생 시그널 (자동 추적)
const doubled = computed(() => count() * 2);

// 부수효과 (자동 추적, 자동 정리)
effect(() => {
  console.log(`현재 값: ${count()}`);
});
```

### 컴포넌트에서 Signal 사용

```typescript
@Component({
  selector: 'app-counter',
  standalone: true,
  template: `
    <div>
      <p>Count: {{ count() }}</p>
      <p>Doubled: {{ doubled() }}</p>
      <button (click)="increment()">+</button>
      <button (click)="decrement()">-</button>
    </div>
  `,
})
export class CounterComponent {
  count = signal(0);
  doubled = computed(() => this.count() * 2);

  increment(): void {
    this.count.update(v => v + 1);
  }

  decrement(): void {
    this.count.update(v => v - 1);
  }
}
```

### Signal Inputs / Outputs (Angular 17.1+)

```typescript
import { Component, input, output, model } from '@angular/core';

@Component({
  selector: 'app-user-card',
  standalone: true,
  template: `
    <div>
      <h3>{{ name() }}</h3>
      <p>{{ role() }}</p>
      <button (click)="select.emit(id())">선택</button>
    </div>
  `,
})
export class UserCardComponent {
  // Signal Input (필수)
  id = input.required<string>();
  name = input.required<string>();

  // Signal Input (선택, 기본값)
  role = input<string>('member');

  // Signal Output
  select = output<string>();

  // Two-way binding (model)
  checked = model(false);
}
```

### RxJS와 Signal 연동

```typescript
import { toSignal, toObservable } from '@angular/core/rxjs-interop';

@Component({ ... })
export class SearchComponent {
  private http = inject(HttpClient);

  query = signal('');

  // Signal → Observable → Signal (검색 debounce)
  results = toSignal(
    toObservable(this.query).pipe(
      debounceTime(300),
      distinctUntilChanged(),
      filter(q => q.length > 2),
      switchMap(q => this.http.get<Result[]>(`/api/search?q=${q}`)),
    ),
    { initialValue: [] }
  );
}
```

---

## 5. RxJS 패턴

### 서비스에서 RxJS

```typescript
@Injectable({ providedIn: 'root' })
export class PostService {
  private http = inject(HttpClient);

  // GET - Observable 반환
  getPosts(): Observable<Post[]> {
    return this.http.get<Post[]>('/api/posts');
  }

  // GET with params
  searchPosts(query: string, page: number): Observable<PaginatedResponse<Post>> {
    return this.http.get<PaginatedResponse<Post>>('/api/posts', {
      params: { q: query, page: page.toString() },
    });
  }

  // POST
  createPost(dto: CreatePostDto): Observable<Post> {
    return this.http.post<Post>('/api/posts', dto);
  }

  // DELETE + 캐시 무효화 패턴
  deletePost(id: string): Observable<void> {
    return this.http.delete<void>(`/api/posts/${id}`);
  }
}
```

### 컴포넌트에서 구독 관리

```typescript
@Component({ ... })
export class PostListComponent implements OnInit {
  private postService = inject(PostService);
  private destroyRef = inject(DestroyRef);

  posts = signal<Post[]>([]);
  loading = signal(false);

  ngOnInit(): void {
    this.loadPosts();
  }

  private loadPosts(): void {
    this.loading.set(true);
    this.postService.getPosts()
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.loading.set(false)),
      )
      .subscribe({
        next: (posts) => this.posts.set(posts),
        error: (err) => console.error('로딩 실패:', err),
      });
  }
}
```

### 안티패턴: RxJS 실수

```typescript
// ❌ subscribe 안에서 subscribe (콜백 지옥)
this.authService.login(creds).subscribe(user => {
  this.postService.getPosts(user.id).subscribe(posts => {
    this.posts = posts;  // 중첩 구독!
  });
});

// ✅ 파이프로 체이닝
this.authService.login(creds).pipe(
  switchMap(user => this.postService.getPosts(user.id)),
  takeUntilDestroyed(this.destroyRef),
).subscribe(posts => this.posts.set(posts));

// ❌ 구독 해제 미처리 (메모리 누수)
ngOnInit() {
  this.service.getData().subscribe(data => { ... });  // 구독 해제 안 됨!
}

// ✅ takeUntilDestroyed 사용
ngOnInit() {
  this.service.getData()
    .pipe(takeUntilDestroyed(this.destroyRef))
    .subscribe(data => { ... });
}
```

---

## 6. Dependency Injection

### inject() 함수 (권장)

```typescript
// ✅ inject() 함수 (Angular 14+, 권장)
@Component({ ... })
export class PostListComponent {
  private postService = inject(PostService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
}

// ❌ 생성자 주입 (레거시, 사용 자제)
@Component({ ... })
export class PostListComponent {
  constructor(
    private postService: PostService,
    private router: Router,
  ) {}
}
```

### 서비스 제공 범위

```typescript
// 앱 전역 싱글톤 (기본 권장)
@Injectable({ providedIn: 'root' })
export class AuthService { }

// 컴포넌트 레벨 (인스턴스마다 새 서비스)
@Component({
  providers: [FormService],  // 이 컴포넌트와 자식에서만 사용
})
export class EditFormComponent {
  private formService = inject(FormService);
}

// 환경 기반 설정 (InjectionToken)
import { InjectionToken } from '@angular/core';

export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL');

// app.config.ts에서 제공
providers: [
  { provide: API_BASE_URL, useValue: environment.apiUrl },
]

// 서비스에서 사용
@Injectable({ providedIn: 'root' })
export class ApiService {
  private baseUrl = inject(API_BASE_URL);
}
```

---

## 7. Forms (Reactive Forms)

### 기본 Reactive Form

```typescript
import { Component, inject } from '@angular/core';
import { ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';

@Component({
  selector: 'app-post-form',
  standalone: true,
  imports: [ReactiveFormsModule],
  template: `
    <form [formGroup]="form" (ngSubmit)="onSubmit()">
      <div>
        <label for="title">제목</label>
        <input id="title" formControlName="title" />
        @if (form.controls.title.errors?.['required'] && form.controls.title.touched) {
          <span class="error">제목은 필수입니다</span>
        }
        @if (form.controls.title.errors?.['maxlength']) {
          <span class="error">200자 이하로 입력하세요</span>
        }
      </div>

      <div>
        <label for="content">내용</label>
        <textarea id="content" formControlName="content"></textarea>
      </div>

      <button type="submit" [disabled]="form.invalid || submitting()">
        {{ submitting() ? '저장 중...' : '저장' }}
      </button>
    </form>
  `,
})
export class PostFormComponent {
  private fb = inject(FormBuilder);
  private postService = inject(PostService);

  submitting = signal(false);

  form = this.fb.nonNullable.group({
    title: ['', [Validators.required, Validators.maxLength(200)]],
    content: ['', [Validators.required]],
  });

  onSubmit(): void {
    if (this.form.invalid) return;

    this.submitting.set(true);
    this.postService.createPost(this.form.getRawValue()).subscribe({
      next: () => {
        this.form.reset();
        this.submitting.set(false);
      },
      error: () => this.submitting.set(false),
    });
  }
}
```

### 커스텀 Validator

```typescript
import { AbstractControl, ValidationErrors, ValidatorFn } from '@angular/forms';

// 동기 밸리데이터
export function noWhitespace(): ValidatorFn {
  return (control: AbstractControl): ValidationErrors | null => {
    const isWhitespace = (control.value || '').trim().length === 0;
    return isWhitespace ? { whitespace: true } : null;
  };
}

// 비동기 밸리데이터 (서버 검증)
export function uniqueEmail(userService: UserService): AsyncValidatorFn {
  return (control: AbstractControl): Observable<ValidationErrors | null> => {
    return userService.checkEmail(control.value).pipe(
      map(exists => exists ? { emailTaken: true } : null),
      catchError(() => of(null)),
    );
  };
}
```

---

## 8. HttpClient 및 인터셉터

### HTTP 인터셉터 (Functional)

```typescript
// core/interceptors/auth.interceptor.ts
import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { AuthService } from '../services/auth.service';

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const authService = inject(AuthService);
  const token = authService.getToken();

  if (token) {
    req = req.clone({
      setHeaders: { Authorization: `Bearer ${token}` },
    });
  }

  return next(req);
};
```

```typescript
// core/interceptors/error.interceptor.ts
import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const router = inject(Router);

  return next(req).pipe(
    catchError((error: HttpErrorResponse) => {
      switch (error.status) {
        case 401:
          router.navigate(['/auth/login']);
          break;
        case 403:
          router.navigate(['/forbidden']);
          break;
        case 500:
          console.error('서버 에러:', error.message);
          break;
      }
      return throwError(() => error);
    }),
  );
};
```

```typescript
// core/interceptors/loading.interceptor.ts
import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { finalize } from 'rxjs';
import { LoadingService } from '../services/loading.service';

export const loadingInterceptor: HttpInterceptorFn = (req, next) => {
  const loadingService = inject(LoadingService);
  loadingService.show();

  return next(req).pipe(
    finalize(() => loadingService.hide()),
  );
};

// app.config.ts에 등록
provideHttpClient(
  withInterceptors([authInterceptor, errorInterceptor, loadingInterceptor]),
),
```

---

## 9. 템플릿 제어 흐름 (Angular 17+)

### 새로운 제어 흐름 문법

```html
<!-- @if / @else -->
@if (isLoggedIn()) {
  <app-dashboard />
} @else {
  <app-login />
}

<!-- @for (track 필수!) -->
@for (post of posts(); track post.id) {
  <app-post-card [post]="post" />
} @empty {
  <p>게시글이 없습니다.</p>
}

<!-- @switch -->
@switch (status()) {
  @case ('loading') { <app-spinner /> }
  @case ('error') { <app-error [message]="errorMessage()" /> }
  @case ('success') { <app-content [data]="data()" /> }
}

<!-- @defer (지연 로딩) -->
@defer (on viewport) {
  <app-heavy-chart />
} @placeholder {
  <div class="skeleton h-64"></div>
} @loading (minimum 500ms) {
  <app-spinner />
} @error {
  <p>차트 로딩 실패</p>
}
```

### 안티패턴: 레거시 구조 디렉티브

```html
<!-- ❌ 레거시 (사용 금지) -->
<div *ngIf="isLoggedIn">...</div>
<div *ngFor="let post of posts">...</div>
<div [ngSwitch]="status">...</div>

<!-- ✅ 새 제어 흐름 (Angular 17+) -->
@if (isLoggedIn()) { ... }
@for (post of posts(); track post.id) { ... }
@switch (status()) { ... }
```

---

## 10. 테스팅

### Jest + Angular Testing Library

```typescript
// jest.config.ts
import type { Config } from 'jest';

const config: Config = {
  preset: 'jest-preset-angular',
  setupFilesAfterSetup: ['<rootDir>/setup-jest.ts'],
  testPathIgnorePatterns: ['/node_modules/', '/e2e/'],
};

export default config;
```

```typescript
// features/posts/post-list/post-list.component.spec.ts
import { render, screen } from '@testing-library/angular';
import { userEvent } from '@testing-library/user-event';
import { PostListComponent } from './post-list.component';
import { PostService } from '../../../core/services/post.service';

describe('PostListComponent', () => {
  const mockPosts = [
    { id: '1', title: '첫 번째 게시글', content: '내용', createdAt: new Date() },
    { id: '2', title: '두 번째 게시글', content: '내용', createdAt: new Date() },
  ];

  it('게시글 목록을 렌더링한다', async () => {
    const mockPostService = {
      posts: signal(mockPosts),
      loadPosts: jest.fn(),
      deletePost: jest.fn().mockReturnValue(of(undefined)),
    };

    await render(PostListComponent, {
      providers: [{ provide: PostService, useValue: mockPostService }],
    });

    expect(screen.getByText('첫 번째 게시글')).toBeTruthy();
    expect(screen.getByText('두 번째 게시글')).toBeTruthy();
    expect(mockPostService.loadPosts).toHaveBeenCalled();
  });

  it('삭제 버튼 클릭 시 삭제 서비스를 호출한다', async () => {
    const mockPostService = {
      posts: signal(mockPosts),
      loadPosts: jest.fn(),
      deletePost: jest.fn().mockReturnValue(of(undefined)),
    };

    await render(PostListComponent, {
      providers: [{ provide: PostService, useValue: mockPostService }],
    });

    const deleteButtons = screen.getAllByText('삭제');
    await userEvent.click(deleteButtons[0]);

    expect(mockPostService.deletePost).toHaveBeenCalledWith('1');
  });
});
```

### 서비스 테스트

```typescript
// core/services/post.service.spec.ts
import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { PostService } from './post.service';

describe('PostService', () => {
  let service: PostService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(PostService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();  // 미처리 요청 확인
  });

  it('게시글 목록을 가져온다', () => {
    const mockPosts = [{ id: '1', title: 'Test' }];

    service.loadPosts();

    const req = httpMock.expectOne('/api/posts');
    expect(req.request.method).toBe('GET');
    req.flush(mockPosts);

    expect(service.posts()).toEqual(mockPosts);
  });
});
```

### Playwright E2E

```typescript
// e2e/navigation.spec.ts
import { test, expect } from '@playwright/test';

test('게시글 페이지 네비게이션', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('h1')).toBeVisible();

  await page.getByRole('link', { name: '게시글' }).click();
  await expect(page).toHaveURL('/posts');
  await expect(page.getByRole('heading', { name: '게시글 목록' })).toBeVisible();
});

test('인증되지 않은 사용자는 대시보드 접근 불가', async ({ page }) => {
  await page.goto('/dashboard');
  await expect(page).toHaveURL('/auth/login');
});
```

---

## 11. 성능 최적화

### OnPush Change Detection

```typescript
import { ChangeDetectionStrategy, Component } from '@angular/core';

@Component({
  selector: 'app-post-card',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,  // 필수!
  template: `...`,
})
export class PostCardComponent {
  post = input.required<Post>();
}
```

### @defer (지연 로딩)

```html
<!-- 뷰포트 진입 시 로딩 -->
@defer (on viewport) {
  <app-comments [postId]="post().id" />
} @placeholder {
  <div class="h-32 animate-pulse"></div>
}

<!-- 유휴 시 프리페치 -->
@defer (on idle; prefetch on hover) {
  <app-recommendations />
}
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
  lazy-chunk: < 50KB (gzip)
```

---

## 12. TypeScript

### 기본 설정

```json
// tsconfig.json
{
  "compilerOptions": {
    "strict": true,
    "paths": { "@app/*": ["./src/app/*"] }
  }
}
```

### 엄격한 템플릿 타입 체크

```json
// tsconfig.json
{
  "angularCompilerOptions": {
    "strictInjectionParameters": true,
    "strictInputAccessModifiers": true,
    "strictTemplates": true
  }
}
```

---

## 13. 흔한 실수 종합

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| `@NgModule` 사용 | Angular 17+ standalone과 충돌 | `standalone: true` 컴포넌트 사용 |
| 생성자 주입 | 레거시 패턴, 가독성 저하 | `inject()` 함수 사용 |
| `*ngIf`, `*ngFor` 사용 | 레거시 구조 디렉티브 | `@if`, `@for` 제어 흐름 사용 |
| `@for`에서 `track` 누락 | 성능 저하, 불필요한 DOM 재생성 | `track post.id` 필수 추가 |
| 구독 해제 미처리 | 메모리 누수 | `takeUntilDestroyed(this.destroyRef)` 사용 |
| 중첩 subscribe | 콜백 지옥, 에러 처리 어려움 | RxJS `switchMap`, `mergeMap` 파이프 사용 |
| 컴포넌트에서 직접 HTTP 호출 | 책임 분리 위반 | 서비스로 분리 |
| `OnPush` 미사용 | 불필요한 변경 감지 실행 | `ChangeDetectionStrategy.OnPush` 설정 |
| Template-driven forms 사용 | 테스트 어려움, 타입 안전 부족 | Reactive Forms 사용 |
| 서비스에서 DOM 접근 | 책임 위반, SSR 호환성 문제 | 디렉티브 또는 컴포넌트에서 처리 |
| `any` 타입 남용 | 타입 안전 무효화 | `strict: true` + 명시적 타입 정의 |
| Signal과 Observable 혼용 없이 사용 | 복잡한 비동기 처리 불가 | `toSignal()`, `toObservable()` 연동 |
