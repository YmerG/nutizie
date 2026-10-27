"""Cherche les flux RSS/Atom d'une liste de sites et vérifie qu'ils répondent.

Usage : python3 outils/decouvrir_flux.py https://site1 https://site2 ...
Pour chaque site : lit les <link rel="alternate"> de la page d'accueil, essaie
quelques chemins courants, et affiche les flux valides avec leur nombre d'articles.
"""
import re
import sys
import urllib.request
from urllib.parse import urljoin

UA = "Mozilla/5.0 (AgregateurCorse; maquette) Python-urllib"
CHEMINS = ["/feed", "/rss", "/rss.xml", "/feed.xml", "/xml/syndication.rss", "/index.xml", "/atom.xml", "/feed/rss"]


def lire(url, maxi=600_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.geturl(), r.status, r.read(maxi).decode("utf-8", "replace")


def est_flux(texte):
    debut = texte[:3000].lower()
    return "<rss" in debut or "<feed" in debut or "<rdf:rdf" in debut


def nb_articles(texte):
    return len(re.findall(r"<item[\s>]|<entry[\s>]", texte))


for site in sys.argv[1:]:
    candidats = [site]  # l'adresse donnée est peut-être déjà un flux
    try:
        final, _, page = lire(site)
        for balise in re.findall(r"<link[^>]+>", page, re.I):
            if re.search(r"rss\+xml|atom\+xml", balise, re.I):
                href = re.search(r'href=["\']([^"\']+)', balise, re.I)
                if href:
                    candidats.append(urljoin(final, href.group(1).replace("&amp;", "&")))
    except Exception as e:
        print(f"## {site}  accueil injoignable : {e}")
        final = site
    candidats += [urljoin(final, c) for c in CHEMINS]
    trouves = []
    for c in dict.fromkeys(candidats):
        try:
            u, st, t = lire(c)
            if est_flux(t):
                trouves.append(f"   OK {nb_articles(t):3d} articles  {u}")
        except Exception:
            pass
        if len(trouves) >= 3:
            break
    print(f"## {site}")
    print("\n".join(trouves) if trouves else "   aucun flux trouvé")
