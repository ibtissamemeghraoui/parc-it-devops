import os

from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from prometheus_flask_exporter import PrometheusMetrics

db = SQLAlchemy()


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

    @app.get("/health")
    def health():
        return "OK", 200

    @app.get("/")
    def index():
        equipements = Equipement.query.order_by(Equipement.id).all()
        return render_template("index.html", equipements=equipements)

    @app.post("/ajouter")
    def ajouter():
        eq = Equipement(
            nom=request.form["nom"],
            type=request.form["type"],
            numero_serie=request.form["numero_serie"],
            statut=request.form.get("statut", "en stock"),
            affectation=request.form.get("affectation") or None,
        )
        db.session.add(eq)
        db.session.commit()
        return redirect(url_for("index"))

    @app.post("/supprimer/<int:eq_id>")
    def supprimer(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        db.session.delete(eq)
        db.session.commit()
        return redirect(url_for("index"))
        # ---------- API JSON (CRUD complet) ----------
    @app.get("/api/equipements")
    def api_lister():
        return jsonify([e.to_dict() for e in Equipement.query.all()])

    @app.post("/api/equipements")
    def api_creer():
        data = request.get_json()
        eq = Equipement(
            nom=data["nom"],
            type=data["type"],
            numero_serie=data["numero_serie"],
            statut=data.get("statut", "en stock"),
            affectation=data.get("affectation"),
        )
        db.session.add(eq)
        db.session.commit()
        return jsonify(eq.to_dict()), 201

    @app.get("/api/equipements/<int:eq_id>")
    def api_lire(eq_id):
        return jsonify(db.get_or_404(Equipement, eq_id).to_dict())

    @app.put("/api/equipements/<int:eq_id>")
    def api_modifier(eq_id):
        eq = db.get_or_404(Equipement, eq_id)
        data = request.get_json()
        for champ in ("nom", "type", "numero_serie", "statut", "affectation"):
            if champ in data:
                setattr(eq, champ, data[champ])
        db.session.commit()
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