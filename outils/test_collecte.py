"""Vérifie le lecteur de flux sans Internet. Usage : python3 outils/test_collecte.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from collecte import lire_flux, date_fr, EXTRAIT_MAX  # noqa: E402

RSS = b"""<?xml version="1.0"?><rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>Bastia &amp; Ajaccio</title><link>https://ex.corsica/a</link>
<pubDate>Tue, 27 Oct 2026 10:16:46 +0000</pubDate><media:content url="https://ex.corsica/i.jpg" medium="image"/>
<description>&lt;p&gt;""" + b"mot " * 200 + b"""&lt;/p&gt;</description></item>
<item><title>Piege</title><link>javascript:alert(1)</link></item>
<item><title>Titre seul</title><link>https://ex.corsica/b</link><description>Titre seul Auteur mar 27/10</description>
<enclosure url="http://ex.corsica/non-https.jpg" type="image/jpeg"/></item>
</channel></rss>"""

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom</title>
<link rel="alternate" href="https://ex.corsica/c"/><updated>2026-10-27T09:00:00Z</updated>
<summary>Court &lt;img src="https://ex.corsica/j.png"&gt; resume</summary></entry></feed>"""

a = list(lire_flux(RSS))
assert [x["titre"] for x in a] == ["Bastia & Ajaccio", "Titre seul"], a  # le lien javascript: est écarté
assert a[0]["image"] == "https://ex.corsica/i.jpg"
assert a[0]["date"].isoformat() == "2026-10-27T10:16:46+00:00"
assert len(a[0]["extrait"]) <= EXTRAIT_MAX + 1 and a[0]["extrait"].endswith("…")
assert a[1]["extrait"] == "" and a[1]["image"] == ""  # titre répété retiré, image http refusée

b = list(lire_flux(ATOM))
assert b[0]["lien"] == "https://ex.corsica/c" and b[0]["image"] == "https://ex.corsica/j.png"
assert b[0]["extrait"] == "Court resume"
PLAN = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"
xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
<url><image:image><image:loc>https://ex.corsica/i.jpg</image:loc><image:title>Legende</image:title></image:image>
<loc>https://ex.corsica/article/a/1/x</loc><news:news><news:publication_date>2026-10-27T12:39:30+01:00</news:publication_date>
<news:title>Coop&#xE9;rative</news:title></news:news></url>
<url><loc>https://ex.corsica/articles/48/l%C3%A9glise-en-corse</loc><lastmod>2026-10-19T18:31:40+00:00</lastmod></url></urlset>"""
p = list(lire_flux(PLAN))
assert p[0] == {"titre": "Coopérative", "lien": "https://ex.corsica/article/a/1/x", "date": p[0]["date"], "image": "", "extrait": ""}, p
assert p[0]["date"].isoformat() == "2026-10-27T11:39:30+00:00"
assert p[1]["titre"] == "Léglise en corse" and p[1]["date"].day == 19  # titre tiré de l'adresse, date lastmod

# Vrais titres du Journal de la Corse : og:title, sinon <title>, cache, repli sur le titre tiré de l'adresse
import tempfile  # noqa: E402
import collecte  # noqa: E402
from collecte import titre_html, completer_titres  # noqa: E402
assert titre_html(b'<meta content="Qu&eacute;bec : l\xe2\x80\x99accession - Journal" property="og:title"/>', "Journal") == "Québec : l’accession"
assert titre_html(b"<meta property='og:title' content='Il a dit \"oui\" - JdC'>", "JdC") == 'Il a dit "oui"'
assert titre_html(b"<title>Football - le derby - JdC</title>", "JdC") == "Football - le derby"  # seul le nom du site est retiré
assert titre_html(b"<p>rien</p>", "JdC") == ""
assert titre_html(b'<meta property="og:title" content=""><title>Repli - JdC</title>', "JdC") == "Repli"  # og:title vide
import time  # noqa: E402
debut = time.monotonic()
titre_html(b"<meta property=og:title " * 20_000 + b"<title" * 40_000, "JdC")  # page piégée de 500 Ko
assert time.monotonic() - debut < 3, "titre_html trop lent sur une page piégée"

appels = []
def faux_lire(url, maxi=0, site=""):
    appels.append(url)
    if url.endswith("/3/c"):
        raise OSError("panne")
    return b"<title>Vrai " + url[-1].encode() + b" - JdC</title>"
collecte.lire, collecte.PAUSE, collecte.CACHE = faux_lire, 0, Path(tempfile.mkdtemp())
src = {"id": "jdc", "nom": "JdC", "flux": "https://ex.corsica/sitemap.xml"}
def arts():
    return [{"titre": "slug", "lien": f"https://www.ex.corsica/articles/{i}/{c}"} for i, c in ((1, "a"), (2, "b"), (3, "c"), (4, "d"))] \
        + [{"titre": "slug", "lien": "https://autre.site/articles/5/e"}]
a = arts()
assert completer_titres(a, src) == 3 and [x["titre"] for x in a] == ["Vrai a", "Vrai b", "slug", "slug", "slug"], a
assert len(appels) == 3  # après l'échec, on n'insiste pas ; un autre site n'est jamais lu
appels.clear()
a = arts()
completer_titres(a, src)
assert appels == [a[2]["lien"]]  # seconde collecte : a et b viennent du cache, seul c est retenté
assert [x["titre"] for x in a[:2]] == ["Vrai a", "Vrai b"] and a[3]["titre"] == "slug"
for abime in ("[]", "null", '{"https://www.ex.corsica/articles/1/a": 7}'):  # cache du mauvais type : pas d'arrêt
    (collecte.CACHE / "titres-jdc.json").write_text(abime)
    completer_titres(arts(), src)

from datetime import datetime, timezone  # noqa: E402
assert date_fr(datetime(2026, 10, 27, 7, 6, tzinfo=timezone.utc)) == "27 oct. 8 h 06"  # pas de « 08 h »
assert date_fr(datetime(2026, 9, 24, 22, 0, tzinfo=timezone.utc)) == "25 sept."  # minuit pile : jour seul

# Rubriques par mots-clés
from collecte import rubrique, sujets, carte  # noqa: E402
def rub(titre, extrait="", lien="https://ex.corsica/a", **src):
    return rubrique({"titre": titre, "extrait": extrait, "lien": lien}, src)
assert rub("Un accident de la circulation fait deux blessés à Bastia") == "faits-divers"
assert rub("Feu de forêt de Meria : 30 hectares détruits") == "faits-divers"
assert rub("Football (Ligue 3) – Laurent Peyrelade limogé de son poste d’entraîneur du SC Bastia") == "sport"
assert rub("U GFCA volley hà vintu") == "sport"  # « vol » ne doit pas faire un faits divers
assert rub("Autonomie de la Corse : le Sénat rejette le texte") == "politique"
assert rub("Le festival Ritrovu annulé") == "culture"
assert rub("Reprise du chantier à Saint-Florent") == "autres"  # « chantier » n'est pas un chant
assert rub("Avec Kcantà, Francine Massiani porte la langue corse au-delà de l'île") == "langue"
assert rub("Solidarité : une soirée caritative au profit des sapeurs-pompiers") == "autres"
assert rub("Transport : nouvelle ligne de bus") == "autres"  # « transport » n'est pas du sport
assert rub("Bastia : un match sous haute surveillance, la police interpelle deux supporters") == "faits-divers"  # égalité : faits divers d'abord
assert rub("Météo", lien="https://ex.corsica/article/faits-divers/x") == "faits-divers"  # rubrique dans l'adresse
assert rub("I maistrelli", rubrique="langue") == "langue"  # rubrique imposée par la source

# « À la une » : même sujet = au moins 2 mots du titre en commun, sans chaîner des sujets différents
def art(titre, sid):
    return ({"titre": titre}, {"id": sid})
g = sujets([art("SC Bastia : Laurent Peyrelade limogé, Michel Moretti assurera l’intérim", "cni"),
            art("Football : Laurent Peyrelade démis de ses fonctions d'entraîneur du SC Bastia", "cm"),
            art("L'entraîneur du Sporting Club de Bastia Laurent Peyrelade relevé de ses fonctions", "f3"),
            art("Un homme en urgence absolue après un accident de la circulation dans le Valincu", "alta"),
            art("Un accident de la circulation fait deux blessés à Bastia", "alta"),
            art("Bastia : deux blessés dans un accident de la circulation", "cni"),
            art("Météo pour ce mardi 27 octobre 2026", "alta"),
            art("Pronostics hippiques du mardi 27 octobre 2026", "paese")])
tailles = sorted(len(x) for x in g)
assert tailles == [1, 1, 1, 2, 3], [[a["titre"] for a, _ in x] for x in g]  # deux accidents différents ne sont pas mêlés ; dates ignorées

# Carte : rubrique affichée, titre en h3, image du flux en chargement différé, adresse et titre échappés
c = carte({"titre": "<b>", "lien": "https://ex.corsica/a?x=1&y=2", "date": None, "image": "https://ex.corsica/i.jpg",
           "extrait": "", "rubrique": "faits-divers"}, {"id": "cni", "nom": "CNI", "couleur": "#000"})
assert 'data-rubrique="faits-divers"' in c and '<span class="rub">Faits divers</span>' in c and 'loading="lazy"' in c
assert "<h3><a href=\"https://ex.corsica/a?x=1&amp;y=2\"" in c and "&lt;b&gt;" in c
print("OK : lecteur de flux, rubriques, sujets")
