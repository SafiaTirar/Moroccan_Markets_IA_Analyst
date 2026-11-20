from decimal import Decimal


def verifier_comptabilite(candidats):
    # Organiser les montants par année et indicateur
    donnees = {}

    for candidat in candidats:
        annee = candidat["annee"]
        nom = candidat["nom"].strip().upper()

        valeur = Decimal(str(candidat["valeur"]))

        if annee not in donnees:
            donnees[annee] = {}

        donnees[annee][nom] = valeur

    # Les 5 relations comptables
    controles = [
        (
            "Résultat financier",
            "TOTAL IV",
            "TOTAL V",
            "VI RESULTAT FINANCIER",
            "-"
        ),
        (
            "Résultat courant",
            "III RESULTAT D'EXPLOITATION",
            "VI RESULTAT FINANCIER",
            "VII RESULTAT COURANT",
            "+"
        ),
        (
            "Résultat non courant",
            "TOTAL VIII",
            "TOTAL IX",
            "X RESULTAT NON COURANT",
            "-"
        ),
        (
            "Résultat avant impôts",
            "VII RESULTAT COURANT",
            "X RESULTAT NON COURANT",
            "XI RESULTAT AVANT IMPÔTS",
            "+"
        ),
        (
            "Résultat net",
            "XI RESULTAT AVANT IMPÔTS",
            "XII IMPÔTS SUR LES RESULTATS",
            "RESULTAT NET",
            "-"
        ),
    ]

    for annee in sorted(donnees):
        print(f"\n--- CONTRÔLES {annee} ---")

        valeurs = donnees[annee]

        def chercher(prefixe):
            for nom, valeur in valeurs.items():  # noqa: B023
                if nom.startswith(prefixe):
                    return valeur
            return None

        for titre, nom_a, nom_b, nom_resultat, operation in controles:
            a = chercher(nom_a)
            b = chercher(nom_b)
            resultat = chercher(nom_resultat)

            if a is None or b is None or resultat is None:
                print(f"{titre} : NON VÉRIFIABLE (données manquantes)")
                continue

            attendu = a + b if operation == "+" else a - b

            if abs(attendu - resultat) <= Decimal("0.01"):
                print(f"{titre} : OK ({resultat:,.2f})")
            else:
                print(
                    f"{titre} : ANOMALIE "
                    f"(calculé={attendu:,.2f}, extrait={resultat:,.2f})"
                )
