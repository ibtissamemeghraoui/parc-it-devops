import csv
import io
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

import click
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from prometheus_flask_exporter import PrometheusMetrics
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import decouverte
import devops

db = SQLAlchemy()

STATUTS = ("en stock", "affecté", "en panne")
ROLES = ("admin", "lecteur")
NOM_VALIDE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
CHAMPS_OBLIGATOIRES = ("nom", "type", "numero_serie")
CLASSES = {"en stock": "stock", "affecté": "affecte", "en panne": "panne"}
COULEURS = {"en stock": "#22c55e", "affecté": "#38bdf8", "en panne": "#ef4444"}

# Les messages sont choisis côté serveur à partir d'un code : on n'affiche
# jamais un texte venu de l'adresse.
MESSAGES = {
    "ajoute": ("succes", "Équipement ajouté."),
    "modifie": ("succes", "Équipement modifié."),
    "supprime": ("succes", "Équipement supprimé."),
    "doublon": ("erreur", "Ce numéro de série existe déjà."),
    "invalide": ("erreur", "Le nom, le type et le numéro de série sont obligatoires."),
}


class Equipement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    numero_serie = db.Column(db.String(100), unique=True, nullable=False)
    statut = db.Column(db.String(30), nullable=False, default="en stock")
    affectation = db.Column(db.String(100))

    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "type": self.type,
            "numero_serie": self.numero_serie,
            "statut": self.statut,
            "affectation": self.affectation,
        }


class Utilisateur(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom_utilisateur = db.Column(db.String(50), unique=True, nullable=False)
    mot_de_passe_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="lecteur")

    def definir_mot_de_passe(self, mot_de_passe):
        self.mot_de_passe_hash = generate_password_hash(mot_de_passe)

    def verifier_mot_de_passe(self, mot_de_passe):
        return check_password_hash(self.mot_de_passe_hash, mot_de_passe)


class Audit(db.Model):
    """Journal des actions : qui a fait quoi, et quand (heure UTC)."""

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
    utilisateur = db.Column(db.String(50), nullable=False)
    action = db.Column(db.String(30), nullable=False)
    details = db.Column(db.String(255))


def maintenant():
    """Heure UTC sans fuseau, comme les dates stockées en base."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Tentative(db.Model):
    """Échecs de connexion récents, pour limiter les essais de mots de passe."""

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False, default=maintenant, index=True)
    nom = db.Column(db.String(50), nullable=False)
    adresse = db.Column(db.String(45), nullable=False)


def verifier_base():
    """Interroge la base : lève une exception si elle ne répond pas."""
    db.session.execute(text("SELECT 1"))


def base_est_ok():
    try:
        verifier_base()
    except Exception:
        db.session.rollback()
        return False
    return True


def lire_formulaire(form):
    """Nettoie les champs d'un formulaire d'équipement."""
    statut = form.get("statut", "en stock")
    return {
        "nom": form.get("nom", "").strip(),
        "type": form.get("type", "").strip(),
        "numero_serie": form.get("numero_serie", "").strip(),
        "statut": statut if statut in STATUTS else "en stock",
        "affectation": form.get("affectation", "").strip() or None,
    }


def neutraliser(valeur):
    """Protège un export CSV contre l'injection de formules (Excel, LibreOffice)."""
    texte = "" if valeur is None else str(valeur)
    return "'" + texte if texte.startswith(("=", "+", "-", "@")) else texte


def style_donut(compteurs):
    """Construit le dégradé conique qui dessine l'anneau des statuts."""
    total = sum(compteurs.values())
    if total == 0:
        return "conic-gradient(#cbd5e1 0% 100%)"
    morceaux, debut = [], 0.0
    for statut in STATUTS:
        part = compteurs[statut] * 100 / total
        if part:
            morceaux.append(f"{COULEURS[statut]} {debut:.2f}% {debut + part:.2f}%")
            debut += part
    return "conic-gradient(" + ", ".join(morceaux) + ")"


def utilisateur_courant():
    """Renvoie l'utilisateur de la session, ou None s'il n'est pas connecté."""
    user_id = session.get("user_id")
    return db.session.get(Utilisateur, user_id) if user_id else None


def exiger(role=None):
    """Protège une route : connexion obligatoire, et un rôle précis si demandé."""

    def decorateur(vue):
        @wraps(vue)
        def enveloppe(*args, **kwargs):
            utilisateur = utilisateur_courant()
            est_api = request.path.startswith("/api/")
            if utilisateur is None:
                if est_api:
                    return jsonify(erreur="authentification requise"), 401
                return redirect(url_for("login"))
            if role and utilisateur.role != role:
                if est_api:
                    return jsonify(erreur="droits insuffisants"), 403
                abort(403)
            return vue(*args, **kwargs)

        return enveloppe

    return decorateur


def journaliser(action, details=None, auteur=None):
    """Ajoute une entrée au journal, dans la même transaction que l'action."""
    if auteur is None:
        courant = utilisateur_courant()
        auteur = courant.nom_utilisateur if courant else "anonyme"
    db.session.add(Audit(utilisateur=auteur, action=action, details=(details or "")[:255] or None))


def changements(eq, nouveaux):
    """Décrit ce qui change entre un équipement et ses nouvelles valeurs."""
    morceaux = []
    for cle, valeur in nouveaux.items():
        ancien = getattr(eq, cle)
        if (ancien or None) != (valeur or None):
            morceaux.append(f"{cle} : {ancien or '—'} → {valeur or '—'}")
    return ", ".join(morceaux)


def attente(requete, maximum, fenetre):
    """Minutes à attendre si `maximum` échecs sont déjà dans la fenêtre, sinon 0."""
    tentatives = requete.order_by(Tentative.date).all()
    if len(tentatives) < maximum:
        return 0
    expiration = tentatives[len(tentatives) - maximum].date + fenetre
    secondes = (expiration - maintenant()).total_seconds()
    return max(1, math.ceil(secondes / 60)) if secondes > 0 else 0


def minutes_a_attendre(nom, adresse, cfg):
    """Deux limites : par compte depuis une adresse, et par adresse seule."""
    fenetre = timedelta(minutes=cfg["BLOCAGE_MINUTES"])
    recentes = Tentative.query.filter(Tentative.date >= maintenant() - fenetre)
    return max(
        attente(recentes.filter_by(nom=nom, adresse=adresse), cfg["TENTATIVES_MAX"], fenetre),
        attente(recentes.filter_by(adresse=adresse), cfg["TENTATIVES_MAX_ADRESSE"], fenetre),
    )


def create_app(config=None):
    app = Flask(__name__)
    # Derrière Nginx : lit l'adresse du visiteur dans X-Forwarded-For (un seul proxy).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
    # La connexion à la base et la clé secrète viennent de variables
    # d'environnement, jamais du code.
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///parc.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
    # Mettre INSCRIPTION_OUVERTE=0 dans .env pour fermer les inscriptions.
    app.config["INSCRIPTION_OUVERTE"] = os.getenv("INSCRIPTION_OUVERTE", "1") == "1"
    # Limitation des essais de connexion : échecs permis pendant la fenêtre de blocage.
    app.config["TENTATIVES_MAX"] = 5
    app.config["TENTATIVES_MAX_ADRESSE"] = 30
    app.config["BLOCAGE_MINUTES"] = 15
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY manquante : définis-la dans le fichier .env")

    db.init_app(app)

    # Métriques Prometheus (route /metrics), désactivées pendant les tests.
    if not app.config.get("TESTING"):
        PrometheusMetrics(app)

    with app.app_context():
        db.create_all()

    @app.context_processor
    def contexte():
        ok = base_est_ok()
        return {
            "base_ok": ok,
            "utilisateur": utilisateur_courant() if ok else None,
            "inscription_ouverte": app.config["INSCRIPTION_OUVERTE"],
        }

    @app.get("/health")
    def health():
        return "OK", 200

    @app.get("/ready")
    def ready():
        try:
            verifier_base()
        except Exception:
            return jsonify(status="base indisponible"), 503
        return jsonify(status="ok"), 200

    # ---------- Connexion ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            nom = request.form.get("nom_utilisateur", "").strip()
            mot_de_passe = request.form.get("mot_de_passe", "")
            adresse = (request.remote_addr or "inconnue")[:45]
            minutes = minutes_a_attendre(nom[:50], adresse, app.config)
            if minutes:
                # Même un bon mot de passe est refusé : sinon l'essai continuerait.
                erreur = f"Trop de tentatives. Réessaie dans {minutes} minute(s)."
                return render_template("login.html", erreur=erreur), 429
            utilisateur = Utilisateur.query.filter_by(nom_utilisateur=nom).first()
            if utilisateur is None or not utilisateur.verifier_mot_de_passe(mot_de_passe):
                limite = maintenant() - timedelta(minutes=app.config["BLOCAGE_MINUTES"])
                Tentative.query.filter(Tentative.date < limite).delete()
                db.session.add(Tentative(nom=nom[:50], adresse=adresse))
                journaliser("connexion échouée", f"compte : {nom[:40]}", auteur="anonyme")
                if minutes_a_attendre(nom[:50], adresse, app.config):
                    journaliser("verrouillage", f"compte : {nom[:40]} ({adresse})", auteur="anonyme")
                db.session.commit()
                return render_template("login.html", erreur="Identifiants invalides."), 401
            session.clear()
            session["user_id"] = utilisateur.id
            session.permanent = True
            Tentative.query.filter_by(nom=nom[:50], adresse=adresse).delete()
            journaliser("connexion", auteur=utilisateur.nom_utilisateur)
            db.session.commit()
            return redirect(url_for("index"))
        return render_template("login.html", erreur=None)

    @app.post("/logout")
    def logout():
        if utilisateur_courant():
            journaliser("déconnexion")
            db.session.commit()
        session.clear()
        return redirect(url_for("login"))

    @app.route("/inscription", methods=["GET", "POST"])
    def inscription():
        if not app.config["INSCRIPTION_OUVERTE"]:
            abort(404)
        if request.method == "POST":
            nom = request.form.get("nom_utilisateur", "").strip()
            mot_de_passe = request.form.get("mot_de_passe", "")
            confirmation = request.form.get("confirmation", "")
            erreur = None
            if not NOM_VALIDE.match(nom):
                erreur = "Le nom d'utilisateur doit faire 3 à 30 caractères (lettres, chiffres, . _ -)."
            elif len(mot_de_passe) < 8:
                erreur = "Le mot de passe doit faire au moins 8 caractères."
            elif mot_de_passe != confirmation:
                erreur = "Les deux mots de passe ne sont pas identiques."
            elif Utilisateur.query.filter(
                func.lower(Utilisateur.nom_utilisateur) == nom.lower()
            ).first():
                erreur = "Ce nom d'utilisateur est déjà pris."
            if erreur:
                return render_template("inscription.html", erreur=erreur, nom=nom), 400
            # Le rôle est toujours « lecteur » : on ne s'attribue jamais admin soi-même.
            utilisateur = Utilisateur(nom_utilisateur=nom, role="lecteur")
            utilisateur.definir_mot_de_passe(mot_de_passe)
            db.session.add(utilisateur)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                erreur = "Ce nom d'utilisateur est déjà pris."
                return render_template("inscription.html", erreur=erreur, nom=nom), 400
            session.clear()
            session["user_id"] = utilisateur.id
            session.permanent = True
            journaliser("inscription", auteur=nom)
            db.session.commit()
            return redirect(url_for("index"))
        return render_template("inscription.html", erreur=None, nom="")

    # ---------- Pages web ----------
    @app.get("/")
    @exiger()
    def index():
        recherche = request.args.get("q", "").strip()
        statut = request.args.get("statut", "")

        requete = Equipement.query
        if recherche:
            motif = f"%{recherche}%"
            requete = requete.filter(
                or_(
                    Equipement.nom.ilike(motif),
                    Equipement.type.ilike(motif),
                    Equipement.numero_serie.ilike(motif),
                    Equipement.affectation.ilike(motif),
                )
            )
        if statut in STATUTS:
            requete = requete.filter_by(statut=statut)

        equipements = requete.order_by(Equipement.id).all()
        compteurs = {s: Equipement.query.filter_by(statut=s).count() for s in STATUTS}

        lignes = (
            db.session.query(Equipement.type, func.count(Equipement.id))
            .group_by(Equipement.type)
            .order_by(func.count(Equipement.id).desc(), Equipement.type)
            .limit(5)
            .all()
        )
        maximum = lignes[0][1] if lignes else 1
        par_type = [(t, n, round(n * 100 / maximum)) for t, n in lignes]

        return render_template(
            "index.html",
            equipements=equipements,
            statuts=STATUTS,
            classes=CLASSES,
            compteurs=compteurs,
            total=Equipement.query.count(),
            donut=style_donut(compteurs),
            par_type=par_type,
            recherche=recherche,
            statut=statut,
            message=MESSAGES.get(request.args.get("msg")),
        )

    @app.get("/export.csv")
    @exiger()
    def export_csv():
        sortie = io.StringIO()
        ecrivain = csv.writer(sortie)
        ecrivain.writerow(["id", "nom", "type", "numero_serie", "statut", "affectation"])
        for e in Equipement.query.order_by(Equipement.id).all():
            ecrivain.writerow(
                [
                    e.id,
                    neutraliser(e.nom),
                    neutraliser(e.type),
                    neutraliser(e.numero_serie),
                    neutraliser(e.statut),
                    neutraliser(e.affectation),
                ]
            )
        return Response(
            sortie.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=equipements.csv"},
        )

    @app.post("/ajouter")
    @exiger("admin")
    def ajouter():
        champs = lire_formulaire(request.form)
        if not all(champs[c] for c in CHAMPS_OBLIGATOIRES):
            return redirect(url_for("index", msg="invalide"))
        db.session.add(Equipement(**champs))
        journaliser("ajout", f"{champs['nom']} ({champs['numero_serie']})")
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return redirect(url_for("index", msg="doublon"))
        return redirect(url_for("index", msg="ajoute"))

    @app.get("/modifier/<int:eq_id>")
    @exiger("admin")
    def modifier_formulaire(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        return render_template("modifier.html", e=eq, statuts=STATUTS)

    @app.post("/modifier/<int:eq_id>")
    @exiger("admin")
    def modifier(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        champs = lire_formulaire(request.form)
        if not all(champs[c] for c in CHAMPS_OBLIGATOIRES):
            return redirect(url_for("index", msg="invalide"))
        diff = changements(eq, champs)
        for cle, valeur in champs.items():
            setattr(eq, cle, valeur)
        if diff:
            journaliser("modification", f"{eq.nom} — {diff}")
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return redirect(url_for("index", msg="doublon"))
        return redirect(url_for("index", msg="modifie"))

    @app.post("/supprimer/<int:eq_id>")
    @exiger("admin")
    def supprimer(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        journaliser("suppression", f"{eq.nom} ({eq.numero_serie})")
        db.session.delete(eq)
        db.session.commit()
        return redirect(url_for("index", msg="supprime"))

    # ---------- API JSON (CRUD complet) ----------
    @app.get("/api/equipements")
    @exiger()
    def api_lister():
        return jsonify([e.to_dict() for e in Equipement.query.all()])

    @app.post("/api/equipements")
    @exiger("admin")
    def api_creer():
        data = request.get_json(silent=True) or {}
        manquants = [c for c in CHAMPS_OBLIGATOIRES if not data.get(c)]
        if manquants:
            return jsonify(erreur="champs manquants", champs=manquants), 400
        statut = data.get("statut", "en stock")
        if statut not in STATUTS:
            return jsonify(erreur="statut invalide", valeurs=list(STATUTS)), 400
        eq = Equipement(
            nom=data["nom"],
            type=data["type"],
            numero_serie=data["numero_serie"],
            statut=statut,
            affectation=data.get("affectation"),
        )
        db.session.add(eq)
        journaliser("ajout", f"{eq.nom} ({eq.numero_serie})")
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(erreur="numero de serie deja utilise"), 409
        return jsonify(eq.to_dict()), 201

    @app.get("/api/equipements/<int:eq_id>")
    @exiger()
    def api_lire(eq_id):
        return jsonify(db.get_or_404(Equipement, eq_id).to_dict())

    @app.put("/api/equipements/<int:eq_id>")
    @exiger("admin")
    def api_modifier(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        data = request.get_json(silent=True) or {}
        if "statut" in data and data["statut"] not in STATUTS:
            return jsonify(erreur="statut invalide", valeurs=list(STATUTS)), 400
        champs = ("nom", "type", "numero_serie", "statut", "affectation")
        diff = changements(eq, {c: data[c] for c in champs if c in data})
        for champ in champs:
            if champ in data:
                setattr(eq, champ, data[champ])
        if diff:
            journaliser("modification", f"{eq.nom} — {diff}")
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(erreur="numero de serie deja utilise"), 409
        return jsonify(eq.to_dict())

    @app.delete("/api/equipements/<int:eq_id>")
    @exiger("admin")
    def api_supprimer(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        journaliser("suppression", f"{eq.nom} ({eq.numero_serie})")
        db.session.delete(eq)
        db.session.commit()
        return "", 204

    # ---------- Découverte du réseau ----------
    @app.route("/decouverte", methods=["GET", "POST"])
    @exiger("admin")
    def decouverte_page():
        reseau_texte = request.form.get("reseau", "").strip()
        resultats, erreur, statut_http = None, None, 200
        if request.method == "POST":
            try:
                reseau = decouverte.valider_reseau(reseau_texte)
                resultats = decouverte.scanner_reseau(reseau)
            except decouverte.ReseauInvalide as e:
                erreur, statut_http = str(e), 400
            except decouverte.ScanEnCours as e:
                erreur, statut_http = str(e), 429
            else:
                journaliser("scan réseau", f"{reseau} : {len(resultats)} machine(s)")
                db.session.commit()
                connus = {
                    e.numero_serie
                    for e in Equipement.query.filter(Equipement.numero_serie.like("IP-%"))
                }
                for machine in resultats:
                    machine["connu"] = f"IP-{machine['ip']}" in connus
        page = render_template(
            "decouverte.html",
            reseau=reseau_texte,
            resultats=resultats,
            erreur=erreur,
            noms=decouverte.PORTS,
        )
        return page, statut_http

    @app.post("/decouverte/ajouter")
    @exiger("admin")
    def decouverte_ajouter():
        nom = request.form.get("nom", "").strip()[:100]
        try:
            reseau = decouverte.valider_reseau(request.form.get("ip", ""))
        except decouverte.ReseauInvalide:
            abort(400)
        if reseau.num_addresses != 1:
            abort(400)
        ip = str(reseau.network_address)
        db.session.add(
            Equipement(
                nom=nom or f"Machine {ip}",
                type="Découvert",
                numero_serie=f"IP-{ip}",
                statut="en stock",
            )
        )
        journaliser("ajout", f"{nom or ip} (IP-{ip}) depuis la découverte réseau")
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return redirect(url_for("index", msg="doublon"))
        return redirect(url_for("index", msg="ajoute"))

    # ---------- Tableau de bord et page DevOps ----------
    @app.get("/tableau-de-bord")
    @exiger()
    def tableau_de_bord():
        debut = (maintenant() - timedelta(days=13)).replace(hour=0, minute=0, second=0, microsecond=0)
        par_jour = {(debut + timedelta(days=i)).date(): 0 for i in range(14)}
        par_action = {}
        for entree in Audit.query.filter(Audit.date >= debut).all():
            par_jour[entree.date.date()] = par_jour.get(entree.date.date(), 0) + 1
            par_action[entree.action] = par_action.get(entree.action, 0) + 1
        maximum = max(par_jour.values()) or 1
        jours = [(j.strftime("%d/%m"), n, round(n * 100 / maximum)) for j, n in par_jour.items()]
        classes_actions = sorted(par_action.items(), key=lambda x: -x[1])[:6]
        plus_haut = classes_actions[0][1] if classes_actions else 1
        actions = [(a, n, round(n * 100 / plus_haut)) for a, n in classes_actions]
        total = Equipement.query.count()
        pannes = Equipement.query.filter_by(statut="en panne").count()
        admin = utilisateur_courant().role == "admin"
        return render_template(
            "tableau_de_bord.html",
            jours=jours,
            actions=actions,
            total=total,
            pannes=pannes,
            taux=round(pannes * 100 / total) if total else 0,
            comptes=Utilisateur.query.count(),
            actions_24h=Audit.query.filter(Audit.date >= maintenant() - timedelta(hours=24)).count(),
            recentes=Audit.query.order_by(Audit.id.desc()).limit(8).all() if admin else None,
        )

    @app.get("/devops")
    @exiger()
    def devops_page():
        debut = time.perf_counter()
        base_ok = base_est_ok()
        latence = round((time.perf_counter() - debut) * 1000)
        return render_template(
            "devops.html",
            infos=devops.informations(db),
            metriques=devops.metriques(),
            latence=latence,
        )

    # ---------- Pages d'erreur ----------
    erreurs = {
        400: ("Requête invalide", "La demande n'a pas pu être comprise."),
        403: ("Accès refusé", "Ton compte n'a pas les droits nécessaires pour cette page."),
        404: ("Page introuvable", "Cette page n'existe pas ou a été déplacée."),
        405: ("Action non autorisée", "Cette page n'accepte pas cette action."),
    }

    def page_erreur(e):
        titre, texte = erreurs[e.code]
        if request.path.startswith("/api/"):
            return jsonify(erreur=titre), e.code
        page = render_template("erreur.html", code=e.code, titre=titre, texte=texte)
        return page, e.code

    for code in erreurs:
        app.register_error_handler(code, page_erreur)

    # ---------- Journal d'audit ----------
    @app.get("/audit")
    @exiger("admin")
    def audit():
        entrees = Audit.query.order_by(Audit.id.desc()).limit(200).all()
        return render_template("audit.html", entrees=entrees)

    # ---------- Commande pour créer les comptes ----------
    @app.cli.command("creer-utilisateur")
    @click.argument("nom_utilisateur")
    @click.option("--role", type=click.Choice(ROLES), default="lecteur", show_default=True)
    @click.password_option()
    def creer_utilisateur(nom_utilisateur, role, password):
        """Crée un utilisateur (le mot de passe est demandé sans écho)."""
        if len(password) < 8:
            raise click.ClickException("Le mot de passe doit faire au moins 8 caractères.")
        if Utilisateur.query.filter_by(nom_utilisateur=nom_utilisateur).first():
            raise click.ClickException("Cet utilisateur existe déjà.")
        utilisateur = Utilisateur(nom_utilisateur=nom_utilisateur, role=role)
        utilisateur.definir_mot_de_passe(password)
        db.session.add(utilisateur)
        db.session.commit()
        click.echo(f"Utilisateur {nom_utilisateur} créé avec le rôle {role}.")

    @app.cli.command("changer-role")
    @click.argument("nom_utilisateur")
    @click.argument("role", type=click.Choice(ROLES))
    def changer_role(nom_utilisateur, role):
        """Change le rôle d'un utilisateur existant (par exemple pour le passer admin)."""
        utilisateur = Utilisateur.query.filter_by(nom_utilisateur=nom_utilisateur).first()
        if utilisateur is None:
            raise click.ClickException("Utilisateur introuvable.")
        utilisateur.role = role
        journaliser("changement de rôle", f"{nom_utilisateur} → {role}", auteur="ligne de commande")
        db.session.commit()
        click.echo(f"{nom_utilisateur} a maintenant le rôle {role}.")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)