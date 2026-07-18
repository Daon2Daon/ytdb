# 그룹 삭제 기능 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 본인 그룹을 UI에서 삭제할 수 있게 하고, 자동 생성된 데이터 평면 스키마까지 완전 삭제한다.

**Architecture:** 백엔드 `DELETE /api/groups/{slug}`는 이미 존재(소유권 검사 포함). 여기에 자동 생성 패턴(`youtube_u{userId}_{hex6}`) 스키마만 `DROP SCHEMA CASCADE`하는 로직을 추가하고, 프론트엔드 그룹 수정 모달에 이름 입력 확인 방식의 위험 구역 UI를 추가한다. 스펙: `docs/superpowers/specs/2026-07-19-group-delete-design.md`

**Tech Stack:** FastAPI + SQLAlchemy async(백엔드), React + TypeScript + Tailwind(프론트), pytest / vitest

---

### Task 1: 자동 생성 스키마 판별 함수 `is_auto_schema`

**Files:**
- Modify: `app/routers/groups.py` (모듈 상단에 함수 추가)
- Test: `tests/test_group_delete.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_group_delete.py` 신규 생성:

```python
"""그룹 삭제: 자동 생성 스키마 판별 + 삭제 라우트 동작."""

from app.routers.groups import is_auto_schema


def test_is_auto_schema_matches_generated_pattern():
    # create_group이 만드는 형태: youtube_u{user_id}_{token_hex(3)}
    assert is_auto_schema("youtube_u1_a1b2c3") is True
    assert is_auto_schema("youtube_u42_00ff00") is True


def test_is_auto_schema_rejects_custom_schemas():
    assert is_auto_schema("youtube_invest") is False        # 레거시/관리자 커스텀
    assert is_auto_schema("youtube_u1_xyz") is False        # hex 아님
    assert is_auto_schema("youtube_u1_a1b2c3d4") is False   # hex 길이 초과
    assert is_auto_schema("youtube_ua_a1b2c3") is False     # user_id 숫자 아님
    assert is_auto_schema("public") is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd /Users/mukymook/Library/CloudStorage/SynologyDrive-mookmuky/04.Coding/ytdb && python -m pytest tests/test_group_delete.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_auto_schema'`

- [ ] **Step 3: 구현**

`app/routers/groups.py`의 import 아래(router 정의 위)에 추가:

```python
import re

# 일반 사용자 그룹 생성 시 자동 부여되는 스키마 패턴(create_group 참조).
# 이 패턴일 때만 그룹 삭제 시 스키마를 DROP한다 — 레거시/관리자 커스텀
# 스키마(youtube_invest 등)를 실수로 지우지 않기 위한 안전장치.
_AUTO_SCHEMA_RE = re.compile(r"^youtube_u\d+_[0-9a-f]{6}$")


def is_auto_schema(schema_name: str) -> bool:
    return _AUTO_SCHEMA_RE.fullmatch(schema_name) is not None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_group_delete.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 커밋**

```bash
git add app/routers/groups.py tests/test_group_delete.py
git commit -m "feat: 자동 생성 스키마 판별 함수 is_auto_schema"
```

---

### Task 2: `DataPlaneEngineManager.drop_schema`

**Files:**
- Modify: `app/services/db_engine.py` (`ensure_schema` 아래에 메서드 추가)
- Test: `tests/test_group_delete.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_group_delete.py`에 추가:

```python
import pytest

from app.services.db_engine import DataPlaneEngineManager, DBNotConfiguredError


class _FakeConn:
    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(str(stmt))


class _FakeBegin:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def begin(self):
        return _FakeBegin(self._conn)


class _FakeCfg:
    def server_signature(self) -> str:
        return "sig"


class _GroupRef:
    group_id = 7
    schema_name = "youtube_u1_a1b2c3"


async def test_drop_schema_executes_drop_and_clears_cache(monkeypatch):
    dpm = DataPlaneEngineManager()
    conn = _FakeConn()

    async def _cfg(group):
        return _FakeCfg()

    async def _shared(cfg):
        return _FakeEngine(conn)

    monkeypatch.setattr(dpm, "_cfg", _cfg)
    monkeypatch.setattr(dpm, "_shared_engine", _shared)
    dpm._initialized.add(("sig", "youtube_u1_a1b2c3"))

    await dpm.drop_schema(_GroupRef())

    assert any(
        'DROP SCHEMA IF EXISTS "youtube_u1_a1b2c3" CASCADE' in s for s in conn.statements
    )
    assert ("sig", "youtube_u1_a1b2c3") not in dpm._initialized


async def test_drop_schema_skips_when_db_not_configured(monkeypatch):
    dpm = DataPlaneEngineManager()

    async def _cfg(group):
        raise DBNotConfiguredError("no db")

    monkeypatch.setattr(dpm, "_cfg", _cfg)
    # 예외 없이 조용히 반환해야 한다(스키마가 만들어진 적 없음).
    await dpm.drop_schema(_GroupRef())
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_group_delete.py -v`
Expected: FAIL — `AttributeError: 'DataPlaneEngineManager' object has no attribute 'drop_schema'`

- [ ] **Step 3: 구현**

`app/services/db_engine.py`의 `ensure_schema` 메서드 아래에 추가:

```python
    async def drop_schema(self, group: GroupRef) -> None:
        """그룹 스키마를 영구 삭제한다(DROP SCHEMA CASCADE).

        DB 설정이 없으면 스키마가 만들어진 적도 없으므로 조용히 건너뛴다.
        호출부(그룹 삭제)는 자동 생성 스키마만 넘겨야 한다.
        """
        try:
            cfg = await self._cfg(group)
        except DBNotConfiguredError:
            return
        engine = await self._shared_engine(cfg)
        async with engine.begin() as conn:
            await conn.execute(
                text(f'DROP SCHEMA IF EXISTS "{group.schema_name}" CASCADE')
            )
        # 같은 이름으로 재생성 시 ensure_schema가 스킵하지 않도록 캐시 제거.
        self._initialized.discard((cfg.server_signature(), group.schema_name))
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_group_delete.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add app/services/db_engine.py tests/test_group_delete.py
git commit -m "feat: 데이터 평면 스키마 DROP 지원 (drop_schema)"
```

---

### Task 3: delete_group 라우트에 스키마 드롭 연결

**Files:**
- Modify: `app/routers/groups.py:103-110` (delete_group)
- Test: `tests/test_group_delete.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_group_delete.py`에 추가. 기존 관례(tests/test_ownership.py)를 따라 dependency override + monkeypatch 사용:

```python
from fastapi.testclient import TestClient

import app.routers.groups as groups_router
from app.control_db import get_session
from app.main import app
from app.models.control.group import Group
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist
from app.services.db_engine import data_plane_engine_manager

ALICE = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="user")
BOB = CurrentUser(user_id=2, email="b@x.com", display_name="B", role="user")


def _make_group(schema_name: str, owner: int | None = 1) -> Group:
    from datetime import datetime, timezone

    g = Group()
    g.group_id, g.slug, g.name, g.schema_name = 1, "g1", "그룹1", schema_name
    g.is_active, g.owner_user_id, g.description = True, owner, None
    g.created_at = g.updated_at = datetime.now(timezone.utc)
    return g


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _DeleteFakeSession:
    """delete_group 경로용: get_group_or_404의 execute 1회 + delete/commit."""

    def __init__(self, group):
        self._group = group
        self.deleted = []
        self.committed = False

    async def execute(self, stmt):
        return _FakeResult(self._group)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


class _Recorder:
    def __init__(self):
        self.drop_calls: list[str] = []
        self.unsub_calls: list[int] = []


def _setup(monkeypatch, group, user) -> tuple[TestClient, _DeleteFakeSession, _Recorder]:
    set_users_exist(True)
    rec = _Recorder()

    async def _user_dep():
        return user

    fake = _DeleteFakeSession(group)

    async def _session_dep():
        yield fake

    async def _fake_drop(g):
        rec.drop_calls.append(g.schema_name)

    async def _fake_unsub(session, group_id):
        rec.unsub_calls.append(group_id)

    app.dependency_overrides[require_user] = _user_dep
    app.dependency_overrides[get_session] = _session_dep
    monkeypatch.setattr(data_plane_engine_manager, "drop_schema", _fake_drop)
    monkeypatch.setattr(groups_router, "remove_group_subscriptions", _fake_unsub)
    return TestClient(app, raise_server_exceptions=False), fake, rec


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_delete_auto_schema_group_drops_schema(monkeypatch):
    group = _make_group("youtube_u1_a1b2c3", owner=1)
    client, fake, rec = _setup(monkeypatch, group, ALICE)
    resp = client.delete("/api/groups/g1")
    assert resp.status_code == 204
    assert rec.drop_calls == ["youtube_u1_a1b2c3"]
    assert rec.unsub_calls == [1]
    assert fake.deleted == [group]
    assert fake.committed is True


def test_delete_custom_schema_group_keeps_schema(monkeypatch):
    group = _make_group("youtube_invest", owner=1)
    client, fake, rec = _setup(monkeypatch, group, ALICE)
    resp = client.delete("/api/groups/g1")
    assert resp.status_code == 204
    assert rec.drop_calls == []            # 커스텀 스키마는 보존
    assert fake.deleted == [group]


def test_delete_stranger_group_404(monkeypatch):
    group = _make_group("youtube_u1_a1b2c3", owner=1)
    client, fake, rec = _setup(monkeypatch, group, BOB)
    resp = client.delete("/api/groups/g1")
    assert resp.status_code == 404
    assert rec.drop_calls == []
    assert fake.deleted == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_group_delete.py -v`
Expected: `test_delete_auto_schema_group_drops_schema` FAIL — `rec.drop_calls == []` (라우트가 아직 drop_schema를 호출하지 않음). 나머지 신규 2건은 통과할 수 있음.

- [ ] **Step 3: 구현**

`app/routers/groups.py`의 delete_group을 다음으로 교체:

```python
@router.delete("/{slug}", status_code=204)
async def delete_group(
    group: Group = Depends(get_group_or_404),
    session: AsyncSession = Depends(get_session),
) -> None:
    # 자동 생성 스키마만 DROP(스펙 2026-07-19). 실패 시 500 → 그룹이 남아 재시도 가능.
    if is_auto_schema(group.schema_name):
        await data_plane_engine_manager.drop_schema(group)
    await remove_group_subscriptions(session, group.group_id)
    await session.delete(group)
    await session.commit()
```

import 추가:

```python
from app.services.db_engine import data_plane_engine_manager
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_group_delete.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 백엔드 전체 회귀 확인**

Run: `python -m pytest -q`
Expected: 전부 PASS (기존 테스트 무손상)

- [ ] **Step 6: 커밋**

```bash
git add app/routers/groups.py tests/test_group_delete.py
git commit -m "feat: 그룹 삭제 시 자동 생성 데이터 스키마까지 삭제"
```

---

### Task 4: 프론트엔드 API — `groupApi.remove`

**Files:**
- Modify: `frontend/src/api/groups.ts`

- [ ] **Step 1: 구현**

`frontend/src/api/groups.ts`의 `groupApi`에 추가:

```typescript
  remove: (slug: string) => rootApi.del<void>(`/groups/${slug}`),
```

(참고: `rootApi.del`은 `frontend/src/api/http.ts:57`에 이미 존재. 204는 undefined 반환.)

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/api/groups.ts
git commit -m "feat: groupApi.remove 추가"
```

---

### Task 5: EditGroupModal 위험 구역 UI

**Files:**
- Modify: `frontend/src/components/GroupModals.tsx` (EditGroupModal)

- [ ] **Step 1: 구현**

`EditGroupModal`을 다음으로 교체 (기존 저장 로직은 유지, 하단에 위험 구역 추가):

```typescript
export function EditGroupModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const { activeGroup, activeSlug, reloadGroups } = useGroup()
  const [name, setName] = useState(activeGroup?.name ?? '')
  const [isActive, setIsActive] = useState(activeGroup?.is_active ?? true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // 삭제 확인 단계: null=미진입, string=사용자가 입력 중인 확인 텍스트
  const [confirmText, setConfirmText] = useState<string | null>(null)

  const submit = async () => {
    if (!name.trim()) return
    setBusy(true)
    setErr(null)
    try {
      await groupApi.update(activeSlug, { name: name.trim(), is_active: isActive })
      await reloadGroups()
      onClose()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    setErr(null)
    try {
      await groupApi.remove(activeSlug)
      onClose()
      // reloadGroups 후 GroupProvider가 남은 첫 그룹 또는 루트로 보정한다.
      await reloadGroups()
      navigate('/', { replace: true })
    } catch (e) {
      setErr((e as Error).message)
      setBusy(false)
    }
  }

  const groupName = activeGroup?.name ?? ''

  return (
    <ModalShell title="그룹 수정" onClose={onClose}>
      {err && <p className="text-sm text-red-600">{err}</p>}
      <div>
        <label className="block text-xs text-gray-500 mb-1">그룹 영문 ID (변경 불가)</label>
        <input value={activeSlug} disabled className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-gray-50 text-gray-400" />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">그룹 명칭</label>
        <input value={name} onChange={(e) => setName(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      </div>
      <div className="border border-gray-200 rounded-lg p-3 space-y-2">
        <label className="flex items-center gap-3 cursor-pointer">
          <button
            type="button"
            onClick={() => setIsActive((v) => !v)}
            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${isActive ? 'bg-blue-600' : 'bg-gray-300'}`}
          >
            <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${isActive ? 'translate-x-6' : 'translate-x-1'}`} />
          </button>
          <span className="text-sm font-medium text-gray-700">{isActive ? '활성 (자동화 동작)' : '비활성 (일시정지)'}</span>
        </label>
        {!isActive && (
          <p className="text-xs text-amber-600">
            자동 수집·분석·다이제스트·알림이 중단됩니다. 데이터 조회와 수동 실행은 계속 가능합니다.
          </p>
        )}
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50">취소</button>
        <button onClick={submit} disabled={busy || !name.trim()}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-60">
          {busy ? '저장 중...' : '저장'}
        </button>
      </div>
      <div className="border-t border-gray-200 pt-4">
        {confirmText === null ? (
          <button
            onClick={() => setConfirmText('')}
            className="px-4 py-2 border border-red-300 text-red-600 rounded-lg text-sm hover:bg-red-50"
          >
            그룹 삭제
          </button>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-red-600 font-medium">
              수집된 영상·분석 데이터가 모두 영구 삭제됩니다. 되돌릴 수 없습니다.
            </p>
            <label className="block text-xs text-gray-500">
              계속하려면 그룹 명칭 <span className="font-bold text-gray-700">{groupName}</span> 을(를) 입력하세요.
            </label>
            <input
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder={groupName}
              className="w-full border border-red-300 rounded-lg px-3 py-2 text-sm"
            />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setConfirmText(null)}
                className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50">취소</button>
              <button
                onClick={remove}
                disabled={busy || confirmText !== groupName}
                className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 disabled:opacity-60"
              >
                {busy ? '삭제 중...' : '영구 삭제'}
              </button>
            </div>
          </div>
        )}
      </div>
    </ModalShell>
  )
}
```

(참고: `useNavigate`는 파일 상단에 이미 import되어 있음 — `frontend/src/components/GroupModals.tsx:2`)

- [ ] **Step 2: 빌드/타입 체크 + 기존 프론트 테스트**

Run: `cd frontend && npm run build && npm test`
Expected: 빌드 성공, 기존 vitest 전부 PASS

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/components/GroupModals.tsx
git commit -m "feat: 그룹 수정 모달에 삭제 위험 구역 추가 (이름 입력 확인)"
```

---

### Task 6: 최종 검증

- [ ] **Step 1: 백엔드 전체 테스트**

Run: `python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 2: 수동 E2E 확인(브라우저)**

1. 로컬 서버 기동, 일반 사용자로 로그인.
2. 테스트 그룹 생성 → 그룹 수정 모달 → "그룹 삭제" → 이름 입력 → "영구 삭제".
3. 확인: 그룹 목록에서 사라지고 남은 그룹(또는 온보딩 랜딩)으로 이동, DB에서 `youtube_uX_*` 스키마가 실제로 드롭됨.

- [ ] **Step 3: 스펙 체크박스/문서 갱신 필요 시 커밋**
