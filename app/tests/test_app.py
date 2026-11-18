import socket
from datetime import timedelta

import pytest

import decouverte
import devops
from app import Audit, Equipement, Tentative, Utilisateur, create_app, db, maintenant


def creer_utilisateur(nom, mot_de_passe, role):
    utilisateur = Utilisateur(nom_utilisateur=nom, role=role)
    utilisateur.definir_mot_de_passe(mot_de_passe)
    db.session.add(utilisateur)
    db.session.commit()


def se_connecter(client, nom, mot_de_passe):
    return client.post("/login", data={"nom_utilisateur": nom, "mot_de_passe": mot_de_passe})


@pytest.fixture
def client():
    # Une application neuve avec une base en mémoire pour chaque test.
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        creer_utilisateur("admin", "motdepasse-admin", "admin")
        creer_utilisateur("lecteur", "motdepasse-lecteur", "lecteur")
        admin = app.test_client()
        se_connecter(admin, "admin", "motdepasse-admin")
        yield admin
        db.drop_all()


@pytest.fixture
def anonyme(client):
    return client.application.test_client()


@pytest.fixture
def lecteur(client):
    autre = client.application.test_client()
    se_connecter(autre, "lecteur", "motdepasse-lecteur")
    return autre


NOUVEAU = {"nom": "PC-01", "type": "PC", "numero_serie": "SN001"}
FORMULAIRE = {"nom": "PC-01", "type": "PC", "numero_serie": "SN001", "statut": "en stock"}


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
    client.post(
        "/api/equipements",
        json={"nom": "A", "type": "PC", "numero_serie": "A1", "statut": "en panne"},
    )
    client.post(
        "/api/equipements",
        json={"nom": "B", "type": "PC", "numero_serie": "B2", "statut": "en stock"},
    )
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


# ---------- Authentification et rôles ----------


def test_page_sans_connexion_redirige_vers_login(anonyme):
    r = anonyme.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_api_sans_connexion_renvoie_401(anonyme):
    assert anonyme.get("/api/equipements").status_code == 401


def test_health_et_ready_restent_publics(anonyme):
    assert anonyme.get("/health").status_code == 200
    assert anonyme.get("/ready").status_code == 200


def test_mauvais_mot_de_passe_refuse(anonyme):
    r = se_connecter(anonyme, "admin", "faux")
    assert r.status_code == 401
    assert anonyme.get("/api/equipements").status_code == 401


def test_lecteur_peut_lire(lecteur):
    assert lecteur.get("/api/equipements").status_code == 200


def test_lecteur_ne_peut_pas_creer_via_api(lecteur):
    assert lecteur.post("/api/equipements", json=NOUVEAU).status_code == 403


def test_lecteur_ne_peut_pas_supprimer(client, lecteur):
    eq_id = client.post("/api/equipements", json=NOUVEAU).get_json()["id"]
    assert lecteur.delete(f"/api/equipements/{eq_id}").status_code == 403
    assert client.get(f"/api/equipements/{eq_id}").status_code == 200


def test_lecteur_ne_peut_pas_ajouter_via_la_page(lecteur):
    assert lecteur.post("/ajouter", data=FORMULAIRE).status_code == 403


def test_deconnexion(client):
    client.post("/logout")
    assert client.get("/api/equipements").status_code == 401


def test_mot_de_passe_est_hache(client):
    utilisateur = Utilisateur.query.filter_by(nom_utilisateur="admin").first()
    assert utilisateur.mot_de_passe_hash != "motdepasse-admin"


# ---------- Journal d'audit ----------


def test_ajout_est_journalise(client):
    client.post("/api/equipements", json=NOUVEAU)
    entree = Audit.query.filter_by(action="ajout").one()
    assert entree.utilisateur == "admin"
    assert "SN001" in entree.details


def test_modification_journalise_les_champs(client):
    eq_id = client.post("/api/equipements", json=NOUVEAU).get_json()["id"]
    client.put(f"/api/equipements/{eq_id}", json={"statut": "en panne"})
    entree = Audit.query.filter_by(action="modification").one()
    assert "en stock → en panne" in entree.details


def test_suppression_journalisee(client):
    eq_id = client.post("/api/equipements", json=NOUVEAU).get_json()["id"]
    client.delete(f"/api/equipements/{eq_id}")
    entree = Audit.query.filter_by(action="suppression").one()
    assert "PC-01" in entree.details


def test_doublon_non_journalise(client):
    client.post("/api/equipements", json=NOUVEAU)
    client.post("/api/equipements", json=NOUVEAU)
    assert Audit.query.filter_by(action="ajout").count() == 1


def test_connexion_echouee_journalisee(anonyme):
    se_connecter(anonyme, "admin", "faux")
    entree = Audit.query.filter_by(action="connexion échouée").one()
    assert entree.utilisateur == "anonyme"
    assert "admin" in entree.details


def test_page_audit_reservee_aux_admins(client, lecteur, anonyme):
    assert client.get("/audit").status_code == 200
    assert lecteur.get("/audit").status_code == 403
    assert anonyme.get("/audit").status_code == 302


def test_page_audit_affiche_les_actions(client):
    client.post("/api/equipements", json=NOUVEAU)
    texte = client.get("/audit").get_data(as_text=True)
    assert "ajout" in texte
    assert "PC-01" in texte


# ---------- Inscription ----------

INSCRIPTION = {
    "nom_utilisateur": "nouveau",
    "mot_de_passe": "motdepasse-ok",
    "confirmation": "motdepasse-ok",
}


def test_page_inscription_accessible(anonyme):
    assert anonyme.get("/inscription").status_code == 200


def test_inscription_cree_un_lecteur_connecte(anonyme):
    r = anonyme.post("/inscription", data=dict(INSCRIPTION, role="admin"))
    assert r.status_code == 302
    utilisateur = Utilisateur.query.filter_by(nom_utilisateur="nouveau").one()
    assert utilisateur.role == "lecteur"
    assert anonyme.get("/api/equipements").status_code == 200
    assert anonyme.post("/api/equipements", json=NOUVEAU).status_code == 403


def test_inscription_mots_de_passe_differents(anonyme):
    r = anonyme.post("/inscription", data=dict(INSCRIPTION, confirmation="autre-chose"))
    assert r.status_code == 400
    assert Utilisateur.query.filter_by(nom_utilisateur="nouveau").first() is None


def test_inscription_mot_de_passe_trop_court(anonyme):
    r = anonyme.post("/inscription", data=dict(INSCRIPTION, mot_de_passe="abc", confirmation="abc"))
    assert r.status_code == 400


def test_inscription_refuse_nom_deja_pris_meme_en_majuscules(anonyme):
    r = anonyme.post("/inscription", data=dict(INSCRIPTION, nom_utilisateur="Admin"))
    assert r.status_code == 400
    assert "déjà pris" in r.get_data(as_text=True)


def test_inscription_refuse_nom_invalide(anonyme):
    r = anonyme.post("/inscription", data=dict(INSCRIPTION, nom_utilisateur="a b"))
    assert r.status_code == 400


def test_inscription_est_journalisee(anonyme):
    anonyme.post("/inscription", data=INSCRIPTION)
    entree = Audit.query.filter_by(action="inscription").one()
    assert entree.utilisateur == "nouveau"


def test_inscription_fermee(client, anonyme):
    client.application.config["INSCRIPTION_OUVERTE"] = False
    assert anonyme.get("/inscription").status_code == 404


# ---------- Limitation des tentatives de connexion ----------


def echouer(client, fois, nom="admin"):
    for _ in range(fois):
        assert se_connecter(client, nom, "faux").status_code == 401


def test_blocage_apres_cinq_echecs(anonyme):
    echouer(anonyme, 5)
    r = se_connecter(anonyme, "admin", "faux")
    assert r.status_code == 429
    assert "Trop de tentatives" in r.get_data(as_text=True)


def test_blocage_refuse_meme_le_bon_mot_de_passe(anonyme):
    echouer(anonyme, 5)
    assert se_connecter(anonyme, "admin", "motdepasse-admin").status_code == 429
    assert anonyme.get("/api/equipements").status_code == 401


def test_blocage_propre_au_compte(anonyme):
    echouer(anonyme, 5)
    r = se_connecter(anonyme, "lecteur", "motdepasse-lecteur")
    assert r.status_code == 302


def test_quatre_echecs_ne_bloquent_pas(anonyme):
    echouer(anonyme, 4)
    assert se_connecter(anonyme, "admin", "motdepasse-admin").status_code == 302


def test_succes_efface_les_echecs(anonyme):
    echouer(anonyme, 3)
    assert se_connecter(anonyme, "admin", "motdepasse-admin").status_code == 302
    assert Tentative.query.count() == 0


def test_blocage_expire_avec_le_temps(anonyme):
    echouer(anonyme, 5)
    Tentative.query.update({"date": maintenant() - timedelta(minutes=16)})
    db.session.commit()
    assert se_connecter(anonyme, "admin", "motdepasse-admin").status_code == 302


def test_blocage_par_adresse(anonyme):
    anonyme.application.config["TENTATIVES_MAX_ADRESSE"] = 3
    for nom in ("a1", "a2", "a3"):
        echouer(anonyme, 1, nom=nom)
    assert se_connecter(anonyme, "admin", "motdepasse-admin").status_code == 429


def test_verrouillage_journalise_une_seule_fois(anonyme):
    echouer(anonyme, 5)
    se_connecter(anonyme, "admin", "faux")
    assert Audit.query.filter_by(action="verrouillage").count() == 1
    assert Tentative.query.count() == 5


# ---------- Découverte du réseau ----------

MACHINE = {"ip": "192.168.1.10", "nom": "pc-bureau", "ports": [22, 80]}


@pytest.mark.parametrize("texte", ["192.168.1.0/24", "10.0.0.5", "172.16.5.0/25", " 192.168.0.7/24 "])
def test_reseaux_acceptes(texte):
    assert decouverte.valider_reseau(texte).num_addresses <= 256


@pytest.mark.parametrize(
    "texte",
    ["8.8.8.8/24", "192.168.0.0/16", "127.0.0.0/24", "169.254.1.0/24", "224.0.0.0/24", "::1", "abc", ""],
)
def test_reseaux_refuses(texte):
    with pytest.raises(decouverte.ReseauInvalide):
        decouverte.valider_reseau(texte)


def test_scanner_hote_distingue_port_ouvert_et_ferme():
    ecoute = socket.socket()
    ecoute.bind(("127.0.0.1", 0))
    ecoute.listen()
    ouvert = ecoute.getsockname()[1]
    libre = socket.socket()
    libre.bind(("127.0.0.1", 0))
    ferme = libre.getsockname()[1]
    libre.close()
    try:
        presente, ports = decouverte.scanner_hote("127.0.0.1", [ouvert, ferme], delai=1)
    finally:
        ecoute.close()
    assert presente is True
    assert ports == [ouvert]


def test_decouverte_reservee_aux_admins(client, lecteur, anonyme):
    assert client.get("/decouverte").status_code == 200
    assert lecteur.get("/decouverte").status_code == 403
    assert anonyme.get("/decouverte").status_code == 302


def test_lecteur_ne_peut_pas_scanner(lecteur):
    assert lecteur.post("/decouverte", data={"reseau": "192.168.1.0/24"}).status_code == 403


def test_scan_affiche_les_machines_et_journalise(client, monkeypatch):
    monkeypatch.setattr(decouverte, "scanner_reseau", lambda reseau: [dict(MACHINE)])
    r = client.post("/decouverte", data={"reseau": "192.168.1.0/24"})
    texte = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "192.168.1.10" in texte
    assert "pc-bureau" in texte
    assert Audit.query.filter_by(action="scan réseau").count() == 1


def test_scan_refuse_un_reseau_public(client):
    r = client.post("/decouverte", data={"reseau": "8.8.8.0/24"})
    assert r.status_code == 400
    assert "privés" in r.get_data(as_text=True)


def test_scan_deja_en_cours(client):
    assert decouverte.VERROU.acquire(blocking=False)
    try:
        r = client.post("/decouverte", data={"reseau": "192.168.1.0/24"})
    finally:
        decouverte.VERROU.release()
    assert r.status_code == 429


def test_ajout_depuis_la_decouverte(client):
    r = client.post("/decouverte/ajouter", data={"ip": "192.168.1.10", "nom": "pc-bureau"})
    assert r.status_code == 302
    eq = Equipement.query.filter_by(numero_serie="IP-192.168.1.10").one()
    assert eq.nom == "pc-bureau"
    assert eq.type == "Découvert"


def test_ajout_decouverte_doublon(client):
    client.post("/decouverte/ajouter", data={"ip": "192.168.1.10"})
    r = client.post("/decouverte/ajouter", data={"ip": "192.168.1.10"}, follow_redirects=True)
    assert "existe déjà" in r.get_data(as_text=True)


def test_ajout_decouverte_refuse_une_adresse_publique_ou_un_reseau(client):
    assert client.post("/decouverte/ajouter", data={"ip": "8.8.8.8"}).status_code == 400
    assert client.post("/decouverte/ajouter", data={"ip": "192.168.1.0/24"}).status_code == 400


def test_ajout_decouverte_reserve_aux_admins(lecteur):
    assert lecteur.post("/decouverte/ajouter", data={"ip": "192.168.1.10"}).status_code == 403


def test_scan_signale_les_machines_deja_connues(client, monkeypatch):
    client.post("/decouverte/ajouter", data={"ip": "192.168.1.10"})
    monkeypatch.setattr(decouverte, "scanner_reseau", lambda reseau: [dict(MACHINE)])
    texte = client.post("/decouverte", data={"reseau": "192.168.1.0/24"}).get_data(as_text=True)
    assert "Déjà dans l'inventaire" in texte


# ---------- Interface ----------


def test_pages_d_erreur_personnalisees(anonyme, lecteur):
    r = anonyme.get("/page-qui-n-existe-pas")
    assert r.status_code == 404
    assert "Page introuvable" in r.get_data(as_text=True)
    r = lecteur.get("/audit")
    assert r.status_code == 403
    assert "Accès refusé" in r.get_data(as_text=True)


def test_erreur_api_reste_en_json(client):
    r = client.get("/api/equipements/999")
    assert r.status_code == 404
    assert r.get_json()["erreur"] == "Page introuvable"


def test_page_contient_theme_indicateurs_et_tri(client):
    texte = client.get("/").get_data(as_text=True)
    assert 'id="theme"' in texte
    assert "Indicateurs" in texte
    assert "triable" in texte


# ---------- Tableau de bord et page DevOps ----------


def test_navigation_par_onglets(client, lecteur):
    texte = client.get("/").get_data(as_text=True)
    assert "Tableau de bord" in texte and "DevOps" in texte and 'href="/audit"' in texte
    assert 'href="/audit"' not in lecteur.get("/").get_data(as_text=True)


def test_tableau_de_bord(client, lecteur):
    client.post("/api/equipements", json=NOUVEAU)
    texte = client.get("/tableau-de-bord").get_data(as_text=True)
    assert "Activité des 14 derniers jours" in texte
    assert "Dernières actions" in texte
    assert "Dernières actions" not in lecteur.get("/tableau-de-bord").get_data(as_text=True)


def test_page_devops_sans_prometheus(client, monkeypatch):
    monkeypatch.setattr(devops, "metriques", lambda: None)
    texte = client.get("/devops").get_data(as_text=True)
    assert "Pipeline CI/CD" in texte
    assert "Prometheus injoignable" in texte


def test_page_devops_avec_metriques(lecteur, monkeypatch):
    monkeypatch.setattr(devops, "metriques", lambda: {"cibles": 2, "debit": 1.5, "erreurs": 0.0})
    assert "1.5 req/s" in lecteur.get("/devops").get_data(as_text=True)


def test_pages_protegees_pour_anonymes(anonyme):
    assert anonyme.get("/devops").status_code == 302
    assert anonyme.get("/tableau-de-bord").status_code == 302


def test_metriques_renvoie_none_si_prometheus_absent():
    assert devops.metriques("http://127.0.0.1:9") is None