# 기본 데이터 평면 DSN 폴백 + 설정 화면 역할 가드 + 관리자 전역 설정 UI

날짜: 2026-07-18
상태: 승인됨 (추천 조합 C + A + 후속 1)

## 배경 / 문제

1. **프론트 노출 버그**: 일반 사용자가 사이드바 "설정"을 클릭하면
   `Layout.tsx`의 `SETTINGS_DEFAULT = SETTING_CATEGORIES[0].key`(= `database`)로
   이동한다. `Settings.tsx`의 리다이렉트 가드는 폼 정의(defs) 존재 여부만 확인해
   admin 전용 카테고리 URL 직접 진입을 막지 못한다. 백엔드는 이미 404로 은닉
   중이므로(설정 라우터 §3.3) 화면에 "설정을 찾을 수 없습니다" 배너 + 빈 DB 폼이
   렌더링된다.
2. **DB 기본값 미적용**: AI gateway는 `resolve_ai_gateway`(그룹 → 전역 → 코드
   기본값) 폴백이 있으나, 데이터 평면 DB는 `db_engine._cfg`가 그룹 설정만 보고
   미설정 시 `DBNotConfiguredError`를 던진다. 일반 사용자 그룹은 생성 시
   port/sslmode만 시드되므로 관리자가 그룹별로 DB 접속 정보를 수동 입력하기
   전까지 동작하지 않는다. 멀티테넌트 스펙 §2.8("제어 평면과 같은 서버 DSN으로
   자동 세팅, 사용자에게 비노출") 미구현 갭.
3. **관리자 전역 설정 UI 부재**: `/api/admin/global-settings` API는 있으나
   관리자 콘솔에 편집 화면이 없다.

## 설계

### A. `resolve_database` 폴백 (백엔드)

`resolve_ai_gateway`와 동일 패턴. 단, DB 접속 정보는 필드 혼합이 위험하므로
**전부-아니면-전무(all-or-nothing)** 로 해석한다:

- 그룹 database 설정이 `is_configured`(host+username+dbname 모두 존재)이면
  그룹 값을 그대로 사용 (관리자 그룹의 커스텀 DB 유지).
- 아니면 전역 기본 DSN을 사용.

전역 키 (`app.global_settings`):

| 키 | 시크릿 | 비고 |
|---|---|---|
| `db_host` | X | |
| `db_port` | X | 양의 정수 검증 |
| `db_name` | X | |
| `db_username` | X | |
| `db_password` | O (Fernet) | `SECRET_KEYS`에 추가 |
| `db_sslmode` | X | 기본 `prefer` |

부트스트랩 시드: `bootstrap_global_settings()`에 `_seed_global_db_from_control_dsn()`
추가 — `db_host` 미시드 시 `CONTROL_DATABASE_URL`을 파싱해 1회 시드(멱등).
FERNET_KEY 부재 시 기존 패턴대로 메시지 출력 후 건너뜀(부팅 비차단).

적용 지점: `db_engine._cfg`가 `get_database` 대신 `resolve_database`를 호출.
둘 다 미설정이면 기존 `DBNotConfiguredError` 유지.

주의: `db_engine`은 `server_signature()`로 엔진을 캐시하므로 폴백 결과도
시그니처가 동일하면 엔진이 공유된다(의도된 동작).

### C. 설정 화면 역할 가드 (프론트)

- `defs.ts`에 `defaultSettingsCategory(role)` 헬퍼 추가:
  `visibleCategories(role)[0].key`.
- `Layout.tsx`: 모듈 상수 `SETTINGS_DEFAULT` 제거, 컴포넌트 내에서
  `defaultSettingsCategory(user?.role)` 사용 (설정 링크·그룹 전환 네비 둘 다).
- `Settings.tsx`: 가드 조건에 "category가 `visibleCategories(role)`에 없으면
  첫 허용 탭으로 리다이렉트" 추가 — URL 직접 진입·북마크 차단.

### 후속 1. 관리자 전역 설정 섹션 (관리자 콘솔)

- `admin.py` `_GLOBAL_KEYS`에 `db_*` 6키 추가. `db_port`는 양의 정수 검증.
- `api/admin.ts`에 `globalSettings()` / `putGlobalSettings(items)` 추가.
- `Admin.tsx`에 "전역 설정" 섹션 추가(기존 섹션 스타일 답습): 키-값 폼,
  시크릿은 마스킹 값 표시(마스킹 재전송 시 무변경 — 서버 가드 기존 동작),
  빈 값 제출은 무변경. 키 라벨·도움말은 한글로 표기.

## 테스트

- `test_global_settings.py` 확장: `resolve_database` 그룹 우선/전역 폴백/
  둘 다 없음, DSN 파싱 시드(멱등·FERNET 부재 스킵).
- `test_admin_api.py`(또는 신규): global-settings에 `db_*` 키 왕복,
  `db_port` 검증 400.
- 프론트 `defs.test.ts`: `defaultSettingsCategory` — admin=`database`,
  user=`polling`(admin 전용 카테고리 제외 확인).

## 비범위

- 그룹 생성 시 DSN 복사(B안) — 채택 안 함.
- 그룹 생성 직후 `ensure_schema` 선실행(후속 2) — 이번 범위 제외.
- shared-schema 전환, PG 결제(E-2) 등 기존 로드맵 무변경.
