# NestJS + TypeScript Quick Reference (Compact)

**Framework**: NestJS + TypeScript | **패턴**: Module-Controller-Service-Repository | **Core**: NestJS + Prisma/TypeORM + class-validator
**TypeScript**: 필수 (`emitDecoratorMetadata`, `experimentalDecorators`, `strictNullChecks`, `@/*` 경로 별칭)

## 디렉토리 구조

> **FATAL RULE**: Module-Controller-Service-Repository 레이어 분리 필수.
> **절대 금지**: Controller에서 DB 직접 접근, Service에서 `@Req()`/`@Res()` 사용, Provider Module 미등록
> **검증 필수**: `package.json` + `tsconfig.json` + `nest-cli.json` + `src/main.ts` + `src/app.module.ts` 존재

```
src/
├── main.ts                     # 부트스트랩 (NestFactory.create, 전역 설정)
├── app.module.ts               # 루트 모듈
├── config/                     # 환경 설정 (ConfigModule + Joi 검증)
├── common/                     # 횡단 관심사
│   ├── decorators/             # @CurrentUser, @Roles 커스텀 데코레이터
│   ├── guards/                 # JwtAuthGuard, RolesGuard (인증/인가)
│   ├── interceptors/           # TransformInterceptor, LoggingInterceptor
│   ├── pipes/                  # 커스텀 Pipe (검증/변환)
│   ├── filters/                # AllExceptionsFilter (에러 처리)
│   └── dto/                    # 공통 DTO (PaginationDto 등)
├── modules/                    # 기능별 모듈 (도메인 중심)
│   └── user/
│       ├── user.module.ts      # 모듈 (imports, controllers, providers, exports)
│       ├── user.controller.ts  # HTTP 라우팅 / 응답
│       ├── user.service.ts     # 비즈니스 로직
│       ├── user.repository.ts  # DB 접근 (Prisma/TypeORM)
│       ├── dto/                # CreateUserDto, UpdateUserDto (class-validator)
│       └── __tests__/          # 단위/E2E 테스트
└── database/                   # PrismaService + PrismaModule (@Global)
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `*.module.ts` | 모듈 정의, 의존성 구성 | 비즈니스 로직, HTTP 처리 |
| `*.controller.ts` | HTTP 라우팅, `@Body/@Param/@Query` 파싱 | DB 접근, `@Req()`/`@Res()` |
| `*.service.ts` | 비즈니스 로직, Repository 호출 | HTTP 객체, 직접 DB 쿼리 |
| `*.repository.ts` | DB CRUD (Prisma/TypeORM) | 비즈니스 로직, HTTP 코드 |
| `dto/` | class-validator 검증 | 로직, DB 접근 |
| `guards/` | 인증/인가 (canActivate) | 비즈니스 로직 |
| `interceptors/` | 요청/응답 변환, 로깅 | 비즈니스 로직 |
| `filters/` | 예외 처리, 에러 포맷팅 | 데이터 변경 |

**데이터 흐름**: `Request` -> `Middleware` -> `Guard` -> `Interceptor(전)` -> `Pipe` -> `Controller` -> `Service` -> `Repository` -> `DB`

> **코드 생성 트리거**: Module/Controller/Service/Repository 코드를 **작성**할 때는
> 반드시 `frameworks/nestjs.md`의 **코드 예시와 안티패턴**을 로드하여 참조할 것.
> compact 테이블만으로 코드 생성 금지.

## 요청 라이프사이클 순서

`Middleware` -> `Guard` -> `Interceptor(전)` -> `Pipe` -> `Controller` -> `Service` -> `Interceptor(후)` -> `ExceptionFilter`
> Guard 거부 시 Pipe/Controller 미도달. Pipe 실패 시 Controller 미도달.

## 핵심 패턴

```typescript
// 전역 ValidationPipe (main.ts)
app.useGlobalPipes(new ValidationPipe({ whitelist: true, forbidNonWhitelisted: true, transform: true }))
// DTO (class-validator)
export class CreateUserDto { @IsEmail() email: string; @IsString() @MinLength(8) password: string }
// 모듈 정의
@Module({ controllers: [UserController], providers: [UserService, UserRepository], exports: [UserService] })
// DI — constructor injection
constructor(private readonly userService: UserService) {}
// Guard + Roles
@UseGuards(JwtAuthGuard, RolesGuard) @Roles('admin')
// Prisma Global Module
@Global() @Module({ providers: [PrismaService], exports: [PrismaService] })
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| Controller에서 DB 직접 접근 | Service/Repository를 통해 접근 |
| Service에서 `@Req()`/`@Res()` 사용 | Controller에서 파싱 후 매개변수 전달 |
| `@Res()` 응답 직접 제어 | Interceptor/Filter 우회됨 — 기본 파이프라인 사용 |
| Provider Module 미등록 | `providers` 배열에 등록 (Nest can't resolve 에러) |
| 순환 의존성 | 구조 재설계 또는 `forwardRef()` |
| `synchronize: true` 프로덕션 | `false` 고정, 마이그레이션 사용 |
| ValidationPipe 미적용 | 전역 `ValidationPipe` + `whitelist` |
| REQUEST scope 남발 | 기본 Singleton 사용 (성능 저하 방지) |
| Guard 순서 무시 | `@UseGuards(JwtAuthGuard, RolesGuard)` 인증 먼저 |
| Swagger 프로덕션 노출 | 환경별 조건부 활성화 |

> **전체 가이드**: `frameworks/nestjs.md` 참조
