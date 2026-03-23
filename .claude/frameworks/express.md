# Express.js + TypeScript Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-11

Express.js + TypeScript 백엔드 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`npm init -y && npm install express typescript`으로 설정하거나, 보일러플레이트 생성기를 사용합니다.

---

## 0. Express vs Fastify 비교

| 항목 | Express | Fastify |
|------|---------|---------|
| 생태계 | 가장 넓은 미들웨어/플러그인 | 빠르게 성장 중, Express 호환 레이어 |
| 성능 | 보통 (충분) | 높음 (JSON 직렬화 최적화) |
| TypeScript | `@types/express` 별도 설치 | 내장 타입 지원 |
| 스키마 검증 | 별도 라이브러리 (zod, joi) | JSON Schema 내장 + `@fastify/type-provider-zod` |
| 플러그인 시스템 | 미들웨어 체인 | 캡슐화된 플러그인 시스템 |
| 학습 곡선 | 낮음 | 중간 |
| 프로덕션 채택률 | 매우 높음 | 증가 추세 |
| 권장 상황 | 범용, 팀 익숙도 높을 때 | 고성능 API, 새 프로젝트 |

> **이 가이드는 Express를 기준으로 작성**되었으나, 아키텍처 패턴(Controller-Service-Repository)은 Fastify에도 동일하게 적용됩니다.

---

## 1. 디렉토리 구조

### FATAL RULE: 레이어 혼합 방지 — Controller-Service-Repository 패턴

> **Express 백엔드는 Controller-Service-Repository(CSR) 패턴을 따릅니다.**
> **각 레이어의 책임을 절대 혼합하지 마세요.**
> **Controller에서 DB 직접 접근, Service에서 `req`/`res` 접근은 금지입니다.**

#### 절대 금지 사항

```typescript
// ⛔ Controller에서 직접 DB 접근 (Repository/Service를 통해야 함)
app.get('/api/users', async (req, res) => {
  const users = await prisma.user.findMany()  // FATAL: 레이어 위반!
  res.json(users)
})

// ⛔ Service에서 req/res 객체 접근 (HTTP 레이어 오염)
class UserService {
  async getUsers(req: Request) {           // FATAL: req 의존 금지!
    return prisma.user.findMany()
  }
}

// ⛔ 비즈니스 로직을 라우트/미들웨어에 직접 작성
router.post('/users', async (req, res) => {
  const hashedPw = await bcrypt.hash(req.body.password, 10) // Service로 이동!
  const user = await prisma.user.create({ data: { ...req.body, password: hashedPw } })
  const token = jwt.sign({ id: user.id }, SECRET) // Service로 이동!
  res.json({ user, token })
})
```

#### 의사결정 트리

```
STEP 1: CWD(현재 작업 디렉토리)를 확인한다

  CWD에 .claude/ 폴더, package.json, 또는 기존 프로젝트 파일이 있는가?
  ├── YES → CWD가 이미 프로젝트 루트
  │         → npm init -y (현재 폴더에서 초기화)
  │
  └── NO  → CWD는 프로젝트의 상위 폴더
            → mkdir [project-name] && cd [project-name]
            → npm init -y
            (새 서브폴더가 프로젝트 루트가 됨)

STEP 2: 구조 검증 (건너뛰기 금지)

  [PASS 조건 — 3개 모두 충족]
    ✅ package.json 이 CWD에 존재
    ✅ tsconfig.json 이 CWD에 존재
    ✅ src/app.ts 또는 src/server.ts 가 존재

  [FATAL 조건 — 하나라도 해당하면 수정 필수]
    ❌ Controller에서 prisma/DB 직접 import → Repository로 이동!
    ❌ Service에서 Request/Response 타입 사용 → DTO로 변환!
    ❌ 비즈니스 로직이 라우트 파일에 존재 → Service로 이동!
```

### Express 기본 구조

```
project-root/
├── src/
│   ├── app.ts                      # Express 앱 설정 (미들웨어, 라우트 등록)
│   ├── server.ts                   # 서버 시작점 (listen)
│   ├── config/                     # 환경 설정
│   │   ├── index.ts                # 통합 설정 export
│   │   ├── database.ts             # DB 연결 설정
│   │   └── env.ts                  # 환경변수 검증 (zod)
│   ├── modules/                    # 기능별 모듈 (도메인 중심)
│   │   ├── user/
│   │   │   ├── user.controller.ts  # HTTP 요청/응답 처리
│   │   │   ├── user.service.ts     # 비즈니스 로직
│   │   │   ├── user.repository.ts  # DB 접근 (Prisma 쿼리)
│   │   │   ├── user.router.ts      # 라우트 정의
│   │   │   ├── user.schema.ts      # 요청/응답 스키마 (zod)
│   │   │   ├── user.types.ts       # 타입 정의
│   │   │   └── __tests__/
│   │   │       ├── user.service.test.ts
│   │   │       └── user.controller.test.ts
│   │   ├── auth/
│   │   │   ├── auth.controller.ts
│   │   │   ├── auth.service.ts
│   │   │   ├── auth.router.ts
│   │   │   └── auth.schema.ts
│   │   └── post/
│   │       ├── ...
│   ├── middlewares/                 # 공통 미들웨어
│   │   ├── error-handler.ts        # 전역 에러 핸들러
│   │   ├── auth.ts                 # 인증 미들웨어
│   │   ├── validate.ts             # 요청 검증 미들웨어
│   │   ├── rate-limit.ts           # 요청 제한
│   │   └── cors.ts                 # CORS 설정
│   ├── shared/                     # 공유 유틸리티
│   │   ├── errors/                 # 커스텀 에러 클래스
│   │   │   ├── app-error.ts        # 기본 에러 클래스
│   │   │   └── index.ts
│   │   ├── utils/                  # 순수 함수
│   │   └── types/                  # 공통 타입
│   └── database/                   # DB 설정
│       ├── prisma.ts               # Prisma 클라이언트 인스턴스
│       └── seed.ts                 # 시드 데이터
├── prisma/
│   ├── schema.prisma               # Prisma 스키마
│   └── migrations/                 # 마이그레이션 파일
├── tests/                          # 통합/E2E 테스트
│   ├── setup.ts                    # 테스트 환경 설정
│   └── helpers/                    # 테스트 헬퍼
├── package.json
├── tsconfig.json
└── .env                            # 환경변수 (Git 제외)
```

### 레이어별 책임 분리 (Architecture Rules)

각 레이어는 명확한 책임 범위를 가집니다. **경계를 넘는 코드는 금지합니다.**

| 레이어 | 책임 | 허용 | 금지 |
|--------|------|------|------|
| `*.router.ts` | 라우트 정의, 미들웨어 연결 | `router.get/post/put/delete`, 미들웨어 체인 | 비즈니스 로직, DB 접근, 직접 응답 생성 |
| `*.controller.ts` | HTTP 요청 파싱, 응답 포맷팅 | `req.body/params/query` 파싱, `res.json/status`, DTO 변환 | DB 접근, 비즈니스 로직, 외부 API 호출 |
| `*.service.ts` | 비즈니스 로직 | Repository 호출, 데이터 가공, 검증, 외부 서비스 호출 | `req`/`res` 접근, HTTP 상태 코드, 직접 DB 쿼리 |
| `*.repository.ts` | DB 접근 (CRUD) | Prisma/TypeORM 쿼리, 데이터 매핑 | 비즈니스 로직, HTTP 관련 코드, 외부 API |
| `*.schema.ts` | 입력 검증 스키마 | zod/joi 스키마 정의, 타입 추론 | 비즈니스 로직, DB 접근 |
| `middlewares/` | 횡단 관심사 | 인증, 로깅, 에러 처리, CORS, 요청 제한 | 비즈니스 로직, 특정 도메인 로직 |
| `shared/` | 공유 유틸, 타입, 에러 | 순수 함수, 타입 정의, 에러 클래스 | DB 접근, HTTP 의존, 상태 |

#### 데이터 흐름 (단방향)

```
Request  →  Router  →  Middleware  →  Controller  →  Service  →  Repository  →  DB
                                          │              │
                                     (req 파싱)    (비즈니스 로직)
                                     (res 반환)    (데이터 가공)
Response ←  Error Handler  ←  Controller  ←  Service  ←  Repository  ←  DB
```

#### 올바른 분리 예시

```typescript
// ✅ modules/user/user.schema.ts - 입력 검증 (zod)
import { z } from 'zod'

export const CreateUserSchema = z.object({
  body: z.object({
    email: z.string().email('유효한 이메일을 입력하세요'),
    password: z.string().min(8, '비밀번호는 8자 이상이어야 합니다'),
    name: z.string().min(1, '이름은 필수입니다').max(50),
  }),
})

export const GetUserParamsSchema = z.object({
  params: z.object({
    id: z.string().uuid('유효한 UUID가 아닙니다'),
  }),
})

export type CreateUserInput = z.infer<typeof CreateUserSchema>['body']
```

```typescript
// ✅ modules/user/user.repository.ts - DB 접근만
import { prisma } from '@/database/prisma'
import type { CreateUserInput } from './user.types'

export class UserRepository {
  async findAll(page: number, limit: number) {
    return prisma.user.findMany({
      skip: (page - 1) * limit,
      take: limit,
      select: { id: true, email: true, name: true, createdAt: true },
    })
  }

  async findById(id: string) {
    return prisma.user.findUnique({ where: { id } })
  }

  async findByEmail(email: string) {
    return prisma.user.findUnique({ where: { email } })
  }

  async create(data: CreateUserInput & { password: string }) {
    return prisma.user.create({
      data,
      select: { id: true, email: true, name: true, createdAt: true },
    })
  }
}
```

```typescript
// ✅ modules/user/user.service.ts - 비즈니스 로직
import bcrypt from 'bcrypt'
import { UserRepository } from './user.repository'
import { AppError } from '@/shared/errors/app-error'
import type { CreateUserInput } from './user.types'

export class UserService {
  constructor(private readonly userRepo: UserRepository) {}

  async getUsers(page: number, limit: number) {
    return this.userRepo.findAll(page, limit)
  }

  async getUserById(id: string) {
    const user = await this.userRepo.findById(id)
    if (!user) {
      throw new AppError('사용자를 찾을 수 없습니다', 404)
    }
    return user
  }

  async createUser(input: CreateUserInput) {
    const existing = await this.userRepo.findByEmail(input.email)
    if (existing) {
      throw new AppError('이미 존재하는 이메일입니다', 409)
    }

    const hashedPassword = await bcrypt.hash(input.password, 12)
    return this.userRepo.create({ ...input, password: hashedPassword })
  }
}
```

```typescript
// ✅ modules/user/user.controller.ts - HTTP 처리만
import type { Request, Response, NextFunction } from 'express'
import { UserService } from './user.service'

export class UserController {
  constructor(private readonly userService: UserService) {}

  getUsers = async (req: Request, res: Response, next: NextFunction) => {
    try {
      const page = Number(req.query.page ?? 1)
      const limit = Number(req.query.limit ?? 20)
      const users = await this.userService.getUsers(page, limit)
      res.json({ data: users, meta: { page, limit } })
    } catch (error) {
      next(error)
    }
  }

  getUserById = async (req: Request, res: Response, next: NextFunction) => {
    try {
      const user = await this.userService.getUserById(req.params.id)
      res.json({ data: user })
    } catch (error) {
      next(error)
    }
  }

  createUser = async (req: Request, res: Response, next: NextFunction) => {
    try {
      const user = await this.userService.createUser(req.body)
      res.status(201).json({ data: user })
    } catch (error) {
      next(error)
    }
  }
}
```

```typescript
// ✅ modules/user/user.router.ts - 라우트 + 미들웨어 연결
import { Router } from 'express'
import { UserController } from './user.controller'
import { UserService } from './user.service'
import { UserRepository } from './user.repository'
import { validate } from '@/middlewares/validate'
import { authenticate } from '@/middlewares/auth'
import { CreateUserSchema, GetUserParamsSchema } from './user.schema'

const userRepo = new UserRepository()
const userService = new UserService(userRepo)
const userController = new UserController(userService)

const router = Router()

router.get('/', authenticate, userController.getUsers)
router.get('/:id', authenticate, validate(GetUserParamsSchema), userController.getUserById)
router.post('/', validate(CreateUserSchema), userController.createUser)

export { router as userRouter }
```

#### 안티패턴: 경계 위반

```typescript
// ❌ Controller에서 DB 직접 접근
import { prisma } from '@/database/prisma'
export class UserController {
  getUsers = async (req: Request, res: Response) => {
    const users = await prisma.user.findMany() // Service/Repository 통해야 함!
    res.json(users)
  }
}

// ❌ Service에서 HTTP 객체 의존
export class UserService {
  async getUsers(req: Request) {         // req 의존 금지!
    const page = req.query.page           // Controller에서 파싱 후 전달!
  }
}

// ❌ Router에서 비즈니스 로직
router.post('/users', async (req, res) => {
  if (await prisma.user.findUnique({ where: { email: req.body.email } })) {
    return res.status(409).json({ error: '이미 존재' })  // Service로 이동!
  }
})
```

---

## 2. 미들웨어 체인

### 미들웨어 등록 순서 (중요!)

```typescript
// src/app.ts
import express from 'express'
import helmet from 'helmet'
import cors from 'cors'
import { rateLimit } from 'express-rate-limit'
import { requestLogger } from '@/middlewares/logger'
import { errorHandler } from '@/middlewares/error-handler'
import { notFoundHandler } from '@/middlewares/not-found'
import { userRouter } from '@/modules/user/user.router'
import { authRouter } from '@/modules/auth/auth.router'

const app = express()

// 1. 보안 헤더 (최우선)
app.use(helmet())

// 2. CORS
app.use(cors({ origin: process.env.ALLOWED_ORIGINS?.split(','), credentials: true }))

// 3. Body 파싱
app.use(express.json({ limit: '10kb' }))
app.use(express.urlencoded({ extended: true }))

// 4. 요청 제한
app.use('/api', rateLimit({ windowMs: 15 * 60 * 1000, max: 100 }))

// 5. 로깅
app.use(requestLogger)

// 6. 라우트
app.use('/api/auth', authRouter)
app.use('/api/users', userRouter)

// 7. 404 핸들러 (라우트 뒤에 위치)
app.use(notFoundHandler)

// 8. 전역 에러 핸들러 (반드시 마지막!)
app.use(errorHandler)

export { app }
```

### 요청 검증 미들웨어 (zod)

```typescript
// middlewares/validate.ts
import type { Request, Response, NextFunction } from 'express'
import { AnyZodObject, ZodError } from 'zod'

export function validate(schema: AnyZodObject) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      await schema.parseAsync({
        body: req.body,
        query: req.query,
        params: req.params,
      })
      next()
    } catch (error) {
      if (error instanceof ZodError) {
        res.status(400).json({
          error: 'Validation failed',
          details: error.errors.map(e => ({
            path: e.path.join('.'),
            message: e.message,
          })),
        })
        return
      }
      next(error)
    }
  }
}
```

---

## 3. 에러 처리

### 커스텀 에러 클래스

```typescript
// shared/errors/app-error.ts
export class AppError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number = 500,
    public readonly code?: string,
    public readonly details?: unknown,
  ) {
    super(message)
    this.name = 'AppError'
    Error.captureStackTrace(this, this.constructor)
  }
}

// 파생 에러
export class NotFoundError extends AppError {
  constructor(resource: string, id?: string) {
    super(`${resource}${id ? ` (${id})` : ''}을(를) 찾을 수 없습니다`, 404, 'NOT_FOUND')
  }
}

export class ConflictError extends AppError {
  constructor(message: string) {
    super(message, 409, 'CONFLICT')
  }
}

export class UnauthorizedError extends AppError {
  constructor(message = '인증이 필요합니다') {
    super(message, 401, 'UNAUTHORIZED')
  }
}
```

### 전역 에러 핸들러

```typescript
// middlewares/error-handler.ts
import type { Request, Response, NextFunction } from 'express'
import { AppError } from '@/shared/errors/app-error'
import { ZodError } from 'zod'

export function errorHandler(err: Error, req: Request, res: Response, _next: NextFunction) {
  // AppError (예상된 에러)
  if (err instanceof AppError) {
    res.status(err.statusCode).json({
      error: { code: err.code, message: err.message, details: err.details },
    })
    return
  }

  // Zod 검증 에러
  if (err instanceof ZodError) {
    res.status(400).json({
      error: { code: 'VALIDATION_ERROR', message: 'Validation failed', details: err.errors },
    })
    return
  }

  // 예상하지 못한 에러 (500)
  console.error('Unhandled error:', err)
  res.status(500).json({
    error: { code: 'INTERNAL_ERROR', message: '서버 내부 오류가 발생했습니다' },
  })
}
```

**핵심 규칙:**
- 에러 핸들러는 반드시 4개 인자 `(err, req, res, next)`를 받아야 Express가 인식
- `next(error)` 호출로 에러를 전파 (try-catch에서)
- 프로덕션에서 스택 트레이스를 클라이언트에 노출 금지

---

## 4. 인증 패턴

### JWT 인증

```typescript
// middlewares/auth.ts
import type { Request, Response, NextFunction } from 'express'
import jwt from 'jsonwebtoken'
import { UnauthorizedError } from '@/shared/errors/app-error'
import { config } from '@/config'

export interface AuthRequest extends Request {
  userId?: string
}

export function authenticate(req: AuthRequest, res: Response, next: NextFunction) {
  const authHeader = req.headers.authorization
  if (!authHeader?.startsWith('Bearer ')) {
    return next(new UnauthorizedError('Bearer 토큰이 필요합니다'))
  }

  try {
    const token = authHeader.split(' ')[1]
    const payload = jwt.verify(token, config.jwtSecret) as { sub: string }
    req.userId = payload.sub
    next()
  } catch {
    next(new UnauthorizedError('유효하지 않은 토큰입니다'))
  }
}

export function authorize(...roles: string[]) {
  return async (req: AuthRequest, res: Response, next: NextFunction) => {
    // userId로 사용자 역할 조회 후 검증
    // ...
    next()
  }
}
```

### 세션 인증 (express-session)

```typescript
// 세션 기반 인증 (Redis 스토어 권장)
import session from 'express-session'
import { RedisStore } from 'connect-redis'

app.use(session({
  store: new RedisStore({ client: redisClient }),
  secret: config.sessionSecret,
  resave: false,
  saveUninitialized: false,
  cookie: {
    httpOnly: true,
    secure: config.nodeEnv === 'production',
    sameSite: 'lax',
    maxAge: 24 * 60 * 60 * 1000, // 24시간
  },
}))
```

---

## 5. Database 통합 (Prisma)

### Prisma 클라이언트 싱글톤

```typescript
// database/prisma.ts
import { PrismaClient } from '@prisma/client'

const globalForPrisma = globalThis as unknown as { prisma: PrismaClient | undefined }

export const prisma =
  globalForPrisma.prisma ??
  new PrismaClient({
    log: process.env.NODE_ENV === 'development' ? ['query', 'warn', 'error'] : ['error'],
  })

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = prisma
```

### 환경변수 검증 (zod)

```typescript
// config/env.ts
import { z } from 'zod'

const EnvSchema = z.object({
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  PORT: z.coerce.number().default(3000),
  DATABASE_URL: z.string().url(),
  JWT_SECRET: z.string().min(32),
  ALLOWED_ORIGINS: z.string().default('http://localhost:3000'),
})

export const env = EnvSchema.parse(process.env)
```

---

## 6. TypeScript 설정

```json
// tsconfig.json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "outDir": "./dist",
    "rootDir": "./src",
    "paths": { "@/*": ["./src/*"] },
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist"]
}
```

---

## 7. 테스팅

### Vitest + supertest (권장)

```typescript
// tests/setup.ts
import { prisma } from '@/database/prisma'

beforeAll(async () => {
  // 테스트 DB 연결 확인
  await prisma.$connect()
})

afterAll(async () => {
  await prisma.$disconnect()
})
```

```typescript
// modules/user/__tests__/user.service.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { UserService } from '../user.service'
import { UserRepository } from '../user.repository'
import { AppError } from '@/shared/errors/app-error'

// Repository를 모킹
vi.mock('../user.repository')

describe('UserService', () => {
  let service: UserService
  let mockRepo: vi.Mocked<UserRepository>

  beforeEach(() => {
    mockRepo = new UserRepository() as vi.Mocked<UserRepository>
    service = new UserService(mockRepo)
  })

  describe('getUserById', () => {
    it('존재하는 사용자를 반환한다', async () => {
      const mockUser = { id: '1', email: 'test@test.com', name: '테스트' }
      mockRepo.findById.mockResolvedValue(mockUser as any)

      const result = await service.getUserById('1')
      expect(result).toEqual(mockUser)
    })

    it('사용자가 없으면 404 에러를 던진다', async () => {
      mockRepo.findById.mockResolvedValue(null)

      await expect(service.getUserById('999')).rejects.toThrow(AppError)
    })
  })
})
```

```typescript
// modules/user/__tests__/user.controller.test.ts (통합 테스트)
import { describe, it, expect } from 'vitest'
import request from 'supertest'
import { app } from '@/app'

describe('GET /api/users', () => {
  it('사용자 목록을 반환한다', async () => {
    const response = await request(app)
      .get('/api/users')
      .set('Authorization', 'Bearer valid-token')
      .expect(200)

    expect(response.body).toHaveProperty('data')
    expect(Array.isArray(response.body.data)).toBe(true)
  })

  it('인증 없이 접근하면 401을 반환한다', async () => {
    await request(app).get('/api/users').expect(401)
  })
})

describe('POST /api/users', () => {
  it('유효하지 않은 입력이면 400을 반환한다', async () => {
    const response = await request(app)
      .post('/api/users')
      .send({ email: 'invalid' })
      .expect(400)

    expect(response.body.error).toBeDefined()
  })
})
```

---

## 8. 성능 & 보안 체크리스트

```yaml
security:
  - helmet() 적용 (보안 헤더)
  - CORS 허용 출처 제한
  - rate-limit 적용
  - body 파서 크기 제한 (10kb)
  - SQL Injection 방지 (Prisma 파라미터 바인딩)
  - JWT secret 최소 32자
  - 비밀번호 해싱 (bcrypt, rounds >= 12)
  - 환경변수 .env Git 제외

performance:
  - DB 인덱스 설정 (자주 조회하는 필드)
  - 페이지네이션 적용 (무한 조회 방지)
  - 응답 압축 (compression 미들웨어)
  - 커넥션 풀링 (Prisma 기본 제공)
  - 불필요한 미들웨어 제거

monitoring:
  - 구조화된 로깅 (pino/winston)
  - 헬스체크 엔드포인트 (/health)
  - 에러 추적 (Sentry 등)
```

---

## 9. 흔한 실수 종합

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| Controller에서 DB 직접 접근 | 레이어 위반, 테스트 불가 | Service/Repository를 통해 접근 |
| Service에서 `req`/`res` 사용 | HTTP 의존성, 재사용 불가 | DTO/매개변수로 필요한 데이터만 전달 |
| 에러 핸들러 인자 3개 | Express가 에러 핸들러로 인식 안 함 | 반드시 `(err, req, res, next)` 4개 인자 |
| `async` 핸들러에서 `next(error)` 누락 | 에러가 전역 핸들러에 도달 안 함 | try-catch로 감싸서 `next(error)` 호출 |
| 환경변수 미검증 | 런타임에 undefined 에러 | zod로 시작 시 검증 |
| `app.listen`을 app.ts에 직접 | 테스트 시 포트 충돌 | server.ts 분리, app만 export |
| 비밀번호 평문 저장 | 보안 위험 | bcrypt/argon2로 해싱 |
| JWT secret 짧은 문자열 | 브루트포스 취약 | 최소 32자 랜덤 문자열 |
| 모든 필드 select 없이 반환 | 민감 정보 노출 (password 등) | `select`로 필요한 필드만 반환 |
| 미들웨어 순서 무시 | CORS/보안/에러 처리 실패 | helmet → cors → body → rate-limit → routes → error |
