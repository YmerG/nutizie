"""Vérifie que la page d'accueil reste identique, octet par octet, d'une version du code à l'autre.

python3 outils/verif_accueil.py figer <dossier>    collecte réelle une fois, gardée dans <dossier>/resultats.pickle
python3 outils/verif_accueil.py rejouer <dossier> <sortie>  régénère le site dans <sortie> à partir de la collecte figée
Comparer ensuite deux index.html : cmp <sortie1>/index.html <sortie2>/index.html
"""
import pickle
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collecte  # noqa: E402

FIXE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


class Fige(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXE


mode, dossier = sys.argv[1], Path(sys.argv[2])
dossier.mkdir(parents=True, exist_ok=True)
conf = collecte.json.loads((collecte.ICI / "sources.json").read_text(encoding="utf-8"))
if mode == "figer":
    with ThreadPoolExecutor(max_workers=6) as pool:
        res = {src["id"]: (arts, err) for src, arts, err in pool.map(collecte.collecter, conf["sources"])}
    (dossier / "resultats.pickle").write_bytes(pickle.dumps(res))
else:
    res = pickle.loads((dossier / "resultats.pickle").read_bytes())
    collecte.collecter = lambda src: (src, *res[src["id"]])
    collecte.datetime = Fige
    collecte.SORTIE = Path(sys.argv[3])
    collecte.main()
