output "url" {
  description = "Adresse de l'application déployée par Terraform."
  value       = "http://localhost:${var.port_externe}"
}