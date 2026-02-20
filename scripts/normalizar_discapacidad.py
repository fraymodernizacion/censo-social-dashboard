#!/usr/bin/env python3
import argparse
import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

import pandas as pd


CATALOGO_TIPO_BASE = {
    "Discapacidad psicosocial",
    "Discapacidades multiples",
    "Fisica Motora",
    "Fisica Visceral",
    "Intelectual",
    "Mental",
    "Sensorial auditivo",
    "Sensorial visual",
    "Sindrome de Down",
    "Trastorno del espectro autista",
    "Otro",
}

SINONIMOS_TIPO = {
    "discapacidades multip les": "Discapacidades multiples",
    "discapacidades multiples": "Discapacidades multiples",
    "fisica motora": "Fisica Motora",
    "fisica visceral": "Fisica Visceral",
    "sensorial auditiva": "Sensorial auditivo",
    "sensorial auditivo": "Sensorial auditivo",
    "sensorial visual": "Sensorial visual",
    "sindrome de down": "Sindrome de Down",
    "trastorno espectro autista": "Trastorno del espectro autista",
    "trastorno del espectro autista": "Trastorno del espectro autista",
    "discapacidad psicosocial": "Discapacidad psicosocial",
    "mental": "Mental",
    "intelectual": "Intelectual",
    "otro": "Otro",
}

SI_TOKENS = {"si", "s", "sí"}
NO_TOKENS = {"no", "n"}
NSNC_TOKENS = {"nsnc", "ns / nc", "ns/nc", "n s n c", "no sabe", "no contesta"}
CUD_EN_TRAMITE = {"en tramite", "en trámite"}

DEPENDENCIA_CANON = {
    "dependiente": "Dependiente",
    "semidependiente": "Semidependiente",
    "independiente": "Independiente",
}


@dataclass
class ProcesamientoResultado:
    nombre: str
    df: pd.DataFrame
    cambios: List[Dict[str, str]]
    invalid_tokens: Dict[str, int]
    freq_tipo: Dict[str, int]
    matriz: pd.DataFrame


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return str(value).strip() == ""


def to_str(value: object) -> str:
    if is_missing(value):
        return ""
    return str(value)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(text: str) -> str:
    text = text.strip().replace('"', "")
    text = strip_accents(text)
    text = normalize_spaces(text.lower())
    return text


def tokenize_multiselect(value: str) -> List[str]:
    if is_missing(value):
        return []
    raw = to_str(value).replace("\r", "\n")
    pieces = re.split(r"[,;\n]+", raw)
    tokens = [normalize_spaces(p.replace('"', "")) for p in pieces if normalize_spaces(p.replace('"', ""))]
    return tokens


def to_si_no_nsnc(value: str) -> str:
    if is_missing(value):
        return ""
    norm = normalize_text(value)
    if norm in SI_TOKENS:
        return "Si"
    if norm in NO_TOKENS:
        return "No"
    if norm in NSNC_TOKENS:
        return "Ns / Nc"
    return ""


def to_cud(value: str) -> str:
    if is_missing(value):
        return ""
    norm = normalize_text(value)
    if norm in SI_TOKENS:
        return "Si"
    if norm in NO_TOKENS:
        return "No"
    if norm in CUD_EN_TRAMITE:
        return "En tramite"
    return ""


def to_dependencia(value: str) -> str:
    if is_missing(value):
        return ""
    norm = normalize_text(value)
    return DEPENDENCIA_CANON.get(norm, "")


def extract_catalogo_tipo(path_variables: Path) -> Set[str]:
    df_vars = pd.read_csv(path_variables, dtype=str)
    row = df_vars[df_vars["Código de columna"].fillna("").str.strip() == "tipo-discapacidad_disc"]
    if row.empty:
        return set(CATALOGO_TIPO_BASE)

    opciones_raw = str(row.iloc[0].get("Opciones") or "")
    tokens = []
    for line in opciones_raw.split("\n"):
        clean = normalize_spaces(line.replace('"', ""))
        if clean:
            tokens.append(clean)

    if not tokens:
        return set(CATALOGO_TIPO_BASE)

    catalogo = set()
    for t in tokens:
        key = normalize_text(t)
        canon = SINONIMOS_TIPO.get(key)
        if canon:
            catalogo.add(canon)
        else:
            title = " ".join(word.capitalize() for word in key.split())
            catalogo.add(title)
    return catalogo


def resolver_columnas(df: pd.DataFrame) -> Tuple[str, str]:
    """Retorna (col_cud, col_dependencia) detectando inversión por contenido."""
    col_a = "CUD-Discapacidad_disc"
    col_b = "dependencia_discapacidad_disc"

    if col_a not in df.columns or col_b not in df.columns:
        return col_a, col_b

    a_vals = set(normalize_text(v) for v in df[col_a].dropna().astype(str) if str(v).strip())
    b_vals = set(normalize_text(v) for v in df[col_b].dropna().astype(str) if str(v).strip())

    dep_words = set(DEPENDENCIA_CANON.keys())
    cud_words = SI_TOKENS | NO_TOKENS | CUD_EN_TRAMITE | NSNC_TOKENS

    a_dep = len(a_vals & dep_words)
    b_dep = len(b_vals & dep_words)
    a_cud = len(a_vals & cud_words)
    b_cud = len(b_vals & cud_words)

    if a_dep > b_dep and b_cud >= a_cud:
        return col_b, col_a
    return col_a, col_b


def normalize_tipo_discapacidad(value: str, catalogo: Set[str]) -> Tuple[str, str, List[str]]:
    tokens = tokenize_multiselect(value)
    if not tokens:
        return "", "VACIO", []

    canonical: List[str] = []
    invalid: List[str] = []
    for tok in tokens:
        key = normalize_text(tok)
        mapped = SINONIMOS_TIPO.get(key)
        if mapped is None:
            title = " ".join(word.capitalize() for word in key.split())
            mapped = title

        if mapped in catalogo:
            canonical.append(mapped)
        else:
            invalid.append(tok)

    canonical_unique = sorted(set(canonical))
    invalid_unique = sorted(set(invalid))

    if not canonical_unique and invalid_unique:
        return "", "FUERA_CATALOGO", invalid_unique
    if canonical_unique and invalid_unique:
        return ",".join(canonical_unique), "FUERA_CATALOGO", invalid_unique
    return ",".join(canonical_unique), "OK", []


def normalize_elementos(value: str) -> str:
    tokens = tokenize_multiselect(value)
    if not tokens:
        return ""
    canon = []
    for tok in tokens:
        key = normalize_text(tok)
        title = " ".join(word.capitalize() for word in key.split())
        canon.append(title)
    return ",".join(sorted(set(canon)))


def consistencia_flag(disc: str, tipo_norm: str, elementos_norm: str) -> str:
    has_tipo = bool(tipo_norm)
    has_elementos = bool(elementos_norm and elementos_norm.lower() != "ninguno")

    if disc == "No" and has_tipo:
        return "INCONSISTENCIA_DISCAPACIDAD"
    if disc == "No" and has_elementos:
        return "INCONSISTENCIA_DISCAPACIDAD"
    if disc == "Si" and not has_tipo:
        return "FALTA_TIPO_DISCAPACIDAD"
    if disc == "":
        if has_tipo or has_elementos:
            return "INFORMACION_DISCAPACIDAD_SIN_DECLARACION"
        return "SIN_DATO"
    return "OK"


def append_change(changes: List[Dict[str, str]], dataset: str, idx: int, campo: str, original: str, normalizado: str, regla: str) -> None:
    original_clean = to_str(original)
    normalizado_clean = to_str(normalizado)
    if original_clean == normalizado_clean:
        return
    changes.append(
        {
            "dataset": dataset,
            "row_index": str(idx),
            "campo": campo,
            "valor_original": original_clean,
            "valor_normalizado": normalizado_clean,
            "regla": regla,
        }
    )


def process_dataset(nombre: str, df: pd.DataFrame, catalogo_tipo: Set[str]) -> ProcesamientoResultado:
    cambios: List[Dict[str, str]] = []
    invalid_tokens: Dict[str, int] = {}

    for needed in [
        "discapacidad_disc",
        "tipo-discapacidad_disc",
        "CUD-Discapacidad_disc",
        "dependencia_discapacidad_disc",
        "elementos_ortopedicos_disc",
    ]:
        if needed not in df.columns:
            df[needed] = ""

    col_cud, col_dep = resolver_columnas(df)

    output = df.copy()
    output["tipo_discapacidad_norm"] = ""
    output["tipo_discapacidad_estado"] = ""
    output["tipo_discapacidad_tokens_invalidos"] = ""
    output["consistencia_discapacidad_flag"] = ""
    output["discapacidad_disc_norm"] = ""
    output["cud_norm"] = ""
    output["dependencia_discapacidad_norm"] = ""
    output["elementos_ortopedicos_norm"] = ""

    for idx, row in output.iterrows():
        disc_orig = to_str(row.get("discapacidad_disc"))
        disc_norm = to_si_no_nsnc(disc_orig)
        append_change(cambios, nombre, idx, "discapacidad_disc", disc_orig, disc_norm, "normalizacion_si_no_nsnc")

        tipo_orig = to_str(row.get("tipo-discapacidad_disc"))
        tipo_norm, tipo_estado, invalid = normalize_tipo_discapacidad(tipo_orig, catalogo_tipo)
        append_change(cambios, nombre, idx, "tipo-discapacidad_disc", tipo_orig, tipo_norm, "tokenizacion_y_mapeo_catalogo")

        tokens_invalid_str = ",".join(invalid)

        cud_orig = to_str(row.get(col_cud))
        cud_norm = to_cud(cud_orig)
        append_change(cambios, nombre, idx, col_cud, cud_orig, cud_norm, "normalizacion_cud")

        dep_orig = to_str(row.get(col_dep))
        dep_norm = to_dependencia(dep_orig)
        append_change(cambios, nombre, idx, col_dep, dep_orig, dep_norm, "normalizacion_dependencia")

        elem_orig = to_str(row.get("elementos_ortopedicos_disc"))
        elem_norm = normalize_elementos(elem_orig)
        append_change(cambios, nombre, idx, "elementos_ortopedicos_disc", elem_orig, elem_norm, "normalizacion_multiseleccion")

        flag = consistencia_flag(disc_norm, tipo_norm, elem_norm)

        output.at[idx, "discapacidad_disc_norm"] = disc_norm
        output.at[idx, "tipo_discapacidad_norm"] = tipo_norm
        output.at[idx, "tipo_discapacidad_estado"] = tipo_estado
        output.at[idx, "tipo_discapacidad_tokens_invalidos"] = tokens_invalid_str
        output.at[idx, "cud_norm"] = cud_norm
        output.at[idx, "dependencia_discapacidad_norm"] = dep_norm
        output.at[idx, "elementos_ortopedicos_norm"] = elem_norm
        output.at[idx, "consistencia_discapacidad_flag"] = flag

        for tok in invalid:
            invalid_tokens[tok] = invalid_tokens.get(tok, 0) + 1

    freq_tipo = (
        output["tipo_discapacidad_norm"]
        .fillna("")
        .astype(str)
        .str.split(",")
        .explode()
        .str.strip()
    )
    freq_tipo = freq_tipo[freq_tipo != ""].value_counts().to_dict()

    matriz = (
        output.groupby(["discapacidad_disc_norm", "cud_norm", "consistencia_discapacidad_flag"], dropna=False)
        .size()
        .reset_index(name="cantidad")
        .sort_values("cantidad", ascending=False)
    )

    return ProcesamientoResultado(
        nombre=nombre,
        df=output,
        cambios=cambios,
        invalid_tokens=invalid_tokens,
        freq_tipo=freq_tipo,
        matriz=matriz,
    )


def write_report(path: Path, resultados: Sequence[ProcesamientoResultado]) -> None:
    rows = []
    for res in resultados:
        rows.append({"dataset": res.nombre, "metrica": "filas", "valor": len(res.df)})
        rows.append(
            {
                "dataset": res.nombre,
                "metrica": "tipo_discapacidad_fuera_catalogo",
                "valor": int((res.df["tipo_discapacidad_estado"] == "FUERA_CATALOGO").sum()),
            }
        )
        rows.append(
            {
                "dataset": res.nombre,
                "metrica": "inconsistencias_discapacidad",
                "valor": int(
                    res.df["consistencia_discapacidad_flag"].isin(
                        [
                            "INCONSISTENCIA_DISCAPACIDAD",
                            "FALTA_TIPO_DISCAPACIDAD",
                            "INFORMACION_DISCAPACIDAD_SIN_DECLARACION",
                        ]
                    ).sum()
                ),
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)


def write_frequency(path: Path, resultados: Sequence[ProcesamientoResultado]) -> None:
    rows = []
    for res in resultados:
        for tipo, cnt in sorted(res.freq_tipo.items(), key=lambda kv: (-kv[1], kv[0])):
            rows.append({"dataset": res.nombre, "tipo_discapacidad_norm": tipo, "cantidad": cnt})
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)


def write_invalid_tokens(path: Path, resultados: Sequence[ProcesamientoResultado]) -> None:
    rows = []
    for res in resultados:
        for tok, cnt in sorted(res.invalid_tokens.items(), key=lambda kv: (-kv[1], kv[0])):
            rows.append({"dataset": res.nombre, "token_fuera_catalogo": tok, "cantidad": cnt})
    pd.DataFrame(rows, columns=["dataset", "token_fuera_catalogo", "cantidad"]).to_csv(
        path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL
    )


def write_matriz(path: Path, resultados: Sequence[ProcesamientoResultado]) -> None:
    matriz_all = []
    for res in resultados:
        m = res.matriz.copy()
        m.insert(0, "dataset", res.nombre)
        matriz_all.append(m)
    pd.concat(matriz_all, ignore_index=True).to_csv(path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)


def main() -> None:
    parser = argparse.ArgumentParser(description="Normaliza campos de discapacidad para padron y grupo familiar.")
    parser.add_argument("--padron", required=True, help="Ruta CSV padron")
    parser.add_argument("--grupo", required=True, help="Ruta CSV grupo familiar")
    parser.add_argument("--variables", required=True, help="Ruta CSV variables/nomenclatura")
    parser.add_argument("--outdir", required=True, help="Directorio de salida")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    padron = pd.read_csv(args.padron, dtype=str)
    grupo = pd.read_csv(args.grupo, dtype=str)
    catalogo_tipo = extract_catalogo_tipo(Path(args.variables))

    res_padron = process_dataset("padronfme", padron, catalogo_tipo)
    res_grupo = process_dataset("grupo_familiar", grupo, catalogo_tipo)
    resultados = [res_padron, res_grupo]

    res_padron.df.to_csv(outdir / "padronfme_normalizado.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    res_grupo.df.to_csv(outdir / "grupo_familiar_normalizado.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)

    cambios = pd.DataFrame(res_padron.cambios + res_grupo.cambios)
    cambios.to_csv(outdir / "registro_cambios.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)

    write_report(outdir / "reporte_calidad.csv", resultados)
    write_frequency(outdir / "reporte_discapacidad_frecuencias.csv", resultados)
    write_invalid_tokens(outdir / "reporte_discapacidad_tokens_fuera_catalogo.csv", resultados)
    write_matriz(outdir / "reporte_discapacidad_matriz_consistencia.csv", resultados)

    casos_revision = []
    for res in resultados:
        flagged = res.df[
            res.df["consistencia_discapacidad_flag"].isin(
                [
                    "INCONSISTENCIA_DISCAPACIDAD",
                    "FALTA_TIPO_DISCAPACIDAD",
                    "INFORMACION_DISCAPACIDAD_SIN_DECLARACION",
                ]
            )
            | (res.df["tipo_discapacidad_estado"] == "FUERA_CATALOGO")
        ].copy()
        flagged.insert(0, "dataset", res.nombre)
        casos_revision.append(flagged)
    if casos_revision:
        pd.concat(casos_revision, ignore_index=True).to_csv(
            outdir / "casos_revision_manual.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL
        )


if __name__ == "__main__":
    main()
