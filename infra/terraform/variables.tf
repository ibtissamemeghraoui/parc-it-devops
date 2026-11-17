variable "docker_host" {
  description = "Adresse du démon Docker (sous Windows : npipe:////./pipe/docker_engine)."
  type        = string
  default     = "unix:///var/run/docker.sock"
}

variable "postgres_user" {
  description = "Nom de l'utilisateur PostgreSQL."
  type        = string
  default     = "parcit"
}

variable "postgres_db" {
  description = "Nom de la base PostgreSQL."
  type        = string
  default     = "parc"
}

variable "postgres_password" {
  description = "Mot de passe PostgreSQL (au moins 8 caractères)."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.postgres_password) >= 8
    error_message = "Le mot de passe PostgreSQL doit faire au moins 8 caractères."
  }
}

variable "secret_key" {
  description = "Clé de signature des sessions Flask (au moins 32 caractères)."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.secret_key) >= 32
    error_message = "La clé secrète doit faire au moins 32 caractères."
  }
}

variable "port_externe" {
  description = "Port local de l'application (8081 pour ne pas gêner Docker Compose sur 8080)."
  type        = number
  default     = 8081
}

variable "app_image" {
  description = "Image de l'application, à construire avant : docker build -t parc-it:terraform -f docker/Dockerfile ."
  type        = string
  default     = "parc-it:terraform"
}
