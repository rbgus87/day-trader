# API 엔드포인트 명세 템플릿

## {{ENDPOINT_NAME}}

### 기본 정보

| 항목 | 내용 |
|------|------|
| **메서드** | `{{METHOD}}` |
| **경로** | `/api/{{PATH}}` |
| **설명** | {{DESCRIPTION}} |
| **인증** | Required / Optional / None |

### Request

#### Headers
```
Authorization: Bearer {{token}}
Content-Type: application/json
```

#### Path Parameters
| 파라미터 | 타입 | 필수 | 설명 |
|----------|------|------|------|
| `id` | string | ✅ | 리소스 ID |

#### Query Parameters
| 파라미터 | 타입 | 필수 | 기본값 | 설명 |
|----------|------|------|--------|------|
| `page` | number | ❌ | 1 | 페이지 번호 |
| `limit` | number | ❌ | 20 | 페이지당 항목 수 |

#### Request Body
```typescript
interface RequestBody {
  name: string       // 필수, 2-100자
  email: string      // 필수, 이메일 형식
  role?: string      // 선택, 기본값: "user"
}
```

**예시:**
```json
{
  "name": "홍길동",
  "email": "hong@example.com",
  "role": "admin"
}
```

### Response

#### 성공 (200 OK)
```typescript
interface SuccessResponse {
  success: true
  data: {
    id: string
    name: string
    email: string
    createdAt: string  // ISO 8601
  }
}
```

**예시:**
```json
{
  "success": true,
  "data": {
    "id": "usr_abc123",
    "name": "홍길동",
    "email": "hong@example.com",
    "createdAt": "2024-01-15T09:30:00Z"
  }
}
```

#### 에러 응답

| 상태 코드 | 코드 | 설명 |
|-----------|------|------|
| 400 | `VALIDATION_ERROR` | 요청 데이터 유효성 검사 실패 |
| 401 | `UNAUTHORIZED` | 인증 토큰 없음 또는 만료 |
| 403 | `FORBIDDEN` | 권한 없음 |
| 404 | `NOT_FOUND` | 리소스 없음 |
| 409 | `CONFLICT` | 중복 데이터 |
| 500 | `INTERNAL_ERROR` | 서버 오류 |

**에러 응답 형식:**
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "이메일 형식이 올바르지 않습니다.",
    "details": [
      {
        "field": "email",
        "message": "유효한 이메일 주소를 입력하세요."
      }
    ]
  }
}
```

### 유효성 검사 규칙

| 필드 | 규칙 |
|------|------|
| `name` | 2-100자, 필수 |
| `email` | 이메일 형식, 필수, 고유값 |
| `role` | "user" \| "admin", 선택 |

### Rate Limiting

| 제한 | 값 |
|------|-----|
| 분당 요청 수 | 60 |
| 일일 요청 수 | 10,000 |

### 보안 고려사항

- [ ] 인증 토큰 검증
- [ ] 입력 데이터 sanitization
- [ ] Rate limiting 적용
- [ ] SQL Injection 방지
- [ ] 민감 데이터 로깅 제외

---

## 구현 체크리스트

- [ ] 엔드포인트 라우트 등록
- [ ] Request validation 구현
- [ ] 비즈니스 로직 구현
- [ ] 에러 핸들링
- [ ] 단위 테스트 작성
- [ ] API 문서 업데이트
