from pydantic import BaseModel, Field


class IndicateurFinancier(BaseModel):
    nom: str
    annee: int
    valeur: float
    ligne_source: str


class ExtractionFinanciere(BaseModel):
    indicateurs: list[IndicateurFinancier] = Field(
        default_factory=list
    )


class CandidatFinancier(IndicateurFinancier):
    entreprise: str
    unite: str | None = None
    perimetre: str | None = None
    source_table: str
    source_page: int
    source_ligne: int
    validation_status: str = "NOT_CHECKED"
