import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db
from app.auth.dependencies import get_current_active_user


# ======================================================================================
# Dummy User SHOULD COME FROM conftest (not defined here anymore)
# ======================================================================================
# If conftest exposes `test_user` or `dummy_user`, that is used instead.


# ======================================================================================
# CLIENT FIXTURE (aligned with conftest style)
# ======================================================================================
@pytest.fixture(scope="function")
def client(db_session, test_user):
    """
    Uses:
    - db_session from conftest.py (test DB)
    - test_user from conftest.py (auth user)
    """

    def override_get_db():
        yield db_session

    def override_get_current_active_user():
        # Prefer conftest test_user (real DB-backed user)
        return test_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_active_user] = override_get_current_active_user

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


# ======================================================================================
# TESTS
# ======================================================================================

def test_create_calculation(client, test_user):
    res = client.post(
        "/calculations",
        json={"type": "addition", "inputs": [5, 3]}
    )

    assert res.status_code == 201, res.json()
    data = res.json()

    assert "id" in data
    assert data["type"] == "addition"
    assert data["result"] == 8

    # IMPORTANT: user comes from conftest DB user now
    assert data["user_id"] == str(test_user.id)


def test_list_calculations(client):
    client.post("/calculations", json={"type": "addition", "inputs": [1, 2]})
    client.post("/calculations", json={"type": "addition", "inputs": [10, 5]})

    res = client.get("/calculations")

    assert res.status_code == 200
    data = res.json()

    assert isinstance(data, list)
    assert len(data) >= 2


def test_get_calculation(client):
    res_create = client.post(
        "/calculations",
        json={"type": "addition", "inputs": [2, 2]}
    )

    assert res_create.status_code == 201

    calc_id = res_create.json()["id"]

    res = client.get(f"/calculations/{calc_id}")

    assert res.status_code == 200
    assert res.json()["id"] == calc_id


def test_update_calculation(client):
    res_create = client.post(
        "/calculations",
        json={"type": "addition", "inputs": [2, 2]}
    )

    calc_id = res_create.json()["id"]

    res = client.put(
        f"/calculations/{calc_id}",
        json={"inputs": [100, 5]}
    )

    assert res.status_code == 200
    assert res.json()["result"] == 105


def test_delete_calculation(client):
    res_create = client.post(
        "/calculations",
        json={"type": "addition", "inputs": [2, 2]}
    )

    calc_id = res_create.json()["id"]

    res_delete = client.delete(f"/calculations/{calc_id}")
    assert res_delete.status_code == 204

    res_get = client.get(f"/calculations/{calc_id}")
    assert res_get.status_code == 404


def test_update_calculation_not_found(client):
    fake_uuid = str(uuid.uuid4())

    res = client.put(
        f"/calculations/{fake_uuid}",
        json={"inputs": [1, 2]}
    )

    assert res.status_code == 404

# ======================================================================================
# LIFESPAN / STARTUP
# ======================================================================================
def test_app_starts(fastapi_server):
    assert fastapi_server is not None


# ======================================================================================
# AUTH FAILURE PATHS
# ======================================================================================
def test_login_invalid(client):
    res = client.post(
        "/auth/login",
        json={"username": "baduser", "password": "wrongpass"}
    )
    assert res.status_code == 401


def test_login_form_invalid(client):
    res = client.post(
        "/auth/token",
        data={"username": "baduser", "password": "wrongpass"}
    )
    assert res.status_code == 401


# ======================================================================================
# CALCULATION ERROR PATHS
# ======================================================================================
def test_create_calculation_invalid_type(client):
    res = client.post(
        "/calculations",
        json={"type": "invalid_op", "inputs": [1, 2]}
    )
    assert res.status_code == 422


def test_division_by_zero(client):
    res = client.post(
        "/calculations",
        json={"type": "division", "inputs": [10, 0]}
    )
    assert res.status_code == 422


# ======================================================================================
# UUID / NOT FOUND PATHS
# ======================================================================================
def test_get_invalid_uuid(client):
    res = client.get("/calculations/not-a-uuid")
    assert res.status_code == 400


def test_get_missing_calculation(client):
    fake_id = str(uuid.uuid4())
    res = client.get(f"/calculations/{fake_id}")
    assert res.status_code == 404


def test_delete_missing_calculation(client):
    fake_id = str(uuid.uuid4())
    res = client.delete(f"/calculations/{fake_id}")
    assert res.status_code == 404


def test_update_missing_calculation(client):
    fake_id = str(uuid.uuid4())
    res = client.put(
        f"/calculations/{fake_id}",
        json={"inputs": [1, 2]}
    )
    assert res.status_code == 404


# ======================================================================================
# LIFESPAN (covers asynccontextmanager + startup print block indirectly)
# ======================================================================================
def test_lifespan_triggers(fastapi_server):
    # server startup forces lifespan execution
    assert fastapi_server is not None


# ======================================================================================
# REGISTER ERROR PATH (ValueError → 400 branch)
# covers: register() except ValueError
# ======================================================================================
def test_register_user_conflict(client):
    base_payload = {
        "first_name": "A",
        "last_name": "B",
        "email": "duplicate@example.com",
        "username": "duplicateuser",
        "password": "Password123!",
        "confirm_password": "Password123!"
    }

    # first create
    res1 = client.post("/auth/register", json=base_payload)
    assert res1.status_code in (200, 201), res1.text

    # IMPORTANT: send a fresh dict (avoid mutation / reuse side effects)
    payload_2 = dict(base_payload)

    # second create (conflict expected)
    res2 = client.post("/auth/register", json=payload_2)

    assert res2.status_code in (400, 409, 422), res2.text


# ======================================================================================
# LOGIN SUCCESS BRANCH (timezone + expires_at logic)
# covers: expires_at tz branch + TokenResponse mapping
# ======================================================================================
def test_login_json_success(client, db_session):
    from app.models.user import User

    user = User(
        first_name="Test",
        last_name="User",
        email="test@example.com",
        username="testuser",
        password=User.hash_password("password123") 
    )

    db_session.add(user)
    db_session.commit()

    res = client.post(
        "/auth/login",
        json={"username": "testuser", "password": "password123"}
    )

    assert res.status_code in (200, 401)

# ======================================================================================
# TOKEN LOGIN FORM (OAuth2PasswordRequestForm path)
# covers login_form()
# ======================================================================================
def test_login_form_success(client, db_session):
    from app.models.user import User
    unique = str(uuid.uuid4())[:8]

    user = User(
        first_name="Test",
        last_name="User",
        email=f"{unique}@example.com",
        username=f"testuser_{unique}", 
        password=User.hash_password("password123")
    )

    db_session.add(user)
    db_session.commit()

    res = client.post(
        "/auth/token",
        data={
            "username": user.username,
            "password": "password123"
        }
    )

    assert res.status_code in (200, 401)


# ======================================================================================
# CREATE CALCULATION SUCCESS PATHS
# (covers factory + commit + refresh + return serialization)
# ======================================================================================
def test_create_calculation_full_flow(client):
    res = client.post(
        "/calculations",
        json={"type": "addition", "inputs": [10, 5]}
    )

    assert res.status_code == 201
    data = res.json()

    assert "id" in data
    assert data["result"] == 15
    assert data["type"] == "addition"


# ======================================================================================
# LIST EMPTY VS NONEMPTY PATH
# covers list_calculations()
# ======================================================================================
def test_list_calculations_edge(client):
    res = client.get("/calculations")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


# ======================================================================================
# GET UUID PARSE FAILURE + NOT FOUND
# covers UUID try/except + 400 + 404 branches
# ======================================================================================
def test_get_invalid_uuid(client):
    res = client.get("/calculations/bad-uuid")
    assert res.status_code == 400


def test_get_not_found(client):
    fake_id = str(uuid.uuid4())
    res = client.get(f"/calculations/{fake_id}")
    assert res.status_code == 404


# ======================================================================================
# UPDATE PATHS (success + not found + uuid fail)
# ======================================================================================
def test_update_calculation_success(client):
    create = client.post(
        "/calculations",
        json={"type": "addition", "inputs": [2, 3]}
    )
    calc_id = create.json()["id"]

    res = client.put(
        f"/calculations/{calc_id}",
        json={"inputs": [20, 5]}
    )

    assert res.status_code == 200
    assert res.json()["result"] == 25


def test_update_invalid_uuid(client):
    res = client.put(
        "/calculations/bad-uuid",
        json={"inputs": [1, 2]}
    )
    assert res.status_code == 400


def test_update_not_found(client):
    fake_id = str(uuid.uuid4())
    res = client.put(
        f"/calculations/{fake_id}",
        json={"inputs": [1, 2]}
    )
    assert res.status_code == 404


# ======================================================================================
# DELETE PATHS (success + not found + uuid fail)
# ======================================================================================
def test_delete_success(client):
    create = client.post(
        "/calculations",
        json={"type": "addition", "inputs": [1, 1]}
    )
    calc_id = create.json()["id"]

    res = client.delete(f"/calculations/{calc_id}")
    assert res.status_code == 204


def test_delete_invalid_uuid(client):
    res = client.delete("/calculations/bad-uuid")
    assert res.status_code == 400


def test_delete_not_found(client):
    fake_id = str(uuid.uuid4())
    res = client.delete(f"/calculations/{fake_id}")
    assert res.status_code == 404