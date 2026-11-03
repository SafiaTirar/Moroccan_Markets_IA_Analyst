# Livraison Financial Quality Gate - 45 rapports AMMC

Origine : extraction structuree des tableaux Docling.
Le traitement global ne lance pas Qwen3.

Fichiers :
- candidats_fqg.json : observations candidates a valider.
- anomalies.json : tableaux et montants necessitant revision.
- bilan.json : resultats par entreprise et annee.

Statut : NOT_CHECKED. Aucune observation n'est VERIFIED.

Attention :
- unite et perimetre peuvent etre null.
- Les valeurs sont des montants bruts, sans conversion systematique en MAD.
- Des doublons peuvent exister.
- Une annee d'observation peut differer de l'annee du rapport.
- Certains rapports n'ont aucune observation extraite.
- Le format reprend les champs S2M, avec indicator et report_year
  supplementaires ; compatibilite FQG a confirmer.
- Ne pas remplacer le fichier S2M_2025 deja partage.
