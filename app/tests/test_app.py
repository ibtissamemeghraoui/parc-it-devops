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
def test_ready_ok(client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_ready_base_indisponible(client, monkeypatch):
    import app as module_app

    def boom():
        raise RuntimeError("base arretee")

    monkeypatch.setattr(module_app, "verifier_base", boom)
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.get_json()["status"] == "base indisponible"


FORMULAIRE = {"nom": "PC-01", "type": "PC", "numero_serie": "SN001", "statut": "en stock"}


def test_api_doublon_renvoie_409(client):
    client.post("/api/equipements", json=NOUVEAU)
    r = client.post("/api/equipements", json=NOUVEAU)
    assert r.status_code == 409


def test_api_champs_manquants_renvoie_400(client):
    r = client.post("/api/equipements", json={"nom": "PC-01"})
    assert r.status_code == 400


def test_page_doublon_affiche_un_message(client):
    client.post("/ajouter", data=FORMULAIRE)
    r = client.post("/ajouter", data=FORMULAIRE, follow_redirects=True)
    assert "existe déjà" in r.get_data(as_text=True)


def test_page_modifier_un_equipement(client):
    client.post("/ajouter", data=FORMULAIRE)
    modification = dict(FORMULAIRE, nom="PC-99", statut="en panne")
    r = client.post("/modifier/1", data=modification, follow_redirects=True)
    texte = r.get_data(as_text=True)
    assert "PC-99" in texte
    assert "Équipement modifié" in texte


def test_recherche_filtre_les_equipements(client):
    client.post("/api/equipements", json={"nom": "Portable", "type": "PC", "numero_serie": "A1"})
    client.post("/api/equipements", json={"nom": "Routeur", "type": "reseau", "numero_serie": "B2"})
    texte = client.get("/?q=Portable").get_data(as_text=True)
    assert "Portable" in texte
    assert "Routeur" not in texte


def test_filtre_par_statut(client):
    client.post("/api/equipements", json={"nom": "A", "type": "PC", "numero_serie": "A1", "statut": "en panne"})
    client.post("/api/equipements", json={"nom": "B", "type": "PC", "numero_serie": "B2", "statut": "en stock"})
    texte = client.get("/?statut=en panne").get_data(as_text=True)
    assert ">A<" in texte
    assert ">B<" not in texte

def test_export_csv_neutralise_les_formules(client):
    client.post("/api/equipements", json={"nom": "=CMD", "type": "PC", "numero_serie": "C1"})
    r = client.get("/export.csv")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    texte = r.get_data(as_text=True)
    assert "id,nom,type,numero_serie,statut,affectation" in texte
    assert "'=CMD" in texte


def test_api_statut_invalide_renvoie_400(client):
    r = client.post("/api/equipements", json=dict(NOUVEAU, statut="inconnu"))
    assert r.status_code == 400


def test_page_affiche_graphiques_et_etat_du_systeme(client):
    client.post("/api/equipements", json=NOUVEAU)
    texte = client.get("/").get_data(as_text=True)
    assert "Répartition par statut" in texte
    assert "Équipements par type" in texte
    assert "Système opérationnel" in texte