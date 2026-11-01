import json
import urllib.request
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError

from pydantic import BaseModel, Field, ValidationError

from .label_detector import detecter_colonne_libelle
from .table_config import obtenir_configuration


class SelectionLignes(BaseModel):
    lignes: list[int] = Field(default_factory=list)


def convertir_montant(texte):
    """Convertit un montant financier en Decimal."""

    texte = str(texte).replace("\u00a0", "").replace(" ", "").strip()

    if texte in ("", "-", "–", "—"):
        return None

    if "," in texte and "." not in texte:
        texte = texte.replace(",", ".")

    elif "," in texte and "." in texte:
        texte = texte.replace(",", "")

    try:
        return Decimal(texte)

    except InvalidOperation:
        return None


def extraire_par_lignes(tableau):
    """Sélectionne les lignes avec Qwen3 et extrait les montants du CSV."""

    lignes = tableau["contenu"].splitlines()

    # Configuration des colonnes financières
    config = obtenir_configuration(tableau)

    colonnes = config["colonnes"]
    colonnes_signes = config["colonnes_signes"]

    if not colonnes:
        raise ValueError(
            f"Aucune colonne financière détectée : {tableau['table_id']}"
        )

    # Détection automatique de la colonne des libellés
    colonne_libelle = detecter_colonne_libelle(tableau)

    if colonne_libelle is None:
        raise ValueError(
            f"Colonne des libellés introuvable : {tableau['table_id']}"
        )

    print("Colonne des libellés :", colonne_libelle)

    # Préparer les lignes pour Qwen3
    contenu = "\n".join(
        f"{i} | {ligne}"
        for i, ligne in enumerate(lignes[1:], start=1)
    )

    instructions = """
Tu es un assistant spécialisé dans l'analyse de tableaux financiers.

Chaque ligne commence par son numéro.

Ta mission :
Identifier tous les numéros des lignes contenant au moins un montant financier.

Règles :
1. Analyse chaque ligne.
2. Inclus les postes comptables, résultats et totaux.
3. Ignore les titres sans montant.
4. Inclus les lignes contenant plusieurs libellés.
5. Inclus les lignes dont les montants semblent décalés.
6. N'invente aucun numéro.
7. Ne recopie aucun montant.
8. Retourne uniquement un objet JSON.

Structure attendue :
{"lignes": [1, 2, 3]}
"""

    donnees = {
        "model": "qwen3:4b",
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "temperature": 0.2,
            "repeat_penalty": 1.15,
            "num_predict": 512,
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

    print("Qwen3 sélectionne les lignes...")

    # ------------------------------------------
    # 1. Appel à Ollama avec nouvelle tentative
    # ------------------------------------------

    selection = None

    for tentative in range(1, 3):

        print(f"Tentative Qwen3 : {tentative}/2")

        try:
            with urllib.request.urlopen(
                requete,
                timeout=300,
            ) as reponse:
                resultat = json.load(reponse)

            # Vérifier la structure de la réponse
            contenu_reponse = resultat["message"]["content"]

            # Vérifier le JSON avec Pydantic
            selection = SelectionLignes.model_validate_json(
                contenu_reponse
            )

            print(
                "Réponse brute de Qwen3 :",
                contenu_reponse,
            )

            print(
                "Lignes sélectionnées :",
                selection.lignes,
            )

            # La réponse est valide : arrêter les tentatives
            break

        except HTTPError as erreur:
            print("Erreur HTTP Ollama :", erreur.code)
            print(
                "Détail :",
                erreur.read().decode(
                    "utf-8",
                    errors="replace",
                ),
            )

        except URLError as erreur:
            print(
                "Impossible de contacter Ollama :",
                erreur.reason,
            )

        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as erreur:
            print("Réponse Qwen3 invalide :", erreur)

    # Arrêter si les deux tentatives ont échoué
    if selection is None:
        raise RuntimeError(
            "Extraction impossible après 2 tentatives Qwen3."
        )

    # ------------------------------------------
    # 2. Préparer les résultats
    # ------------------------------------------

    candidats = []
    anomalies = []
    deja_vus = set()

    # ------------------------------------------
    # 3. Ajouter un candidat financier
    # ------------------------------------------

    def ajouter_candidat(
        indice,
        nom,
        annee,
        montant,
        statut="NOT_CHECKED",
        ligne_source=None,
    ):
        candidats.append(
            {
                "nom": nom,
                "annee": annee,
                "valeur": float(montant),
                "ligne_source": (
                    ligne_source
                    if ligne_source is not None
                    else nom
                ),
                "source_table": tableau["table_id"],
                "source_page": tableau["pages"][0],
                "source_ligne": indice,
                "validation_status": statut,
            }
        )

    # ------------------------------------------
    # 4. Documenter une anomalie
    # ------------------------------------------

    def ajouter_anomalie(
        indice,
        nom,
        raison,
        statut="TO_REVIEW",
    ):
        anomalies.append(
            {
                "table_id": tableau["table_id"],
                "page": tableau["pages"][0],
                "ligne": indice,
                "indicateur": nom,
                "raison": raison,
                "statut": statut,
            }
        )

    # ------------------------------------------
    # 5. Corrections PDF de S2M 2025
    # ------------------------------------------

    est_s2m_2025 = (
        tableau.get("table_id") == "DOCLING_018"
        and tableau.get("entreprise") == "S2M"
        and tableau.get("annee") == 2025
    )

    corrections = (
        {
            20: "III RESULTAT D'EXPLOITATION (I-II)",
            37: "* Autres produits non courants",
            41: (
                "* Valeurs nettes d'amortissements "
                "des immobilisations cédées"
            ),
        }
        if est_s2m_2025
        else {}
    )

    # Conserver les lignes Qwen3 et les corrections PDF
    indices = list(
        dict.fromkeys(
            selection.lignes + list(corrections)
        )
    )

    # ------------------------------------------
    # 6. Extraction des montants
    # ------------------------------------------

    for indice in indices:

        if indice <= 0 or indice >= len(lignes):
            continue

        if indice in deja_vus:
            continue

        deja_vus.add(indice)

        cellules = lignes[indice].split(" | ")

        # Vérifier la présence des colonnes nécessaires
        if len(cellules) <= max(colonnes.values()):
            continue

        if len(cellules) <= colonne_libelle:
            continue

        # Libellé détecté automatiquement
        nom = cellules[colonne_libelle].strip()

        if not nom:
            continue

        # --------------------------------------
        # 7. Traitement des corrections PDF
        # --------------------------------------

        if indice in corrections:

            nom_corrige = corrections[indice]
            correction_effectuee = False

            annees = (
                [(2025, 2, 1)]
                if indice == 20
                else [(2025, 2, 1), (2024, 4, 3)]
            )

            for annee, colonne, colonne_signe in annees:

                if len(cellules) <= max(
                    colonne,
                    colonne_signe,
                ):
                    continue

                montant = convertir_montant(
                    cellules[colonne]
                )

                if montant is None:
                    continue

                if cellules[colonne_signe].strip() == "-":
                    montant = -montant

                ajouter_candidat(
                    indice,
                    nom_corrige,
                    annee,
                    montant,
                    "PDF_CONFIRMED",
                    nom,
                )

                correction_effectuee = True

            if correction_effectuee:
                ajouter_anomalie(
                    indice,
                    nom_corrige,
                    (
                        "Libellé ou montant corrigé après "
                        "vérification du PDF original"
                    ),
                    "CORRECTED_PDF_CONFIRMED",
                )

                print(f"CORRECTION PDF : ligne {indice}")

            continue

        # --------------------------------------
        # 8. Détecter les libellés fusionnés
        # --------------------------------------

        if nom.count("*") > 1:
            ajouter_anomalie(
                indice,
                nom,
                (
                    "Plusieurs libellés fusionnés : "
                    "vérification manuelle nécessaire"
                ),
            )
            continue

        # --------------------------------------
        # 9. Extraire les montants par année
        # --------------------------------------

        for annee, colonne in colonnes.items():

            if len(cellules) <= colonne:
                continue

            montant = convertir_montant(
                cellules[colonne]
            )

            if montant is None:
                continue

            # Appliquer un éventuel signe négatif
            colonne_signe = colonnes_signes.get(annee)

            if colonne_signe is not None:

                if len(cellules) <= colonne_signe:
                    continue

                if cellules[colonne_signe].strip() == "-":
                    montant = -montant

            ajouter_candidat(
                indice,
                nom,
                annee,
                montant,
            )

    return candidats, anomalies
