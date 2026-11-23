import json
import re
import unicodedata
import urllib.request
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class CandidatNarratif(BaseModel):
    indicator: Literal[
        "REVENUE",
        "NET_INCOME",
        "OPERATING_INCOME",
    ]
    annee: int
    valeur: Decimal
    unite: Literal["MAD", "KMAD", "MMAD"]
    ligne_source: str = Field(min_length=1)


class ExtractionNarrative(BaseModel):
    indicateurs: list[CandidatNarratif]


class AssociationAnnee(BaseModel):
    annee: int
    montant_source: str


class AssociationsAnnees(BaseModel):
    observations: list[AssociationAnnee]


INDICATEURS = {
    "REVENUE": "chiffre d'affaires",
    "OPERATING_INCOME": "resultat d'exploitation",
    "NET_INCOME": "resultat net",
}


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte)

    texte = "".join(
        c for c in texte
        if not unicodedata.combining(c)
    )

    return texte.lower().replace("’", "'")


def extraire_montants(ligne):
    """Récupère les montants exacts avec leurs unités."""

    pattern = (
        r"([+-]?\s*\d+(?:[,.]\d+)?)"
        r"\s*(MMAD|KMAD|MAD)\b"
    )

    return list(
        re.finditer(pattern, ligne, re.IGNORECASE)
    )


def convertir_montant(montant):
    """Conserve le signe et la précision décimale."""

    return Decimal(
        montant.replace(" ", "").replace(",", ".")
    )


def interroger_qwen(
    contexte,
    ligne,
    indicator,
    entreprise,
    annee_rapport,
):
    """Demande uniquement l'association montant-année."""

    instructions = f"""
Tu analyses un rapport financier marocain.

Entreprise : {entreprise}
Année du rapport : {annee_rapport}
Indicateur recherché : {indicator}

Ta seule mission est d'associer les montants
de cet indicateur à leurs années.

Règles :
- Recopie exactement chaque montant avec son unité.
- Ne modifie jamais les signes ou les décimales.
- Extrais les deux exercices si disponibles.
- Ignore les montants des autres indicateurs.
- N'effectue aucun calcul.
- N'invente aucune information.
- Retourne uniquement du JSON valide.
"""

    contenu = (
        f"Contexte : {contexte}\n"
        f"Ligne à analyser : {ligne}"
    )

    donnees = {
        "model": "qwen3:4b",
        "stream": False,
        "think": False,
        "format": AssociationsAnnees.model_json_schema(),
        "options": {
            "temperature": 0,
            "num_predict": 1024,
        },
        "messages": [
            {
                "role": "system",
                "content": instructions,
            },
            {
                "role": "user",
                "content": contenu,
            },
        ],
    }

    requete = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=json.dumps(donnees).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(
        requete,
        timeout=180,
    ) as reponse:
        resultat = json.load(reponse)

    return AssociationsAnnees.model_validate_json(
        resultat["message"]["content"]
    )


def extraire_narratif(texte, entreprise, annee_rapport):
    """Extraction narrative hybride : Qwen3, regex et Pydantic."""

    candidats = []

    for ligne in texte.splitlines():

        ligne_normalisee = normaliser(ligne)

        for indicator, libelle in INDICATEURS.items():

            if libelle not in ligne_normalisee:
                continue

            # Limiter l'analyse au premier indicateur
            # avant une éventuelle mention de la production.
            ligne_analysee = re.split(
                r",\s*la production",
                ligne,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]

            montants = extraire_montants(ligne_analysee)

            if not montants:
                continue

            print(f"Extraction narrative : {indicator}")

            associations = interroger_qwen(
                texte,
                ligne_analysee,
                indicator,
                entreprise,
                annee_rapport,
            )

            for observation in associations.observations:

                if observation.annee not in [
                    annee_rapport,
                    annee_rapport - 1,
                ]:
                    continue

                for montant in montants:

                    source = montant.group(0).strip()

                    source_normalisee = re.sub(
                        r"\s+", "", source
                    ).upper().replace(",", ".")

                    observation_normalisee = re.sub(
                        r"\s+", "", observation.montant_source
                    ).upper().replace(",", ".")

                    if source_normalisee != observation_normalisee:
                        continue

                    candidat = CandidatNarratif(
                        indicator=indicator,
                        annee=observation.annee,
                        valeur=convertir_montant(
                            montant.group(1)
                        ),
                        unite=montant.group(2).upper(),
                        ligne_source=ligne,
                    )

                    if candidat not in candidats:
                        candidats.append(candidat)

    return ExtractionNarrative(
        indicateurs=candidats
    )
