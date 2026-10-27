"""Collecte les flux RSS/Atom de sources.json et génère le site statique dans site/.

Usage : python3 collecte.py
Produit site/index.html (articles déjà dans la page : s'ouvre sans serveur, même sans JS)
et site/data.json (les mêmes données, pour d'autres usages).
Bibliothèque standard uniquement.
"""
import html
import json
import re
import sys
import time
import urllib.request
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:  # pas de base des fuseaux : on reste en UTC
    PARIS = timezone.utc

ICI = Path(__file__).resolve().parent
SORTIE = ICI / "site"
CACHE = ICI / "cache"  # titres déjà lus (gardé entre deux collectes, aussi sur GitHub Actions)
PAUSE = 2             # secondes entre deux pages d'un même site
UA = "Mozilla/5.0 (compatible; AgregateurCorse/1.0; lecteur de flux RSS)"
PAR_SOURCE = 25      # pour qu'une source très active n'écrase pas les autres
EXTRAIT_MAX = 200    # caractères : un extrait court, jamais le texte complet
SITEMAP = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
NEWS = "{http://www.google.com/schemas/sitemap-news/0.9}"
MOIS = "janv. févr. mars avr. mai juin juil. août sept. oct. nov. déc.".split()


def local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def texte(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def sans_html(s):
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"The post .*? appeared first on .*?\.?$", "", s.strip())  # pied WordPress
    s = re.sub(r"L’article .*? est apparu en premier sur .*?\.?$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def couper(s, n=EXTRAIT_MAX):
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip(" ,;:.-–") + "…"


def lire_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=PARIS)
    return d.astimezone(timezone.utc)


def url_web(u, https_seul=False):
    u = html.unescape((u or "").strip())
    if u.startswith("//"):
        u = "https:" + u
    ok = u.startswith("https://") or (not https_seul and u.startswith("http://"))
    return u if ok else ""  # écarte javascript:, data:, chemins relatifs…


def image_de(item, corps):
    for el in item.iter():
        nom = local(el.tag)
        if nom == "enclosure" and el.get("type", "").startswith("image"):
            u = url_web(el.get("url"), True)
        elif nom in ("thumbnail", "content") and el.get("url"):  # media:thumbnail / media:content
            typ = el.get("medium", "") + el.get("type", "")
            if typ and "image" not in typ:
                continue
            u = url_web(el.get("url"), True)
        else:
            continue
        if u:
            return u
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)", corps, re.I)
    return url_web(m.group(1), True) if m else ""


def lire_plan(racine):
    """Plan de site (sitemap) : titre, date et lien seulement, ni image ni extrait."""
    for url in racine:  # espaces de noms explicites : <image:loc> et <image:title> ne doivent pas être pris
        lien = url_web(texte(url.find(SITEMAP + "loc")))
        # shortcut: sans news:title, titre tiré de l'adresse (sans accents) ; à remplacer si le site ouvre un flux RSS
        titre = (sans_html(texte(url.find(f"{NEWS}news/{NEWS}title")))
                 or unquote(lien.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ").capitalize())
        # shortcut: sans news:publication_date, lastmod = date de dernière modification, pas de publication
        date = lire_date(texte(url.find(f"{NEWS}news/{NEWS}publication_date")) or texte(url.find(SITEMAP + "lastmod")))
        if lien and titre:
            yield {"titre": titre, "lien": lien, "date": date, "image": "", "extrait": ""}


def lire_flux(contenu):
    racine = ET.fromstring(contenu)
    if local(racine.tag) == "urlset":
        yield from lire_plan(racine)
        return
    for item in racine.iter():
        if local(item.tag) not in ("item", "entry"):
            continue
        champs = {}
        for el in item:
            champs.setdefault(local(el.tag), el)
        lien = ""
        for el in item:
            if local(el.tag) == "link":
                if el.get("href") and el.get("rel", "alternate") == "alternate":
                    lien = el.get("href")
                    break
                if texte(el):
                    lien = texte(el)
                    break
        if not lien and "guid" in champs and champs["guid"].get("isPermaLink", "true") != "false":
            lien = texte(champs["guid"])
        lien = url_web(lien)
        titre = sans_html(texte(champs.get("title")))
        if not lien or not titre:
            continue
        corps = ""
        for cle in ("encoded", "content", "description", "summary"):
            if cle in champs and texte(champs[cle]):
                corps = texte(champs[cle])
                if cle in ("description", "summary"):
                    break
        resume = texte(champs.get("description")) or texte(champs.get("summary")) or corps
        date = None
        for cle in ("pubdate", "published", "updated", "date", "issued"):
            if cle in champs:
                date = lire_date(texte(champs[cle]))
                if date:
                    break
        extrait = sans_html(resume)
        if extrait.startswith(titre):  # certains flux répètent le titre puis l'auteur et la date
            extrait = extrait[len(titre):].strip()
            if len(extrait) < 60:
                extrait = ""
        yield {"titre": titre, "lien": lien, "date": date,
               "image": image_de(item, corps), "extrait": couper(extrait)}


def lire(url, maxi=5_000_000, site=""):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        if site and not meme_site(r.geturl(), site):
            raise ValueError("redirection hors du site")
        return r.read(maxi)


# bornes {0,n} : une page piégée ne peut pas faire tourner l'expression pendant des minutes
OG_TITLE = re.compile(r"""<meta\b(?=[^>]{0,500}\bproperty=["']og:title["'])[^>]{0,500}\bcontent=(["'])(.{0,1000}?)\1""", re.I | re.S)
TITLE = re.compile(r"<title[^>]{0,200}>(.{0,1000}?)</title>", re.I | re.S)


def titre_html(page, nom_site):
    """Titre d'une page d'article : og:title, sinon <title>, sans le « - Nom du site » final."""
    s = page.decode("utf-8", "replace")
    m = OG_TITLE.search(s)
    t = sans_html(m.group(2)) if m else ""
    if not t:
        m = TITLE.search(s)
        t = sans_html(m.group(1)) if m else ""
    return re.sub(r"\s*[-–|]\s*" + re.escape(nom_site) + r"\s*$", "", t, flags=re.I)[:300]


def meme_site(a, b):
    return (urlparse(a).hostname or "").removeprefix("www.") == (urlparse(b).hostname or "").removeprefix("www.")


def completer_titres(articles, src):
    """Plan de site sans titres : lit le vrai titre des seuls nouveaux articles, mémorisé dans cache/."""
    fichier = CACHE / f"titres-{src['id']}.json"
    try:
        vus = json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        vus = {}
    if not isinstance(vus, dict):  # cache abîmé : on repart de zéro plutôt que d'arrêter la collecte
        vus = {}
    vus = {k: v for k, v in vus.items() if isinstance(v, str) and v}
    lus, panne = 0, False
    for a in articles:
        if a["lien"] in vus:
            a["titre"] = vus[a["lien"]]
            continue
        if panne or not meme_site(a["lien"], src["flux"]):
            continue  # titre tiré de l'adresse ; un échec est retenté à la collecte suivante
        if lus:
            time.sleep(PAUSE)  # ménage le site : une page à la fois
        lus += 1
        try:
            t = titre_html(lire(a["lien"], 500_000, src["flux"]), src["nom"])
        except Exception:
            panne = True  # le site ne répond pas : on n'insiste pas
            continue
        if t:
            a["titre"] = t
        vus[a["lien"]] = a["titre"]  # page sans titre : on garde celui de l'adresse, sans la redemander
    CACHE.mkdir(exist_ok=True)
    fichier.write_text(json.dumps(vus, ensure_ascii=False, indent=0), encoding="utf-8")
    return lus


def collecter(src):
    try:
        contenu = lire(src["flux"])
        articles = [a for a in lire_flux(contenu) if re.search(src.get("liens", ""), a["lien"])]  # « liens » : garder seulement ces adresses
    except Exception as e:  # une source en panne ne bloque pas les autres
        return src, [], f"{type(e).__name__}: {e}"[:160]
    maintenant = datetime.now(timezone.utc)
    for a in articles:  # date dans le futur (article programmé mais déjà dans le flux) : il est lisible maintenant
        if a["date"] and a["date"] > maintenant:
            a["date"] = maintenant
    articles.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    articles = articles[:PAR_SOURCE]
    if src.get("titres_pages"):
        completer_titres(articles, src)
    return src, articles, ""


def date_fr(d):
    d = d.astimezone(PARIS)
    jour = f"{d.day} {MOIS[d.month - 1]}"
    return jour if (d.hour, d.minute) == (0, 0) else f"{jour} {d.hour} h {d:%M}"  # minuit pile : le flux ne donne que le jour


def carte(a, src):
    e = html.escape
    img = (f'<img src="{e(a["image"])}" alt="" width="640" height="360" loading="lazy" decoding="async" referrerpolicy="no-referrer">'
           if a["image"] else "")
    quand = (f'<time datetime="{a["date"].isoformat()}">{date_fr(a["date"])}</time>' if a["date"] else "date inconnue")
    extrait = f'<p class="extrait">{e(a["extrait"])}</p>' if a["extrait"] and a["extrait"] != a["titre"] else ""
    return (f'<article class="carte" data-source="{src["id"]}" style="--c:{e(src["couleur"])}">{img}'
            f'<div class="corps"><p class="meta"><span class="pastille" aria-hidden="true"></span>{e(src["nom"])}<span aria-hidden="true">·</span>{quand}</p>'
            f'<h2><a href="{e(a["lien"])}" rel="noopener">{e(a["titre"])}</a></h2>{extrait}</div></article>')


def main():
    conf = json.loads((ICI / "sources.json").read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        resultats = list(pool.map(collecter, conf["sources"]))

    tous, vus, bilan = [], set(), []
    for src, articles, erreur in resultats:
        n = 0
        for a in articles:
            if a["lien"] not in vus:
                vus.add(a["lien"])
                tous.append((a, src))
                n += 1
        bilan.append({**src, "nb": n, "erreur": erreur})
        print(f"{'OK ' if not erreur else 'ERR'} {n:3d}  {src['nom']}  {erreur}")
    if not tous:  # tout est en panne (ou pas d'Internet) : on garde le site précédent
        sys.exit("Aucun article collecté : site non modifié.")
    tous.sort(key=lambda t: t[0]["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    maintenant = datetime.now(timezone.utc)

    e = html.escape
    filtres = "".join(
        f'<button type="button" class="filtre" data-source="{s["id"]}" aria-pressed="false" style="--c:{e(s["couleur"])}">'
        f'<span class="pastille" aria-hidden="true"></span>{e(s["nom"])} <span class="nb">{s["nb"]}</span></button>'
        for s in bilan if s["nb"])
    liste_sources = "".join(
        f'<li><a href="{e(s["site"])}" rel="noopener">{e(s["nom"])}</a>'
        + (f' <small>(flux indisponible lors de la dernière collecte)</small>' if s["erreur"] else "") + "</li>"
        for s in bilan)
    liste_sans = "".join(f'<li><a href="{e(s["site"])}" rel="noopener">{e(s["nom"])}</a> <small>({e(s["raison"])})</small></li>'
                         for s in conf.get("sans_flux", []))
    remplacements = {
        "<!--FILTRES-->": filtres,
        "<!--ARTICLES-->": "\n".join(carte(a, s) for a, s in tous),
        "<!--NB-->": str(len(tous)),
        "<!--MAJ-->": f'<time datetime="{maintenant.isoformat(timespec="seconds")}">{maintenant.astimezone(PARIS):%d/%m/%Y à %H h %M}</time>',
        "<!--SOURCES-->": liste_sources,
        "<!--SANS_FLUX-->": liste_sans,
    }
    page = (ICI / "modele.html").read_text(encoding="utf-8")
    for cle, val in remplacements.items():
        page = page.replace(cle, val)
    SORTIE.mkdir(exist_ok=True)
    (SORTIE / "index.html").write_text(page, encoding="utf-8")

    donnees = {
        "genere_le": maintenant.isoformat(timespec="seconds"),
        "sources": [{k: s[k] for k in ("id", "nom", "site", "flux", "nb", "erreur")} for s in bilan],
        "articles": [{"titre": a["titre"], "lien": a["lien"], "source": s["id"],
                      "date": a["date"].isoformat() if a["date"] else None,
                      "image": a["image"], "extrait": a["extrait"]} for a, s in tous],
    }
    (SORTIE / "data.json").write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(tous)} articles -> {SORTIE / 'index.html'}")


if __name__ == "__main__":
    main()
