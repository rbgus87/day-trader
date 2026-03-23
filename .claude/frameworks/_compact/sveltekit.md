# SvelteKit Quick Reference (Compact)

**Framework**: SvelteKit | **라우팅**: `src/routes/` | **Core**: Svelte 5 + Vite
**TypeScript**: 권장 (`$types` 자동 생성)

## 디렉토리 구조

> **FATAL RULE**: `+page.ts`(universal)와 `+page.server.ts`(서버 전용)를 혼동하지 말 것.
> **DB/비밀키**는 반드시 `+page.server.ts` 또는 `+server.ts`에서만 사용.
> **절대 금지**: `+page.ts`에서 `$lib/server` import ← 서버 코드가 클라이언트 번들에 포함!
> **검증 필수**: `svelte.config.js`가 루트에 존재 + `src/routes/` 디렉토리 존재

```
src/
├── routes/                 # 파일 기반 라우팅
│   ├── +page.svelte        # 페이지 컴포넌트
│   ├── +page.server.ts     # 서버 load + form actions
│   ├── +page.ts            # universal load (public API만)
│   ├── +layout.svelte      # 레이아웃
│   ├── +error.svelte       # 에러 페이지
│   └── api/.../+server.ts  # REST API 엔드포인트
├── lib/                    # $lib alias
│   ├── components/         # 재사용 컴포넌트
│   ├── server/             # 서버 전용 ($lib/server)
│   ├── stores/             # Svelte stores
│   └── utils/              # 순수 함수
├── hooks.server.ts         # 서버 훅 (handle, handleError)
└── app.html                # HTML 템플릿
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `+page.svelte` | UI 렌더링 (data prop 사용) | 직접 DB 접근, 비즈니스 로직 |
| `+page.server.ts` | 서버 데이터 로드, form actions | 클라이언트 상태, DOM 접근 |
| `+page.ts` | public API 호출, 데이터 변환 | DB 접근, `$lib/server` import |
| `+server.ts` | REST API 엔드포인트 | 컴포넌트 렌더링 |
| `lib/components/` | UI 렌더링 (props/events) | 직접 API 호출, 전역 상태 변경 |
| `lib/server/` | DB, 인증, 외부 API | 클라이언트 코드 |
| `lib/stores/` | 클라이언트 상태 관리 | DB 접근, 서버 로직 |
| `lib/utils/` | 순수 함수 (상태 없음) | store, 사이드이펙트 |

**데이터 흐름**: `lib/server/` → `+page.server.ts` → `+page.svelte` → `lib/components/`

> **코드 생성 트리거**: 컴포넌트/load함수/action 코드를 **작성**할 때는
> 반드시 `frameworks/sveltekit.md`의 **코드 예시와 안티패턴**을 로드하여 참조할 것.
> compact 테이블만으로 코드 생성 금지.

## 데이터 페칭

| 파일 | 실행 위치 | DB 접근 | 비고 |
|------|----------|---------|------|
| `+page.server.ts` | 서버 전용 | ✅ | 민감 데이터, form actions |
| `+page.ts` | 서버+클라이언트 | ❌ | public API만 |
| `+server.ts` | 서버 전용 | ✅ | REST API 엔드포인트 |

## 핵심 패턴

```typescript
// 서버 load (+page.server.ts)
export const load: PageServerLoad = async ({ params }) => {
  return { post: await db.post.findUnique({ where: { id: params.id } }) };
};
// Form action (+page.server.ts)
export const actions = {
  default: async ({ request }) => {
    const data = await request.formData();
    if (!data.get('title')) return fail(400, { error: '제목 필수' });
    redirect(303, '/posts');
  }
};
// hooks.server.ts
export const handle: Handle = async ({ event, resolve }) => {
  event.locals.user = await getUserFromCookie(event.cookies);
  return resolve(event);
};
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| `+page.ts`에서 `$lib/server` import | `+page.server.ts` 사용 |
| `onMount` + fetch로 초기 데이터 | load 함수 사용 |
| `throw new Error()` (서버) | `error(404, 'msg')` 사용 |
| store에서 `browser` 미체크 | `import { browser } from '$app/environment'` |
| `use:enhance` 미사용 | 폼에 `use:enhance` 추가 |
| 글로벌 fetch 사용 (load 내) | load 매개변수의 `fetch` 사용 |
| 모듈 레벨 상태 (서버) | `event.locals` 사용 |
| 스트리밍에서 `await` 추가 | 프로미스 직접 반환 (await 없이) |

> **전체 가이드**: `frameworks/sveltekit.md` 참조
