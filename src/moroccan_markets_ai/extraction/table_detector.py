import re
import unicodedata


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte)

    texte = "".join(
        c for c in texte
        if not unicodedata.combining(c)
    )

    texte = texte.upper()

    # Uniformiser les apostrophes
    texte = texte.replace("’", "'")

    # Supprimer les espaces autour des apostrophes
    texte = re.sub(r"\s*'\s*", "'", texte)

    # Uniformiser les espaces
    texte = re.sub(r"\s+", " ", texte)

    return texte.strip()


def detecter_type_tableau(tableau):
    lignes = tableau["contenu"].splitlines()

    if not lignes:
        return None

    # Examiner les 15 premières lignes
    debut = normaliser(" ".join(lignes[:15]))

    # Examiner également le contenu complet
    contenu_complet = normaliser(" ".join(lignes))

    # Exclure les tableaux ESG et TFR
    if (
        "TABLEAU DE FORMATION DES RESULTATS" in debut
        or "ETAT DES SOLDES DE GESTION" in debut
    ):
        return None

    # Examiner les trois premières lignes pour trouver
    # le véritable en-tête du tableau bancaire.
    premiers_libelles = [
        normaliser(ligne.split("|")[0])
        for ligne in lignes[:3]
    ]

    est_actif_bancaire = "ACTIF" in premiers_libelles
    est_passif_bancaire = "PASSIF" in premiers_libelles

    # ----------------------------------------
    # 1. Détection des bilans bancaires
    # ----------------------------------------

    # Bilan passif bancaire
    if (
        est_passif_bancaire
        and "DETTES ENVERS LA CLIENTELE" in contenu_complet
        and "CAPITAUX PROPRES" in contenu_complet
        and "TOTAL PASSIF" in contenu_complet
    ):
        return "BILAN_PASSIF"

    # Bilan actif bancaire
    if (
        est_actif_bancaire
        and "PRETS ET CREANCES SUR LA CLIENTELE" in contenu_complet
        and "TOTAL ACTIF" in contenu_complet
    ):
        return "BILAN_ACTIF"

    # ----------------------------------------
    # 2. Détection du bilan actif classique
    # ----------------------------------------
    if (
        "ACTIF" in debut
        and "IMMOBILISATIONS" in debut
        and (
            "BRUT" in debut
            or "ACTIF IMMOBILISE" in debut
        )
        and "FINANCEMENT PERMANENT" not in debut
    ):
        return "BILAN_ACTIF"

    # ----------------------------------------
    # 3. Détection du bilan passif classique
    # ----------------------------------------
    if (
        (
            "PASSIF" in debut
            and (
                "CAPITAUX PROPRES" in debut
                or "PROPRES CAPITAUX" in debut
            )
            and (
                "CAPITAL" in debut
                or "RESERVES" in debut
                or "DETTES DE FINANCEMENT" in debut
            )
        )
        or (
            "FINANCEMENT PERMANENT" in debut
            and "CAPITAUX PROPRES" in debut
            and "DETTES DE FINANCEMENT" in contenu_complet
            and "PASSIF CIRCULANT" in contenu_complet
        )
    ):
        return "BILAN_PASSIF"

    # ----------------------------------------
    # 4. Détection du compte de résultat IFRS
    # ----------------------------------------
    if (
        "OPERATIONNEL RESULTAT" in contenu_complet
        and "ENDETTEMENT'L DE COUT" in contenu_complet
        and "AFFAIRES'D CHIFFRE" in contenu_complet
        and "NET RESULTAT" in contenu_complet
    ):
        return "COMPTE_RESULTAT_IFRS"

    # ----------------------------------------
    # 5. Détection du CPC marocain
    # ----------------------------------------
    if (
        (
            "PRODUITS D'EXPLOITATION" in debut
            and "CHARGES D'EXPLOITATION" in debut
            and (
                "ACHATS" in debut
                or "CHARGES DE PERSONNEL" in debut
            )
        )
        or (
            "EXPLOITATION'D PRODUITS" in debut
            and "EXPLOITATION'D CHARGES" in contenu_complet
            and "FINANCIERS PRODUITS" in contenu_complet
            and "FINANCIERES CHARGES" in contenu_complet
            and "NET RESULTAT" in contenu_complet
        )
    ):
        return "CPC"

    return None


def identifier_tableaux_financiers(tableaux):
    resultats = []

    for tableau in tableaux:
        type_tableau = detecter_type_tableau(tableau)

        if type_tableau:
            resultats.append({
                "table_id": tableau["table_id"],
                "type": type_tableau,
                "pages": tableau["pages"]
            })

    return resultats
