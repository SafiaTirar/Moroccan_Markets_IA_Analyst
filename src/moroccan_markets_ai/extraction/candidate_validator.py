import re
import unicodedata
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from .label_detector import detecter_colonne_libelle
from .schemas import CandidatFinancier
from .table_config import obtenir_configuration

# --------------------------------------------------
# 1. Fonctions utilitaires
# --------------------------------------------------

def convertir_montant(texte):
    """Convertit un montant financier en Decimal."""

    texte = str(texte).replace("\u00a0", "")
    texte = texte.replace(" ", "").strip()

    # Cellules vides ou tirets
    if texte in ("", "-", "–", "—"):
        return None

    # Format français : 134023110,29
    if "," in texte and "." not in texte:
        texte = texte.replace(",", ".")

    # Format anglais : 134,023,110.29
    elif "," in texte and "." in texte:
        texte = texte.replace(",", "")

    try:
        return Decimal(texte)
    except InvalidOperation:
        return None


def normaliser(texte):
    """Simplifie un libellé pour faciliter la comparaison."""

    texte = unicodedata.normalize("NFKD", texte)

    texte = "".join(
        caractere
        for caractere in texte
        if not unicodedata.combining(caractere)
    )

    return re.sub(
        r"[^a-z0-9]+",
        " ",
        texte.lower()
    ).strip()


# --------------------------------------------------
# 2. Ancien validateur
# --------------------------------------------------

def verifier_candidats(tableau, extraction):
    """
    Vérifie les indicateurs de l'ancienne extraction
    Qwen3 en comparant les montants et les libellés.

    Ne constitue pas une vérification du PDF.
    """

    lignes = tableau["contenu"].splitlines()

    retenus = []
    a_examiner = []
    deja_vus = set()

    for indicateur in extraction.indicateurs:

        montant = Decimal(str(indicateur.valeur))

        # Ancien format du CPC
        colonne = {
            2025: 2,
            2024: 4
        }.get(indicateur.annee)

        if colonne is None:
            a_examiner.append(
                (indicateur, "Année inconnue")
            )
            continue

        correspondances = []

        for ligne in lignes[1:]:

            cellules = ligne.split(" | ")

            if len(cellules) <= colonne:
                continue

            valeur_csv = convertir_montant(
                cellules[colonne]
            )

            if valeur_csv == montant:
                correspondances.append(
                    cellules[0].strip()
                )

        if not correspondances:
            a_examiner.append(
                (
                    indicateur,
                    "Montant absent de la colonne"
                )
            )
            continue

        libelle = normaliser(indicateur.nom)

        libelles_compatibles = [
            source
            for source in correspondances
            if libelle
            and libelle in normaliser(source)
        ]

        if not libelles_compatibles:
            a_examiner.append(
                (
                    indicateur,
                    "Libellé différent du CSV"
                )
            )
            continue

        if any(
            source.count("*") > 1
            for source in libelles_compatibles
        ):
            a_examiner.append(
                (
                    indicateur,
                    "Plusieurs libellés possibles"
                )
            )
            continue

        cle = (
            libelle,
            indicateur.annee,
            montant
        )

        if cle in deja_vus:
            a_examiner.append(
                (indicateur, "Doublon exact")
            )
            continue

        deja_vus.add(cle)
        retenus.append(indicateur)

    return retenus, a_examiner


# --------------------------------------------------
# 3. Nouveau validateur : Qwen3 + Pydantic
# --------------------------------------------------

def valider_nouveaux_candidats(
    tableau,
    candidats,
    entreprise
):
    """
    Valide les candidats produits par line_extractor.py.

    Vérifications :
    1. Structure Pydantic
    2. Ligne source
    3. Année et colonne
    4. Montant
    5. Signe
    6. Libellé
    7. Libellés fusionnés
    8. Doublons
    9. Corrections PDF connues

    Cette fonction ne réalise pas le
    Financial Quality Gate.
    """

    lignes = tableau["contenu"].splitlines()

    # Configuration adaptée au tableau
    config = obtenir_configuration(tableau)

    colonne_libelle = detecter_colonne_libelle(tableau)

    if colonne_libelle is None:
        raise ValueError(
            f"Colonne des libellés introuvable : {tableau['table_id']}"
        )

    colonnes = config["colonnes"]
    colonnes_signes = config["colonnes_signes"]

    retenus = []
    a_examiner = []
    deja_vus = set()

    for candidat in candidats:

        # ------------------------------------------
        # 1. Validation Pydantic
        # ------------------------------------------

        donnees = {
            **candidat,
            "entreprise": entreprise
        }

        try:
            objet = CandidatFinancier.model_validate(
                donnees
            )

        except ValidationError as erreur:
            a_examiner.append(
                (
                    candidat,
                    f"Erreur Pydantic : {erreur}"
                )
            )
            continue

        # ------------------------------------------
        # 2. Vérification de la ligne source
        # ------------------------------------------

        indice = objet.source_ligne

        if indice <= 0 or indice >= len(lignes):
            a_examiner.append(
                (objet, "Ligne source invalide")
            )
            continue

        cellules = lignes[indice].split(" | ")

        # ------------------------------------------
        # 3. Vérification de l'année
        # ------------------------------------------

        if objet.annee not in colonnes:
            a_examiner.append(
                (objet, "Année inconnue")
            )
            continue

        colonne = colonnes[objet.annee]

        colonne_signe = colonnes_signes.get(
            objet.annee
        )

        if len(cellules) <= colonne:
            a_examiner.append(
                (objet, "Colonne absente du CSV")
            )
            continue

        # ------------------------------------------
        # 4. Vérification du montant
        # ------------------------------------------

        montant_csv = convertir_montant(
            cellules[colonne]
        )

        if montant_csv is None:
            a_examiner.append(
                (objet, "Montant absent du CSV")
            )
            continue

        # ------------------------------------------
        # 5. Vérification du signe
        # ------------------------------------------

        if colonne_signe is not None:

            if len(cellules) <= colonne_signe:
                a_examiner.append(
                    (objet, "Colonne de signe absente")
                )
                continue

            signe = cellules[colonne_signe].strip()

            if signe == "-":
                montant_csv = -montant_csv

        # Comparaison du montant extrait
        if montant_csv != Decimal(str(objet.valeur)):
            a_examiner.append(
                (
                    objet,
                    "Montant différent du CSV"
                )
            )
            continue

        # ------------------------------------------
        # 6. Corrections PDF du CPC S2M 2025
        # ------------------------------------------

        est_cpc_s2m = (
            tableau["table_id"] == "DOCLING_018"
            and entreprise == "S2M"
            and tableau.get("entreprise") == "S2M"
            and tableau.get("annee") == 2025
        )

        correction_pdf = (
            est_cpc_s2m
            and objet.validation_status == "PDF_CONFIRMED"
            and (
                (
                    indice == 20
                    and objet.annee == 2025
                    and objet.nom
                    == "III RESULTAT D'EXPLOITATION (I-II)"
                )
                or (
                    indice == 37
                    and objet.nom
                    == "* Autres produits non courants"
                )
                or (
                    indice == 41
                    and objet.nom.startswith(
                        "* Valeurs nettes d'amortissements"
                    )
                )
            )
        )

        # ------------------------------------------
        # 7. Vérification du libellé
        # ------------------------------------------

        # ------------------------------------------
        # 7. Vérification du libellé
        # ------------------------------------------

        if not correction_pdf:

            # Vérifier que la colonne existe
            if len(cellules) <= colonne_libelle:
                a_examiner.append(
                    (objet, "Colonne du libellé absente du CSV")
                )
                continue

            # Lire le libellé dans la bonne colonne
            libelle_csv = cellules[colonne_libelle].strip()

            # Comparer le libellé extrait au CSV
            if normaliser(objet.nom) != normaliser(libelle_csv):
                a_examiner.append(
                    (objet, "Libellé différent du CSV")
                )
                continue

            # Détecter les libellés fusionnés
            if libelle_csv.count("*") > 1:
                a_examiner.append(
                    (objet, "Libellés fusionnés")
                )
                continue

        # ------------------------------------------
        # 8. Détection des doublons
        # ------------------------------------------

        cle = (
            normaliser(objet.nom),
            objet.annee
        )

        if cle in deja_vus:
            a_examiner.append(
                (objet, "Doublon")
            )
            continue

        deja_vus.add(cle)

        # ------------------------------------------
        # 9. Candidat retenu
        # ------------------------------------------

        retenus.append(objet)

    return retenus, a_examiner
