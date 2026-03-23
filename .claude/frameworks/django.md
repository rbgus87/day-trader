# Django + Django REST Framework Development Guide

> **version**: 1.0.0 | **updated**: 2026-02-11

Django + DRF(Django REST Framework) 프로젝트를 위한 베스트 프랙티스 및 패턴 가이드. 모든 역할이 참조합니다.
`django-admin startproject config .`으로 프로젝트를 초기화합니다.

---

## 0. Django vs FastAPI 비교

| 항목 | Django + DRF | FastAPI |
|------|-------------|---------|
| 아키텍처 | MTV (Model-Template-View) + Serializers | ASGI 마이크로프레임워크 |
| ORM | Django ORM (내장, 마이그레이션 포함) | SQLAlchemy / Tortoise ORM (별도) |
| 인증 | Django Auth (내장, 세션/JWT 등) | 별도 구현 (OAuth2 유틸 내장) |
| Admin | Django Admin (자동 생성) | 없음 (별도 구현) |
| API 문서 | DRF Browsable API + drf-spectacular | Swagger UI 자동 생성 (내장) |
| 성능 | 동기 기반 (ASGI 지원 점진 확대) | 비동기 네이티브 (고성능) |
| 타입 시스템 | Serializer 기반 검증 | Pydantic 네이티브 타입 검증 |
| 생태계 | 매우 넓음 (배터리 포함) | 빠르게 성장 중 |
| 학습 곡선 | 중간 (규약이 많지만 체계적) | 낮음~중간 |
| 권장 상황 | 풀스택, Admin 필요, 복잡한 비즈니스 | 마이크로서비스, 고성능 API |

> **이 가이드는 Django + DRF를 기준으로 작성**되었습니다.

---

## 1. 디렉토리 구조

### FATAL RULE: 프로젝트/앱 구조 혼동 방지

> **Django는 "프로젝트"와 "앱"의 두 계층 구조를 가집니다.**
> **`startproject`는 설정용 프로젝트를, `startapp`은 기능 단위 앱을 생성합니다.**
> **모든 비즈니스 로직은 앱 단위로 분리하고, 프로젝트(config/)에는 설정만 둡니다.**

#### 절대 금지 사항

```bash
# ⛔ 프로젝트 이름으로 중첩 디렉토리 생성
django-admin startproject myproject    # → myproject/myproject/ 이중 중첩!

# ⛔ views.py에서 직접 SQL 쿼리
cursor.execute("SELECT * FROM users WHERE id = %s" % user_id)  # SQL Injection!

# ⛔ views.py에 복잡한 비즈니스 로직 작성 (Fat View)
# ⛔ serializer에서 DB 쿼리 N+1 문제 무시
# ⛔ settings.py에 SECRET_KEY 하드코딩
```

#### 의사결정 트리

```
STEP 1: CWD(현재 작업 디렉토리)를 확인한다

  CWD에 .claude/ 폴더, manage.py, 또는 기존 프로젝트 파일이 있는가?
  ├── YES → CWD가 이미 프로젝트 루트
  │         → django-admin startproject config .
  │         (현재 폴더를 그대로 프로젝트 루트로 사용, config/ 설정 디렉토리 생성)
  │
  └── NO  → CWD는 프로젝트의 상위 폴더
            → mkdir [project-name] && cd [project-name]
            → django-admin startproject config .
            (새 서브폴더가 프로젝트 루트가 됨)

STEP 2: 구조 검증 (건너뛰기 금지)

  [PASS 조건 — 3개 모두 충족]
    ✅ manage.py 가 CWD에 존재
    ✅ config/settings.py (또는 config/settings/) 가 존재
    ✅ config/urls.py 가 존재

  [FATAL 조건 — 하나라도 해당하면 수정 필수]
    ❌ config/config/ 이중 중첩 → startproject 인자 오류!
    ❌ SECRET_KEY가 settings.py에 하드코딩 → 환경변수로 이동!
    ❌ DEBUG = True 가 프로덕션에서 활성화 → 환경별 분리!
```

#### 검증 명령어

```bash
# 프로젝트 루트에서 확인
ls manage.py           # ✅ 존재해야 함
ls config/settings.py  # ✅ 존재해야 함 (또는 config/settings/)
ls config/urls.py      # ✅ 존재해야 함

# 이중 중첩 체크
ls config/config/      # ❌ 존재하면 안 됨
```

### Django 기본 구조

```
project-root/                       # ← manage.py가 여기에 위치
├── config/                         # 프로젝트 설정 (startproject config .)
│   ├── __init__.py
│   ├── settings/                   # 환경별 설정 분리 (권장)
│   │   ├── __init__.py
│   │   ├── base.py                 # 공통 설정
│   │   ├── development.py          # 개발 환경
│   │   ├── production.py           # 프로덕션
│   │   └── test.py                 # 테스트 환경
│   ├── urls.py                     # 루트 URL 설정
│   ├── wsgi.py                     # WSGI 엔트리
│   └── asgi.py                     # ASGI 엔트리
├── apps/                           # 앱 모듈 디렉토리
│   ├── users/                      # 사용자 앱
│   │   ├── __init__.py
│   │   ├── admin.py                # Django Admin 설정
│   │   ├── apps.py                 # 앱 설정
│   │   ├── models.py               # DB 모델
│   │   ├── serializers.py          # DRF 직렬화/검증
│   │   ├── views.py                # DRF 뷰 (API 엔드포인트)
│   │   ├── urls.py                 # 앱별 URL 패턴
│   │   ├── permissions.py          # 커스텀 권한
│   │   ├── filters.py              # 필터링 로직 (django-filter)
│   │   ├── services.py             # 비즈니스 로직 (선택)
│   │   ├── signals.py              # 시그널 핸들러
│   │   ├── managers.py             # 커스텀 매니저/QuerySet
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   ├── test_models.py
│   │   │   ├── test_views.py
│   │   │   └── test_serializers.py
│   │   └── migrations/             # DB 마이그레이션 (자동 생성)
│   ├── posts/
│   │   ├── ...
│   └── common/                     # 공통 앱 (추상 모델, 유틸, 미들웨어)
│       ├── models.py               # 추상 모델 (TimeStampedModel 등)
│       ├── permissions.py          # 공통 권한
│       ├── pagination.py           # 커스텀 페이지네이션
│       ├── exceptions.py           # 커스텀 예외 핸들러
│       └── utils.py                # 유틸리티 함수
├── manage.py                       # Django CLI
├── requirements/                   # 의존성 분리
│   ├── base.txt
│   ├── development.txt
│   └── production.txt
├── .env                            # 환경변수 (Git 제외)
└── pytest.ini                      # pytest 설정
```

### 레이어별 책임 분리 (Architecture Rules)

각 레이어는 명확한 책임 범위를 가집니다. **경계를 넘는 코드는 금지합니다.**

| 레이어 | 책임 | 허용 | 금지 |
|--------|------|------|------|
| `models.py` | DB 스키마 정의, 모델 메서드 | 필드 정의, `__str__`, 단순 속성/프로퍼티, 커스텀 Manager | 뷰 로직, HTTP 처리, 외부 API 호출 |
| `serializers.py` | 직렬화/역직렬화, 입력 검증 | 필드 검증, `validate_*`, `create`, `update` | 복잡한 비즈니스 로직, 직접 뷰 호출 |
| `views.py` | HTTP 요청/응답 처리 | ViewSet/APIView, 권한 체크, Serializer 호출 | 복잡한 비즈니스 로직, 직접 SQL, 모델 대량 조작 |
| `urls.py` | URL 패턴 → 뷰 매핑 | `path()`, Router 등록 | 로직, 데이터 처리 |
| `services.py` | 복잡한 비즈니스 로직 (선택) | 여러 모델 조작, 외부 API, 트랜잭션 | HTTP 처리, Serializer 호출 |
| `permissions.py` | 접근 제어 | `has_permission`, `has_object_permission` | 비즈니스 로직, DB 쿼리 (최소화) |
| `admin.py` | Django Admin 커스터마이징 | `ModelAdmin`, 인라인, 액션 | API 로직 |
| `managers.py` | 커스텀 QuerySet 메서드 | 재사용 가능한 쿼리 체인 | 뷰/시리얼라이저 로직 |

#### 데이터 흐름 (단방향)

```
Request  →  urls.py  →  middleware  →  views.py  →  serializers.py  →  models.py  →  DB
                                          │              │
                                     (권한 체크)     (검증/변환)
                                     (응답 반환)
Response ←  Exception Handler  ←  views.py  ←  serializers.py  ←  models.py  ←  DB
```

#### Thin View / Fat Model 원칙

```python
# ✅ models.py - 모델 메서드와 매니저
from django.db import models

class PostManager(models.Manager):
    def published(self):
        return self.filter(status='published')

    def by_author(self, user):
        return self.filter(author=user)

class Post(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    author = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='posts')
    status = models.CharField(max_length=20, choices=[
        ('draft', '초안'),
        ('published', '공개'),
    ], default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PostManager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['author', 'status']),
        ]

    def __str__(self):
        return self.title

    @property
    def is_published(self):
        return self.status == 'published'

    def publish(self):
        self.status = 'published'
        self.save(update_fields=['status', 'updated_at'])
```

```python
# ✅ serializers.py - 직렬화 + 검증
from rest_framework import serializers
from .models import Post

class PostSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source='author.username', read_only=True)

    class Meta:
        model = Post
        fields = ['id', 'title', 'content', 'author', 'author_name', 'status', 'created_at']
        read_only_fields = ['author', 'created_at']

    def validate_title(self, value):
        if len(value) < 5:
            raise serializers.ValidationError('제목은 5자 이상이어야 합니다')
        return value

    def create(self, validated_data):
        validated_data['author'] = self.context['request'].user
        return super().create(validated_data)
```

```python
# ✅ views.py - 얇은 뷰 (HTTP 처리만)
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Post
from .serializers import PostSerializer
from .permissions import IsAuthorOrReadOnly
from .filters import PostFilter

class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.select_related('author').all()
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsAuthorOrReadOnly]
    filterset_class = PostFilter

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == 'list':
            return qs.filter(status='published')
        return qs

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        post = self.get_object()
        post.publish()
        return Response(PostSerializer(post).data)
```

```python
# ✅ urls.py - URL 패턴 정의
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PostViewSet

router = DefaultRouter()
router.register('posts', PostViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
```

```python
# ✅ config/urls.py - 루트 URL
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('apps.posts.urls')),
    path('api/v1/', include('apps.users.urls')),
]
```

#### 안티패턴: 경계 위반

```python
# ❌ Fat View - 뷰에 비즈니스 로직 직접 작성
class PostViewSet(viewsets.ModelViewSet):
    def create(self, request):
        # 이 모든 로직이 뷰에 있으면 안 됨!
        if Post.objects.filter(author=request.user).count() > 100:
            return Response({'error': '게시글 제한 초과'}, status=400)
        post = Post.objects.create(**request.data, author=request.user)
        send_notification(post.author, '새 게시글 작성')  # 서비스로 이동!
        update_stats(post.author)                         # 서비스로 이동!
        return Response(PostSerializer(post).data)

# ❌ Serializer에서 복잡한 외부 호출
class PostSerializer(serializers.ModelSerializer):
    def create(self, validated_data):
        post = super().create(validated_data)
        send_email(post.author.email, '게시글 작성 완료')  # 서비스로 이동!
        return post

# ❌ Model에서 HTTP 의존
class Post(models.Model):
    def save(self, *args, **kwargs):
        from django.http import HttpResponse  # 모델에서 HTTP 의존 금지!
        super().save(*args, **kwargs)
```

---

## 2. 모델 패턴

### 추상 기본 모델

```python
# apps/common/models.py
from django.db import models
import uuid

class TimeStampedModel(models.Model):
    """생성/수정 시간 자동 추적"""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class UUIDModel(TimeStampedModel):
    """UUID PK + 타임스탬프"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True
```

### 쿼리 최적화 (N+1 방지)

```python
# ✅ select_related (ForeignKey, OneToOne — JOIN 사용)
Post.objects.select_related('author').all()

# ✅ prefetch_related (ManyToMany, reverse FK — 별도 쿼리)
Post.objects.prefetch_related('tags', 'comments').all()

# ✅ Prefetch 객체 (조건부 프리페치)
from django.db.models import Prefetch
Post.objects.prefetch_related(
    Prefetch('comments', queryset=Comment.objects.filter(is_approved=True))
)

# ✅ ViewSet에서 최적화
class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.select_related('author').prefetch_related('tags')
```

---

## 3. 인증 & 권한

### DRF 인증 설정

```python
# config/settings/base.py
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'apps.common.pagination.StandardPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'EXCEPTION_HANDLER': 'apps.common.exceptions.custom_exception_handler',
}
```

### JWT 설정 (Simple JWT)

```python
# config/settings/base.py
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}
```

### 커스텀 권한

```python
# apps/posts/permissions.py
from rest_framework import permissions

class IsAuthorOrReadOnly(permissions.BasePermission):
    """작성자만 수정/삭제 가능, 나머지는 읽기만"""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.author == request.user
```

---

## 4. 미들웨어

```python
# apps/common/middleware.py
import time
import logging

logger = logging.getLogger(__name__)

class RequestLoggingMiddleware:
    """요청 로깅 미들웨어"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start_time = time.time()
        response = self.get_response(request)
        duration = time.time() - start_time

        logger.info(
            '%s %s %s %.2fms',
            request.method,
            request.path,
            response.status_code,
            duration * 1000,
        )
        return response
```

```python
# config/settings/base.py
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',          # CORS (django-cors-headers)
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.common.middleware.RequestLoggingMiddleware', # 커스텀 로깅
]
```

---

## 5. 에러 처리

### 커스텀 예외 핸들러

```python
# apps/common/exceptions.py
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
import logging

logger = logging.getLogger(__name__)

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        response.data = {
            'error': {
                'code': response.status_code,
                'message': _get_error_message(response.data),
                'details': response.data if isinstance(response.data, dict) else None,
            }
        }
    else:
        # 예상하지 못한 에러
        logger.exception('Unhandled exception: %s', exc)
        response = Response(
            {'error': {'code': 500, 'message': '서버 내부 오류가 발생했습니다'}},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return response

def _get_error_message(data):
    if isinstance(data, dict) and 'detail' in data:
        return str(data['detail'])
    return '요청 처리 중 오류가 발생했습니다'
```

---

## 6. 환경 설정

### settings 분리 패턴

```python
# config/settings/base.py
import os
from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('DJANGO_SECRET_KEY')
DEBUG = False  # 기본값 False (환경별로 오버라이드)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third party
    'rest_framework',
    'django_filters',
    'corsheaders',
    # Local apps
    'apps.common',
    'apps.users',
    'apps.posts',
]

DATABASES = {
    'default': env.db('DATABASE_URL', default='sqlite:///db.sqlite3'),
}
```

```python
# config/settings/development.py
from .base import *

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1']
```

```python
# config/settings/production.py
from .base import *

DEBUG = False
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
```

---

## 7. 서비스 레이어 (복잡한 비즈니스 로직)

```python
# apps/posts/services.py
from django.db import transaction
from django.core.exceptions import ValidationError
from .models import Post
from apps.users.models import User

class PostService:
    @staticmethod
    @transaction.atomic
    def create_post(author: User, title: str, content: str) -> Post:
        """게시글 생성 + 관련 부수효과"""
        if Post.objects.filter(author=author).count() >= 100:
            raise ValidationError('게시글 수 제한을 초과했습니다 (최대 100개)')

        post = Post.objects.create(
            author=author,
            title=title,
            content=content,
        )

        # 부수효과 (알림, 통계 등)
        author.profile.post_count = author.posts.count()
        author.profile.save(update_fields=['post_count'])

        return post

    @staticmethod
    @transaction.atomic
    def publish_post(post: Post, user: User) -> Post:
        """게시글 공개 + 알림"""
        if post.author != user:
            raise ValidationError('작성자만 공개할 수 있습니다')

        post.publish()
        # 구독자 알림 등 추가 로직...
        return post
```

---

## 8. TypeScript/타입 힌트

```python
# Python 타입 힌트 (Django/DRF에서 권장)
from __future__ import annotations
from typing import Optional
from django.db.models import QuerySet
from .models import Post

class PostManager:
    def get_published(self, author_id: Optional[int] = None) -> QuerySet[Post]:
        qs = Post.objects.filter(status='published')
        if author_id:
            qs = qs.filter(author_id=author_id)
        return qs
```

---

## 9. 테스팅

### pytest-django (권장)

```python
# pytest.ini (또는 pyproject.toml)
[pytest]
DJANGO_SETTINGS_MODULE = config.settings.test
python_files = tests.py test_*.py *_tests.py
addopts = --reuse-db -v
```

```python
# apps/posts/tests/test_models.py
import pytest
from apps.posts.models import Post
from apps.users.tests.factories import UserFactory

@pytest.mark.django_db
class TestPostModel:
    def test_create_post(self):
        user = UserFactory()
        post = Post.objects.create(
            title='테스트 게시글',
            content='내용',
            author=user,
        )
        assert post.title == '테스트 게시글'
        assert post.status == 'draft'
        assert not post.is_published

    def test_publish(self):
        user = UserFactory()
        post = Post.objects.create(title='테스트', content='내용', author=user)
        post.publish()
        assert post.is_published
```

```python
# apps/posts/tests/test_views.py
import pytest
from rest_framework.test import APIClient
from rest_framework import status
from apps.posts.models import Post
from apps.users.tests.factories import UserFactory

@pytest.mark.django_db
class TestPostViewSet:
    def setup_method(self):
        self.client = APIClient()
        self.user = UserFactory()

    def test_list_published_posts(self):
        Post.objects.create(title='공개', content='내용', author=self.user, status='published')
        Post.objects.create(title='초안', content='내용', author=self.user, status='draft')

        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/v1/posts/')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) == 1

    def test_create_post_authenticated(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/v1/posts/', {
            'title': '새 게시글',
            'content': '내용입니다',
        })
        assert response.status_code == status.HTTP_201_CREATED
        assert Post.objects.count() == 1

    def test_create_post_unauthenticated(self):
        response = self.client.post('/api/v1/posts/', {
            'title': '새 게시글',
            'content': '내용입니다',
        })
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_post_by_other_user(self):
        post = Post.objects.create(title='원본', content='내용', author=self.user)
        other_user = UserFactory()
        self.client.force_authenticate(user=other_user)

        response = self.client.patch(f'/api/v1/posts/{post.id}/', {'title': '수정'})
        assert response.status_code == status.HTTP_403_FORBIDDEN
```

### Factory Boy (테스트 팩토리)

```python
# apps/users/tests/factories.py
import factory
from apps.users.models import User

class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f'user{n}')
    email = factory.LazyAttribute(lambda obj: f'{obj.username}@test.com')
    password = factory.PostGenerationMethodCall('set_password', 'testpass123')
```

---

## 10. 보안 & 성능 체크리스트

```yaml
security:
  - SECRET_KEY 환경변수에서 로드
  - DEBUG = False (프로덕션)
  - ALLOWED_HOSTS 제한
  - CORS 허용 출처 제한
  - CSRF 보호 활성화
  - SQL Injection 방지 (ORM 사용, raw SQL 최소화)
  - XSS 방지 (Django 템플릿 자동 이스케이프)
  - HTTPS 강제 (SECURE_SSL_REDIRECT)
  - 세션/CSRF 쿠키 Secure 플래그
  - HSTS 헤더 설정

performance:
  - select_related / prefetch_related (N+1 방지)
  - DB 인덱스 설정 (Meta.indexes)
  - 페이지네이션 필수
  - QuerySet은 지연 평가 활용
  - .only() / .defer() 로 필요한 필드만 로드
  - 캐싱 (django-redis, per-view cache)
  - DB 커넥션 풀링 (django-db-connection-pool)
```

---

## 11. 흔한 실수 종합

| 실수 | 문제 | 올바른 방법 |
|------|------|------------|
| `startproject name`으로 이중 중첩 | `name/name/` 구조 | `startproject config .` (마침표 필수) |
| Fat View (뷰에 모든 로직) | 유지보수/테스트 어려움 | 모델 메서드 + 서비스 레이어 분리 |
| N+1 쿼리 무시 | 심각한 성능 저하 | `select_related`/`prefetch_related` 필수 |
| SECRET_KEY 하드코딩 | 보안 위험 | `django-environ`으로 환경변수 로드 |
| `DEBUG = True` 프로덕션 | 디버그 정보 노출 | 환경별 settings 분리 |
| raw SQL 직접 사용 | SQL Injection 위험 | Django ORM 사용, 불가피 시 파라미터 바인딩 |
| Serializer에서 `__all__` 필드 | 민감 정보 노출 | 필요한 필드만 명시적으로 나열 |
| 마이그레이션 미커밋 | 배포 시 DB 불일치 | `makemigrations` 결과 Git에 포함 |
| `filter()` 체인 없이 `.all()` 반환 | 대량 데이터 무제한 조회 | 페이지네이션 + 필터링 필수 |
| 테스트에서 실제 DB 사용 | 느리고 불안정 | `@pytest.mark.django_db` + Factory 패턴 |
