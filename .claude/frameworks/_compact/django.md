# Django + DRF Quick Reference (Compact)

**Framework**: Django + Django REST Framework | **패턴**: MTV + Serializers | **Core**: Django ORM + DRF ViewSets
**Python**: 타입 힌트 권장 (`from __future__ import annotations`)

## 디렉토리 구조

> **FATAL RULE**: 프로젝트(`config/`)와 앱(`apps/`) 구조 분리 필수. 비즈니스 로직은 앱 단위로.
> **절대 금지**: `startproject name` (이중 중첩!), Fat View, SECRET_KEY 하드코딩, N+1 쿼리 무시, Serializer `fields = '__all__'`
> **검증 필수**: `manage.py` + `config/settings.py` + `config/urls.py` 존재 + `config/config/` 없어야 정상
> **초기화**: `django-admin startproject config .` (마침표 필수!)

```
config/                         # 프로젝트 설정 (startproject config .)
├── settings/                   # 환경별 분리 (base, development, production, test)
├── urls.py                     # 루트 URL
├── wsgi.py / asgi.py           # 서버 엔트리
apps/                           # 앱 모듈 디렉토리
├── users/                      # 기능별 앱
│   ├── models.py               # DB 모델 + 커스텀 Manager
│   ├── serializers.py          # DRF 직렬화/검증
│   ├── views.py                # DRF ViewSet/APIView (얇게)
│   ├── urls.py                 # 앱 URL 패턴 + Router
│   ├── permissions.py          # 커스텀 권한
│   ├── services.py             # 복잡한 비즈니스 로직 (선택)
│   └── tests/                  # pytest-django 테스트
└── common/                     # 추상 모델, 유틸, 페이지네이션, 예외 핸들러
```

## 레이어별 책임 (Architecture Rules)

| 레이어 | 책임 | 금지 |
|--------|------|------|
| `models.py` | DB 스키마, 모델 메서드, Manager | 뷰 로직, HTTP 처리 |
| `serializers.py` | 직렬화/역직렬화, 입력 검증 | 복잡한 비즈니스 로직, 외부 API |
| `views.py` | HTTP 처리, 권한 체크 (얇게) | DB 대량 조작, 복잡한 로직 |
| `urls.py` | URL -> 뷰 매핑, Router | 로직, 데이터 처리 |
| `services.py` | 복잡한 비즈니스 로직, 트랜잭션 | HTTP 처리, Serializer 호출 |
| `permissions.py` | 접근 제어 (`has_permission`) | 비즈니스 로직 |

**데이터 흐름**: `Request` -> `urls.py` -> `middleware` -> `views.py` -> `serializers.py` -> `models.py` -> `DB`

> **코드 생성 트리거**: Model/Serializer/ViewSet 코드를 **작성**할 때는
> 반드시 `frameworks/django.md`의 **코드 예시와 안티패턴**을 로드하여 참조할 것.
> compact 테이블만으로 코드 생성 금지.

## 핵심 패턴

```python
# ViewSet (DRF Router 자동 URL 생성)
class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.select_related('author').all()  # N+1 방지!
    serializer_class = PostSerializer
    permission_classes = [IsAuthenticatedOrReadOnly, IsAuthorOrReadOnly]
    filterset_class = PostFilter

# Serializer (검증 + 직렬화)
class PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ['id', 'title', 'content', 'author', 'created_at']  # __all__ 금지!
        read_only_fields = ['author', 'created_at']

# 추상 모델 (TimeStamped + UUID)
class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        abstract = True

# JWT 인증 (Simple JWT)
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework_simplejwt.authentication.JWTAuthentication'],
}
```

## 흔한 실수

| 실수 | 해결 |
|------|------|
| `startproject name` (이중 중첩) | `startproject config .` (마침표 필수) |
| Fat View (뷰에 모든 로직) | 모델 메서드 + 서비스 레이어 분리 |
| N+1 쿼리 무시 | `select_related`/`prefetch_related` 필수 |
| SECRET_KEY 하드코딩 | `django-environ`으로 환경변수 로드 |
| Serializer `fields = '__all__'` | 필요한 필드만 명시적 나열 |
| `DEBUG = True` 프로덕션 | 환경별 settings 분리 |
| raw SQL 직접 사용 | Django ORM 사용 (SQL Injection 방지) |
| 마이그레이션 미커밋 | `makemigrations` 결과 Git에 포함 |
| `.all()` 무제한 반환 | 페이지네이션 + 필터링 필수 |

> **전체 가이드**: `frameworks/django.md` 참조
