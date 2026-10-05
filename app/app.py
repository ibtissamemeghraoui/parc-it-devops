import os

from flask import Flask, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Equipement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    numero_serie = db.Column(db.String(100), unique=True, nullable=False)
    statut = db.Column(db.String(30), nullable=False, default="en stock")
    affectation = db.Column(db.String(100))


def create_app(config=None):
    app = Flask(__name__)
    # La connexion à la base vient d'une variable d'environnement, jamais du code.
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///parc.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    if config:
        app.config.update(config)

    db.init_app(app)

    with app.app_context():
        db.create_all()

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

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)