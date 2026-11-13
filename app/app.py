import csv
import io
import os

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from prometheus_flask_exporter import PrometheusMetrics
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError

db = SQLAlchemy()

STATUTS = ("en stock", "affecté", "en panne")
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


def create_app(config=None):
    app = Flask(__name__)
    # La connexion à la base vient d'une variable d'environnement, jamais du code.
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///parc.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    if config:
        app.config.update(config)

    db.init_app(app)

    # Métriques Prometheus (route /metrics), désactivées pendant les tests.
    if not app.config.get("TESTING"):
        PrometheusMetrics(app)

    with app.app_context():
        db.create_all()

    @app.context_processor
    def etat_systeme():
        return {"base_ok": base_est_ok()}

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

    # ---------- Pages web ----------
    @app.get("/")
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
    def export_csv():
        sortie = io.StringIO()
        ecrivain = csv.writer(sortie)
        ecrivain.writerow(["id", "nom", "type", "numero_serie", "statut", "affectation"])
        for e in Equipement.query.order_by(Equipement.id).all():
            ecrivain.writerow(
                [e.id, neutraliser(e.nom), neutraliser(e.type), neutraliser(e.numero_serie),
                 neutraliser(e.statut), neutraliser(e.affectation)]
            )
        return Response(
            sortie.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=equipements.csv"},
        )

    @app.post("/ajouter")
    def ajouter():
        champs = lire_formulaire(request.form)
        if not all(champs[c] for c in CHAMPS_OBLIGATOIRES):
            return redirect(url_for("index", msg="invalide"))
        db.session.add(Equipement(**champs))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return redirect(url_for("index", msg="doublon"))
        return redirect(url_for("index", msg="ajoute"))

    @app.get("/modifier/<int:eq_id>")
    def modifier_formulaire(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        return render_template("modifier.html", e=eq, statuts=STATUTS)

    @app.post("/modifier/<int:eq_id>")
    def modifier(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        champs = lire_formulaire(request.form)
        if not all(champs[c] for c in CHAMPS_OBLIGATOIRES):
            return redirect(url_for("index", msg="invalide"))
        for cle, valeur in champs.items():
            setattr(eq, cle, valeur)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return redirect(url_for("index", msg="doublon"))
        return redirect(url_for("index", msg="modifie"))

    @app.post("/supprimer/<int:eq_id>")
    def supprimer(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        db.session.delete(eq)
        db.session.commit()
        return redirect(url_for("index", msg="supprime"))

    # ---------- API JSON (CRUD complet) ----------
    @app.get("/api/equipements")
    def api_lister():
        return jsonify([e.to_dict() for e in Equipement.query.all()])

    @app.post("/api/equipements")
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
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(erreur="numero de serie deja utilise"), 409
        return jsonify(eq.to_dict()), 201

    @app.get("/api/equipements/<int:eq_id>")
    def api_lire(eq_id):
        return jsonify(db.get_or_404(Equipement, eq_id).to_dict())

    @app.put("/api/equipements/<int:eq_id>")
    def api_modifier(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        data = request.get_json(silent=True) or {}
        if "statut" in data and data["statut"] not in STATUTS:
            return jsonify(erreur="statut invalide", valeurs=list(STATUTS)), 400
        for champ in ("nom", "type", "numero_serie", "statut", "affectation"):
            if champ in data:
                setattr(eq, champ, data[champ])
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(erreur="numero de serie deja utilise"), 409
        return jsonify(eq.to_dict())

    @app.delete("/api/equipements/<int:eq_id>")
    def api_supprimer(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        db.session.delete(eq)
        db.session.commit()
        return "", 204

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)