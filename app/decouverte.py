"""Découverte des machines d'un réseau local, sans outil externe.

Pour chaque adresse, on tente une connexion TCP sur quelques ports courants. Un port
ouvert, ou un refus de connexion, prouve qu'une machine répond. Une machine dont le
pare-feu ignore tout (aucune réponse) n'est pas détectée : c'est une limite de la méthode.
"""

import ipaddress
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

PORTS = {
    22: "SSH",
    80: "HTTP",
    139: "NetBIOS",
    443: "HTTPS",
    445: "SMB",
    3389: "RDP",
    8080: "HTTP-alt",
}
MAX_ADRESSES = 256
DELAI = 0.5  # secondes d'attente par connexion
VERROU = threading.Lock()  # un seul scan à la fois (par processus)


class ReseauInvalide(ValueError):
    """Le réseau demandé est refusé (format, public, trop grand…)."""


class ScanEnCours(RuntimeError):
    """Un autre scan est déjà en train de tourner."""


def valider_reseau(texte):
    """Accepte un réseau IPv4 privé d'au plus 256 adresses, sinon lève ReseauInvalide."""
    try:
        reseau = ipaddress.ip_network(texte.strip(), strict=False)
    except ValueError:
        raise ReseauInvalide("Réseau invalide : écris par exemple 192.168.1.0/24.") from None
    if reseau.version != 4:
        raise ReseauInvalide("Seuls les réseaux IPv4 sont acceptés.")
    if (
        not reseau.is_private
        or reseau.is_loopback
        or reseau.is_link_local
        or reseau.is_multicast
        or reseau.is_unspecified
    ):
        raise ReseauInvalide(
            "Seuls les réseaux privés (192.168.x.x, 10.x.x.x, 172.16 à 31.x.x) sont acceptés."
        )
    if reseau.num_addresses > MAX_ADRESSES:
        raise ReseauInvalide(f"Réseau trop grand : {MAX_ADRESSES} adresses au maximum (un /24).")
    return reseau


def scanner_hote(ip, ports=tuple(PORTS), delai=DELAI):
    """Teste les ports d'une adresse. Renvoie (machine_presente, ports_ouverts)."""
    presente, ouverts = False, []
    for port in ports:
        try:
            with socket.create_connection((ip, port), timeout=delai):
                presente = True
                ouverts.append(port)
        except ConnectionRefusedError:
            presente = True
        except OSError:
            pass
    return presente, ouverts


def nom_machine(ip):
    """Nom de la machine par DNS inverse, ou None s'il n'est pas connu."""
    try:
        return socket.gethostbyaddr(ip)[0][:100]
    except OSError:
        return None


def scanner_reseau(reseau):
    """Scanne un réseau déjà validé. Renvoie la liste des machines trouvées, triée par IP."""
    if not VERROU.acquire(blocking=False):
        raise ScanEnCours("Un scan est déjà en cours : réessaie dans quelques secondes.")
    try:
        adresses = [str(h) for h in reseau.hosts()]

        def sonder(ip):
            presente, ports = scanner_hote(ip)
            return {"ip": ip, "nom": nom_machine(ip), "ports": ports} if presente else None

        with ThreadPoolExecutor(max_workers=64) as pool:
            trouvees = [m for m in pool.map(sonder, adresses) if m]
        return sorted(trouvees, key=lambda m: ipaddress.ip_address(m["ip"]))
    finally:
        VERROU.release()