#!/usr/bin/env python3
import argparse
import csv
import math
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

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


def is_missing(v: object) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except Exception:
        pass
    return str(v).strip() == ""


def s(v: object) -> str:
    return "" if is_missing(v) else str(v).strip()


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(text: str) -> str:
    text = strip_accents(str(text).replace('"', "")).lower()
    return normalize_spaces(text)


def tokenize_multiselect(value: object) -> List[str]:
    if is_missing(value):
        return []
    raw = str(value).replace("\r", "\n")
    parts = re.split(r"[,;\n]+", raw)
    return [normalize_spaces(p.replace('"', "")) for p in parts if normalize_spaces(p.replace('"', ""))]


def log_change(changes: List[Dict[str, str]], dataset: str, idx: int, field: str, original: object, normalized: object, rule: str) -> None:
    o = s(original)
    n = s(normalized)
    if o == n:
        return
    changes.append(
        {
            "dataset": dataset,
            "row_index": str(idx),
            "campo": field,
            "valor_original": o,
            "valor_normalizado": n,
            "regla": rule,
        }
    )


def normalize_dni(value: object) -> Tuple[str, str]:
    raw = s(value)
    if raw == "":
        return "", "VACIO"
    digits = re.sub(r"\D", "", raw)
    if digits == "":
        return "", "NO_NUMERICO"
    if len(digits) in (7, 8):
        return digits, "OK"
    return digits, "ANOMALIA_LONGITUD"


def parse_date_any(value: object) -> Optional[pd.Timestamp]:
    raw = s(value)
    if raw == "":
        return None
    # prioriza ISO explícito para evitar ambigüedad
    iso_like = bool(re.match(r"^\d{4}-\d{2}-\d{2}", raw))
    if iso_like:
        dt = pd.to_datetime(raw, errors="coerce", utc=True)
        if pd.notna(dt):
            return dt
    # intento formato local
    dt = pd.to_datetime(raw, errors="coerce", utc=True, dayfirst=True)
    if pd.notna(dt):
        return dt
    # fallback estandar
    dt2 = pd.to_datetime(raw, errors="coerce", utc=True)
    if pd.notna(dt2):
        return dt2
    return None


def normalize_date_value(value: object) -> Tuple[str, str]:
    raw = s(value)
    if raw == "":
        return "", "VACIA"
    dt = parse_date_any(raw)
    if dt is None:
        return "", "INVALIDA_FORMATO"
    now = pd.Timestamp.now(tz="UTC")
    if dt > now:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "FUTURA"
    if dt.year < 1900:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "ANIO_IMPROBABLE"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "OK"


def compute_age(birth: pd.Timestamp, ref: pd.Timestamp) -> int:
    b = birth.date()
    r = ref.date()
    age = r.year - b.year - ((r.month, r.day) < (b.month, b.day))
    return int(age)


def normalize_phone(value: object) -> Tuple[str, str, str]:
    raw = s(value)
    if raw == "":
        return "", "", "VACIO"
    nums = re.findall(r"\d{8,13}", raw)
    if len(nums) >= 2:
        return nums[0], nums[1], "MULTIPLE_EN_CAMPO"
    if len(nums) == 1:
        return nums[0], "", "OK"
    digits = re.sub(r"\D", "", raw)
    if 8 <= len(digits) <= 13:
        return digits, "", "OK"
    return digits, "", "INVALIDO_LONGITUD"


def parse_money(value: object) -> Tuple[Optional[float], str]:
    raw = s(value)
    if raw == "":
        return None, "VACIO"
    cleaned = raw.replace("$", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = cleaned.replace(".", "").replace(",", ".")
    num = pd.to_numeric(cleaned, errors="coerce")
    if pd.isna(num):
        return None, "INVALIDO_FORMATO"
    v = float(num)
    if v < 0:
        return v, "NEGATIVO"
    if v == 0:
        return v, "CERO"
    if v > 100_000_000:
        return v, "EXTREMO_GT_100M"
    return v, "OK"


def to_si_no_nsnc(value: object) -> str:
    raw = s(value)
    if raw == "":
        return ""
    n = normalize_text(raw)
    if n in SI_TOKENS:
        return "Si"
    if n in NO_TOKENS:
        return "No"
    if n in NSNC_TOKENS:
        return "Ns / Nc"
    return ""


def to_cud(value: object) -> str:
    raw = s(value)
    if raw == "":
        return ""
    n = normalize_text(raw)
    if n in SI_TOKENS:
        return "Si"
    if n in NO_TOKENS:
        return "No"
    if n in CUD_EN_TRAMITE:
        return "En tramite"
    return ""


def to_dependencia(value: object) -> str:
    raw = s(value)
    if raw == "":
        return ""
    return DEPENDENCIA_CANON.get(normalize_text(raw), "")


def normalize_genero(genero: object, sexo: object) -> str:
    g = normalize_text(s(genero))
    sx = normalize_text(s(sexo))
    val = g if g else sx
    if val in {"m", "masculino", "varon", "hombre"}:
        return "Varon"
    if val in {"f", "femenino", "mujer"}:
        return "Mujer"
    if val in {"varon trans", "transmasculino", "hombre trans", "mujer trans/ travesti", "mujer trans", "travesti"}:
        return "Varon trans"
    return ""


def normalize_elementos(value: object) -> str:
    toks = tokenize_multiselect(value)
    if not toks:
        return ""
    canon = []
    for t in toks:
        canon.append(" ".join(w.capitalize() for w in normalize_text(t).split()))
    return ",".join(sorted(set(canon)))


def extract_catalogo_tipo(path_variables: Path) -> Set[str]:
    df = pd.read_csv(path_variables, dtype=str)
    row = df[df["Código de columna"].fillna("").str.strip() == "tipo-discapacidad_disc"]
    if row.empty:
        return set(CATALOGO_TIPO_BASE)
    opts = str(row.iloc[0].get("Opciones") or "")
    out: Set[str] = set()
    for ln in opts.split("\n"):
        t = normalize_spaces(ln.replace('"', ""))
        if not t:
            continue
        key = normalize_text(t)
        out.add(SINONIMOS_TIPO.get(key, " ".join(w.capitalize() for w in key.split())))
    return out or set(CATALOGO_TIPO_BASE)


def normalize_tipo_disc(value: object, catalogo: Set[str]) -> Tuple[str, str, str]:
    toks = tokenize_multiselect(value)
    if not toks:
        return "", "VACIO", ""
    canonical = []
    invalid = []
    for t in toks:
        key = normalize_text(t)
        mapped = SINONIMOS_TIPO.get(key, " ".join(w.capitalize() for w in key.split()))
        if mapped in catalogo:
            canonical.append(mapped)
        else:
            invalid.append(t)
    canonical_u = sorted(set(canonical))
    invalid_u = sorted(set(invalid))
    if invalid_u:
        return ",".join(canonical_u), "FUERA_CATALOGO", ",".join(invalid_u)
    return ",".join(canonical_u), "OK", ""


def consistencia_disc(disc: str, tipo_norm: str, elem_norm: str) -> str:
    has_tipo = bool(tipo_norm)
    has_elem = bool(elem_norm and elem_norm.lower() != "ninguno")
    if disc == "No" and (has_tipo or has_elem):
        return "INCONSISTENCIA_DISCAPACIDAD"
    if disc == "Si" and not has_tipo:
        return "FALTA_TIPO_DISCAPACIDAD"
    if disc == "" and (has_tipo or has_elem):
        return "INFORMACION_DISCAPACIDAD_SIN_DECLARACION"
    if disc == "":
        return "SIN_DATO"
    return "OK"


def resolve_cud_dependencia_columns(df: pd.DataFrame) -> Tuple[str, str]:
    a = "CUD-Discapacidad_disc"
    b = "dependencia_discapacidad_disc"
    if a not in df.columns or b not in df.columns:
        return a, b
    a_vals = set(normalize_text(v) for v in df[a].dropna().astype(str) if str(v).strip())
    b_vals = set(normalize_text(v) for v in df[b].dropna().astype(str) if str(v).strip())
    dep_words = set(DEPENDENCIA_CANON.keys())
    cud_words = SI_TOKENS | NO_TOKENS | CUD_EN_TRAMITE | NSNC_TOKENS
    a_dep, b_dep = len(a_vals & dep_words), len(b_vals & dep_words)
    a_cud, b_cud = len(a_vals & cud_words), len(b_vals & cud_words)
    if a_dep > b_dep and b_cud >= a_cud:
        return b, a
    return a, b


def row_completitud_score(df: pd.DataFrame) -> pd.Series:
    return df.fillna("").astype(str).apply(lambda c: c.str.strip().ne(""), axis=0).sum(axis=1)


def normalize_dataset(df: pd.DataFrame, dataset: str, catalogo_tipo: Set[str], changes: List[Dict[str, str]]) -> pd.DataFrame:
    out = df.copy()

    for c in [
        "dni",
        "fecha_nacimiento",
        "edad",
        "telefono",
        "monto_ingresomensual_grupofamiliar_tb",
        "monto_subjetivo_gastrosmensuales_tb",
        "discapacidad_disc",
        "tipo-discapacidad_disc",
        "CUD-Discapacidad_disc",
        "dependencia_discapacidad_disc",
        "elementos_ortopedicos_disc",
        "genero",
        "sexo",
        "censado_fecha",
    ]:
        if c not in out.columns:
            out[c] = ""

    out["dni_norm"] = ""
    out["dni_estado"] = ""
    out["fecha_nacimiento_norm"] = ""
    out["fecha_nacimiento_estado"] = ""
    out["edad_norm"] = ""
    out["edad_estado"] = ""
    out["edad_fuente"] = ""
    out["telefono_norm"] = ""
    out["telefono_secundario"] = ""
    out["telefono_estado"] = ""
    out["ingreso_mensual_norm"] = ""
    out["ingreso_estado"] = ""
    out["gasto_mensual_norm"] = ""
    out["gasto_estado"] = ""

    out["discapacidad_disc_norm"] = ""
    out["tipo_discapacidad_norm"] = ""
    out["tipo_discapacidad_estado"] = ""
    out["tipo_discapacidad_tokens_invalidos"] = ""
    out["cud_norm"] = ""
    out["dependencia_discapacidad_norm"] = ""
    out["elementos_ortopedicos_norm"] = ""
    out["consistencia_discapacidad_flag"] = ""
    out["genero_norm"] = ""
    out["persona_censada_flag"] = ""

    col_cud, col_dep = resolve_cud_dependencia_columns(out)

    for idx, row in out.iterrows():
        dni_norm, dni_estado = normalize_dni(row.get("dni"))
        out.at[idx, "dni_norm"] = dni_norm
        out.at[idx, "dni_estado"] = dni_estado
        log_change(changes, dataset, idx, "dni", row.get("dni"), dni_norm, "normalizar_dni")

        ref_dt = parse_date_any(row.get("censado_fecha"))
        if ref_dt is None:
            ref_dt = pd.Timestamp("2025-12-31", tz="UTC")

        birth_raw = row.get("fecha_nacimiento")
        birth_dt = parse_date_any(birth_raw)
        birth_state = "VACIA"
        birth_norm = ""
        if birth_dt is not None:
            birth_norm = birth_dt.date().isoformat()
            birth_state = "OK"
            if birth_dt > ref_dt:
                birth_state = "FUTURA"
            elif birth_dt.year < 1900:
                birth_state = "ANIO_IMPROBABLE"
        elif s(birth_raw):
            birth_state = "INVALIDA_FORMATO"

        edad_raw = s(row.get("edad"))
        edad_num = pd.to_numeric(edad_raw.replace(",", ".") if edad_raw else "", errors="coerce")
        edad_state = "VACIA"
        edad_norm = ""
        edad_fuente = ""

        if pd.notna(edad_num):
            edad_int = int(round(float(edad_num)))
            if edad_int < 0 or edad_int > 110:
                edad_state = "INVALIDA_RANGO"
            elif float(edad_num) % 1 != 0:
                edad_state = "DECIMAL_AJUSTADA"
                edad_norm = str(edad_int)
                edad_fuente = "original"
            else:
                edad_state = "OK"
                edad_norm = str(edad_int)
                edad_fuente = "original"
        elif edad_raw:
            edad_state = "INVALIDA_FORMATO"

        # inferencia de fecha por edad si fecha futura y edad válida
        if birth_state == "FUTURA" and edad_norm:
            edad_i = int(edad_norm)
            year = ref_dt.year - edad_i
            month = birth_dt.month if birth_dt is not None else 7
            day = birth_dt.day if birth_dt is not None else 1
            try:
                inferred = pd.Timestamp(date(year, month, day), tz="UTC")
            except ValueError:
                inferred = pd.Timestamp(date(year, 7, 1), tz="UTC")
            birth_dt = inferred
            birth_norm = inferred.date().isoformat()
            birth_state = "INFERIDA_DESDE_EDAD"
            log_change(changes, dataset, idx, "fecha_nacimiento", birth_raw, birth_norm, "inferir_anio_por_edad")

        if birth_dt is not None and birth_state in {"OK", "INFERIDA_DESDE_EDAD"}:
            edad_calc = compute_age(birth_dt, ref_dt)
            if 0 <= edad_calc <= 110:
                prev = edad_norm
                edad_norm = str(edad_calc)
                if prev and prev != edad_norm:
                    edad_state = "RECALCULADA_DIFIERE"
                elif not prev:
                    edad_state = "RECALCULADA"
                edad_fuente = "recalculada"

        phone_norm, phone_sec, phone_state = normalize_phone(row.get("telefono"))
        out.at[idx, "telefono_norm"] = phone_norm
        out.at[idx, "telefono_secundario"] = phone_sec
        out.at[idx, "telefono_estado"] = phone_state

        ingreso_val, ingreso_state = parse_money(row.get("monto_ingresomensual_grupofamiliar_tb"))
        gasto_val, gasto_state = parse_money(row.get("monto_subjetivo_gastrosmensuales_tb"))
        out.at[idx, "ingreso_mensual_norm"] = "" if ingreso_val is None else f"{ingreso_val:.2f}"
        out.at[idx, "gasto_mensual_norm"] = "" if gasto_val is None else f"{gasto_val:.2f}"
        out.at[idx, "ingreso_estado"] = ingreso_state
        out.at[idx, "gasto_estado"] = gasto_state

        disc_norm = to_si_no_nsnc(row.get("discapacidad_disc"))
        tipo_norm, tipo_state, tipo_invalid = normalize_tipo_disc(row.get("tipo-discapacidad_disc"), catalogo_tipo)
        cud_norm = to_cud(row.get(col_cud))
        dep_norm = to_dependencia(row.get(col_dep))
        elem_norm = normalize_elementos(row.get("elementos_ortopedicos_disc"))
        cons_flag = consistencia_disc(disc_norm, tipo_norm, elem_norm)
        genero_norm = normalize_genero(row.get("genero"), row.get("sexo"))
        censado_flag = "SI" if parse_date_any(row.get("censado_fecha")) is not None else "NO"

        out.at[idx, "fecha_nacimiento_norm"] = birth_norm
        out.at[idx, "fecha_nacimiento_estado"] = birth_state
        out.at[idx, "edad_norm"] = edad_norm
        out.at[idx, "edad_estado"] = edad_state
        out.at[idx, "edad_fuente"] = edad_fuente

        out.at[idx, "discapacidad_disc_norm"] = disc_norm
        out.at[idx, "tipo_discapacidad_norm"] = tipo_norm
        out.at[idx, "tipo_discapacidad_estado"] = tipo_state
        out.at[idx, "tipo_discapacidad_tokens_invalidos"] = tipo_invalid
        out.at[idx, "cud_norm"] = cud_norm
        out.at[idx, "dependencia_discapacidad_norm"] = dep_norm
        out.at[idx, "elementos_ortopedicos_norm"] = elem_norm
        out.at[idx, "consistencia_discapacidad_flag"] = cons_flag
        out.at[idx, "genero_norm"] = genero_norm
        out.at[idx, "persona_censada_flag"] = censado_flag

        log_change(changes, dataset, idx, "telefono", row.get("telefono"), phone_norm, "normalizar_telefono")
        log_change(changes, dataset, idx, "edad", row.get("edad"), edad_norm, "normalizar_recalcular_edad")
        log_change(changes, dataset, idx, "monto_ingresomensual_grupofamiliar_tb", row.get("monto_ingresomensual_grupofamiliar_tb"), out.at[idx, "ingreso_mensual_norm"], "normalizar_monto")
        log_change(changes, dataset, idx, "monto_subjetivo_gastrosmensuales_tb", row.get("monto_subjetivo_gastrosmensuales_tb"), out.at[idx, "gasto_mensual_norm"], "normalizar_monto")
        log_change(changes, dataset, idx, "tipo-discapacidad_disc", row.get("tipo-discapacidad_disc"), tipo_norm, "normalizar_discapacidad")
        log_change(changes, dataset, idx, "genero", row.get("genero"), genero_norm, "normalizar_genero")

    # Normalización genérica para todas las columnas de fecha existentes
    date_cols = [c for c in out.columns if "fecha" in c.lower()]
    for date_col in date_cols:
        norm_col = f"{date_col}_norm"
        state_col = f"{date_col}_estado"
        if norm_col not in out.columns:
            out[norm_col] = ""
        if state_col not in out.columns:
            out[state_col] = ""
        # fecha_nacimiento ya tiene lógica específica (incluye inferencia por edad)
        if date_col == "fecha_nacimiento":
            continue
        for idx, raw in out[date_col].items():
            norm, state = normalize_date_value(raw)
            out.at[idx, norm_col] = norm
            out.at[idx, state_col] = state
            log_change(changes, dataset, idx, date_col, raw, norm, "normalizar_fecha_generica")

    # outliers IQR montos
    for val_col, state_col, label in [
        ("ingreso_mensual_norm", "ingreso_estado", "OUTLIER_IQR"),
        ("gasto_mensual_norm", "gasto_estado", "OUTLIER_IQR"),
    ]:
        nums = pd.to_numeric(out[val_col], errors="coerce").dropna()
        if len(nums) >= 10:
            q1 = nums.quantile(0.25)
            q3 = nums.quantile(0.75)
            iqr = q3 - q1
            upper = q3 + 1.5 * iqr
            mask = pd.to_numeric(out[val_col], errors="coerce") > upper
            out.loc[mask & out[state_col].eq("OK"), state_col] = label

    out["row_completitud_score"] = row_completitud_score(out)
    return out


def dedupe_padron(padron: pd.DataFrame) -> pd.DataFrame:
    work = padron.copy()
    work["censado_fecha_dt"] = pd.to_datetime(work.get("censado_fecha", ""), errors="coerce", utc=True)
    work["dup_dni_flag"] = work["dni_norm"].duplicated(keep=False) & work["dni_norm"].ne("")
    work["dup_es_fila_ganadora"] = True
    work["dup_resolucion_metodo"] = "UNICO"

    keep_indices = []
    for dni, g in work[work["dni_norm"].ne("")].groupby("dni_norm", dropna=False):
        if len(g) == 1:
            keep_indices.append(g.index[0])
            continue
        best_score = g["row_completitud_score"].max()
        cands = g[g["row_completitud_score"] == best_score]
        if len(cands) == 1:
            winner = cands.index[0]
            method = "COMPLETITUD"
        else:
            cands = cands.sort_values("censado_fecha_dt", ascending=False)
            winner = cands.index[0]
            method = "COMPLETITUD_MAS_FECHA"
        keep_indices.append(winner)
        work.loc[g.index, "dup_es_fila_ganadora"] = False
        work.loc[winner, "dup_es_fila_ganadora"] = True
        work.loc[g.index, "dup_resolucion_metodo"] = method

    dedup = work.loc[sorted(set(keep_indices) | set(work[work["dni_norm"].eq("")].index))].copy()
    dedup = dedup.drop(columns=["censado_fecha_dt"], errors="ignore")
    return dedup


def join_grupo_padron(grupo: pd.DataFrame, padron_dedup: pd.DataFrame) -> pd.DataFrame:
    out = grupo.copy()
    if "dni_jefehogar" not in out.columns:
        out["dni_jefehogar"] = ""
    out[["dni_jefehogar_norm", "dni_jefehogar_estado"]] = out["dni_jefehogar"].apply(lambda x: pd.Series(normalize_dni(x)))

    pad_key = padron_dedup[["dni_norm", "nombrecompleto"]].copy()
    pad_key = pad_key.rename(columns={"nombrecompleto": "jefe_nombre_padron"})
    merged = out.merge(pad_key, how="left", left_on="dni_jefehogar_norm", right_on="dni_norm", suffixes=("", "_pad"))

    merged["match_padron_flag"] = "MATCH"
    merged.loc[merged["dni_jefehogar_norm"].eq(""), "match_padron_flag"] = "JEFE_VACIO"
    merged.loc[merged["dni_jefehogar_norm"].ne("") & merged["jefe_nombre_padron"].isna(), "match_padron_flag"] = "JEFE_NO_ENCONTRADO"
    return merged


def build_reports(
    padron: pd.DataFrame,
    grupo: pd.DataFrame,
    padron_dedup: pd.DataFrame,
    grupo_join: pd.DataFrame,
    changes: Sequence[Dict[str, str]],
    outdir: Path,
) -> None:
    rep = []
    for name, df in [("padronfme", padron), ("grupo_familiar", grupo)]:
        rep.append({"dataset": name, "metrica": "filas", "valor": len(df)})
        rep.append({"dataset": name, "metrica": "dni_anomalia_longitud", "valor": int(df["dni_estado"].eq("ANOMALIA_LONGITUD").sum())})
        rep.append({"dataset": name, "metrica": "telefonos_invalidos", "valor": int(df["telefono_estado"].eq("INVALIDO_LONGITUD").sum())})
        rep.append({"dataset": name, "metrica": "fechas_invalidas", "valor": int(df["fecha_nacimiento_estado"].isin(["INVALIDA_FORMATO", "FUTURA", "ANIO_IMPROBABLE"]).sum())})
        rep.append({"dataset": name, "metrica": "edades_invalidas", "valor": int(df["edad_estado"].isin(["INVALIDA_FORMATO", "INVALIDA_RANGO"]).sum())})
        rep.append({"dataset": name, "metrica": "tipo_discapacidad_fuera_catalogo", "valor": int(df["tipo_discapacidad_estado"].eq("FUERA_CATALOGO").sum())})
        rep.append({"dataset": name, "metrica": "inconsistencias_discapacidad", "valor": int(df["consistencia_discapacidad_flag"].isin(["INCONSISTENCIA_DISCAPACIDAD", "FALTA_TIPO_DISCAPACIDAD", "INFORMACION_DISCAPACIDAD_SIN_DECLARACION"]).sum())})

    dup_original = int(padron["dni_norm"].ne("").groupby(padron["dni_norm"]).transform("sum").gt(1).sum())
    rep.append({"dataset": "padronfme", "metrica": "dni_duplicados_original", "valor": dup_original})
    rep.append({"dataset": "padronfme_dedup", "metrica": "filas", "valor": len(padron_dedup)})
    rep.append({"dataset": "grupo_familiar_con_padron", "metrica": "jefe_no_encontrado", "valor": int(grupo_join["match_padron_flag"].eq("JEFE_NO_ENCONTRADO").sum())})

    pd.DataFrame(rep).to_csv(outdir / "reporte_calidad.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)

    # Reportes específicos discapacidad
    freq_rows = []
    invalid_rows = []
    matriz_rows = []
    for name, df in [("padronfme", padron), ("grupo_familiar", grupo)]:
        freq = (
            df["tipo_discapacidad_norm"].fillna("").astype(str).str.split(",").explode().str.strip()
        )
        freq = freq[freq.ne("")].value_counts()
        for k, v in freq.items():
            freq_rows.append({"dataset": name, "tipo_discapacidad_norm": k, "cantidad": int(v)})

        invalid = df["tipo_discapacidad_tokens_invalidos"].fillna("").astype(str)
        invalid = invalid[invalid.ne("")].str.split(",").explode().str.strip().value_counts()
        for k, v in invalid.items():
            invalid_rows.append({"dataset": name, "token_fuera_catalogo": k, "cantidad": int(v)})

        m = (
            df.groupby(["discapacidad_disc_norm", "cud_norm", "consistencia_discapacidad_flag"], dropna=False)
            .size()
            .reset_index(name="cantidad")
        )
        m.insert(0, "dataset", name)
        matriz_rows.extend(m.to_dict("records"))

    pd.DataFrame(freq_rows, columns=["dataset", "tipo_discapacidad_norm", "cantidad"]).to_csv(
        outdir / "reporte_discapacidad_frecuencias.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL
    )
    pd.DataFrame(invalid_rows, columns=["dataset", "token_fuera_catalogo", "cantidad"]).to_csv(
        outdir / "reporte_discapacidad_tokens_fuera_catalogo.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL
    )
    pd.DataFrame(matriz_rows).to_csv(
        outdir / "reporte_discapacidad_matriz_consistencia.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL
    )

    pd.DataFrame(list(changes), columns=["dataset", "row_index", "campo", "valor_original", "valor_normalizado", "regla"]).to_csv(
        outdir / "registro_cambios.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL
    )

    casos = []
    for name, df in [("padronfme", padron), ("grupo_familiar", grupo)]:
        mask = (
            df["dni_estado"].eq("ANOMALIA_LONGITUD")
            | df["telefono_estado"].eq("INVALIDO_LONGITUD")
            | df["fecha_nacimiento_estado"].isin(["INVALIDA_FORMATO", "FUTURA", "ANIO_IMPROBABLE"])
            | df["edad_estado"].isin(["INVALIDA_FORMATO", "INVALIDA_RANGO"])
            | df["ingreso_estado"].isin(["INVALIDO_FORMATO", "NEGATIVO", "EXTREMO_GT_100M", "OUTLIER_IQR"])
            | df["gasto_estado"].isin(["INVALIDO_FORMATO", "NEGATIVO", "EXTREMO_GT_100M", "OUTLIER_IQR"])
            | df["consistencia_discapacidad_flag"].isin(["INCONSISTENCIA_DISCAPACIDAD", "FALTA_TIPO_DISCAPACIDAD", "INFORMACION_DISCAPACIDAD_SIN_DECLARACION"])
            | df["tipo_discapacidad_estado"].eq("FUERA_CATALOGO")
        )
        c = df[mask].copy()
        c.insert(0, "dataset", name)
        casos.append(c)

    if casos:
        pd.concat(casos, ignore_index=True).to_csv(
            outdir / "casos_revision_manual.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalizacion integral de censo")
    parser.add_argument("--padron", required=True)
    parser.add_argument("--grupo", required=True)
    parser.add_argument("--variables", required=True)
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    padron = pd.read_csv(args.padron, dtype=str)
    grupo = pd.read_csv(args.grupo, dtype=str)
    catalogo = extract_catalogo_tipo(Path(args.variables))

    changes: List[Dict[str, str]] = []
    padron_n = normalize_dataset(padron, "padronfme", catalogo, changes)
    grupo_n = normalize_dataset(grupo, "grupo_familiar", catalogo, changes)

    padron_d = dedupe_padron(padron_n)
    grupo_join = join_grupo_padron(grupo_n, padron_d)

    padron_n.to_csv(outdir / "padronfme_normalizado.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    grupo_n.to_csv(outdir / "grupo_familiar_normalizado.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    padron_d.to_csv(outdir / "padronfme_deduplicado.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    grupo_join.to_csv(outdir / "grupo_familiar_con_padron.csv", index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)

    build_reports(padron_n, grupo_n, padron_d, grupo_join, changes, outdir)


if __name__ == "__main__":
    main()
