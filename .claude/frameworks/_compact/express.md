# Express.js + TypeScript Quick Reference (Compact)

**Framework**: Express.js + TypeScript | **패턴**: Controller-Service-Repository | **Core**: Express + Prisma + zod
**TypeScript**: 필수 (`strict: true`, `@/*` 경로 별칭, `noUncheckedIndexedAccess`)

## 디렉토리 구조

> **FATAL RULE**: Controller-Service-Repository 레이어 분리 필수.
> **절대 금지**: Controller에서 DB 직접 접근, Service에서 `req`/`res` 사용, 라우트 파일에 비즈니스 로직 작성
> **검증 필수**: `package.json` + `tsconfig.json` + `src/app.ts` 존재

```
src/
├── app.ts                      # Express 앱 설정 (미들웨어, 라우트 등록)
├── server.ts                   # 서버 시작점 (listen) — app과 분리 필수
├── config/                     # 환경 설정 (env 검증: zod)
├── modules/                    # 기능별 모듈 (도메인 중심)
│   └── user/
│       ├── user.router.ts      # 라우트 정의 + 미들웨어 연결
│       ├── user.controller.ts  # HTTP 요청 파싱 / 응답 포맷팅
│       ├── user.service.ts     # 비즈니스 로직
│       ├── user.repository.ts  # DB 접근 (Prisma 쿼리)
│       ├── user.schema.ts      # 입력 검증 (zod)
│       └── __tests__/          # 단위/통합 테스트
├── middlewares/                 # 공통: error-handler, auth, validate, rate-limit
├── shared/                     # 에러 클래스, 유틸, 공통 타입
└── database/prisma.ts          # Prisma 클라이언트 싱글톤
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `*.router.ts` | 라우트 정의, 미들웨어 체인 | 비즈니스 로직, DB 접근 |
| `*.controller.ts` | `req` 파싱, `res` 반환, DTO 변환 | DB 접근, 비즈니스 로직 |
| `*.service.ts` | 비즈니스 로직, Repository 호출 | `req`/`res` 접근, 직접 DB 쿼리 |
| `*.repository.ts` | DB CRUD (Prisma) | 비즈니스 로직, HTTP 코드 |
| `*.schema.ts` | zod 스키마 + 타입 추론 | 로직, DB 접근 |
| `middlewares/` | 인증, 검증, 에러 처리, CORS | 도메인 로직 |

**데이터 흐름**: `Request` -> `Router` -> `Middleware` -> `Controller` -> `Service` -> `Repository` -> `DB`

> **코드 생성 트리거**: Controller/Service/Repository 코드를 **작성**할 때는
> 반드시 `frameworks/express.md`의 **코드 예시와 안티패턴**을 로드하여 참조할 것.
> compact 테이블만으로 코드 생성 금지.

## 미들웨어 등록 순서

`helmet` -> `cors` -> `body parser (10kb)` -> `rate-limit` -> `logger` -> **라우트** -> `404 handler` -> **에러 핸들러(마지막!)**

## 핵심 패턴

```typescript
// 검증 미들웨어 (zod)
export function validate(schema: AnyZodObject) {
  return async (req, res, next) => {
    try { await schema.parseAsync({ body: req.body, query: req.query, params: req.params }); next() }
    catch (e) { res.status(400).json({ error: 'Validation failed', details: e.errors }) }
  }
}
// 에러 핸들러 (반드시 4개 인자!)
export function errorHandler(err: Error, req: Request, res: Response, _next: NextFunction) { ... }
// JWT 인증
export function authenticate(req, res, next) {
  const token = req.headers.authorization?.split(' ')[1]
  req.userId = jwt.verify(token, secret).sub; next()
}
// Prisma 싱글톤 (globalThis 패턴)
const globalForPrisma = globalThis as unknown as { prisma: PrismaClient | undefined }
export const prisma = globalForPrisma.prisma ?? new PrismaClient()
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| Controller에서 DB 직접 접근 | Service/Repository를 통해 접근 |
| Service에서 `req`/`res` 사용 | 매개변수로 필요한 데이터만 전달 |
| 에러 핸들러 인자 3개 | 반드시 `(err, req, res, next)` 4개 |
| `async` 핸들러에서 `next(error)` 누락 | try-catch + `next(error)` |
| 환경변수 미검증 | zod로 시작 시 검증 |
| `app.listen`을 app.ts에 직접 배치 | server.ts로 분리 (테스트 포트 충돌 방지) |
| 비밀번호 평문 저장 | bcrypt/argon2 해싱 (rounds >= 12) |
| `select` 없이 전체 필드 반환 | 민감 정보 노출 방지: 필요한 필드만 select |
| 미들웨어 순서 무시 | helmet -> cors -> body -> routes -> error |

> **전체 가이드**: `frameworks/express.md` 참조
