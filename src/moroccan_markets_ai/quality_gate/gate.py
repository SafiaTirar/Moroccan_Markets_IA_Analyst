"""Financial Quality Gate (FQG).

Reçoit des candidats financiers (JSON) et décide pour chacun :
REJECTED, REVIEW_REQUIRED ou NOT_CHECKED, avec les contrôles effectués,
les raisons de la décision et la provenance du candidat.

Conformément à la politique du projet (docs/parsing_ocr/DATA_QUALITY_AND_LIMITATIONS.md),
VERIFIED exige une preuve externe issue du PDF original. Une égalité
actif/passif ou deux occurrences concordantes sont des contrôles
complémentaires : elles ne suffisent jamais seules, donc ce module ne produit
pas VERIFIED. Un candidat qui réussit les contrôles reste NOT_CHECKED.

Conformément à la politique des versions (docs/financial_version_policy.md),
une valeur reste attachée au rapport qui la publie : une valeur différente dans
un autre rapport est une autre version (comparatif, retraitement), pas un
conflit. Seules des valeurs différentes au sein d'un même rapport sont une
divergence.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

ANNEE_MIN = 1990
ECART_ANNEE_MAX = 1  # l'observation est de l'année du rapport ou de la précédente
TOTAL_ACTIF = "TOTAL_ASSETS"
TOTAL_PASSIF = "TOTAL_LIABILITIES_AND_EQUITY"


class Decision(StrEnum):
    VERIFIED = "VERIFIED"  # réservé : exige une preuve externe (PDF)
    REJECTED = "REJECTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_CHECKED = "NOT_CHECKED"


class Niveau(StrEnum):
    OK = "OK"
    AVERTISSEMENT = "AVERTISSEMENT"  # information, ne bloque pas
    REVUE = "REVUE"  # vérification manuelle nécessaire
    REJET = "REJET"  # donnée invalide


class Candidat(BaseModel):
    """Un candidat tel que livré par l'extraction (formats S2M et 45 rapports)."""

    nom: str
    annee: int
    valeur: float
    ligne_source: str
    entreprise: str
    unite: str | None = None
    perimetre: str | None = None
    source_table: str
    source_page: int
    source_ligne: int
    validation_status: str = "NOT_CHECKED"
    indicator: str | None = None
    report_year: int | None = None

    @property
    def indicateur(self) -> str:
        return self.indicator or self.nom

    @property
    def cle_version(self) -> tuple:
        """Une version = une observation publiée dans un rapport donné."""
        return (
            self.entreprise,
            self.report_year,
            self.annee,
            self.indicateur,
            self.perimetre,
            self.unite,
        )


class Controle(BaseModel):
    nom: str
    niveau: Niveau
    detail: str


class DecisionCandidat(BaseModel):
    idx: int
    decision: Decision
    entreprise: str | None = None
    report_year: int | None = None
    annee: int | None = None
    indicateur: str | None = None
    valeur: float | None = None
    unite: str | None = None
    perimetre: str | None = None
    source_table: str | None = None
    source_page: int | None = None
    source_ligne: int | None = None
    ligne_source: str | None = None
    controles: list[Controle]
    raisons: list[str]
    avertissements: list[str]


# --------------------------------------------------------------------------
# Outils
# --------------------------------------------------------------------------


def _proches(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=0.005)


def _valeurs_distinctes(valeurs: list[float]) -> list[float]:
    distinctes: list[float] = []
    for valeur in sorted(valeurs):
        if not any(_proches(valeur, d) for d in distinctes):
            distinctes.append(valeur)
    return distinctes


def _resume_erreur(erreur: ValidationError) -> str:
    premiere = erreur.errors()[0]
    champ = ".".join(str(p) for p in premiere["loc"]) or "candidat"
    return f"schéma invalide ({champ} : {premiere['msg']})"


def _texte(valeur: Any) -> str | None:
    return valeur if isinstance(valeur, str) else None


def _indexer_anomalies(anomalies: list[dict]) -> tuple[dict, dict]:
    """Indexe anomalies.json : au niveau du tableau et au niveau du montant."""
    tables: dict[tuple, str] = {}
    montants: dict[tuple, str] = {}
    for a in anomalies:
        raison = a.get("raison")
        if raison == "TABLEAU_TO_REVIEW":
            cle = (a.get("entreprise"), a.get("report_year"), a.get("table_id"))
            tables[cle] = raison
        elif raison == "MONTANT_A_VERIFIER":
            cle = (
                a.get("entreprise"),
                a.get("report_year"),
                a.get("table_id"),
                a.get("indicator"),
                a.get("observation_year"),
            )
            montants[cle] = raison
    return tables, montants


# --------------------------------------------------------------------------
# Contrôles propres à chaque candidat
# --------------------------------------------------------------------------


def _controles_statiques(c: Candidat, tables: dict, montants: dict) -> list[Controle]:
    resultats: list[Controle] = []

    if math.isfinite(c.valeur):
        resultats.append(
            Controle(nom="valeur", niveau=Niveau.OK, detail="valeur numérique")
        )
    else:
        resultats.append(
            Controle(
                nom="valeur",
                niveau=Niveau.REJET,
                detail=f"valeur non finie ({c.valeur})",
            )
        )

    if c.annee < ANNEE_MIN:
        niveau, detail = Niveau.REJET, f"année {c.annee} impossible"
    elif c.report_year is None:
        niveau = Niveau.AVERTISSEMENT
        detail = "report_year absent : année non comparée au rapport"
    elif c.annee > c.report_year:
        niveau = Niveau.REJET
        detail = f"année {c.annee} postérieure à l'année du rapport {c.report_year}"
    elif c.report_year - c.annee > ECART_ANNEE_MAX:
        niveau = Niveau.REVUE
        detail = f"année {c.annee} trop éloignée de l'année du rapport {c.report_year}"
    else:
        niveau, detail = Niveau.OK, "année cohérente avec le rapport"
    resultats.append(Controle(nom="annee", niveau=niveau, detail=detail))

    if c.unite is None:
        resultats.append(
            Controle(
                nom="unite", niveau=Niveau.AVERTISSEMENT, detail="unité inconnue (null)"
            )
        )
    else:
        resultats.append(
            Controle(nom="unite", niveau=Niveau.OK, detail=f"unité {c.unite}")
        )

    if c.perimetre is None:
        resultats.append(
            Controle(
                nom="perimetre",
                niveau=Niveau.AVERTISSEMENT,
                detail="périmètre inconnu (null)",
            )
        )
    else:
        resultats.append(
            Controle(
                nom="perimetre", niveau=Niveau.OK, detail=f"périmètre {c.perimetre}"
            )
        )

    cle_table = (c.entreprise, c.report_year, c.source_table)
    cle_montant = (*cle_table, c.indicateur, c.annee)
    if cle_table in tables:
        resultats.append(
            Controle(
                nom="anomalie_source",
                niveau=Niveau.REVUE,
                detail=f"tableau {c.source_table} signalé à revoir (anomalies.json)",
            )
        )
    elif cle_montant in montants:
        resultats.append(
            Controle(
                nom="anomalie_source",
                niveau=Niveau.REVUE,
                detail=f"montant {c.indicateur} {c.annee} signalé à vérifier (anomalies.json)",
            )
        )
    else:
        resultats.append(
            Controle(
                nom="anomalie_source",
                niveau=Niveau.OK,
                detail="aucune anomalie signalée",
            )
        )

    if c.validation_status == "PDF_CONFIRMED":
        resultats.append(
            Controle(
                nom="statut_extraction",
                niveau=Niveau.AVERTISSEMENT,
                detail="PDF_CONFIRMED déclaré par l'extraction, sans validation de référence",
            )
        )

    return resultats


# --------------------------------------------------------------------------
# Évaluation d'un lot de candidats
# --------------------------------------------------------------------------


def evaluer(
    candidats_bruts: list[dict], anomalies: list[dict] | None = None
) -> list[DecisionCandidat]:
    """Évalue tous les candidats et renvoie une décision par candidat, dans l'ordre."""
    tables, montants = _indexer_anomalies(anomalies or [])
    controles: dict[int, list[Controle]] = defaultdict(list)
    candidats: dict[int, Candidat] = {}
    schemas_invalides: dict[int, str] = {}

    for idx, brut in enumerate(candidats_bruts):
        try:
            candidats[idx] = Candidat.model_validate(brut)
        except ValidationError as erreur:
            schemas_invalides[idx] = _resume_erreur(erreur)

    for idx, c in candidats.items():
        controles[idx].extend(_controles_statiques(c, tables, montants))

    rejetes = {
        idx
        for idx, cs in controles.items()
        if any(x.niveau == Niveau.REJET for x in cs)
    }

    # Doublons exacts : on garde le premier, on rejette les suivants.
    vus: dict[tuple, int] = {}
    for idx, c in candidats.items():
        if idx in rejetes:
            continue
        cle = (*c.cle_version, c.source_table, c.source_ligne, round(c.valeur, 6))
        if cle in vus:
            controles[idx].append(
                Controle(
                    nom="doublon",
                    niveau=Niveau.REJET,
                    detail=f"doublon exact du candidat #{vus[cle]}",
                )
            )
            rejetes.add(idx)
        else:
            vus[cle] = idx
            controles[idx].append(
                Controle(nom="doublon", niveau=Niveau.OK, detail="pas de doublon exact")
            )

    en_jeu = [idx for idx in candidats if idx not in rejetes]

    # Une version (rapport, année observée, indicateur, périmètre, unité) doit
    # avoir une seule valeur. Plusieurs valeurs dans le même rapport = divergence.
    par_version: dict[tuple, list[int]] = defaultdict(list)
    for idx in en_jeu:
        par_version[candidats[idx].cle_version].append(idx)

    for membres in par_version.values():
        valeurs = _valeurs_distinctes([candidats[i].valeur for i in membres])
        if len(valeurs) > 1:
            texte = ", ".join(f"{v:,.2f}" for v in valeurs)
            detail = (
                f"{len(valeurs)} valeurs différentes dans la même version : {texte}"
            )
            for i in membres:
                controles[i].append(
                    Controle(nom="divergence", niveau=Niveau.REVUE, detail=detail)
                )
            continue
        for i in membres:
            controles[i].append(
                Controle(
                    nom="divergence",
                    niveau=Niveau.OK,
                    detail="valeur unique dans cette version",
                )
            )
        tableaux = {candidats[i].source_table for i in membres}
        if len(tableaux) >= 2:
            detail = f"même valeur dans {len(tableaux)} tableaux du même rapport"
            for i in membres:
                controles[i].append(
                    Controle(nom="confirmation", niveau=Niveau.OK, detail=detail)
                )

    # La même observation dans plusieurs rapports : une valeur identique est un
    # contrôle complémentaire ; une valeur différente est une autre version.
    entre_rapports: dict[tuple, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for idx in en_jeu:
        c = candidats[idx]
        cle = (c.entreprise, c.annee, c.indicateur, c.perimetre, c.unite)
        entre_rapports[cle][str(c.report_year)].append(idx)

    for par_rapport in entre_rapports.values():
        if len(par_rapport) < 2:
            continue
        valeur_du_rapport: dict[str, float | None] = {}
        for rapport, membres in par_rapport.items():
            distinctes = _valeurs_distinctes([candidats[i].valeur for i in membres])
            valeur_du_rapport[rapport] = distinctes[0] if len(distinctes) == 1 else None
        for rapport, membres in par_rapport.items():
            propre = valeur_du_rapport[rapport]
            if propre is None:
                continue
            memes = sorted(
                r
                for r, v in valeur_du_rapport.items()
                if r != rapport and v is not None and _proches(propre, v)
            )
            differents = sorted(
                r
                for r, v in valeur_du_rapport.items()
                if r != rapport and v is not None and not _proches(propre, v)
            )
            for i in membres:
                if memes:
                    detail = f"même valeur dans le(s) rapport(s) {', '.join(memes)}"
                    controles[i].append(
                        Controle(nom="confirmation", niveau=Niveau.OK, detail=detail)
                    )
                if differents:
                    detail = (
                        f"valeur différente dans le(s) rapport(s) {', '.join(differents)} : "
                        "autre version (comparatif ou retraitement possible)"
                    )
                    controles[i].append(
                        Controle(
                            nom="versions", niveau=Niveau.AVERTISSEMENT, detail=detail
                        )
                    )

    # Équilibre du bilan : Total actif = Total passif, dans un même rapport.
    # Un écart n'est concluant que si l'unité et le périmètre sont connus.
    bilans: dict[tuple, dict[str, list[int]]] = defaultdict(
        lambda: {"actif": [], "passif": []}
    )
    for idx in en_jeu:
        c = candidats[idx]
        cle = (c.entreprise, c.report_year, c.annee, c.perimetre, c.unite)
        if c.indicator == TOTAL_ACTIF:
            bilans[cle]["actif"].append(idx)
        elif c.indicator == TOTAL_PASSIF:
            bilans[cle]["passif"].append(idx)

    for cle, cotes in bilans.items():
        actif, passif = cotes["actif"], cotes["passif"]
        if not actif or not passif:
            continue
        contexte_complet = cle[3] is not None and cle[4] is not None
        for cote, autre in ((actif, passif), (passif, actif)):
            for i in cote:
                if any(
                    _proches(candidats[i].valeur, candidats[j].valeur) for j in autre
                ):
                    controles[i].append(
                        Controle(
                            nom="equilibre",
                            niveau=Niveau.OK,
                            detail="Total actif = Total passif",
                        )
                    )
                elif contexte_complet:
                    controles[i].append(
                        Controle(
                            nom="equilibre",
                            niveau=Niveau.REVUE,
                            detail="aucune valeur du côté opposé du bilan n'est égale",
                        )
                    )
                else:
                    controles[i].append(
                        Controle(
                            nom="equilibre",
                            niveau=Niveau.AVERTISSEMENT,
                            detail="écart actif/passif non concluant : unité ou périmètre inconnu",
                        )
                    )

    decisions: list[DecisionCandidat] = []
    for idx, brut in enumerate(candidats_bruts):
        if idx in schemas_invalides:
            base = brut if isinstance(brut, dict) else {}
            detail = schemas_invalides[idx]
            decisions.append(
                DecisionCandidat(
                    idx=idx,
                    decision=Decision.REJECTED,
                    entreprise=_texte(base.get("entreprise")),
                    source_table=_texte(base.get("source_table")),
                    controles=[
                        Controle(nom="schema", niveau=Niveau.REJET, detail=detail)
                    ],
                    raisons=[detail],
                    avertissements=[],
                )
            )
            continue

        c = candidats[idx]
        cs = controles[idx]
        rejets = [x.detail for x in cs if x.niveau == Niveau.REJET]
        revues = [x.detail for x in cs if x.niveau == Niveau.REVUE]

        if rejets:
            decision, raisons = Decision.REJECTED, rejets
        elif revues:
            decision, raisons = Decision.REVIEW_REQUIRED, revues
        else:
            # Aucun problème détecté, mais aucune preuve externe : NOT_CHECKED.
            decision = Decision.NOT_CHECKED
            complementaires = [
                x.detail
                for x in cs
                if x.nom in ("confirmation", "equilibre") and x.niveau == Niveau.OK
            ]
            if complementaires:
                raisons = [
                    f"contrôle complémentaire réussi : {d}" for d in complementaires
                ]
            else:
                raisons = ["aucun contrôle complémentaire possible (source unique)"]
            raisons.append(
                "pas de preuve externe (PDF) : VERIFIED impossible à ce stade"
            )

        decisions.append(
            DecisionCandidat(
                idx=idx,
                decision=decision,
                entreprise=c.entreprise,
                report_year=c.report_year,
                annee=c.annee,
                indicateur=c.indicateur,
                valeur=c.valeur,
                unite=c.unite,
                perimetre=c.perimetre,
                source_table=c.source_table,
                source_page=c.source_page,
                source_ligne=c.source_ligne,
                ligne_source=c.ligne_source,
                controles=cs,
                raisons=raisons,
                avertissements=[
                    x.detail for x in cs if x.niveau == Niveau.AVERTISSEMENT
                ],
            )
        )

    return decisions


# --------------------------------------------------------------------------
# Entrées / sorties
# --------------------------------------------------------------------------


def charger_json(chemin: str | Path) -> list[dict]:
    return json.loads(Path(chemin).read_text(encoding="utf-8"))


def ecrire_decisions(decisions: list[DecisionCandidat], chemin: str | Path) -> None:
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    donnees = [d.model_dump(mode="json") for d in decisions]
    chemin.write_text(
        json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def resume(decisions: list[DecisionCandidat]) -> dict:
    par_decision = Counter(d.decision.value for d in decisions)
    par_controle = Counter(
        c.nom
        for d in decisions
        for c in d.controles
        if c.niveau in (Niveau.REVUE, Niveau.REJET)
    )
    return {
        "total": len(decisions),
        "decisions": dict(par_decision),
        "controles_en_echec": dict(par_controle),
    }
