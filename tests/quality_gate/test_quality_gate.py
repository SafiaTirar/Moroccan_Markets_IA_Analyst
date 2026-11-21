from moroccan_markets_ai.quality_gate import Decision, evaluer


def cand(**champs):
    base = {
        "nom": "Capitaux propres",
        "annee": 2023,
        "valeur": 100.0,
        "ligne_source": "Capitaux propres | 100",
        "entreprise": "acme",
        "unite": None,
        "perimetre": None,
        "source_table": "DOCLING_001",
        "source_page": 1,
        "source_ligne": 1,
        "validation_status": "NOT_CHECKED",
        "indicator": "EQUITY",
        "report_year": 2023,
    }
    base.update(champs)
    return base


def decisions(candidats, anomalies=None):
    return [d.decision for d in evaluer(candidats, anomalies)]


def test_liste_vide():
    assert evaluer([]) == []


def test_schema_invalide_est_rejete():
    brut = cand()
    del brut["entreprise"]
    resultat = evaluer([brut])
    assert resultat[0].decision == Decision.REJECTED
    assert "schéma invalide" in resultat[0].raisons[0]


def test_valeur_non_finie_est_rejetee():
    assert decisions([cand(valeur=float("nan"))]) == [Decision.REJECTED]


def test_annee_posterieure_au_rapport_est_rejetee():
    assert decisions([cand(annee=2024)]) == [Decision.REJECTED]


def test_annee_trop_ancienne_va_en_revue():
    assert decisions([cand(annee=2021)]) == [Decision.REVIEW_REQUIRED]


def test_candidat_sans_probleme_reste_not_checked():
    resultat = evaluer([cand()])
    assert resultat[0].decision == Decision.NOT_CHECKED
    assert "source unique" in resultat[0].raisons[0]
    assert "VERIFIED impossible" in resultat[0].raisons[-1]


def test_unite_et_perimetre_inconnus_sont_des_avertissements():
    resultat = evaluer([cand()])
    assert len(resultat[0].avertissements) == 2


def test_doublon_exact_est_rejete():
    resultat = evaluer([cand(), cand()])
    assert resultat[0].decision == Decision.NOT_CHECKED
    assert resultat[1].decision == Decision.REJECTED
    assert "#0" in resultat[1].raisons[0]


def test_meme_valeur_dans_deux_rapports_ne_verifie_jamais():
    a = cand()
    b = cand(report_year=2024, source_table="DOCLING_002")
    resultat = evaluer([a, b])
    assert [d.decision for d in resultat] == [
        Decision.NOT_CHECKED,
        Decision.NOT_CHECKED,
    ]
    assert "contrôle complémentaire réussi" in resultat[0].raisons[0]


def test_meme_valeur_dans_deux_tableaux_du_meme_rapport_reste_not_checked():
    a = cand()
    b = cand(source_table="DOCLING_002")
    resultat = evaluer([a, b])
    assert [d.decision for d in resultat] == [
        Decision.NOT_CHECKED,
        Decision.NOT_CHECKED,
    ]
    assert "contrôle complémentaire réussi" in resultat[0].raisons[0]


def test_valeur_differente_dans_un_autre_rapport_est_une_autre_version():
    a = cand(valeur=100.0)
    b = cand(valeur=95.0, report_year=2024, source_table="DOCLING_002")
    resultat = evaluer([a, b])
    assert [d.decision for d in resultat] == [
        Decision.NOT_CHECKED,
        Decision.NOT_CHECKED,
    ]
    assert any("autre version" in x for x in resultat[0].avertissements)


def test_valeurs_divergentes_dans_la_meme_version_vont_en_revue():
    a = cand(valeur=100.0)
    b = cand(valeur=9.0, source_table="DOCLING_002")
    assert decisions([a, b]) == [Decision.REVIEW_REQUIRED, Decision.REVIEW_REQUIRED]


def test_equilibre_du_bilan_est_un_controle_complementaire():
    actif = cand(indicator="TOTAL_ASSETS", nom="TOTAL ACTIF", valeur=500.0)
    passif = cand(
        indicator="TOTAL_LIABILITIES_AND_EQUITY",
        nom="TOTAL PASSIF",
        valeur=500.0,
        source_table="DOCLING_002",
    )
    resultat = evaluer([actif, passif])
    assert [d.decision for d in resultat] == [
        Decision.NOT_CHECKED,
        Decision.NOT_CHECKED,
    ]
    assert "Total actif = Total passif" in resultat[0].raisons[0]


def test_desequilibre_avec_contexte_complet_va_en_revue():
    contexte = {"unite": "MAD", "perimetre": "SOCIAL"}
    actif = cand(indicator="TOTAL_ASSETS", nom="TOTAL ACTIF", valeur=500.0, **contexte)
    passif = cand(
        indicator="TOTAL_LIABILITIES_AND_EQUITY",
        nom="TOTAL PASSIF",
        valeur=400.0,
        source_table="DOCLING_002",
        **contexte,
    )
    assert decisions([actif, passif]) == [
        Decision.REVIEW_REQUIRED,
        Decision.REVIEW_REQUIRED,
    ]


def test_desequilibre_sans_contexte_est_non_concluant():
    actif = cand(indicator="TOTAL_ASSETS", nom="TOTAL ACTIF", valeur=500.0)
    passif = cand(
        indicator="TOTAL_LIABILITIES_AND_EQUITY",
        nom="TOTAL PASSIF",
        valeur=400.0,
        source_table="DOCLING_002",
    )
    resultat = evaluer([actif, passif])
    assert [d.decision for d in resultat] == [
        Decision.NOT_CHECKED,
        Decision.NOT_CHECKED,
    ]
    assert any("non concluant" in a for a in resultat[0].avertissements)


def test_tableau_signale_va_en_revue():
    anomalies = [
        {
            "entreprise": "acme",
            "report_year": 2023,
            "table_id": "DOCLING_001",
            "raison": "TABLEAU_TO_REVIEW",
        }
    ]
    assert decisions([cand()], anomalies) == [Decision.REVIEW_REQUIRED]


def test_montant_signale_va_en_revue():
    anomalies = [
        {
            "entreprise": "acme",
            "report_year": 2023,
            "table_id": "DOCLING_001",
            "indicator": "EQUITY",
            "observation_year": 2023,
            "raison": "MONTANT_A_VERIFIER",
        }
    ]
    assert decisions([cand()], anomalies) == [Decision.REVIEW_REQUIRED]


def test_pdf_confirmed_declare_ne_verifie_pas():
    resultat = evaluer([cand(validation_status="PDF_CONFIRMED")])
    assert resultat[0].decision == Decision.NOT_CHECKED
    assert any("PDF_CONFIRMED" in a for a in resultat[0].avertissements)


def test_le_module_ne_produit_jamais_verified():
    lot = [
        cand(),
        cand(report_year=2024, source_table="DOCLING_002"),
        cand(
            indicator="TOTAL_ASSETS", nom="TOTAL ACTIF", valeur=500.0, source_table="D3"
        ),
        cand(
            indicator="TOTAL_LIABILITIES_AND_EQUITY",
            nom="TOTAL PASSIF",
            valeur=500.0,
            source_table="D4",
        ),
        cand(validation_status="PDF_CONFIRMED", annee=2022, valeur=7.0),
    ]
    assert Decision.VERIFIED not in decisions(lot)


def test_compatible_avec_le_format_s2m_sans_indicator_ni_report_year():
    brut = cand()
    del brut["indicator"]
    del brut["report_year"]
    resultat = evaluer([brut])
    assert resultat[0].decision == Decision.NOT_CHECKED
    assert any("report_year" in a for a in resultat[0].avertissements)
