import pytest

from app import create_app, db


@pytest.fixture
def client():
    # Une application neuve avec une base en mémoire pour chaque test.
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.drop_all()


NOUVEAU = {"nom": "PC-01", "type": "PC", "numero_serie": "SN001"}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.data == b"OK"


def test_creer_et_lister(client):
    r = client.post("/api/equipements", json=NOUVEAU)
    assert r.status_code == 201
    liste = client.get("/api/equipements").get_json()
    assert len(liste) == 1
    assert liste[0]["nom"] == "PC-01"


def test_modifier(client):
    eq_id = client.post("/api/equipements", json=NOUVEAU).get_json()["id"]
    r = client.put(f"/api/equipements/{eq_id}", json={"statut": "en panne"})
    assert r.get_json()["statut"] == "en panne"


def test_supprimer(client):
    eq_id = client.post("/api/equipements", json=NOUVEAU).get_json()["id"]
    assert client.delete(f"/api/equipements/{eq_id}").status_code == 204
    assert client.get(f"/api/equipements/{eq_id}").status_code == 404