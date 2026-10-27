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
import unicodedata
import urllib.request
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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

# Rubriques : motifs cherchés dans le titre (compte double), l'extrait et l'adresse, en minuscules sans accents.
# L'ordre départage les égalités : les faits divers d'abord (priorité de Rémy).
# shortcut: mots-clés, donc quelques erreurs de classement ; à affiner avec les rubriques fournies par les flux si besoin
RUBRIQUES = {
    "faits-divers": ("Faits divers", r"faits?[- ]divers|accident|collision|blesse|incendie|feux? de (?:foret|vegetation|broussaille)|depart de feu"
                     r"|\bles secours\b|sauvetage|helitreuil|heliporte|urgence absolue|gendarme|policier|\bpolice\b|interpell|garde a vue"
                     r"|mise? en examen|tribunal|cour d'assises|aux assises|\bproces\b|condamne|prison|detenu|homicide|meurtre|assassin|\btues?\b|noyade|\bnoye"
                     r"|disparition inquietante|agression|cambriol|braquage|\bvols?\b|attentat|explosion|plastiqu|mitraill|coups? de feu"
                     r"|stupefiant|trafic de|drogue|cocaine|cannabis|enquete (?:ouverte|judiciaire)|une enquete\b|parquet|procureur|\bjuge\b|justice|violences?\b|\bfocu\b|feriti?\b"),
    "sport": ("Sport", r"\bsport|football|\bfoot\b|ligue [123]|\bmatch|sc bastia|sporting|\baca\b|ac ajaccio|gfca|rugby|basket|handball|volley"
              r"|tennis|cyclis|\bvelo|\btrail|marathon|triathlon|duathlon|squash|hippi|natation|\bvoile\b|regate|giraglia|tour de corse"
              r"|rallye|coupe de france|championnat|campiunatu|entraineur|joueu|\bbuts?\b|\bstade\b|\bboxe|judo|karate|aviron|kayak|\bgolf"
              r"|\bski\b|escrime|athletisme|olympi|ballon d'or"),
    "politique": ("Politique", r"politi|autonom|nationalist|independan|\bsenat|depute|assemblee de corse|assemblea|collectivite de corse|executif"
                  r"|\bmaires?\b|municipal|\belus?\b|election|scrutin|\bvote|gouvernement|ministre|\bprefet|prefecture|prifittura|beauvau"
                  r"|femu a corsica|core in fronte|\bpnc\b|avanzemu|rassemblement national|macron|darmanin|\bloi\b|reforme|constitution"
                  r"|conseil (?:municipal|departemental|communautaire)|budget|\bmotion|sessione?\b"),
    "culture": ("Culture", r"cultur|festival|concert|musique|musical|\bchants?\b|chanteu|chanson|polyphon|exposition|\bexpo\b|musee|theatre"
                r"|cinema|\bfilms?\b|documentaire|danse|choregraph|\blivres?\b|litter|\broman\b|ecrivain|poesie|poete|patrimoine|spectacle"
                r"|artiste|\barts?\b|photograph|chorale|opera|bande dessinee|conference|histoire|memoire|halloween|\bfetes?\b|\bsalon\b"),
    "langue": ("Langue corse", r"langue corse|\blingua\b|\bcorsu\b|bilingu|co-?officialit|\bscola\b|immersion|parlata|traduction|dictionnaire"
               r"|vocabulaire|\bmots? corses?\b|maistr"),
}
AUTRES = "autres"
NOMS_RUBRIQUES = {**{k: v[0] for k, v in RUBRIQUES.items()}, AUTRES: "Autres"}
MOTIFS = {k: re.compile(v[1]) for k, v in RUBRIQUES.items()}
# mots trop courants pour dire que deux titres parlent du même sujet
VIDES = set("""avec pour dans apres avant sous entre plus sans leur leurs cette sont etre fait font mais tout tous toute toutes elle elles
nous vous dont quand comme aussi encore depuis vers chez ainsi alors selon contre corse corses corsica video direct deux trois quatre cinq
premier premiere annee annees jour jours lundi mardi mercredi jeudi vendredi samedi dimanche janvier fevrier avril juin juillet aout
septembre octobre novembre decembre retrouvez""".split())


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


def normal(s):
    s = unicodedata.normalize("NFD", s.lower().replace("’", "'"))
    return "".join(c for c in s if not unicodedata.combining(c))


def rubrique(a, src):
    """Rubrique d'un article : celle de la source si elle est imposée, sinon la plus citée par les mots-clés."""
    if src.get("rubrique"):
        return src["rubrique"]
    titre, reste = normal(a["titre"]), normal(a["extrait"] + " " + urlparse(a["lien"]).path.replace("-", " "))
    scores = {k: 2 * len(m.findall(titre)) + len(m.findall(reste)) for k, m in MOTIFS.items()}
    meilleure = max(scores, key=scores.get)  # à égalité, la première dans RUBRIQUES
    return meilleure if scores[meilleure] else AUTRES


def mots(titre):
    return {m[:6] for m in re.findall(r"[a-z][a-z0-9]{3,}", normal(titre)) if m not in VIDES}  # 6 lettres : « limogé » = « limogée »


def sujets(tous):
    """Regroupe les articles dont les titres partagent assez de mots : 3, ou 2 qui font 30 % des mots des deux titres."""
    def proches(m, n):
        commun = len(m & n)
        return commun >= 3 or (commun == 2 and commun >= 0.3 * len(m | n))
    groupes = []
    for a, src in tous:
        m = mots(a["titre"])
        nouveau = {"articles": [(a, src)], "mots": [m]}
        for g in [g for g in groupes if any(proches(m, n) for n in g["mots"])]:
            nouveau["articles"] += g["articles"]
            nouveau["mots"] += g["mots"]
            groupes.remove(g)
        groupes.append(nouveau)
    return [g["articles"] for g in groupes]


def date_fr(d):
    d = d.astimezone(PARIS)
    jour = f"{d.day} {MOIS[d.month - 1]}"
    return jour if (d.hour, d.minute) == (0, 0) else f"{jour} {d.hour} h {d:%M}"  # minuit pile : le flux ne donne que le jour


def carte(a, src, classe="carte", charger="lazy"):
    e = html.escape
    # image : seulement celle du flux du média ; la vignette entière mène à l'article d'origine
    img = (f'<img src="{e(a["image"])}" alt="" width="640" height="360" loading="{charger}" decoding="async" referrerpolicy="no-referrer">'
           if a["image"] else "")
    quand = (f'<time datetime="{a["date"].isoformat()}">{date_fr(a["date"])}</time>' if a["date"] else "date inconnue")
    extrait = f'<p class="extrait">{e(a["extrait"])}</p>' if a["extrait"] and a["extrait"] != a["titre"] else ""
    rub = f'<span class="rub">{NOMS_RUBRIQUES[a["rubrique"]]}</span>' if a["rubrique"] != AUTRES else ""
    return (f'<article class="{classe}" data-source="{src["id"]}" data-rubrique="{a["rubrique"]}" style="--c:{e(src["couleur"])}">{img}'
            f'<div class="corps"><p class="meta"><span class="pastille" aria-hidden="true"></span>{e(src["nom"])}<span aria-hidden="true">·</span>{quand}{rub}</p>'
            f'<h3><a href="{e(a["lien"])}" rel="noopener">{e(a["titre"])}</a></h3>{extrait}</div></article>')


def bloc_faits_divers(tous, n=6):
    fd = [(a, s) for a, s in tous if a["rubrique"] == "faits-divers"]
    if not fd:
        return ""
    total, fd = len(fd), fd[:n]
    tete = next((t for t in fd if t[0]["image"]), fd[0])  # en grand : le plus récent avec photo
    fd = [tete] + [t for t in fd if t is not tete]
    # la première arrive tout de suite à l'écran : pas de chargement différé pour elle
    cartes = "".join(carte(a, s, "carte" if i == 0 else "carte compacte", "eager" if i == 0 else "lazy")
                     for i, (a, s) in enumerate(fd))
    return (f'<section class="bloc fd" aria-labelledby="t-fd"><div class="bloc-tete"><h2 id="t-fd">Faits divers</h2>'
            f'<a class="voir" href="#toutes" data-rubrique="faits-divers">Tous les faits divers ({total})</a></div>'
            f'<div class="fd-liste">{cartes}</div></section>')


def bloc_une(tous, depuis, n=4):
    """« À la une » : les sujets traités par le plus de médias depuis 48 h, l'article avec photo en tête."""
    groupes = []
    for g in sujets([(a, s) for a, s in tous if a["date"] and a["date"] >= depuis]):
        medias = {s["id"] for _, s in g}
        if len(medias) >= 2:
            groupes.append((len(medias), max(a["date"] for a, _ in g), g))
    groupes.sort(key=lambda t: t[:2], reverse=True)
    e = html.escape
    html_sujets = ""
    for nb, _, g in groupes[:n]:
        g.sort(key=lambda t: t[0]["date"], reverse=True)
        tete = next((t for t in g if t[0]["image"]), g[0])
        autres = [t for t in g if t is not tete][:4]
        aussi = "".join(f'<li><a href="{e(a["lien"])}" rel="noopener">{e(a["titre"])}</a> <span class="de">{e(s["nom"])}</span></li>'
                        for a, s in autres)
        html_sujets += (f'<div class="sujet">{carte(*tete)}<p class="nb-medias">{nb} médias en parlent</p>'
                        f'<ul class="aussi">{aussi}</ul></div>')
    if not html_sujets:
        return ""
    return f'<section class="bloc" aria-labelledby="t-une"><h2 id="t-une">À la une</h2><div class="sujets">{html_sujets}</div></section>'


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
    for a, src in tous:
        a["rubrique"] = rubrique(a, src)

    e = html.escape
    nb_rub = {k: sum(a["rubrique"] == k for a, _ in tous) for k in NOMS_RUBRIQUES}
    rubriques = "".join(
        f'<button type="button" class="filtre" data-rubrique="{k}" aria-pressed="false">{nom} <span class="nb">{nb_rub[k]}</span></button>'
        for k, nom in NOMS_RUBRIQUES.items() if nb_rub[k])
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
        "<!--FAITS_DIVERS-->": bloc_faits_divers(tous),
        "<!--UNE-->": bloc_une(tous, maintenant - timedelta(hours=48)),
        "<!--RUBRIQUES-->": rubriques,
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
        "articles": [{"titre": a["titre"], "lien": a["lien"], "source": s["id"], "rubrique": a["rubrique"],
                      "date": a["date"].isoformat() if a["date"] else None,
                      "image": a["image"], "extrait": a["extrait"]} for a, s in tous],
    }
    (SORTIE / "data.json").write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(tous)} articles -> {SORTIE / 'index.html'}")


if __name__ == "__main__":
    main()
