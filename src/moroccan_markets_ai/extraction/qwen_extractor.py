import json
import urllib.error
import urllib.request

from pydantic import ValidationError

from .schemas import ExtractionFinanciere


def extraire_indicateurs(tableau):
    """
    Découpe un tableau en petits groupes de lignes
    et extrait les indicateurs financiers avec Qwen3.
    """

    # 1. Récupérer les lignes du tableau
    lignes = tableau["contenu"].splitlines()

    # Garder les deux premières lignes comme contexte
    # Première ligne : en-têtes des colonnes
    entete = lignes[:1]

    # Toutes les autres lignes sont analysées une seule fois
    donnees = lignes[1:]

    taille_groupe = 4
    indicateurs = []
    # 2. Définir les instructions pour Qwen3
    instructions = """
Tu es un extracteur de données financières marocaines.

Analyse les lignes d'un tableau comptable.

Règles obligatoires :
- Les colonnes correspondent à 2025 et 2024.
- Identifie les indicateurs financiers présents.
- Pour chaque indicateur, extrais les deux années
  lorsque les valeurs sont disponibles.
- Utilise uniquement les montants écrits.
- Ne calcule aucune valeur manquante.
- N'invente aucun indicateur ni aucune valeur.
- Ignore les lignes ambiguës ou décalées.
- Ne confonds pas les titres et les totaux.
- Pour ligne_source, recopie uniquement le libellé
  comptable exact, sans les montants ni les séparateurs.
- Produis un JSON compact, sans indentation.
- Ne répète pas les explications.
- Retourne uniquement du JSON valide.

Format attendu :
{
  "indicateurs": [
    {
      "nom": "CHIFFRE D'AFFAIRES",
      "annee": 2025,
      "valeur": 366496287.39,
      "ligne_source": "ligne exacte du CSV"
    }
  ]
}
"""

    # 3. Traiter chaque groupe de lignes
    for debut in range(0, len(donnees), taille_groupe):

        groupe = donnees[debut:debut + taille_groupe]

        contenu_groupe = "\n".join(entete + groupe)

        # 4. Préparer la requête Ollama
        donnees_ollama = {
            "model": "qwen3:4b",
            "stream": False,
            "think": False,
            "format": "json",
            "options": {
                "num_predict": 2048,
                "temperature": 0,
            },
            "messages": [
                {
                    "role": "system",
                    "content": instructions,
                },
                {
                    "role": "user",
                    "content": contenu_groupe,
                },
            ],
        }

        requete = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=json.dumps(donnees_ollama).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        numero_groupe = debut // taille_groupe + 1

        print(f"Traitement du groupe {numero_groupe}...")

        # 5. Envoyer la requête à Qwen3
        try:
            with urllib.request.urlopen(
                requete,
                timeout=180,
            ) as reponse:
                resultat = json.load(reponse)

            # 6. Récupérer la réponse JSON
            texte = resultat["message"]["content"]

            # 7. Vérifier la structure avec Pydantic
            extraction = ExtractionFinanciere.model_validate_json(
                texte
            )

            # 8. Ajouter les indicateurs trouvés
            indicateurs.extend(extraction.indicateurs)

            print(
                f"Groupe {numero_groupe} terminé : "
                f"{len(extraction.indicateurs)} indicateurs"
            )

        # 9. Gérer les erreurs spécifiques
        except (
            TimeoutError,
            urllib.error.URLError,
            json.JSONDecodeError,
            ValidationError,
            KeyError,
        ) as erreur:
            print(
                f"Erreur dans le groupe {numero_groupe} : "
                f"{erreur}"
            )
            # Ne pas présenter une extraction partielle
            # comme un résultat complet.
            raise

    # 10. Retourner les résultats structurés
    return ExtractionFinanciere(
        indicateurs=indicateurs
    )
