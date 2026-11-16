# Les mêmes éléments que docker-compose.yml (réseau, volume, base, application, Nginx),
# décrits en Terraform. La supervision (Prometheus, Grafana) reste dans Docker Compose.

resource "docker_network" "backend" {
  name = "parc-it-backend"
}

resource "docker_volume" "pgdata" {
  name = "parc-it-pgdata"
}

resource "docker_image" "postgres" {
  name         = "postgres:17-alpine"
  keep_locally = true
}

resource "docker_image" "nginx" {
  name         = "nginx:1.27-alpine"
  keep_locally = true
}

# L'image de l'application est construite à part (voir le README) :
#   docker build -t parc-it:terraform -f docker/Dockerfile .
# Terraform déploie cette image existante, il ne la construit pas : si l'image est
# reconstruite, un nouveau « terraform apply » remplace le conteneur.
data "docker_image" "app" {
  name = var.app_image
}

resource "docker_container" "db" {
  name         = "parc-it-db"
  image        = docker_image.postgres.image_id
  restart      = "unless-stopped"
  wait         = true
  wait_timeout = 60

  env = [
    "POSTGRES_USER=${var.postgres_user}",
    "POSTGRES_PASSWORD=${var.postgres_password}",
    "POSTGRES_DB=${var.postgres_db}",
  ]

  volumes {
    volume_name    = docker_volume.pgdata.name
    container_path = "/var/lib/postgresql/data"
  }

  healthcheck {
    test     = ["CMD-SHELL", "pg_isready -U ${var.postgres_user} -d ${var.postgres_db}"]
    interval = "5s"
    timeout  = "5s"
    retries  = 10
  }

  networks_advanced {
    name    = docker_network.backend.name
    aliases = ["db"]
  }
}

resource "docker_container" "app" {
  name    = "parc-it-app"
  image   = data.docker_image.app.id
  restart = "unless-stopped"

  env = [
    "DATABASE_URL=postgresql+psycopg://${var.postgres_user}:${urlencode(var.postgres_password)}@db:5432/${var.postgres_db}",
    "SECRET_KEY=${var.secret_key}",
  ]

  networks_advanced {
    name    = docker_network.backend.name
    aliases = ["app"]
  }

  # Terraform attend que la base soit « healthy » (wait = true) avant de créer l'application.
  depends_on = [docker_container.db]
}

resource "docker_container" "nginx" {
  name    = "parc-it-nginx"
  image   = docker_image.nginx.image_id
  restart = "unless-stopped"

  ports {
    internal = 80
    external = var.port_externe
  }

  # La configuration Nginx du projet est copiée dans le conteneur.
  upload {
    file    = "/etc/nginx/conf.d/default.conf"
    content = file("${path.root}/../../docker/nginx.conf")
  }

  networks_advanced {
    name = docker_network.backend.name
  }

  depends_on = [docker_container.app]
}
