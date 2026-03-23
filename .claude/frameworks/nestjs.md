# NestJS + TypeScript Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-11

NestJS + TypeScript 백엔드 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`npx @nestjs/cli new project-name`으로 프로젝트를 초기화합니다.

---

## 0. NestJS vs Express 비교

| 항목 | NestJS | Express |
|------|--------|---------|
| 아키텍처 | Module-Controller-Service-Repository (DI 기반) | 미들웨어 체인 (수동 구조화) |
| TypeScript | 네이티브 지원 (데코레이터 기반) | `@types/express` 별도 설치 |
| 의존성 주입 | 내장 IoC 컨테이너 | 없음 (수동 또는 tsyringe 등) |
| 모듈 시스템 | 캡슐화된 모듈 (Angular 스타일) | 없음 (수동 분리) |
| 검증 | class-validator + class-transformer 내장 지원 | zod/joi 별도 설정 |
| 마이크로서비스 | 내장 지원 (TCP, Redis, NATS, gRPC 등) | 없음 (별도 구현) |
| OpenAPI | `@nestjs/swagger` 자동 생성 | swagger-jsdoc 별도 설정 |
| 학습 곡선 | 중~높음 (데코레이터, DI, 모듈 개념) | 낮음 |
| 권장 상황 | 대규모 엔터프라이즈, 마이크로서비스 | 소규모, 유연성 우선 |

> **이 가이드는 NestJS를 기준으로 작성**되었으며, Module-Controller-Service-Repository 아키텍처를 따릅니다.

---

## 1. 디렉토리 구조

### FATAL RULE: 레이어 혼합 방지 — Module-Controller-Service-Repository 패턴

> **NestJS는 Module-Controller-Service-Repository(MCSR) 패턴을 따릅니다.**
> **각 레이어의 책임을 절대 혼합하지 마세요.**
> **Controller에서 DB 직접 접근, Service에서 `@Req()`/`@Res()` 사용은 금지입니다.**
> **모든 Provider는 반드시 Module에 등록되어야 합니다.**

#### 절대 금지 사항

```typescript
// ⛔ Controller에서 DB 직접 접근 (Service를 통해야 함)
@Controller('users')
export class UserController {
  constructor(private readonly prisma: PrismaService) {}  // FATAL!
  @Get()
  findAll() { return this.prisma.user.findMany() }        // FATAL: 레이어 위반!
}

// ⛔ Service에서 HTTP 객체 직접 접근
@Injectable()
export class UserService {
  async getUsers(req: Request) { ... }  // FATAL: req 의존 금지!
}

// ⛔ Provider를 Module에 등록하지 않고 사용 → Nest can't resolve dependencies 에러!
// ⛔ 순환 의존성: UserModule → AuthModule → UserModule 순환 import!
```

#### 의사결정 트리

```
STEP 1: CWD(현재 작업 디렉토리)를 확인한다

  CWD에 .claude/ 폴더, package.json, 또는 기존 프로젝트 파일이 있는가?
  ├── YES → npx @nestjs/cli new . --skip-git (현재 폴더에서 초기화)
  └── NO  → npx @nestjs/cli new [project-name] (새 서브폴더 생성)

STEP 2: 구조 검증 (건너뛰기 금지)

  [PASS 조건 — 4개 모두 충족]
    ✅ package.json + tsconfig.json + nest-cli.json 존재
    ✅ src/main.ts + src/app.module.ts 존재

  [FATAL 조건 — 하나라도 해당하면 수정 필수]
    ❌ Controller에서 Repository/DB 직접 import → Service를 통해 접근!
    ❌ Service에서 @Req()/@Res() 사용 → DTO로 변환!
    ❌ Provider가 Module에 등록되지 않음 → providers 배열에 추가!
    ❌ 순환 의존성 존재 → forwardRef() 또는 구조 재설계!
```

### NestJS 기본 구조

```
project-root/
├── src/
│   ├── main.ts                        # 앱 부트스트랩 (NestFactory.create)
│   ├── app.module.ts                  # 루트 모듈
│   ├── config/                        # 환경 설정
│   │   ├── configuration.ts           # ConfigModule 설정 팩토리
│   │   └── validation.ts              # 환경변수 스키마 (Joi)
│   ├── common/                        # 공통 모듈 (횡단 관심사)
│   │   ├── decorators/                # @CurrentUser, @Roles
│   │   ├── filters/                   # AllExceptionsFilter
│   │   ├── guards/                    # JwtAuthGuard, RolesGuard
│   │   ├── interceptors/              # TransformInterceptor, LoggingInterceptor
│   │   ├── pipes/                     # 커스텀 Pipe
│   │   └── dto/                       # 공통 DTO (PaginationDto 등)
│   ├── modules/                       # 기능별 모듈 (도메인 중심)
│   │   ├── user/
│   │   │   ├── user.module.ts         # 모듈 정의
│   │   │   ├── user.controller.ts     # HTTP 요청/응답 처리
│   │   │   ├── user.service.ts        # 비즈니스 로직
│   │   │   ├── user.repository.ts     # DB 접근 (Prisma/TypeORM)
│   │   │   ├── dto/                   # CreateUserDto, UpdateUserDto
│   │   │   ├── entities/              # TypeORM 엔티티 (Prisma 시 생략)
│   │   │   └── __tests__/             # 단위/E2E 테스트
│   │   ├── auth/
│   │   │   ├── auth.module.ts
│   │   │   ├── auth.service.ts
│   │   │   └── strategies/            # jwt.strategy.ts, local.strategy.ts
│   │   └── post/ ...
│   └── database/                      # PrismaService + PrismaModule (@Global)
├── test/                              # E2E 테스트
├── prisma/                            # Prisma 스키마/마이그레이션
├── package.json
├── tsconfig.json
├── nest-cli.json
└── .env                               # 환경변수 (Git 제외)
```

### 레이어별 책임 분리 (Architecture Rules)

| 레이어 | 책임 | 허용 | 금지 |
|--------|------|------|------|
| `*.module.ts` | 모듈 정의, 의존성 구성 | `imports`, `controllers`, `providers`, `exports` | 비즈니스 로직, HTTP 처리 |
| `*.controller.ts` | HTTP 요청 라우팅, 응답 | `@Get/@Post`, `@Body/@Param/@Query`, DTO 검증 | DB 접근, `@Req()`/`@Res()` 직접 사용 |
| `*.service.ts` | 비즈니스 로직 | Repository 호출, 데이터 가공, 외부 서비스 | HTTP 객체 접근, 직접 DB 쿼리 |
| `*.repository.ts` | DB 접근 (CRUD) | Prisma/TypeORM 쿼리, 데이터 매핑 | 비즈니스 로직, HTTP 관련 코드 |
| `dto/` | 입력 검증, 응답 형식 | class-validator 데코레이터 | 비즈니스 로직, DB 접근 |
| `guards/` | 인증/인가 | `canActivate`, 토큰 검증, 역할 체크 | 비즈니스 로직 |
| `interceptors/` | 요청/응답 변환, 로깅 | 응답 매핑, 성능 로깅 | 비즈니스 로직 |
| `pipes/` | 데이터 변환/검증 | 타입 변환, 스키마 검증 | 비즈니스 로직, DB 접근 |
| `filters/` | 예외 처리 | 에러 포맷팅, 로깅 | 비즈니스 로직, 데이터 변경 |

#### 요청 라이프사이클 (Request Lifecycle)

```
1. Middleware           → Express/Fastify 미들웨어 (전역 → 모듈별)
2. Guard                → 인증/인가 체크 (전역 → 컨트롤러 → 라우트)
3. Interceptor (전처리) → 요청 변환, 로깅 시작
4. Pipe                 → 데이터 검증/변환 (전역 → 컨트롤러 → 라우트 → 파라미터)
5. Controller Method    → 핸들러 실행
6. Service              → 비즈니스 로직
7. Interceptor (후처리) → 응답 변환, 로깅 완료
8. Exception Filter     → 에러 발생 시 처리
```

> **Guard에서 거부되면 Pipe/Controller에 도달하지 않습니다.**
> **Pipe에서 실패하면 Controller에 도달하지 않습니다.**

#### 올바른 분리 예시

```typescript
// ✅ dto/create-user.dto.ts — 입력 검증
import { IsEmail, IsString, MinLength, MaxLength } from 'class-validator'
export class CreateUserDto {
  @IsEmail({}, { message: '유효한 이메일을 입력하세요' })
  email: string
  @IsString() @MinLength(8, { message: '비밀번호는 8자 이상' })
  password: string
  @IsString() @MinLength(1) @MaxLength(50)
  name: string
}

// ✅ dto/update-user.dto.ts — PartialType으로 부분 업데이트
import { PartialType } from '@nestjs/mapped-types'
export class UpdateUserDto extends PartialType(CreateUserDto) {}
```

```typescript
// ✅ user.repository.ts — DB 접근만
@Injectable()
export class UserRepository {
  constructor(private readonly prisma: PrismaService) {}
  async findAll(page: number, limit: number) {
    return this.prisma.user.findMany({
      skip: (page - 1) * limit, take: limit,
      select: { id: true, email: true, name: true, createdAt: true },
    })
  }
  async findById(id: string) { return this.prisma.user.findUnique({ where: { id } }) }
  async findByEmail(email: string) { return this.prisma.user.findUnique({ where: { email } }) }
  async create(data: { email: string; name: string; password: string }) {
    return this.prisma.user.create({ data, select: { id: true, email: true, name: true, createdAt: true } })
  }
}
```

```typescript
// ✅ user.service.ts — 비즈니스 로직
@Injectable()
export class UserService {
  constructor(private readonly userRepo: UserRepository) {}
  async getUsers(page: number, limit: number) { return this.userRepo.findAll(page, limit) }
  async getUserById(id: string) {
    const user = await this.userRepo.findById(id)
    if (!user) throw new NotFoundException('사용자를 찾을 수 없습니다')
    return user
  }
  async createUser(dto: CreateUserDto) {
    const existing = await this.userRepo.findByEmail(dto.email)
    if (existing) throw new ConflictException('이미 존재하는 이메일입니다')
    const hashedPassword = await bcrypt.hash(dto.password, 12)
    return this.userRepo.create({ ...dto, password: hashedPassword })
  }
}
```

```typescript
// ✅ user.controller.ts — HTTP 처리만
@Controller('users')
export class UserController {
  constructor(private readonly userService: UserService) {}
  @Get() @UseGuards(JwtAuthGuard)
  findAll(@Query('page') page = 1, @Query('limit') limit = 20) {
    return this.userService.getUsers(page, limit)
  }
  @Get(':id') @UseGuards(JwtAuthGuard)
  findOne(@Param('id', ParseUUIDPipe) id: string) { return this.userService.getUserById(id) }
  @Post() @HttpCode(HttpStatus.CREATED)
  create(@Body() dto: CreateUserDto) { return this.userService.createUser(dto) }
}
```

```typescript
// ✅ user.module.ts — 모듈 정의
@Module({
  controllers: [UserController],
  providers: [UserService, UserRepository],
  exports: [UserService],
})
export class UserModule {}
```

---

## 2. 모듈 시스템 & 의존성 주입

### 모듈 패턴

```typescript
// Global 모듈 (모든 모듈에서 import 없이 사용)
@Global()
@Module({ providers: [PrismaService], exports: [PrismaService] })
export class PrismaModule {}

// Dynamic 모듈 (설정값에 따라 다르게 구성)
@Module({})
export class MailModule {
  static forRoot(options: MailModuleOptions): DynamicModule {
    return {
      module: MailModule,
      providers: [{ provide: MAIL_OPTIONS, useValue: options }, MailService],
      exports: [MailService],
    }
  }
}

// 순환 의존성 해결 (최후 수단! 먼저 구조 재설계 고려)
@Module({ imports: [forwardRef(() => AuthModule)], exports: [UserService] })
export class UserModule {}
```

### Custom Provider & Scope

```typescript
// useValue: 상수/설정값
{ provide: 'API_KEY', useValue: process.env.API_KEY }
// useClass: 환경별 구현체 교체
{ provide: LoggerService, useClass: isProduction ? CloudLogger : ConsoleLogger }
// useFactory: 비동기 초기화
{ provide: 'DB', useFactory: async (config: ConfigService) => createConnection(config.get('db')), inject: [ConfigService] }

// Scope (기본: Singleton 권장)
@Injectable()                           // DEFAULT — 싱글톤 (권장)
@Injectable({ scope: Scope.REQUEST })   // REQUEST — 요청별 (성능 주의!)
@Injectable({ scope: Scope.TRANSIENT }) // TRANSIENT — 주입마다 새 인스턴스
// ⚠️ REQUEST/TRANSIENT는 성능 저하 원인. 반드시 필요한 경우에만!
```

---

## 3. Guards, Interceptors, Pipes, Filters

### Guard (인증/인가)

```typescript
// common/guards/jwt-auth.guard.ts
@Injectable()
export class JwtAuthGuard extends AuthGuard('jwt') {
  canActivate(context: ExecutionContext) { return super.canActivate(context) }
}

// common/guards/roles.guard.ts
@Injectable()
export class RolesGuard implements CanActivate {
  constructor(private readonly reflector: Reflector) {}
  canActivate(context: ExecutionContext): boolean {
    const requiredRoles = this.reflector.getAllAndOverride<string[]>(ROLES_KEY, [
      context.getHandler(), context.getClass(),
    ])
    if (!requiredRoles) return true
    const { user } = context.switchToHttp().getRequest()
    return requiredRoles.some((role) => user.roles?.includes(role))
  }
}

// common/decorators/roles.decorator.ts
export const ROLES_KEY = 'roles'
export const Roles = (...roles: string[]) => SetMetadata(ROLES_KEY, roles)
// 사용: @UseGuards(JwtAuthGuard, RolesGuard) @Roles('admin')
```

### Interceptor (요청/응답 변환)

```typescript
// 응답 포맷 통일
@Injectable()
export class TransformInterceptor<T> implements NestInterceptor<T, ResponseFormat<T>> {
  intercept(context: ExecutionContext, next: CallHandler): Observable<ResponseFormat<T>> {
    return next.handle().pipe(
      map((data) => ({
        data, statusCode: context.switchToHttp().getResponse().statusCode,
        timestamp: new Date().toISOString(),
      })),
    )
  }
}

// 요청 로깅
@Injectable()
export class LoggingInterceptor implements NestInterceptor {
  private readonly logger = new Logger(LoggingInterceptor.name)
  intercept(context: ExecutionContext, next: CallHandler): Observable<any> {
    const { method, url } = context.switchToHttp().getRequest()
    const now = Date.now()
    return next.handle().pipe(tap(() => this.logger.log(`${method} ${url} ${Date.now() - now}ms`)))
  }
}
```

### Pipe (검증) & Exception Filter (에러 처리)

```typescript
// ✅ 전역 ValidationPipe (main.ts)
app.useGlobalPipes(new ValidationPipe({
  whitelist: true,               // DTO 미정의 속성 제거
  forbidNonWhitelisted: true,    // 미정의 속성 전송 시 에러
  transform: true,               // 자동 타입 변환
  transformOptions: { enableImplicitConversion: true },
}))

// ✅ AllExceptionsFilter
@Catch()
export class AllExceptionsFilter implements ExceptionFilter {
  private readonly logger = new Logger(AllExceptionsFilter.name)
  catch(exception: unknown, host: ArgumentsHost) {
    const ctx = host.switchToHttp()
    const response = ctx.getResponse<Response>()
    const request = ctx.getRequest<Request>()
    const status = exception instanceof HttpException ? exception.getStatus() : 500
    const message = exception instanceof HttpException ? exception.getResponse() : '서버 내부 오류'
    if (!(exception instanceof HttpException)) this.logger.error('Unhandled:', exception)
    response.status(status).json({
      error: { statusCode: status, message: typeof message === 'string' ? message : (message as any).message,
        timestamp: new Date().toISOString(), path: request.url },
    })
  }
}
```

### 전역 등록 (Module 방식 권장 — DI 가능)

```typescript
// Module에서 전역 등록 (권장!)
@Module({
  providers: [
    { provide: APP_GUARD, useClass: JwtAuthGuard },
    { provide: APP_INTERCEPTOR, useClass: TransformInterceptor },
    { provide: APP_PIPE, useValue: new ValidationPipe({ whitelist: true }) },
    { provide: APP_FILTER, useClass: AllExceptionsFilter },
  ],
})
export class AppModule {}
```

---

## 4. 인증 (Passport + JWT)

```typescript
// strategies/jwt.strategy.ts
@Injectable()
export class JwtStrategy extends PassportStrategy(Strategy) {
  constructor(configService: ConfigService, private readonly userService: UserService) {
    super({
      jwtFromRequest: ExtractJwt.fromAuthHeaderAsBearerToken(),
      ignoreExpiration: false,
      secretOrKey: configService.get<string>('JWT_SECRET'),
    })
  }
  async validate(payload: { sub: string; email: string }) {
    const user = await this.userService.getUserById(payload.sub)
    if (!user) throw new UnauthorizedException('유효하지 않은 토큰입니다')
    return user  // req.user에 할당됨
  }
}

// strategies/local.strategy.ts
@Injectable()
export class LocalStrategy extends PassportStrategy(Strategy) {
  constructor(private readonly authService: AuthService) { super({ usernameField: 'email' }) }
  async validate(email: string, password: string) {
    const user = await this.authService.validateUser(email, password)
    if (!user) throw new UnauthorizedException('이메일 또는 비밀번호가 올바르지 않습니다')
    return user
  }
}
```

```typescript
// auth.service.ts
@Injectable()
export class AuthService {
  constructor(private readonly userService: UserService, private readonly jwtService: JwtService) {}
  async validateUser(email: string, password: string) {
    const user = await this.userService.findByEmail(email)
    if (user && await bcrypt.compare(password, user.password)) {
      const { password: _, ...result } = user
      return result
    }
    return null
  }
  async login(user: { id: string; email: string }) {
    const payload = { sub: user.id, email: user.email }
    return {
      access_token: this.jwtService.sign(payload),
      refresh_token: this.jwtService.sign(payload, { expiresIn: '7d' }),
    }
  }
}

// auth.module.ts
@Module({
  imports: [
    UserModule, PassportModule,
    JwtModule.registerAsync({
      inject: [ConfigService],
      useFactory: (config: ConfigService) => ({
        secret: config.get<string>('JWT_SECRET'), signOptions: { expiresIn: '30m' },
      }),
    }),
  ],
  controllers: [AuthController],
  providers: [AuthService, JwtStrategy, LocalStrategy],
  exports: [AuthService],
})
export class AuthModule {}
```

### 커스텀 데코레이터 & CASL 인가

```typescript
// @CurrentUser 데코레이터
export const CurrentUser = createParamDecorator(
  (data: string | undefined, ctx: ExecutionContext) => {
    const user = ctx.switchToHttp().getRequest().user
    return data ? user?.[data] : user
  },
)
// 사용: @Get('profile') @UseGuards(JwtAuthGuard) getProfile(@CurrentUser() user: User) { ... }

// CASL 기반 인가 (복잡한 권한 관리)
@Injectable()
export class CaslAbilityFactory {
  createForUser(user: { id: string; roles: string[] }): AppAbility {
    const { can, cannot, build } = new AbilityBuilder<AppAbility>(createMongoAbility)
    if (user.roles.includes('admin')) { can('manage', 'all') }
    else {
      can('read', 'Post'); can('create', 'Post')
      can('update', 'Post', { authorId: user.id })  // 자신의 글만 수정
      can('delete', 'Post', { authorId: user.id })
      can('read', 'User'); can('update', 'User', { id: user.id })
      cannot('delete', 'User')
    }
    return build()
  }
}
```

---

## 5. Database 통합

### Prisma

```typescript
// database/prisma.service.ts
@Injectable()
export class PrismaService extends PrismaClient implements OnModuleInit, OnModuleDestroy {
  async onModuleInit() { await this.$connect() }
  async onModuleDestroy() { await this.$disconnect() }
}

// database/prisma.module.ts
@Global()
@Module({ providers: [PrismaService], exports: [PrismaService] })
export class PrismaModule {}
```

### TypeORM

```typescript
// 엔티티 정의
@Entity('users')
export class User {
  @PrimaryGeneratedColumn('uuid') id: string
  @Column({ unique: true }) email: string
  @Column() password: string
  @Column() name: string
  @CreateDateColumn() createdAt: Date
  @UpdateDateColumn() updatedAt: Date
}

// 모듈 설정
TypeOrmModule.forRootAsync({
  inject: [ConfigService],
  useFactory: (config: ConfigService) => ({
    type: 'postgres', host: config.get('DB_HOST'), port: config.get<number>('DB_PORT'),
    username: config.get('DB_USERNAME'), password: config.get('DB_PASSWORD'),
    database: config.get('DB_NAME'), entities: [__dirname + '/../**/*.entity{.ts,.js}'],
    synchronize: false,  // 프로덕션에서 절대 true 금지!
  }),
})
// 모듈에서 엔티티 등록: TypeOrmModule.forFeature([User])
```

### 환경 설정 (ConfigModule)

```typescript
// config/validation.ts
export const validationSchema = Joi.object({
  NODE_ENV: Joi.string().valid('development', 'production', 'test').default('development'),
  PORT: Joi.number().default(3000),
  DATABASE_URL: Joi.string().required(),
  JWT_SECRET: Joi.string().min(32).required(),
})

// app.module.ts
@Module({
  imports: [
    ConfigModule.forRoot({ isGlobal: true, load: [configuration], validationSchema }),
    PrismaModule, UserModule, AuthModule,
  ],
})
export class AppModule {}
```

---

## 6. main.ts 부트스트랩

```typescript
import { NestFactory } from '@nestjs/core'
import { ValidationPipe, Logger } from '@nestjs/common'
import helmet from 'helmet'

async function bootstrap() {
  const app = await NestFactory.create(AppModule)
  const configService = app.get(ConfigService)

  app.use(helmet())
  app.enableCors({ origin: configService.get<string>('ALLOWED_ORIGINS')?.split(','), credentials: true })
  app.setGlobalPrefix('api/v1')
  app.useGlobalPipes(new ValidationPipe({
    whitelist: true, forbidNonWhitelisted: true, transform: true,
    transformOptions: { enableImplicitConversion: true },
  }))
  app.useGlobalFilters(new AllExceptionsFilter())
  app.useGlobalInterceptors(new LoggingInterceptor(), new TransformInterceptor())

  // Swagger (개발 환경만)
  if (configService.get('NODE_ENV') !== 'production') {
    const { DocumentBuilder, SwaggerModule } = await import('@nestjs/swagger')
    const config = new DocumentBuilder().setTitle('API').setVersion('1.0').addBearerAuth().build()
    SwaggerModule.setup('docs', app, SwaggerModule.createDocument(app, config))
  }

  const port = configService.get<number>('PORT') || 3000
  await app.listen(port)
  new Logger('Bootstrap').log(`Application listening on port ${port}`)
}
bootstrap()
```

---

## 7. 테스팅

### Jest 단위 테스트

```typescript
// user.service.spec.ts
describe('UserService', () => {
  let service: UserService
  let repository: jest.Mocked<UserRepository>

  beforeEach(async () => {
    const mockRepo = { findAll: jest.fn(), findById: jest.fn(), findByEmail: jest.fn(), create: jest.fn() }
    const module = await Test.createTestingModule({
      providers: [UserService, { provide: UserRepository, useValue: mockRepo }],
    }).compile()
    service = module.get(UserService)
    repository = module.get(UserRepository)
  })

  it('존재하는 사용자를 반환한다', async () => {
    const mockUser = { id: '1', email: 'test@test.com', name: '테스트' }
    repository.findById.mockResolvedValue(mockUser as any)
    expect(await service.getUserById('1')).toEqual(mockUser)
  })

  it('사용자가 없으면 NotFoundException', async () => {
    repository.findById.mockResolvedValue(null)
    await expect(service.getUserById('999')).rejects.toThrow(NotFoundException)
  })

  it('이미 존재하는 이메일이면 ConflictException', async () => {
    repository.findByEmail.mockResolvedValue({ id: '1' } as any)
    await expect(service.createUser({ email: 'a@b.com', password: '12345678', name: 'x' }))
      .rejects.toThrow(ConflictException)
  })
})
```

### E2E 테스트 (supertest)

```typescript
// test/app.e2e-spec.ts
describe('UserController (e2e)', () => {
  let app: INestApplication
  beforeAll(async () => {
    const module = await Test.createTestingModule({ imports: [AppModule] }).compile()
    app = module.createNestApplication()
    app.useGlobalPipes(new ValidationPipe({ whitelist: true, transform: true }))
    await app.init()
  })
  afterAll(async () => { await app.close() })

  it('POST /users — 유효한 입력이면 201', () =>
    request(app.getHttpServer()).post('/users')
      .send({ email: 'test@test.com', password: 'password123', name: '테스트' }).expect(201))

  it('POST /users — 유효하지 않은 이메일이면 400', () =>
    request(app.getHttpServer()).post('/users')
      .send({ email: 'invalid', password: 'password123', name: '테스트' }).expect(400))

  it('GET /users — 인증 없이 401', () =>
    request(app.getHttpServer()).get('/users').expect(401))
})

// 테스트 모듈 오버라이드 (DB/외부 의존성 모킹)
const module = await Test.createTestingModule({ imports: [AppModule] })
  .overrideProvider(PrismaService).useValue(mockPrismaService)
  .overrideGuard(JwtAuthGuard).useValue({ canActivate: () => true })
  .compile()
```

---

## 8. 마이크로서비스 패턴 (선택)

```typescript
// 하이브리드 앱 (HTTP + 마이크로서비스 동시 운영)
async function bootstrap() {
  const app = await NestFactory.create(AppModule)
  app.connectMicroservice<MicroserviceOptions>({
    transport: Transport.REDIS, options: { host: 'localhost', port: 6379 },
  })
  await app.startAllMicroservices()
  await app.listen(3000)
}

// 요청-응답 패턴
@MessagePattern({ cmd: 'get_user' })
async getUser(data: { id: string }) { return this.userService.getUserById(data.id) }
// 클라이언트: this.client.send({ cmd: 'get_user' }, { id }).toPromise()

// 이벤트 패턴 (Fire-and-forget)
@EventPattern('user_created')
async handleUserCreated(data: { userId: string; email: string }) { /* 부수효과 */ }
// 발행: this.client.emit('user_created', { userId: user.id, email: user.email })
```

---

## 9. TypeScript 설정

```json
{
  "compilerOptions": {
    "module": "commonjs",
    "emitDecoratorMetadata": true,
    "experimentalDecorators": true,
    "target": "ES2021",
    "outDir": "./dist",
    "baseUrl": "./",
    "strictNullChecks": true,
    "noImplicitAny": true,
    "skipLibCheck": true,
    "paths": { "@/*": ["src/*"] }
  }
}
```

**필수 설정**: `emitDecoratorMetadata` (NestJS DI), `experimentalDecorators` (데코레이터), `strictNullChecks` (null 안전성)

---

## 10. 성능 & 보안 체크리스트

```yaml
security:
  - helmet() 적용 (보안 헤더)
  - CORS 허용 출처 제한
  - ValidationPipe whitelist + forbidNonWhitelisted (mass assignment 방지)
  - JWT secret 최소 32자, 만료 시간 설정
  - 비밀번호 해싱 (bcrypt, rounds >= 12)
  - Swagger 프로덕션 비활성화
  - rate-limit (@nestjs/throttler)

performance:
  - Scope.DEFAULT 사용 (REQUEST/TRANSIENT 최소화)
  - select로 필요한 필드만 조회
  - 페이지네이션 적용, DB 인덱스 설정
  - 캐싱 (@nestjs/cache-manager)
  - Fastify 어댑터 고려 (고성능)

monitoring:
  - 헬스체크 (@nestjs/terminus)
  - 에러 추적 (Sentry + @sentry/nestjs)
```

---

## 11. 흔한 실수 종합

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| Controller에서 DB 직접 접근 | 레이어 위반, 테스트 불가 | Service/Repository를 통해 접근 |
| Service에서 `@Req()`/`@Res()` 사용 | HTTP 의존성, 재사용 불가 | DTO/매개변수로 전달 |
| `@Res()`로 응답 직접 제어 | Interceptor/Filter 우회됨 | NestJS 기본 응답 파이프라인 사용 |
| Provider Module 미등록 | `Nest can't resolve dependencies` | `providers` 배열에 등록 필수 |
| 순환 의존성 | 런타임 에러 | 구조 재설계 또는 `forwardRef()` |
| `synchronize: true` 프로덕션 | 데이터 손실 위험 | `false` 고정, 마이그레이션 사용 |
| ValidationPipe 미적용 | 입력 검증 없이 DB 접근 | 전역 ValidationPipe 설정 |
| `whitelist` 미설정 | Mass Assignment 취약점 | `whitelist` + `forbidNonWhitelisted` |
| REQUEST scope 남발 | 성능 심각 저하 | 기본 Singleton, 필요 시만 REQUEST |
| Guard 순서 무시 | 인증 전 인가 실행 | `@UseGuards(JwtAuthGuard, RolesGuard)` |
| Swagger 프로덕션 노출 | API 구조 노출 | 환경별 조건부 활성화 |
| 테스트에서 실제 DB 사용 | 느리고 불안정 | `overrideProvider`로 모킹 |
