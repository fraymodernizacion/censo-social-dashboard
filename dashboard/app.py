#!/usr/bin/env python3
import io
import importlib.util
from pathlib import Path
from typing import List

import pandas as pd
import pydeck as pdk
import streamlit as st
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    PDF_ENABLED = True
except Exception:
    PDF_ENABLED = False

EXCEL_ENGINES = []
if importlib.util.find_spec('xlsxwriter') is not None:
    EXCEL_ENGINES.append('xlsxwriter')
if importlib.util.find_spec('openpyxl') is not None:
    EXCEL_ENGINES.append('openpyxl')
EXCEL_ENABLED = len(EXCEL_ENGINES) > 0

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT = PROJECT_ROOT / 'output'
VAR_FILE = Path('/Users/matiascardozo/Downloads/Preguntas Censo 2025 - variables y nomenclatura - variables.csv')

st.set_page_config(page_title='Censo FME 2025 - Informe', layout='wide')
st.title('Informe Estadistico - Censo FME 2025')
st.caption('Dashboard analitico con base en padron, grupo familiar y diccionario de variables')

st.markdown(
    """
<style>
div[data-testid="stMetric"] label {font-size: 0.95rem !important;}
div[data-testid="stMetricValue"] {font-size: 1.6rem !important;}
.chip {
  display:inline-block;
  padding:6px 10px;
  margin:2px 6px 2px 0;
  background:#eef3f8;
  border:1px solid #d8e3ef;
  border-radius:999px;
  font-size:0.85rem;
}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data
def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str)


def num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors='coerce')


def non_empty(s: pd.Series) -> pd.Series:
    return s.fillna('').astype(str).str.strip().ne('')


def val_counts(df: pd.DataFrame, col: str, topn: int = 12, exclude_missing: bool = True) -> pd.DataFrame:
    if col not in df.columns:
        return pd.DataFrame(columns=['valor', 'cantidad'])
    s = df[col].fillna('').astype(str).str.strip()
    if exclude_missing:
        s = s[s.ne('')]
    vc = s.value_counts().head(topn)
    out = vc.reset_index()
    out.columns = ['valor', 'cantidad']
    return out


def token_counts(df: pd.DataFrame, col: str, topn: int = 20) -> pd.DataFrame:
    if col not in df.columns:
        return pd.DataFrame(columns=['valor', 'cantidad'])
    s = df[col].fillna('').astype(str).str.strip()
    s = s[s.ne('')]
    if s.empty:
        return pd.DataFrame(columns=['valor', 'cantidad'])
    tokens = s.str.replace('\r', '\n', regex=False).str.split(r'[,;\n]+').explode().astype(str).str.strip()
    tokens = tokens[tokens.ne('')]
    if tokens.empty:
        return pd.DataFrame(columns=['valor', 'cantidad'])
    vc = tokens.value_counts().head(topn).reset_index()
    vc.columns = ['valor', 'cantidad']
    return vc


def add_pct(df_counts: pd.DataFrame, denom: int, col_count: str = 'cantidad', out_col: str = 'pct_sobre_viv_censadas_%') -> pd.DataFrame:
    if df_counts.empty:
        return df_counts
    out = df_counts.copy()
    base = max(int(denom), 1)
    out[out_col] = (out[col_count].astype(float) / base * 100).round(2)
    return out


def to_excel_bytes(df: pd.DataFrame, sheet_name: str = 'datos') -> bytes:
    if not EXCEL_ENABLED:
        raise RuntimeError('Excel no disponible: faltan xlsxwriter/openpyxl')
    buffer = io.BytesIO()
    last_err = None
    for engine in EXCEL_ENGINES:
        try:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine=engine) as writer:
                df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
            buffer.seek(0)
            return buffer.read()
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f'No se pudo generar Excel: {last_err}')


def to_pdf_bytes(df: pd.DataFrame, title: str = 'Reporte', filters: List[str] = None) -> bytes:
    if not PDF_ENABLED:
        raise RuntimeError('PDF no disponible: falta reportlab')
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    elements = [Paragraph(title, styles['Heading2']), Spacer(1, 8)]
    if filters:
        elements.append(Paragraph('Filtros aplicados:', styles['Heading4']))
        for f in filters:
            elements.append(Paragraph(f'- {f}', styles['BodyText']))
        elements.append(Spacer(1, 8))

    body_style = styles['BodyText']
    body_style.fontSize = 10
    body_style.leading = 12
    body_style.wordWrap = 'CJK'

    header_style = styles['BodyText']
    header_style.fontSize = 11
    header_style.leading = 13
    header_style.wordWrap = 'CJK'

    def _pdf_cell_text(v: object, max_chars: int = 220) -> str:
        txt = str(v) if v is not None else ''
        txt = txt.replace('\r', ' ').replace('\n', ' ')
        txt = ' '.join(txt.split())
        if len(txt) > max_chars:
            return txt[: max_chars - 1] + '…'
        return txt

    page_width = landscape(A4)[0]
    usable_width = page_width - 48  # márgenes izq/der de 24

    if df.empty:
        table_data = [[Paragraph('Sin datos', body_style)]]
        table = Table(table_data, colWidths=[usable_width])
        table.setStyle(
            TableStyle(
                [
                    ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
                    ('FONTSIZE', (0, 0), (-1, -1), 12),
                    ('LEFTPADDING', (0, 0), (-1, -1), 2),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(table)
    else:
        cols = list(df.columns)

        # Evita celdas con ancho negativo en tablas con muchas columnas.
        min_col_width = 85
        max_cols_per_block = max(1, int(usable_width // min_col_width))
        col_blocks = [cols[i:i + max_cols_per_block] for i in range(0, len(cols), max_cols_per_block)]

        for idx, cols_block in enumerate(col_blocks, start=1):
            if len(col_blocks) > 1:
                elements.append(Paragraph(f'Bloque de columnas {idx}/{len(col_blocks)}', styles['Heading4']))
                elements.append(Spacer(1, 4))

            body = df[cols_block].fillna('').astype(str).values.tolist()
            table_data = [[Paragraph(_pdf_cell_text(c, max_chars=120), header_style) for c in cols_block]]
            for row in body:
                table_data.append([Paragraph(_pdf_cell_text(v, max_chars=220), body_style) for v in row])

            ncols = max(len(cols_block), 1)
            per_col = max(min_col_width, usable_width / ncols)
            col_widths = [per_col] * ncols

            table = Table(table_data, repeatRows=1, colWidths=col_widths)
            table.setStyle(
                TableStyle(
                    [
                        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
                        ('FONTSIZE', (0, 0), (-1, -1), 12),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 2),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                        ('TOPPADDING', (0, 0), (-1, -1), 2),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                    ]
                )
            )
            elements.append(table)
            elements.append(Spacer(1, 6))

    doc.build(elements)
    buffer.seek(0)
    return buffer.read()


def token_universe(df: pd.DataFrame, col: str) -> List[str]:
    if col not in df.columns:
        return []
    s = df[col].fillna('').astype(str).str.strip()
    s = s[s.ne('')]
    if s.empty:
        return []
    tokens = s.str.replace('\r', '\n', regex=False).str.split(r'[,;\n]+').explode().astype(str).str.strip()
    tokens = sorted(tokens[tokens.ne('')].unique().tolist())
    return tokens


def filter_by_tokens(df: pd.DataFrame, col: str, selected_tokens: List[str], mode: str = 'ANY') -> pd.DataFrame:
    if col not in df.columns or not selected_tokens:
        return df.copy()
    s = df[col].fillna('').astype(str).str.lower()
    escaped = [t.lower() for t in selected_tokens]
    if mode == 'ALL':
        mask = pd.Series(True, index=df.index)
        for t in escaped:
            mask = mask & s.str.contains(t, regex=False)
    else:
        mask = pd.Series(False, index=df.index)
        for t in escaped:
            mask = mask | s.str.contains(t, regex=False)
    return df[mask].copy()


def apply_condition(df: pd.DataFrame, col: str, op: str, v1: str = '', v2: str = '', tokens: List[str] = None, token_mode: str = 'ANY') -> pd.Series:
    if col not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    s_raw = df[col].fillna('').astype(str).str.strip()
    s_low = s_raw.str.lower()

    if op == 'esta vacio':
        return s_raw.eq('')
    if op == 'no vacio':
        return s_raw.ne('')
    if op == 'igual':
        return s_raw.eq(v1)
    if op == 'contiene':
        return s_raw.str.contains(v1, case=False, regex=False)
    if op == 'distinto':
        return s_raw.ne(v1)

    if op in {'>', '>=', '<', '<=', 'entre'}:
        n = pd.to_numeric(s_raw, errors='coerce')
        a = pd.to_numeric(pd.Series([v1]), errors='coerce').iloc[0]
        b = pd.to_numeric(pd.Series([v2]), errors='coerce').iloc[0] if v2 != '' else None
        if pd.isna(a):
            return pd.Series([False] * len(df), index=df.index)
        if op == '>':
            return n.gt(a)
        if op == '>=':
            return n.ge(a)
        if op == '<':
            return n.lt(a)
        if op == '<=':
            return n.le(a)
        if b is None or pd.isna(b):
            return pd.Series([False] * len(df), index=df.index)
        lo, hi = (a, b) if a <= b else (b, a)
        return n.between(lo, hi, inclusive='both')

    if op == 'token multirespuesta':
        selected = tokens or []
        if not selected:
            return pd.Series([True] * len(df), index=df.index)
        m = pd.Series(True, index=df.index) if token_mode == 'ALL' else pd.Series(False, index=df.index)
        for t in selected:
            hit = s_low.str.contains(str(t).lower(), regex=False)
            if token_mode == 'ALL':
                m = m & hit
            else:
                m = m | hit
        return m

    return pd.Series([False] * len(df), index=df.index)


def normalize_yes_no(text: pd.Series) -> pd.Series:
    s = text.fillna('').astype(str).str.strip().str.lower()
    s = s.str.replace('í', 'i', regex=False)
    return s


def build_priorization_matrix(pad_df: pd.DataFrame) -> pd.DataFrame:
    if pad_df.empty or 'localidad-vv' not in pad_df.columns:
        return pd.DataFrame()
    d = pad_df.copy()
    d = d[non_empty(d['localidad-vv'])].copy()
    if d.empty:
        return pd.DataFrame()

    # Flags de riesgo por vivienda
    s_tieneb = normalize_yes_no(d.get('tienebaño_vv', pd.Series('', index=d.index)))
    s_basural = normalize_yes_no(d.get('basuralpermanente_300mts_vv', pd.Series('', index=d.index)))
    s_recolec = normalize_yes_no(d.get('servicioregular_recoleccionresiduos_vv', pd.Series('', index=d.index)))
    s_napas = normalize_yes_no(d.get('problema_napas_vv', pd.Series('', index=d.index)))
    s_med = normalize_yes_no(d.get('consiguio_medicamentos_salud', pd.Series('', index=d.index)))
    s_desemp = normalize_yes_no(d.get('trabajo_tb', pd.Series('', index=d.index)))

    d['r_sin_banio'] = s_tieneb.isin(['no', 'sin', 'no tiene']).astype(int)
    d['r_basural'] = s_basural.isin(['si', 'sí']).astype(int)
    d['r_sin_recoleccion'] = s_recolec.isin(['no']).astype(int)
    d['r_napas'] = s_napas.isin(['si', 'sí']).astype(int)
    d['r_no_medicacion'] = s_med.isin(['no']).astype(int)
    d['r_desempleo'] = s_desemp.str.contains('desemple', regex=False).astype(int)

    grp = d.groupby('localidad-vv', dropna=False).agg(
        viviendas=('dni', 'count'),
        sin_banio=('r_sin_banio', 'sum'),
        basural=('r_basural', 'sum'),
        sin_recoleccion=('r_sin_recoleccion', 'sum'),
        napas=('r_napas', 'sum'),
        no_medicacion=('r_no_medicacion', 'sum'),
        desempleo=('r_desempleo', 'sum'),
    ).reset_index()

    for c in ['sin_banio', 'basural', 'sin_recoleccion', 'napas', 'no_medicacion', 'desempleo']:
        grp[f'{c}_pct'] = (grp[c] / grp['viviendas'] * 100).round(2)

    # Score ponderado (0-100 aprox)
    grp['score_prioridad'] = (
        grp['sin_banio_pct'] * 0.25
        + grp['basural_pct'] * 0.20
        + grp['sin_recoleccion_pct'] * 0.20
        + grp['napas_pct'] * 0.15
        + grp['no_medicacion_pct'] * 0.10
        + grp['desempleo_pct'] * 0.10
    ).round(2)

    grp['nivel_prioridad'] = pd.cut(
        grp['score_prioridad'],
        bins=[-1, 20, 40, 60, 1000],
        labels=['Baja', 'Media', 'Alta', 'Critica']
    ).astype(str)

    return grp.sort_values(['score_prioridad', 'viviendas'], ascending=[False, False])


GLOSSARY = {
    'Mascotas': {
        'tiene_mascotas': 'Indica si en la vivienda hay mascotas.',
        'cuantas_mascotas': 'Cantidad total de mascotas declaradas por vivienda.',
        'cuantas_mascotas_castradas': 'Cantidad de mascotas castradas declaradas.',
    },
    'Vivienda y servicios': {
        'tienebaño_vv': 'Disponibilidad de baño en la vivienda.',
        'servicioregular_recoleccionresiduos_vv': 'Si recibe recolección de residuos regular.',
        'basuralpermanente_300mts_vv': 'Si hay basural permanente a 300 metros.',
    },
    'Empleo y movilidad': {
        'trabajo_tb': 'Situación laboral declarada.',
        'movilidad_trabajo_tb': 'Medio principal para trasladarse al trabajo.',
        'aportes_jubilatorios_tb': 'Indica aportes previsionales vinculados al trabajo.',
    },
    'Salud y discapacidad': {
        'cobertura_salud': 'Tipo/s de cobertura de salud declarada/s.',
        'consiguio_medicamentos_salud': 'Si pudo conseguir medicamentos recetados.',
        'dependencia_discapacidad_disc': 'Grado de dependencia asociado a discapacidad.',
    },
    'Asistencia social': {
        'beneficiario_plan_asistencia': 'Si recibe algún plan de asistencia.',
        'miembro_flia_auh_asistencia': 'Si hay AUH en la familia.',
        'frecuencia_merenderocomedor_asistencia': 'Frecuencia de asistencia a merendero/comedor.',
    },
}

HUMAN_LABELS = {
    'tienebaño_vv': 'Tiene baño',
    'servicioregular_recoleccionresiduos_vv': 'Recolección regular',
    'basuralpermanente_300mts_vv': 'Basural a 300m',
    'problema_napas_vv': 'Problema de napas',
    'medicamentosrecetados_ultimoaño_salud': 'Medicamentos recetados último año',
    'consiguio_medicamentos_salud': 'Consiguió medicamentos',
    'cobertura_salud': 'Cobertura de salud',
    'trabajo_tb': 'Situación laboral',
    'tiene_mascotas': 'Tiene mascotas',
    'cuantas_mascotas': 'Cantidad de mascotas',
    'cuantas_mascotas_castradas': 'Mascotas castradas',
    'CUD-Discapacidad_disc': 'CUD discapacidad',
    'discapacidad_disc_norm': 'Discapacidad',
    'localidad-vv': 'Localidad',
    'genero_norm': 'Género',
}


def hlabel(col: str) -> str:
    return HUMAN_LABELS.get(col, col)


def apply_normalized_dates_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Si existe <col>_norm, lo usa para mostrar <col> y evitar formatos crudos."""
    out = df.copy()
    norm_cols = [c for c in out.columns if c.endswith('_norm')]
    for nc in norm_cols:
        base = nc[:-5]
        if base in out.columns:
            mask = non_empty(out[nc])
            out.loc[mask, base] = out.loc[mask, nc]

    # Formato legible para todas las columnas de fecha en visualización.
    date_like_cols = [c for c in out.columns if 'fecha' in c.lower()]
    for c in date_like_cols:
        s = out[c].fillna('').astype(str).str.strip()
        dt = pd.to_datetime(s, errors='coerce', utc=True)
        if 'nacimiento' in c.lower():
            formatted = dt.dt.strftime('%d/%m/%Y').fillna('')
        else:
            formatted = dt.dt.strftime('%d/%m/%Y %H:%M').fillna('')
        mask = non_empty(s) & formatted.ne('')
        out.loc[mask, c] = formatted[mask]
    return out


def parse_gps(df: pd.DataFrame, col: str = 'ubicacion-vv') -> pd.DataFrame:
    if col not in df.columns:
        return pd.DataFrame(columns=['lat', 'lon'])
    s = df[col].fillna('').astype(str).str.strip()
    g = s[s.ne('')].str.extract(r'^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$')
    g.columns = ['lat', 'lon']
    g['lat'] = pd.to_numeric(g['lat'], errors='coerce')
    g['lon'] = pd.to_numeric(g['lon'], errors='coerce')
    g = g.dropna()
    g = g[(g['lat'].between(-90, 90)) & (g['lon'].between(-180, 180))]
    return g


def add_google_maps_link(df: pd.DataFrame, coord_col: str = 'ubicacion-vv') -> pd.DataFrame:
    out = df.copy()
    if coord_col not in out.columns:
        return out
    coords = out[coord_col].fillna('').astype(str).str.strip()
    match = coords.str.extract(r'^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$')
    lat = match[0]
    lon = match[1]
    link = 'https://www.google.com/maps?q=' + lat + ',' + lon
    out['ubicacion_maps_link'] = ''
    valid = lat.notna() & lon.notna()
    out.loc[valid, 'ubicacion_maps_link'] = link[valid]
    return out


def select_existing_columns(df: pd.DataFrame, preferred: List[str]) -> List[str]:
    cols: List[str] = []
    seen = set()
    for c in preferred:
        if c in df.columns and c not in seen:
            cols.append(c)
            seen.add(c)
    return cols


def censada(df: pd.DataFrame) -> pd.Series:
    if 'persona_censada_flag' in df.columns:
        return df['persona_censada_flag'].fillna('').eq('SI')
    if 'censado_fecha' in df.columns:
        return non_empty(df['censado_fecha'])
    return pd.Series([False] * len(df), index=df.index)


def con_nombre(df: pd.DataFrame) -> pd.Series:
    if 'nombrecompleto' in df.columns:
        return non_empty(df['nombrecompleto'])
    return pd.Series([True] * len(df), index=df.index)


def build_coverage_table(var_df: pd.DataFrame, pad: pd.DataFrame, gru: pd.DataFrame) -> pd.DataFrame:
    if var_df.empty:
        return pd.DataFrame()
    v = var_df.copy()
    v['Código de columna'] = v['Código de columna'].fillna('').astype(str).str.strip()
    v['Módulo'] = v['Módulo'].ffill()
    v = v[v['Código de columna'].ne('')].copy()

    rows: List[dict] = []
    n_pad = max(len(pad), 1)
    n_gru = max(len(gru), 1)

    for _, r in v.iterrows():
        c = r['Código de columna']
        p_non = int(non_empty(pad[c]).sum()) if c in pad.columns else 0
        g_non = int(non_empty(gru[c]).sum()) if c in gru.columns else 0
        rows.append({
            'modulo': r.get('Módulo', ''),
            'pregunta': r.get('Pregunta', ''),
            'columna': c,
            'en_padron': c in pad.columns,
            'en_grupo': c in gru.columns,
            'padron_completitud_%': round((p_non / n_pad) * 100, 2),
            'grupo_completitud_%': round((g_non / n_gru) * 100, 2),
            'padron_no_vacios': p_non,
            'grupo_no_vacios': g_non,
        })
    return pd.DataFrame(rows)


def build_extra_columns_table(var_df: pd.DataFrame, pad: pd.DataFrame, gru: pd.DataFrame) -> pd.DataFrame:
    var_codes = set()
    if not var_df.empty and 'Código de columna' in var_df.columns:
        var_codes = set(var_df['Código de columna'].fillna('').astype(str).str.strip())
        var_codes.discard('')

    rows: List[dict] = []
    for origen, df in [('padron', pad), ('grupo', gru)]:
        n = max(len(df), 1)
        for c in df.columns:
            if c in var_codes:
                continue
            non = int(non_empty(df[c]).sum())
            rows.append({
                'origen': origen,
                'columna': c,
                'no_vacios': non,
                'completitud_%': round((non / n) * 100, 2),
            })
    return pd.DataFrame(rows).sort_values(['origen', 'completitud_%', 'columna'], ascending=[True, False, True])


def combined_people(pad: pd.DataFrame, gru: pd.DataFrame) -> pd.DataFrame:
    commons = sorted(set(pad.columns) & set(gru.columns))
    p = pad[commons].copy()
    g = gru[commons].copy()
    p['origen_persona'] = 'padron_jefe'
    g['origen_persona'] = 'grupo_familiar'
    return pd.concat([p, g], ignore_index=True)


def build_population_pyramid(df: pd.DataFrame) -> pd.DataFrame:
    if 'edad_norm' not in df.columns or 'genero_norm' not in df.columns:
        return pd.DataFrame()

    tmp = df[['edad_norm', 'genero_norm']].copy()
    tmp['edad_num'] = pd.to_numeric(tmp['edad_norm'], errors='coerce')
    tmp = tmp[tmp['edad_num'].between(0, 110, inclusive='both')]
    tmp = tmp[tmp['genero_norm'].isin(['Varon', 'Mujer', 'Varon trans'])]
    if tmp.empty:
        return pd.DataFrame()

    bins = list(range(0, 86, 5)) + [200]
    labels = [f'{i}-{i+4}' for i in range(0, 85, 5)] + ['85+']
    tmp['grupo_edad'] = pd.cut(tmp['edad_num'], bins=bins, labels=labels, right=False, include_lowest=True)

    pyr = pd.crosstab(tmp['grupo_edad'], tmp['genero_norm'])
    for c in ['Varon', 'Mujer', 'Varon trans']:
        if c not in pyr.columns:
            pyr[c] = 0
    pyr = pyr[['Varon', 'Mujer', 'Varon trans']].sort_index()
    pyr['Varon'] = -pyr['Varon']
    return pyr


padron = load_csv(OUT / 'padronfme_normalizado.csv')
grupo = load_csv(OUT / 'grupo_familiar_normalizado.csv')
padron_dedup = load_csv(OUT / 'padronfme_deduplicado.csv')
calidad = load_csv(OUT / 'reporte_calidad.csv')
casos = load_csv(OUT / 'casos_revision_manual.csv')
var_df = load_csv(VAR_FILE)

if padron.empty or grupo.empty:
    st.error('No se encontraron salidas normalizadas en output/. Ejecuta primero el pipeline integral.')
    st.stop()

people_all = combined_people(padron, grupo)
coverage = build_coverage_table(var_df, padron, grupo)
extra_cols = build_extra_columns_table(var_df, padron, grupo)

global_filter_lines = [
    'Solo personas censadas: SI (fijo)',
]

pad_f = padron.copy()
gru_f = grupo.copy()
all_f = people_all.copy()

# Contexto global opcional (persistente en la sesión)
ctx1, ctx2 = st.columns(2)
localidades_ctx = sorted([x for x in padron.get('localidad-vv', pd.Series(dtype=str)).dropna().astype(str).unique() if str(x).strip()])
generos_ctx = ['Varon', 'Mujer', 'Varon trans']
with ctx1:
    global_loc_sel = st.multiselect('Contexto global: Localidad', localidades_ctx, key='ctx_global_loc')
with ctx2:
    global_gen_sel = st.multiselect('Contexto global: Género', generos_ctx, key='ctx_global_gen')

if global_loc_sel:
    if 'localidad-vv' in pad_f.columns:
        pad_f = pad_f[pad_f['localidad-vv'].isin(global_loc_sel)]
    if 'localidad-vv' in gru_f.columns:
        gru_f = gru_f[gru_f['localidad-vv'].isin(global_loc_sel)]
if global_gen_sel:
    if 'genero_norm' in pad_f.columns:
        pad_f = pad_f[pad_f['genero_norm'].isin(global_gen_sel)]
    if 'genero_norm' in gru_f.columns:
        gru_f = gru_f[gru_f['genero_norm'].isin(global_gen_sel)]

all_f = combined_people(pad_f, gru_f)

# Filtro global fijo: solo personas censadas.
pad_f = pad_f[censada(pad_f) & con_nombre(pad_f)]
gru_f = gru_f[censada(gru_f) & con_nombre(gru_f)]
all_f = all_f[censada(all_f) & con_nombre(all_f)]

if global_loc_sel:
    global_filter_lines.append(f"Localidades: {', '.join(global_loc_sel)}")
if global_gen_sel:
    global_filter_lines.append(f"Género: {', '.join(global_gen_sel)}")

chips = ''.join([f"<span class='chip'>{f}</span>" for f in global_filter_lines])
st.markdown(f"**Filtros activos**  \n{chips}", unsafe_allow_html=True)

# En visualización, reemplaza fechas crudas por normalizadas cuando existan.
pad_f = apply_normalized_dates_for_display(pad_f)
gru_f = apply_normalized_dates_for_display(gru_f)
all_f = apply_normalized_dates_for_display(all_f)

tabs = st.tabs([
    '0. Resumen Ejecutivo',
    '1. Consultas Guiadas',
    '2. Diccionario y Cobertura',
    '3. Filtro Dinamico',
    '4. Consulta Avanzada',
    '5. Demografia y Poblacion',
    '6. Vivienda y Servicios',
    '7. Educacion',
    '8. Trabajo y Movilidad',
    '9. Salud y Discapacidad',
    '10. Asistencia y Dinamica Familiar',
    '11. Territorio (Mapa)',
    '12. Calidad y Revision',
    '13. Explorador Completo',
    '14. Priorizacion Territorial'
])

with tabs[0]:
    
    # KPIs
    personas_total = len(all_f)
    jefes_total = len(pad_f)
    miembros_total = len(gru_f)
    
    if not padron_dedup.empty and 'censado_fecha' in padron_dedup.columns:
        viviendas_censadas = int(non_empty(padron_dedup['censado_fecha']).sum())
    else:
        viviendas_censadas = len(pad_f)
    
    edad_prom = num(all_f.get('edad_norm', pd.Series(dtype=str))).dropna()
    edad_prom_val = round(float(edad_prom.mean()), 2) if len(edad_prom) else None
    
    cel = num(pad_f.get('celulares_activos_vv', pd.Series(dtype=str))).dropna()
    cel_total = int(cel.sum()) if len(cel) else 0
    hogares = num(pad_f.get('cantidad_hogares_vv', pd.Series(dtype=str))).dropna()
    hogares_total = int(hogares.sum()) if len(hogares) else 0
    
    # KPIs complementarios (sin duplicar hallazgos ejecutivos)
    yn = normalize_yes_no
    yes_vals_kpi = ['si', 'sí']
    
    sin_banio_kpi = yn(pad_f.get('tienebaño_vv', pd.Series('', index=pad_f.index))).isin(['no', 'sin', 'no tiene'])
    sin_recoleccion_kpi = yn(pad_f.get('servicioregular_recoleccionresiduos_vv', pd.Series('', index=pad_f.index))).isin(['no'])
    basural_kpi = yn(pad_f.get('basuralpermanente_300mts_vv', pd.Series('', index=pad_f.index))).isin(yes_vals_kpi)
    napas_kpi = yn(pad_f.get('problema_napas_vv', pd.Series('', index=pad_f.index))).isin(yes_vals_kpi)
    no_posee_salud_kpi = yn(all_f.get('cobertura_salud', pd.Series('', index=all_f.index))).str.contains('no posee', regex=False)
    tiene_masc_kpi = yn(pad_f.get('tiene_mascotas', pd.Series('', index=pad_f.index))).isin(yes_vals_kpi)
    matricula_decl_kpi = non_empty(pad_f.get('matricula_catastral', pd.Series('', index=pad_f.index)))
    
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric('Personas censadas total', personas_total)
    k2.metric('Jefes de hogar', jefes_total)
    k3.metric('Miembros grupo familiar', miembros_total)
    k4.metric('Viviendas censadas', viviendas_censadas)
    k5.metric('Edad promedio', f'{edad_prom_val}' if edad_prom_val is not None else 'N/D')
    
    k7, k8, k9 = st.columns(3)
    k7.metric('Celulares activos promedio por vivienda', f'{cel.mean():.2f}' if len(cel) else 'N/D')
    k8.metric('Total celulares activos', cel_total)
    k9.metric('Cantidad de hogares reportados', hogares_total)
    
    st.write('**KPIs complementarios**')
    r1, r2, r3, r4 = st.columns(4)
    r1.metric(
        'Cobertura salud: No posee',
        f"{int(no_posee_salud_kpi.sum())}",
        f"{(no_posee_salud_kpi.sum() / max(len(all_f), 1) * 100):.2f}% personas",
    )
    r2.metric(
        'Viviendas sin baño',
        f"{int(sin_banio_kpi.sum())}",
        f"{(sin_banio_kpi.sum() / max(len(pad_f), 1) * 100):.2f}% viviendas",
    )
    r3.metric(
        'Viviendas sin recolección',
        f"{int(sin_recoleccion_kpi.sum())}",
        f"{(sin_recoleccion_kpi.sum() / max(len(pad_f), 1) * 100):.2f}% viviendas",
    )
    r4.metric(
        'Basural a 300m',
        f"{int(basural_kpi.sum())}",
        f"{(basural_kpi.sum() / max(len(pad_f), 1) * 100):.2f}% viviendas",
    )
    
    r5, r6, r7, r8 = st.columns(4)
    r5.metric(
        'Problema de napas',
        f"{int(napas_kpi.sum())}",
        f"{(napas_kpi.sum() / max(len(pad_f), 1) * 100):.2f}% viviendas",
    )
    r6.metric('Hogares con mascotas', f"{int(tiene_masc_kpi.sum())}", f"{(tiene_masc_kpi.sum() / max(len(pad_f), 1) * 100):.2f}% viviendas")
    r7.metric(
        'Matrícula catastral declarada',
        f"{int(matricula_decl_kpi.sum())}",
        f"{(matricula_decl_kpi.sum() / max(len(pad_f), 1) * 100):.2f}% viviendas",
    )
    r8.metric('Viviendas sin matrícula declarada', f"{int((~matricula_decl_kpi).sum())}", f"{((~matricula_decl_kpi).sum() / max(len(pad_f), 1) * 100):.2f}% viviendas")
    
    with st.expander('Resumen en 30 segundos'):
        st.write(
            f"Personas censadas: **{personas_total}** | Viviendas censadas: **{viviendas_censadas}** | "
            f"Edad promedio: **{edad_prom_val if edad_prom_val is not None else 'N/D'}**"
        )
        st.write(
            f"Viviendas sin baño: **{int(sin_banio_kpi.sum())}** ({(sin_banio_kpi.sum() / max(len(pad_f), 1) * 100):.2f}%) | "
            f"Sin recolección: **{int(sin_recoleccion_kpi.sum())}** ({(sin_recoleccion_kpi.sum() / max(len(pad_f), 1) * 100):.2f}%)"
        )
        st.write(
            f"Basural 300m: **{int(basural_kpi.sum())}** | No posee cobertura de salud: **{int(no_posee_salud_kpi.sum())}** personas"
        )
        st.caption('Usá Hallazgos Ejecutivos para abrir el detalle operativo y descargar los listados.')
    
    # Informe ejecutivo
    st.subheader('Hallazgos Ejecutivos')
    loc_top = val_counts(pad_f, 'localidad-vv', 3)
    gen_top = val_counts(all_f, 'genero_norm', 3)
    trab_top = val_counts(all_f, 'trabajo_tb', 3)
    mov_top = val_counts(all_f, 'movilidad_trabajo_tb', 3)
    
    insights = []
    if not loc_top.empty:
        insights.append(f"Concentracion territorial: {loc_top.iloc[0]['valor']} ({int(loc_top.iloc[0]['cantidad'])} personas en padron filtrado).")
    if not gen_top.empty:
        insights.append(f"Composicion por genero: predomina {gen_top.iloc[0]['valor']} con {int(gen_top.iloc[0]['cantidad'])} personas.")
    if not trab_top.empty:
        insights.append(f"Actividad laboral mas frecuente (padron+grupo): {trab_top.iloc[0]['valor']} ({int(trab_top.iloc[0]['cantidad'])}).")
    if not mov_top.empty:
        insights.append(f"Movilidad principal: {mov_top.iloc[0]['valor']} ({int(mov_top.iloc[0]['cantidad'])}).")
    if edad_prom_val is not None:
        insights.append(f"Edad promedio estimada del universo filtrado: {edad_prom_val} anos.")
    
    for i in insights:
        st.write(f'- {i}')
    
    # Hallazgos priorizados accionables
    yes_vals = ['si', 'sí']
    
    sin_banio = normalize_yes_no(pad_f.get('tienebaño_vv', pd.Series('', index=pad_f.index))).isin(['no', 'sin', 'no tiene'])
    con_basural = normalize_yes_no(pad_f.get('basuralpermanente_300mts_vv', pd.Series('', index=pad_f.index))).isin(yes_vals)
    sin_recoleccion = normalize_yes_no(pad_f.get('servicioregular_recoleccionresiduos_vv', pd.Series('', index=pad_f.index))).isin(['no'])
    con_napas = normalize_yes_no(pad_f.get('problema_napas_vv', pd.Series('', index=pad_f.index))).isin(yes_vals)
    
    riesgos_vv = (
        sin_banio.astype(int)
        + con_basural.astype(int)
        + sin_recoleccion.astype(int)
        + con_napas.astype(int)
    )
    mask_hall_1 = riesgos_vv >= 2
    hall_1_df = pad_f[mask_hall_1].copy()
    hall_1_df['riesgos_detectados'] = riesgos_vv[mask_hall_1].astype(int)
    hall_1_den = max(len(pad_f), 1)
    hall_1_pct = round((len(hall_1_df) / hall_1_den) * 100, 2)
    
    med_recetados = normalize_yes_no(all_f.get('medicamentosrecetados_ultimoaño_salud', pd.Series('', index=all_f.index))).isin(yes_vals)
    no_consig_meds = normalize_yes_no(all_f.get('consiguio_medicamentos_salud', pd.Series('', index=all_f.index))).isin(['no'])
    mask_hall_2 = med_recetados & no_consig_meds
    hall_2_df = all_f[mask_hall_2].copy()
    hall_2_den = int(med_recetados.sum())
    hall_2_pct = round((len(hall_2_df) / max(hall_2_den, 1)) * 100, 2)
    
    trab_norm = normalize_yes_no(all_f.get('trabajo_tb', pd.Series('', index=all_f.index)))
    mask_hall_3 = trab_norm.str.contains('desemple', regex=False) | trab_norm.str.contains('desocup', regex=False)
    hall_3_df = all_f[mask_hall_3].copy()
    hall_3_den = max(len(all_f), 1)
    hall_3_pct = round((len(hall_3_df) / hall_3_den) * 100, 2)
    
    tiene_masc = normalize_yes_no(pad_f.get('tiene_mascotas', pd.Series('', index=pad_f.index))).isin(yes_vals)
    cant_masc = pd.to_numeric(pad_f.get('cuantas_mascotas', pd.Series('', index=pad_f.index)), errors='coerce')
    cant_castr = pd.to_numeric(pad_f.get('cuantas_mascotas_castradas', pd.Series('', index=pad_f.index)), errors='coerce')
    mask_hall_4 = tiene_masc & cant_masc.notna() & cant_castr.notna() & cant_castr.lt(cant_masc)
    hall_4_df = pad_f[mask_hall_4].copy()
    hall_4_df['brecha_castracion'] = (cant_masc[mask_hall_4] - cant_castr[mask_hall_4]).astype(int)
    hall_4_den = int(tiene_masc.sum())
    hall_4_pct = round((len(hall_4_df) / max(hall_4_den, 1)) * 100, 2)
    
    disc_base = all_f.get('discapacidad_disc_norm', all_f.get('discapacidad_disc', pd.Series('', index=all_f.index)))
    disc_si = normalize_yes_no(disc_base).isin(yes_vals)
    cud_si = normalize_yes_no(all_f.get('CUD-Discapacidad_disc', pd.Series('', index=all_f.index))).isin(yes_vals)
    cud_no = normalize_yes_no(all_f.get('CUD-Discapacidad_disc', pd.Series('', index=all_f.index))).isin(['no'])
    mask_hall_6 = disc_si & cud_no
    hall_6_df = all_f[mask_hall_6].copy()
    hall_6_den = int(disc_si.sum())
    hall_6_pct = round((len(hall_6_df) / max(hall_6_den, 1)) * 100, 2)
    hall_6_cud_si = int((disc_si & cud_si).sum())
    
    st.write('**Hallazgos priorizados**')
    h1, h2, h3, h4, h6 = st.columns(5)
    h1.metric('Viviendas con 2+ riesgos', f'{len(hall_1_df)}', f'{hall_1_pct}%')
    h2.metric('Receta y no consiguió meds', f'{len(hall_2_df)}', f'{hall_2_pct}% de recetados')
    h3.metric('Desempleo reportado', f'{len(hall_3_df)}', f'{hall_3_pct}% de personas')
    h4.metric('Mascotas no totalmente castradas', f'{len(hall_4_df)}', f'{hall_4_pct}% de hogares con mascotas')
    h6.metric('Discapacidad + CUD No', f'{len(hall_6_df)}', f'{hall_6_pct}% de discapacidad')
    st.caption(f'Declaran discapacidad: {hall_6_den} personas. Con CUD Sí: {hall_6_cud_si}.')
    
    hallazgos_detalle = {
        '1) Viviendas con 2 o mas riesgos habitacionales/ambientales': {
            'df': hall_1_df,
            'tipo': 'viviendas',
            'resumen': f'{len(hall_1_df)} viviendas ({hall_1_pct}% sobre viviendas censadas filtradas).',
            'extra_cols': ['riesgos_detectados', 'tienebaño_vv', 'basuralpermanente_300mts_vv', 'servicioregular_recoleccionresiduos_vv', 'problema_napas_vv'],
        },
        '2) Personas con receta medica que no consiguieron medicamentos': {
            'df': hall_2_df,
            'tipo': 'personas',
            'resumen': f'{len(hall_2_df)} personas ({hall_2_pct}% sobre quienes tuvieron medicamentos recetados).',
            'extra_cols': ['medicamentosrecetados_ultimoaño_salud', 'consiguio_medicamentos_salud', 'cobertura_salud'],
        },
        '3) Personas en desempleo reportado': {
            'df': hall_3_df,
            'tipo': 'personas',
            'resumen': f'{len(hall_3_df)} personas ({hall_3_pct}% sobre personas censadas filtradas).',
            'extra_cols': ['trabajo_tb', 'buscando_trabajo_tb', 'tiene_oficio_tb', 'cual_oficio_tb'],
        },
        '4) Viviendas con mascotas no totalmente castradas': {
            'df': hall_4_df,
            'tipo': 'viviendas',
            'resumen': f'{len(hall_4_df)} viviendas ({hall_4_pct}% sobre viviendas con mascotas declaradas).',
            'extra_cols': ['tiene_mascotas', 'cuantas_mascotas', 'cuantas_mascotas_castradas', 'brecha_castracion'],
        },
        '6) Personas con discapacidad declarada y CUD = No': {
            'df': hall_6_df,
            'tipo': 'personas',
            'resumen': f'{len(hall_6_df)} personas ({hall_6_pct}% sobre quienes declaran discapacidad).',
            'extra_cols': ['discapacidad_disc_norm', 'discapacidad_disc', 'CUD-Discapacidad_disc', 'tipo_discapacidad_norm', 'dependencia_discapacidad_disc'],
        },
    }
    
    st.write('**Ampliar hallazgo con detalle operativo y descarga**')
    hall_sel = st.selectbox('Selecciona un hallazgo', list(hallazgos_detalle.keys()), key='hallazgo_detalle_select')
    hall_info = hallazgos_detalle[hall_sel]
    hall_df = hall_info['df'].copy()
    st.write(hall_info['resumen'])
    
    base_cols = [
        'origen_persona', 'dni', 'nombrecompleto', 'nombre_jefehogar', 'dni_jefehogar',
        'localidad-vv', 'domicilio-dni-vv', 'calle_vv', 'numero_vv',
        'censado_fecha', 'censado_fecha_norm', 'ubicacion-vv'
    ]
    view_cols = select_existing_columns(hall_df, base_cols + hall_info['extra_cols'])
    hall_view = add_google_maps_link(hall_df[view_cols].copy()) if view_cols else add_google_maps_link(hall_df.copy())
    st.dataframe(hall_view, use_container_width=True, height=300)
    
    dh1, dh2, dh3 = st.columns(3)
    safe_name = hall_sel.lower().replace(' ', '_').replace(')', '').replace('(', '').replace('/', '_').replace('=', '')
    dh1.download_button(
        'Descargar CSV',
        data=hall_view.to_csv(index=False).encode('utf-8-sig'),
        file_name=f'hallazgo_{safe_name}.csv',
        mime='text/csv',
    )
    if EXCEL_ENABLED:
        dh2.download_button(
            'Descargar Excel',
            data=to_excel_bytes(hall_view, sheet_name='hallazgo'),
            file_name=f'hallazgo_{safe_name}.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    else:
        dh2.caption('Excel no disponible (instalar xlsxwriter u openpyxl)')
    if PDF_ENABLED:
        dh3.download_button(
            'Descargar PDF',
            data=to_pdf_bytes(
                hall_view,
                title='Hallazgo ejecutivo - Censo FME 2025',
                filters=global_filter_lines + [f'Hallazgo: {hall_sel}', f"Resumen: {hall_info['resumen']}"],
            ),
            file_name=f'hallazgo_{safe_name}.pdf',
            mime='application/pdf',
        )
    else:
        dh3.caption('PDF no disponible (instalar reportlab)')

with tabs[2]:
    st.subheader('Cobertura por variable (referencia: variables y nomenclatura)')
    if coverage.empty:
        st.info('No se pudo leer el archivo de variables y nomenclatura.')
    else:
        modulos = ['Todos'] + sorted([m for m in coverage['modulo'].dropna().unique()])
        mod_sel = st.selectbox('Modulo', modulos)
        view = coverage if mod_sel == 'Todos' else coverage[coverage['modulo'] == mod_sel]
        st.dataframe(view, use_container_width=True, height=420)

        st.write('**Variables con menor completitud en padron (top 15, >0%)**')
        low_p = coverage[(coverage['padron_completitud_%'] > 0)].sort_values('padron_completitud_%').head(15)
        st.dataframe(low_p[['modulo', 'columna', 'padron_completitud_%', 'padron_no_vacios']], use_container_width=True)

    st.write('**Columnas fuera de nomenclatura (incluidas para analisis)**')
    if not extra_cols.empty:
        st.dataframe(extra_cols, use_container_width=True, height=260)
    else:
        st.info('No se detectaron columnas fuera del diccionario.')

    st.write('**Diccionario vivo (interactivo)**')
    if coverage.empty or var_df.empty:
        st.info('No hay diccionario disponible para esta sección.')
    else:
        vv = var_df.copy()
        vv['Código de columna'] = vv['Código de columna'].fillna('').astype(str).str.strip()
        vv['Módulo'] = vv['Módulo'].ffill()
        vv = vv[vv['Código de columna'].ne('')].copy()
        cols_dict = sorted(vv['Código de columna'].unique().tolist())
        col_dic = st.selectbox('Selecciona variable del diccionario', cols_dict, key='dict_col')
        row_dic = vv[vv['Código de columna'] == col_dic].head(1)
        if not row_dic.empty:
            drow = row_dic.iloc[0]
            st.write(f"**Módulo:** {drow.get('Módulo','')}")
            st.write(f"**Pregunta:** {drow.get('Pregunta','')}")
            st.write(f"**Opciones esperadas:**")
            st.code(str(drow.get('Opciones', '') or ''))

        cov_row = coverage[coverage['columna'] == col_dic].head(1)
        if not cov_row.empty:
            st.dataframe(
                cov_row[['modulo', 'columna', 'padron_completitud_%', 'grupo_completitud_%', 'padron_no_vacios', 'grupo_no_vacios']],
                use_container_width=True
            )

        c1, c2 = st.columns(2)
        with c1:
            if col_dic in pad_f.columns:
                st.write('Ejemplos en padrón')
                ex = pad_f[col_dic].fillna('').astype(str).str.strip()
                ex = ex[ex.ne('')].head(10).reset_index(drop=True)
                st.dataframe(ex.to_frame('valor'), use_container_width=True)
        with c2:
            if col_dic in gru_f.columns:
                st.write('Ejemplos en grupo familiar')
                ex = gru_f[col_dic].fillna('').astype(str).str.strip()
                ex = ex[ex.ne('')].head(10).reset_index(drop=True)
                st.dataframe(ex.to_frame('valor'), use_container_width=True)

with tabs[5]:
    st.subheader('Demografia integrada (padron + grupo)')
    c1, c2 = st.columns(2)

    with c1:
        g = val_counts(all_f, 'genero_norm', 10)
        if not g.empty:
            st.bar_chart(g.set_index('valor'))

    with c2:
        edades = num(all_f.get('edad_norm', pd.Series(dtype=str))).dropna()
        if len(edades):
            bins = pd.cut(edades, bins=[0, 5, 12, 17, 29, 44, 59, 110], include_lowest=True)
            d = bins.value_counts().sort_index().rename_axis('rango').reset_index(name='cantidad')
            d['rango'] = d['rango'].astype(str)
            st.bar_chart(d.set_index('rango'))

    st.write('**Piramide poblacional (Varon a la izquierda, Mujer/Varon trans a la derecha)**')
    pyr = build_population_pyramid(all_f)
    if pyr.empty:
        st.info('No hay datos suficientes para construir la piramide poblacional en el filtro actual.')
    else:
        st.bar_chart(pyr)

    st.write('**Cruce: localidad x genero (padron)**')
    if {'localidad-vv', 'genero_norm'}.issubset(pad_f.columns):
        tmp = pad_f[['localidad-vv', 'genero_norm']].copy()
        tmp = tmp[non_empty(tmp['localidad-vv']) & non_empty(tmp['genero_norm'])]
        if not tmp.empty:
            ct = pd.crosstab(tmp['localidad-vv'], tmp['genero_norm'])
            st.dataframe(ct, use_container_width=True, height=300)

    st.write('**Nacionalidad (padron + grupo)**')
    nac = val_counts(all_f, 'nacionalidad', 15)
    if not nac.empty:
        st.bar_chart(nac.set_index('valor'))

with tabs[6]:
    st.subheader('Vivienda y servicios (jefes de hogar)')
    cols = [
        'tipo_vv', 'tenencia_vv', 'cantidad_habitaciones_vv',
        'material_techo_vv', 'material_piso_vv',
        'tienebaño_vv', 'elbañotiene_vv', 'electricidad_vv', 'tieneagua_vv',
        'basuralpermanente_300mts_vv', 'servicioregular_recoleccionresiduos_vv',
        'problema_napas_vv', 'bicis_particulares', 'bicis_frayenbici'
    ]
    for col in cols:
        if col in pad_f.columns:
            st.write(f'**{col}**')
            vc = val_counts(pad_f, col, 12)
            if not vc.empty:
                vc = add_pct(vc, viviendas_censadas)
                st.bar_chart(vc.set_index('valor')[['cantidad']])
                st.dataframe(vc, use_container_width=True)

    st.write('**servicios_vv (categorias unicas dentro de respuestas multiples)**')
    sv = token_counts(pad_f, 'servicios_vv', topn=20)
    if not sv.empty:
        sv = add_pct(sv, viviendas_censadas)
        st.bar_chart(sv.set_index('valor')[['cantidad']])
        st.dataframe(sv, use_container_width=True)

    st.write('**equipamiento_vv (categorias unicas dentro de respuestas multiples)**')
    eq = token_counts(pad_f, 'equipamiento_vv', topn=20)
    if not eq.empty:
        eq = add_pct(eq, viviendas_censadas)
        st.bar_chart(eq.set_index('valor')[['cantidad']])
        st.dataframe(eq, use_container_width=True)

    st.write('**matricula_catastral**')
    if 'matricula_catastral' in pad_f.columns:
        mat_decl = int(non_empty(pad_f['matricula_catastral']).sum())
        denom_viv = max(viviendas_censadas, 1)
        st.write(f'Viviendas con matricula catastral declarada: {mat_decl} ({(mat_decl / denom_viv) * 100:.2f}% sobre viviendas censadas)')

    st.write('**valoracion_gestion (categorias unicas y % sobre viviendas censadas)**')
    vg = token_counts(pad_f, 'valoracion_gestion', topn=20)
    if not vg.empty:
        vg = add_pct(vg, viviendas_censadas)
        st.bar_chart(vg.set_index('valor')[['cantidad']])
        st.dataframe(vg, use_container_width=True)

    st.write('**Mascotas en viviendas**')
    tm = val_counts(pad_f, 'tiene_mascotas', 8)
    if not tm.empty:
        tm = add_pct(tm, viviendas_censadas)
        st.bar_chart(tm.set_index('valor')[['cantidad']])
        st.dataframe(tm, use_container_width=True)
    q_masc = num(pad_f.get('cuantas_mascotas', pd.Series(dtype=str))).dropna()
    q_cast = num(pad_f.get('cuantas_mascotas_castradas', pd.Series(dtype=str))).dropna()
    c1, c2 = st.columns(2)
    c1.metric('Total mascotas reportadas', int(q_masc.sum()) if len(q_masc) else 0)
    c2.metric('Total mascotas castradas reportadas', int(q_cast.sum()) if len(q_cast) else 0)

    st.write('**Cruce: tipo de vivienda x tenencia**')
    if {'tipo_vv', 'tenencia_vv'}.issubset(pad_f.columns):
        tmp = pad_f[['tipo_vv', 'tenencia_vv']].copy()
        tmp = tmp[non_empty(tmp['tipo_vv']) & non_empty(tmp['tenencia_vv'])]
        if not tmp.empty:
            ct = pd.crosstab(tmp['tipo_vv'], tmp['tenencia_vv'])
            st.dataframe(ct, use_container_width=True, height=280)

with tabs[7]:
    st.subheader('Educacion (foco en grupo familiar por mayor cobertura)')
    for col in ['asistencia_establecimientoeducativo_edu', 'tipo_educacion_edu', 'nivel-estudios-cursa', 'nivel-estudiosalcanzados']:
        if col in gru_f.columns:
            st.write(f'**{col}**')
            st.bar_chart(val_counts(gru_f, col, 12).set_index('valor'))

    st.write('**tipo_educacion_edu (padron + grupo)**')
    te = val_counts(all_f, 'tipo_educacion_edu', 12)
    if not te.empty:
        st.bar_chart(te.set_index('valor'))

    if 'carrera_edu' in gru_f.columns or 'carrera_edu' in pad_f.columns:
        st.write('**Top carrera_edu (padron + grupo)**')
        car = all_f.get('carrera_edu', pd.Series(dtype=str)).fillna('').astype(str).str.strip()
        car = car[car.ne('')].value_counts().head(20).reset_index()
        if not car.empty:
            car.columns = ['carrera', 'cantidad']
            st.bar_chart(car.set_index('carrera'))

    if 'carrera_edu' in gru_f.columns:
        st.write('**Top carreras declaradas (texto libre)**')
        car = gru_f['carrera_edu'].fillna('').astype(str).str.strip()
        car = car[car.ne('')].value_counts().head(20).reset_index()
        if not car.empty:
            car.columns = ['carrera', 'cantidad']
            st.dataframe(car, use_container_width=True)

with tabs[8]:
    st.subheader('Trabajo y movilidad (padron + grupo)')
    c1, c2 = st.columns(2)
    with c1:
        st.write('**Condicion laboral**')
        st.bar_chart(val_counts(all_f, 'trabajo_tb', 15).set_index('valor'))

    with c2:
        st.write('**Movilidad para trabajo**')
        st.bar_chart(val_counts(all_f, 'movilidad_trabajo_tb', 10).set_index('valor'))

    extra_trab = [
        'aportes_jubilatorios_tb', 'sutrabajoes_tb', 'buscando_trabajo_tb',
        'juridisccion_trabajo_tb', 'tiene_oficio_tb'
    ]
    for col in extra_trab:
        vc = val_counts(all_f, col, 12)
        if not vc.empty:
            st.write(f'**{col}**')
            st.bar_chart(vc.set_index('valor'))

    ofi = val_counts(all_f, 'cual_oficio_tb', 20)
    if not ofi.empty:
        st.write('**cual_oficio_tb (top declaraciones)**')
        st.dataframe(ofi, use_container_width=True)

    st.write('**Cruce: trabajo x movilidad**')
    if {'trabajo_tb', 'movilidad_trabajo_tb'}.issubset(all_f.columns):
        tmp = all_f[['trabajo_tb', 'movilidad_trabajo_tb']].copy()
        tmp = tmp[non_empty(tmp['trabajo_tb']) & non_empty(tmp['movilidad_trabajo_tb'])]
        if not tmp.empty:
            ct = pd.crosstab(tmp['trabajo_tb'], tmp['movilidad_trabajo_tb'])
            st.dataframe(ct, use_container_width=True, height=320)

with tabs[9]:
    st.subheader('Salud y discapacidad')
    c1, c2 = st.columns(2)
    with c1:
        st.write('**Cobertura de salud (categorias unicas dentro de respuestas multiples)**')
        cob = token_counts(all_f, 'cobertura_salud', topn=20)
        if not cob.empty:
            st.bar_chart(cob.set_index('valor'))
    with c2:
        st.write('**Discapacidad declarada**')
        st.bar_chart(val_counts(all_f, 'discapacidad_disc_norm', 8).set_index('valor'))

    salud_cols = [
        'enfermedadescronicas_tratamientosprolongados_salud',
        'cantidadtotal_embarazos_salud',
        'cantidadtotal_partos',
        'esta_embarazada_salud',
        'medicamentosrecetados_ultimoaño_salud',
        'consiguio_medicamentos_salud',
    ]
    for col in salud_cols:
        if col == 'enfermedadescronicas_tratamientosprolongados_salud':
            vc = token_counts(all_f, col, topn=20)
        else:
            vc = val_counts(all_f, col, 15)
        if not vc.empty:
            st.write(f'**{col}**')
            st.bar_chart(vc.set_index('valor'))

    st.write('**Tipos de discapacidad normalizados**')
    if 'tipo_discapacidad_norm' in all_f.columns:
        td = all_f['tipo_discapacidad_norm'].fillna('').astype(str)
        td = td[td.ne('')].str.split(',').explode().str.strip()
        tdf = td.value_counts().head(20).reset_index()
        if not tdf.empty:
            tdf.columns = ['tipo_discapacidad', 'cantidad']
            st.dataframe(tdf, use_container_width=True)

    dep = val_counts(all_f, 'dependencia_discapacidad_disc', 10)
    if not dep.empty:
        st.write('**dependencia_discapacidad_disc**')
        st.bar_chart(dep.set_index('valor'))

    el = token_counts(all_f, 'elementos_ortopedicos_disc', topn=20)
    if not el.empty:
        st.write('**elementos_ortopedicos_disc (categorias unicas)**')
        st.bar_chart(el.set_index('valor'))

    st.write('**Cruce: discapacidad x cobertura de salud**')
    if {'discapacidad_disc_norm', 'cobertura_salud'}.issubset(all_f.columns):
        tmp = all_f[['discapacidad_disc_norm', 'cobertura_salud']].copy()
        tmp = tmp[non_empty(tmp['discapacidad_disc_norm']) & non_empty(tmp['cobertura_salud'])]
        if not tmp.empty:
            ct = pd.crosstab(tmp['discapacidad_disc_norm'], tmp['cobertura_salud'])
            st.dataframe(ct, use_container_width=True)

with tabs[10]:
    st.subheader('Asistencia y dinamica familiar')
    cols = [
        'beneficiario_plan_asistencia', 'miembro_flia_auh_asistencia', 'cantidad_auh_asistencia',
        'miembro_tarjetaalimentar_asistencia', 'cantidad_tarjetaalimentar_asistencia',
        'frecuencia_merenderocomedor_asistencia', 'quienseocupa_cuidadoniños_flia', 'quienseocupa_tareas_flia'
    ]
    for col in cols:
        if col in all_f.columns:
            st.write(f'**{col}**')
            st.bar_chart(val_counts(all_f, col, 12).set_index('valor'))

    bg = token_counts(all_f, 'bienes_gratuitos_asistencia', topn=20)
    if not bg.empty:
        st.write('**bienes_gratuitos_asistencia (categorizado por item unico)**')
        st.bar_chart(bg.set_index('valor'))

    dep_muni = token_counts(all_f, 'conoce_dependenciasmunicipales', topn=25)
    if not dep_muni.empty:
        st.write('**conoce_dependenciasmunicipales (categorias unicas)**')
        st.bar_chart(dep_muni.set_index('valor'))

with tabs[11]:
    st.subheader('Territorio y mapa operativo por capas (viviendas)')
    gps = parse_gps(pad_f)
    st.caption(f'Puntos GPS validos en filtro actual: {len(gps)}')

    loc = val_counts(pad_f, 'localidad-vv', 20)
    if not loc.empty:
        st.write('**Distribucion territorial por localidad (padron)**')
        st.bar_chart(loc.set_index('valor'))

    if gps.empty:
        st.info('No hay coordenadas GPS validas para este filtro.')
    else:
        lat, lon = gps['lat'].mean(), gps['lon'].mean()
        capas = st.multiselect(
            'Capas operativas',
            ['Heatmap general', 'Puntos viviendas', 'Sin baño', 'Basural 300m', 'Sin recolección', 'Problema napas'],
            default=['Heatmap general', 'Puntos viviendas'],
            key='map_layers'
        )
        layers = []
        layer_counts = []
        if 'Heatmap general' in capas:
            layers.append(
                pdk.Layer(
                    'HeatmapLayer',
                    data=gps,
                    get_position='[lon, lat]',
                    get_weight=1,
                    radiusPixels=45,
                )
            )
            layer_counts.append(('Heatmap general', len(gps)))

        if 'Puntos viviendas' in capas:
            layers.append(
                pdk.Layer(
                    'ScatterplotLayer',
                    data=gps,
                    get_position='[lon, lat]',
                    get_radius=20,
                    get_fill_color='[40,120,200,130]',
                )
            )
            layer_counts.append(('Puntos viviendas', len(gps)))

        def points_from_filter(mask: pd.Series):
            tmp = pad_f[mask].copy()
            pts = parse_gps(tmp)
            return pts

        if 'Sin baño' in capas and 'tienebaño_vv' in pad_f.columns:
            yn = normalize_yes_no(pad_f['tienebaño_vv'])
            pts = points_from_filter(yn.isin(['no']))
            if not pts.empty:
                layers.append(
                    pdk.Layer('ScatterplotLayer', data=pts, get_position='[lon, lat]', get_radius=35, get_fill_color='[220,20,60,170]')
                )
            layer_counts.append(('Sin baño', len(pts)))

        if 'Basural 300m' in capas and 'basuralpermanente_300mts_vv' in pad_f.columns:
            yn = normalize_yes_no(pad_f['basuralpermanente_300mts_vv'])
            pts = points_from_filter(yn.isin(['si', 'sí']))
            if not pts.empty:
                layers.append(
                    pdk.Layer('ScatterplotLayer', data=pts, get_position='[lon, lat]', get_radius=35, get_fill_color='[255,140,0,170]')
                )
            layer_counts.append(('Basural 300m', len(pts)))

        if 'Sin recolección' in capas and 'servicioregular_recoleccionresiduos_vv' in pad_f.columns:
            yn = normalize_yes_no(pad_f['servicioregular_recoleccionresiduos_vv'])
            pts = points_from_filter(yn.isin(['no']))
            if not pts.empty:
                layers.append(
                    pdk.Layer('ScatterplotLayer', data=pts, get_position='[lon, lat]', get_radius=35, get_fill_color='[128,0,128,170]')
                )
            layer_counts.append(('Sin recolección', len(pts)))

        if 'Problema napas' in capas and 'problema_napas_vv' in pad_f.columns:
            yn = normalize_yes_no(pad_f['problema_napas_vv'])
            pts = points_from_filter(yn.isin(['si', 'sí']))
            if not pts.empty:
                layers.append(
                    pdk.Layer('ScatterplotLayer', data=pts, get_position='[lon, lat]', get_radius=35, get_fill_color='[0,128,0,170]')
                )
            layer_counts.append(('Problema napas', len(pts)))

        view = pdk.ViewState(latitude=float(lat), longitude=float(lon), zoom=11, pitch=35)
        st.pydeck_chart(
            pdk.Deck(
                layers=layers,
                initial_view_state=view,
                map_provider='carto',
                map_style='light',
            )
        )
        st.write('**Capas activas y cantidad de puntos**')
        if layer_counts:
            st.dataframe(pd.DataFrame(layer_counts, columns=['capa', 'puntos']), use_container_width=True, height=180)
        st.caption('Leyenda: rojo=sin baño, naranja=basural 300m, violeta=sin recolección, verde=napas, azul=puntos generales.')
        st.write('**Listado operativo por capa**')
        capa_det = st.selectbox('Ver detalle de capa', ['Sin baño', 'Basural 300m', 'Sin recolección', 'Problema napas'], key='map_det_layer')
        if capa_det == 'Sin baño':
            mk = normalize_yes_no(pad_f.get('tienebaño_vv', pd.Series('', index=pad_f.index))).isin(['no'])
        elif capa_det == 'Basural 300m':
            mk = normalize_yes_no(pad_f.get('basuralpermanente_300mts_vv', pd.Series('', index=pad_f.index))).isin(['si', 'sí'])
        elif capa_det == 'Sin recolección':
            mk = normalize_yes_no(pad_f.get('servicioregular_recoleccionresiduos_vv', pd.Series('', index=pad_f.index))).isin(['no'])
        else:
            mk = normalize_yes_no(pad_f.get('problema_napas_vv', pd.Series('', index=pad_f.index))).isin(['si', 'sí'])
        det_map = add_google_maps_link(pad_f[mk].copy())
        cols_det_map = select_existing_columns(det_map, ['dni', 'nombrecompleto', 'localidad-vv', 'calle_vv', 'numero_vv', 'ubicacion-vv', 'ubicacion_maps_link'])
        st.dataframe(det_map[cols_det_map] if cols_det_map else det_map.head(200), use_container_width=True, height=240)

with tabs[12]:
    st.subheader('Calidad de datos y trazabilidad')
    if not calidad.empty:
        st.write('**Reporte de calidad**')
        st.dataframe(calidad, use_container_width=True)

    st.write('**Estados de validacion principales**')
    c1, c2, c3 = st.columns(3)
    if 'dni_estado' in all_f.columns:
        c1.bar_chart(val_counts(all_f, 'dni_estado', 8).set_index('valor'))
    if 'telefono_estado' in all_f.columns:
        c2.bar_chart(val_counts(all_f, 'telefono_estado', 8).set_index('valor'))
    if 'fecha_nacimiento_estado' in all_f.columns:
        c3.bar_chart(val_counts(all_f, 'fecha_nacimiento_estado', 8).set_index('valor'))

    if not casos.empty:
        st.write('**Casos para revision manual**')
        cols_show = [
            c for c in [
                'dataset', 'dni', 'dni_norm', 'dni_estado',
                'fecha_nacimiento', 'fecha_nacimiento_norm', 'fecha_nacimiento_estado',
                'edad', 'edad_norm', 'edad_estado',
                'telefono', 'telefono_norm', 'telefono_estado',
                'ingreso_mensual_norm', 'ingreso_estado', 'gasto_mensual_norm', 'gasto_estado',
                'consistencia_discapacidad_flag'
            ] if c in casos.columns
        ]
        st.dataframe(casos[cols_show], use_container_width=True, height=320)

with tabs[13]:
    st.subheader('Explorador completo de variables (incluye fuera de nomenclatura)')
    fuente = st.selectbox('Fuente', ['padron', 'grupo'])
    src = pad_f if fuente == 'padron' else gru_f
    columnas = sorted(src.columns.tolist())
    col_map = {f"{hlabel(c)} (`{c}`)": c for c in columnas}
    col_sel_label = st.selectbox('Columna', list(col_map.keys()), index=0 if columnas else None)
    col_sel = col_map[col_sel_label] if columnas else None
    topn = st.slider('Top valores', min_value=5, max_value=30, value=15, step=1)

    if col_sel:
        nn = int(non_empty(src[col_sel]).sum())
        st.write(f'No vacios: {nn} / {len(src)} ({(nn / max(len(src),1))*100:.2f}%)')
        vc = val_counts(src, col_sel, topn=topn, exclude_missing=True)
        if vc.empty:
            st.info('No hay datos no vacios para graficar en la columna seleccionada.')
        else:
            st.bar_chart(vc.set_index('valor'))
            st.dataframe(vc, use_container_width=True)
            valor_sel = st.selectbox('Detalle por valor (drill-down)', vc['valor'].tolist(), key='explore_val_sel')
            det = src[src[col_sel].fillna('').astype(str).str.strip().eq(valor_sel)].copy()
            st.write(f'Registros con "{valor_sel}": **{len(det)}**')
            det_cols = select_existing_columns(det, ['dni', 'nombrecompleto', 'dni_jefehogar', 'nombre_jefehogar', 'localidad-vv', 'censado_fecha', 'ubicacion-vv', col_sel])
            det_view = add_google_maps_link(det[det_cols].copy() if det_cols else det.copy())
            st.dataframe(det_view, use_container_width=True, height=260)

with tabs[3]:
    st.subheader('Filtro dinamico operativo')
    st.caption('Permite filtrar por columna + valor y obtener listado de casos para accion en territorio, incluyendo respuestas multiples por token.')

    if 'favoritos_filtro' not in st.session_state:
        st.session_state['favoritos_filtro'] = []

    st.write('**Buscador rápido**')
    q = st.text_input('Buscar por DNI, nombre, jefe o localidad', key='quick_search')
    if q.strip():
        qq = q.strip().lower()
        search_src = add_google_maps_link(all_f.copy())
        mask = pd.Series(False, index=search_src.index)
        for c in ['dni', 'nombrecompleto', 'nombre_jefehogar', 'dni_jefehogar', 'localidad-vv']:
            if c in search_src.columns:
                mask = mask | search_src[c].fillna('').astype(str).str.lower().str.contains(qq, regex=False)
        found = search_src[mask].copy()
        st.write(f'Resultados búsqueda: {len(found)}')
        show_cols = [c for c in ['origen_persona', 'dni', 'nombrecompleto', 'nombre_jefehogar', 'dni_jefehogar', 'localidad-vv', 'censado_fecha', 'ubicacion-vv', 'ubicacion_maps_link'] if c in found.columns]
        st.dataframe(found[show_cols], use_container_width=True, height=260)
        st.divider()

    fuente = st.selectbox(
        'Fuente de datos',
        ['personas (padron + grupo)', 'padron (viviendas/jefes)', 'grupo_familiar (miembros)']
    )
    if fuente.startswith('personas'):
        src = all_f
    elif fuente.startswith('padron'):
        src = pad_f
    else:
        src = gru_f

    cols_ops = sorted(src.columns.tolist())
    labels_ops = {f"{hlabel(c)}  (`{c}`)": c for c in cols_ops}
    label_keys = list(labels_ops.keys())
    col_label = st.selectbox('Columna a filtrar', label_keys, key='op_col_label')
    col_op = labels_ops[col_label]

    if col_op:
        vals = src[col_op].fillna('').astype(str).str.strip()
        vals = sorted([v for v in vals.unique().tolist() if v != ''])
        if vals:
            modo = st.radio(
                'Modo de comparacion',
                ['Coincidencia exacta', 'Contiene texto', 'Token multirespuesta'],
                horizontal=True
            )
            detalle_filtro = ''
            if modo == 'Coincidencia exacta':
                val_op = st.selectbox('Valor', vals, key='op_val_exact')
                filtrado = src[src[col_op].fillna('').astype(str).str.strip().eq(val_op)].copy()
                detalle_filtro = f"{col_op} == {val_op}"
            elif modo == 'Contiene texto':
                val_op = st.selectbox('Texto', vals, key='op_val_contains')
                filtrado = src[src[col_op].fillna('').astype(str).str.contains(val_op, case=False, regex=False)].copy()
                detalle_filtro = f"{col_op} contiene '{val_op}'"
            else:
                tokens = token_universe(src, col_op)
                selected_tokens = st.multiselect('Selecciona uno o mas tokens', tokens, key='op_tokens')
                token_logic = st.radio('Logica de tokens', ['ANY (cualquiera)', 'ALL (todos)'], horizontal=True)
                logic = 'ALL' if token_logic.startswith('ALL') else 'ANY'
                filtrado = filter_by_tokens(src, col_op, selected_tokens, mode=logic)
                detalle_filtro = f"{col_op} token {logic}: {', '.join(selected_tokens) if selected_tokens else '(sin tokens)'}"

                st.write(f'Resultados: {len(filtrado)}')

            id_cols_base = [
                'origen_persona', 'dni', 'nombrecompleto', 'nombre_jefehogar', 'dni_jefehogar',
                'localidad-vv', 'domicilio-dni-vv', 'calle_vv', 'numero_vv',
                'tipo_vv', 'tenencia_vv', 'tienebaño_vv', 'elbañotiene_vv',
                'telefono', 'telefono_norm', 'censado_fecha_norm', 'censado_fecha',
                'fecha_nacimiento_norm', 'fecha_nacimiento', 'ubicacion-vv'
            ]
            id_cols = [c for c in id_cols_base if c in filtrado.columns]
            if col_op not in id_cols:
                id_cols.append(col_op)
            view_fil = add_google_maps_link(filtrado[id_cols].copy())
            if 'ubicacion_maps_link' not in id_cols and 'ubicacion-vv' in filtrado.columns:
                # lo agrega para visualización/exportación cuando haya coordenadas
                pass
                st.dataframe(view_fil, use_container_width=True, height=380)

                fav1, fav2 = st.columns([2, 3])
                fav_name = fav1.text_input('Guardar como favorito', placeholder='Ej: Sin baño en San Antonio', key='fav_name_filter')
                if fav2.button('Guardar favorito actual', key='save_filter_fav'):
                    st.session_state['favoritos_filtro'].append({
                        'nombre': fav_name.strip() if fav_name.strip() else f'Favorito {len(st.session_state["favoritos_filtro"]) + 1}',
                        'fuente': fuente,
                        'columna': col_op,
                        'modo': modo,
                        'detalle': detalle_filtro,
                        'filas': len(view_fil),
                    })
                    st.success('Favorito guardado.')

                if st.session_state['favoritos_filtro']:
                    fav_opts = [f"{f['nombre']} | {f['detalle']} ({f['filas']} filas)" for f in st.session_state['favoritos_filtro']]
                    st.selectbox('Favoritos guardados (referencia)', fav_opts, key='fav_list_view')

                st.write('**Exportar resultado del filtro dinámico**')
            cexp1, cexp2, cexp3 = st.columns(3)
            cexp1.download_button(
                'Descargar CSV',
                data=view_fil.to_csv(index=False).encode('utf-8-sig'),
                file_name='filtro_dinamico.csv',
                mime='text/csv',
            )
            if EXCEL_ENABLED:
                cexp2.download_button(
                    'Descargar Excel',
                    data=to_excel_bytes(view_fil, sheet_name='filtro_dinamico'),
                    file_name='filtro_dinamico.xlsx',
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
            else:
                cexp2.caption('Excel no disponible (instalar xlsxwriter u openpyxl)')
            if PDF_ENABLED:
                cexp3.download_button(
                    'Descargar PDF',
                    data=to_pdf_bytes(
                        view_fil,
                        title='Filtro dinamico - Censo FME 2025',
                        filters=global_filter_lines + [f'Fuente: {fuente}', f'Modo: {modo}', f'Condicion: {detalle_filtro}'],
                    ),
                    file_name='filtro_dinamico.pdf',
                    mime='application/pdf',
                )
            else:
                cexp3.caption('PDF no disponible (instalar reportlab)')
        else:
            st.info('La columna seleccionada no tiene valores no vacíos para filtrar. Probá otra variable o quitá filtros globales.')

with tabs[14]:
    st.subheader('Matriz de priorizacion territorial (localidad)')
    st.caption('Score sintetico 0-100 basado en indicadores de riesgo habitacional, ambiental, acceso a salud y empleo.')
    mat = build_priorization_matrix(pad_f)
    if mat.empty:
        st.info('No hay datos suficientes para construir la matriz en el filtro actual.')
    else:
        st.dataframe(mat, use_container_width=True, height=360)
        st.write('**Top localidades prioritarias**')
        top = mat[['localidad-vv', 'score_prioridad', 'nivel_prioridad', 'viviendas']].head(10)
        st.dataframe(top, use_container_width=True)

with tabs[1]:
    st.subheader('Consultas guiadas')
    st.caption('Modo simple para usuarios no técnicos: elegir perfil, responder preguntas simples y ver resultados.')

    st.write('**Constructor simplificado por preguntas**')
    q1, q2 = st.columns(2)
    universo_q = q1.selectbox('1) Quiero analizar', ['Personas', 'Viviendas'], key='simpleq_universo')
    localidad_q = q2.selectbox(
        '2) En qué territorio',
        ['Todas'] + sorted([x for x in pad_f.get('localidad-vv', pd.Series(dtype=str)).dropna().astype(str).unique() if str(x).strip()]),
        key='simpleq_loc',
    )
    salida_q = 'Resumen + listado'

    if universo_q == 'Personas':
        situaciones_por_categoria = {
            'Trabajo': [
                'Desempleo reportado',
                'Buscando trabajo activamente',
                'Desempleo y buscando trabajo',
                'Con oficio declarado',
                'Sin aportes jubilatorios (aproximado)',
                'Trabajo fuera de jurisdicción local (aproximado)',
            ],
            'Salud': [
                'Sin cobertura de salud',
                'Cobertura PAMI',
                'Cobertura OSEP',
                'Con receta y no consiguió medicamentos',
                'No consiguió medicamentos (cualquier caso)',
                'Con enfermedades crónicas/tratamiento prolongado',
                'Con tratamiento crónico y sin cobertura',
                'Embarazo actual (Mujer/Varon trans 10-60)',
                'Embarazo adolescente (<20)',
                'Con 3 o más embarazos acumulados',
            ],
            'Discapacidad': [
                'Con discapacidad y CUD = No',
                'Con discapacidad y CUD = Sí',
                'Con discapacidad y dependencia declarada',
                'Con elementos ortopédicos declarados',
            ],
            'Asistencia': [
                'Con asistencia social declarada',
                'Con AUH en el hogar',
                'Con plan y buscando trabajo',
                'Asiste merendero/comedor',
                'No conoce dependencias municipales',
            ],
            'Educación y perfil': [
                'Sin secundario completo (aproximado)',
                'Con estudios superiores (aproximado)',
                'Sin teléfono declarado',
                'Niñez y adolescencia (0-17 años)',
                'Adultos mayores (65+ años)',
                'Nacionalidad no argentina (aproximado)',
                'Varón trans',
            ],
        }
    else:
        situaciones_por_categoria = {
            'Riesgos vivienda': [
                'Sin baño',
                'Sin agua en vivienda',
                'Sin electricidad en vivienda',
                'Sin recolección de residuos',
                'Con basural a 300m',
                'Con problema de napas',
                'Sin baño y sin recolección',
                'Con basural y sin recolección',
                'Sin baño o sin agua (al menos uno)',
                'Con basural o sin recolección (al menos uno)',
                'Con 2 o más riesgos (baño/recolección/basural/napas)',
            ],
            'Mascotas': [
                'Con mascotas',
                'Con mascotas no totalmente castradas',
                'Con mascotas y ninguna castrada',
                'Con 3 o más mascotas',
            ],
            'Servicios y equipamiento': [
                'Con servicio de gas declarado',
                'Con servicio de internet declarado',
                'Sin internet declarado',
                'Con internet y sin gas',
                'Sin servicios declarados',
                'Con equipamiento declarado',
                'Sin equipamiento declarado',
                'Sin computadora en equipamiento',
                'Con computadora en equipamiento',
            ],
            'Tenencia y registro': [
                'Sin matrícula catastral declarada',
                'Con matrícula catastral declarada',
                'Tenencia precaria (aproximado)',
                'Con valoración de gestión negativa (aproximado)',
                'Con valoración de gestión positiva (aproximado)',
            ],
            'Hogares y movilidad': [
                'Con más de 1 hogar en la vivienda',
                'Con más de 3 hogares en la vivienda',
                'Con más de 2 celulares activos',
                'Sin celulares activos',
                'Con bicicletas particulares (>0)',
                'Con bicicletas Fray en Bici (>0)',
                'Con movilidad sustentable en vivienda (bicis >0)',
                'Sin internet y sin computadora',
            ],
        }

    opciones_q = []
    for cat in situaciones_por_categoria.keys():
        for s in situaciones_por_categoria.get(cat, []):
            opciones_q.append(f'{cat} | {s}')
    foco_q_labels = st.multiselect(
        '3) Qué situación(es) querés ver',
        opciones_q,
        default=opciones_q[:1] if opciones_q else [],
        key='simpleq_focos',
    )
    foco_q_list = [x.split(' | ', 1)[1] for x in foco_q_labels]
    logica_q = 'AND (todas)'
    st.caption('Combinación de situaciones: AND (todas) [fijo]')

    base_q = all_f.copy() if universo_q == 'Personas' else pad_f.copy()
    if localidad_q != 'Todas' and 'localidad-vv' in base_q.columns:
        base_q = base_q[base_q['localidad-vv'].eq(localidad_q)]

    yq = normalize_yes_no

    def mask_situacion(df: pd.DataFrame, situacion: str) -> pd.Series:
        if situacion == 'Desempleo reportado':
            tx = yq(df.get('trabajo_tb', pd.Series('', index=df.index)))
            return tx.str.contains('desemple', regex=False) | tx.str.contains('desocup', regex=False)
        if situacion == 'Buscando trabajo activamente':
            tx = yq(df.get('buscando_trabajo_tb', pd.Series('', index=df.index)))
            return tx.isin(['si', 'sí'])
        if situacion == 'Desempleo y buscando trabajo':
            t1 = yq(df.get('trabajo_tb', pd.Series('', index=df.index)))
            t2 = yq(df.get('buscando_trabajo_tb', pd.Series('', index=df.index)))
            return (t1.str.contains('desemple', regex=False) | t1.str.contains('desocup', regex=False)) & t2.isin(['si', 'sí'])
        if situacion == 'Sin cobertura de salud':
            tx = yq(df.get('cobertura_salud', pd.Series('', index=df.index)))
            return tx.str.contains('no posee', regex=False)
        if situacion == 'Cobertura PAMI':
            tx = yq(df.get('cobertura_salud', pd.Series('', index=df.index)))
            return tx.str.contains('pami', regex=False)
        if situacion == 'Cobertura OSEP':
            tx = yq(df.get('cobertura_salud', pd.Series('', index=df.index)))
            return tx.str.contains('osep', regex=False)
        if situacion == 'Con receta y no consiguió medicamentos':
            r = yq(df.get('medicamentosrecetados_ultimoaño_salud', pd.Series('', index=df.index))).isin(['si', 'sí'])
            n = yq(df.get('consiguio_medicamentos_salud', pd.Series('', index=df.index))).isin(['no'])
            return r & n
        if situacion == 'No consiguió medicamentos (cualquier caso)':
            n = yq(df.get('consiguio_medicamentos_salud', pd.Series('', index=df.index))).isin(['no'])
            return n
        if situacion == 'Con discapacidad y CUD = No':
            disc_col = df.get('discapacidad_disc_norm', df.get('discapacidad_disc', pd.Series('', index=df.index)))
            d = yq(disc_col).isin(['si', 'sí'])
            c = yq(df.get('CUD-Discapacidad_disc', pd.Series('', index=df.index))).isin(['no'])
            return d & c
        if situacion == 'Con discapacidad y dependencia declarada':
            disc_col = df.get('discapacidad_disc_norm', df.get('discapacidad_disc', pd.Series('', index=df.index)))
            d = yq(disc_col).isin(['si', 'sí'])
            dep = df.get('dependencia_discapacidad_disc', pd.Series('', index=df.index)).fillna('').astype(str).str.strip()
            return d & dep.ne('')
        if situacion == 'Con elementos ortopédicos declarados':
            eo = df.get('elementos_ortopedicos_disc', pd.Series('', index=df.index)).fillna('').astype(str).str.strip()
            return eo.ne('')
        if situacion == 'Con asistencia social declarada':
            a = yq(df.get('beneficiario_plan_asistencia', pd.Series('', index=df.index))).isin(['si', 'sí'])
            return a
        if situacion == 'Con AUH en el hogar':
            a = yq(df.get('miembro_flia_auh_asistencia', pd.Series('', index=df.index))).isin(['si', 'sí'])
            return a
        if situacion == 'Con plan y buscando trabajo':
            a = yq(df.get('beneficiario_plan_asistencia', pd.Series('', index=df.index))).isin(['si', 'sí'])
            b = yq(df.get('buscando_trabajo_tb', pd.Series('', index=df.index))).isin(['si', 'sí'])
            return a & b
        if situacion == 'Con oficio declarado':
            a = yq(df.get('tiene_oficio_tb', pd.Series('', index=df.index))).isin(['si', 'sí'])
            b = non_empty(df.get('cual_oficio_tb', pd.Series('', index=df.index)))
            return a | b
        if situacion == 'Sin aportes jubilatorios (aproximado)':
            a = yq(df.get('aportes_jubilatorios_tb', pd.Series('', index=df.index)))
            return a.isin(['no']) | a.str.contains('no', regex=False)
        if situacion == 'Trabajo fuera de jurisdicción local (aproximado)':
            a = yq(df.get('juridisccion_trabajo_tb', pd.Series('', index=df.index)))
            return a.str.contains('otra', regex=False) | a.str.contains('fuera', regex=False) | a.str.contains('provincia', regex=False)
        if situacion == 'Asiste merendero/comedor':
            a = yq(df.get('frecuencia_merenderocomedor_asistencia', pd.Series('', index=df.index)))
            return a.ne('')
        if situacion == 'No conoce dependencias municipales':
            a = yq(df.get('conoce_dependenciasmunicipales', pd.Series('', index=df.index)))
            return a.isin(['no']) | a.str.contains('no', regex=False)
        if situacion == 'Nacionalidad no argentina (aproximado)':
            n = yq(df.get('nacionalidad', pd.Series('', index=df.index)))
            return n.ne('') & ~n.str.contains('argentin', regex=False)
        if situacion == 'Varón trans':
            g = df.get('genero_norm', pd.Series('', index=df.index)).fillna('').astype(str)
            return g.eq('Varon trans')
        if situacion == 'Con estudios superiores (aproximado)':
            n = yq(df.get('nivel-estudiosalcanzados', pd.Series('', index=df.index)))
            return n.str.contains('terciario', regex=False) | n.str.contains('universitario', regex=False)
        if situacion == 'Con tratamiento crónico y sin cobertura':
            c = df.get('enfermedadescronicas_tratamientosprolongados_salud', pd.Series('', index=df.index)).fillna('').astype(str).str.strip().ne('')
            s = yq(df.get('cobertura_salud', pd.Series('', index=df.index))).str.contains('no posee', regex=False)
            return c & s
        if situacion == 'Con discapacidad y CUD = Sí':
            disc_col = df.get('discapacidad_disc_norm', df.get('discapacidad_disc', pd.Series('', index=df.index)))
            d = yq(disc_col).isin(['si', 'sí'])
            c = yq(df.get('CUD-Discapacidad_disc', pd.Series('', index=df.index))).isin(['si', 'sí'])
            return d & c
        if situacion == 'Embarazo actual (Mujer/Varon trans 10-60)':
            g = df.get('genero_norm', pd.Series('', index=df.index)).isin(['Mujer', 'Varon trans'])
            e = pd.to_numeric(df.get('edad_norm', pd.Series('', index=df.index)), errors='coerce').between(10, 60, inclusive='both')
            p = yq(df.get('esta_embarazada_salud', pd.Series('', index=df.index))).isin(['si', 'sí'])
            return g & e & p
        if situacion == 'Embarazo adolescente (<20)':
            g = df.get('genero_norm', pd.Series('', index=df.index)).isin(['Mujer', 'Varon trans'])
            e = pd.to_numeric(df.get('edad_norm', pd.Series('', index=df.index)), errors='coerce').between(10, 19, inclusive='both')
            p = yq(df.get('esta_embarazada_salud', pd.Series('', index=df.index))).isin(['si', 'sí'])
            return g & e & p
        if situacion == 'Con 3 o más embarazos acumulados':
            c = pd.to_numeric(df.get('cantidadtotal_embarazos_salud', pd.Series('', index=df.index)), errors='coerce')
            return c.ge(3)
        if situacion == 'Con enfermedades crónicas/tratamiento prolongado':
            a = df.get('enfermedadescronicas_tratamientosprolongados_salud', pd.Series('', index=df.index)).fillna('').astype(str).str.strip()
            return a.ne('')
        if situacion == 'Sin teléfono declarado':
            t = non_empty(df.get('telefono', pd.Series('', index=df.index)))
            return ~t
        if situacion == 'Niñez y adolescencia (0-17 años)':
            e = pd.to_numeric(df.get('edad_norm', pd.Series('', index=df.index)), errors='coerce')
            return e.between(0, 17, inclusive='both')
        if situacion == 'Adultos mayores (65+ años)':
            e = pd.to_numeric(df.get('edad_norm', pd.Series('', index=df.index)), errors='coerce')
            return e.ge(65)
        if situacion == 'Sin secundario completo (aproximado)':
            ncol = df.get('nivel-estudiosalcanzados', pd.Series('', index=df.index)).fillna('').astype(str).str.lower()
            ncol = ncol.str.replace('í', 'i', regex=False)
            alto = ncol.str.contains('secundario completo|terciario|universitario', regex=True)
            return ncol.ne('') & ~alto
        if situacion == 'Sin baño':
            b = yq(df.get('tienebaño_vv', pd.Series('', index=df.index))).isin(['no', 'sin', 'no tiene'])
            return b
        if situacion == 'Sin recolección de residuos':
            b = yq(df.get('servicioregular_recoleccionresiduos_vv', pd.Series('', index=df.index))).isin(['no'])
            return b
        if situacion == 'Con basural a 300m':
            b = yq(df.get('basuralpermanente_300mts_vv', pd.Series('', index=df.index))).isin(['si', 'sí'])
            return b
        if situacion == 'Con problema de napas':
            b = yq(df.get('problema_napas_vv', pd.Series('', index=df.index))).isin(['si', 'sí'])
            return b
        if situacion == 'Con basural y sin recolección':
            b1 = yq(df.get('basuralpermanente_300mts_vv', pd.Series('', index=df.index))).isin(['si', 'sí'])
            b2 = yq(df.get('servicioregular_recoleccionresiduos_vv', pd.Series('', index=df.index))).isin(['no'])
            return b1 & b2
        if situacion == 'Sin baño y sin recolección':
            b1 = yq(df.get('tienebaño_vv', pd.Series('', index=df.index))).isin(['no', 'sin', 'no tiene'])
            b2 = yq(df.get('servicioregular_recoleccionresiduos_vv', pd.Series('', index=df.index))).isin(['no'])
            return b1 & b2
        if situacion == 'Sin agua en vivienda':
            a = yq(df.get('tieneagua_vv', pd.Series('', index=df.index))).isin(['no'])
            return a
        if situacion == 'Sin electricidad en vivienda':
            a = yq(df.get('electricidad_vv', pd.Series('', index=df.index))).isin(['no'])
            return a
        if situacion == 'Con mascotas no totalmente castradas':
            t = yq(df.get('tiene_mascotas', pd.Series('', index=df.index))).isin(['si', 'sí'])
            m = pd.to_numeric(df.get('cuantas_mascotas', pd.Series('', index=df.index)), errors='coerce')
            c = pd.to_numeric(df.get('cuantas_mascotas_castradas', pd.Series('', index=df.index)), errors='coerce')
            return t & m.notna() & c.notna() & c.lt(m)
        if situacion == 'Con mascotas':
            t = yq(df.get('tiene_mascotas', pd.Series('', index=df.index))).isin(['si', 'sí'])
            return t
        if situacion == 'Sin matrícula catastral declarada':
            m = non_empty(df.get('matricula_catastral', pd.Series('', index=df.index)))
            return ~m
        if situacion == 'Con matrícula catastral declarada':
            m = non_empty(df.get('matricula_catastral', pd.Series('', index=df.index)))
            return m
        if situacion == 'Con más de 2 celulares activos':
            c = pd.to_numeric(df.get('celulares_activos_vv', pd.Series('', index=df.index)), errors='coerce')
            return c.gt(2)
        if situacion == 'Sin celulares activos':
            c = pd.to_numeric(df.get('celulares_activos_vv', pd.Series('', index=df.index)), errors='coerce')
            return c.eq(0)
        if situacion == 'Con más de 1 hogar en la vivienda':
            c = pd.to_numeric(df.get('cantidad_hogares_vv', pd.Series('', index=df.index)), errors='coerce')
            return c.gt(1)
        if situacion == 'Con bicicletas particulares (>0)':
            c = pd.to_numeric(df.get('bicis_particulares', pd.Series('', index=df.index)), errors='coerce')
            return c.gt(0)
        if situacion == 'Con bicicletas Fray en Bici (>0)':
            c = pd.to_numeric(df.get('bicis_frayenbici', pd.Series('', index=df.index)), errors='coerce')
            return c.gt(0)
        if situacion == 'Tenencia precaria (aproximado)':
            t = yq(df.get('tenencia_vv', pd.Series('', index=df.index)))
            return t.str.contains('prestad', regex=False) | t.str.contains('ocup', regex=False) | t.str.contains('alquil', regex=False) | t.str.contains('cedida', regex=False)
        if situacion == 'Con valoración de gestión negativa (aproximado)':
            t = yq(df.get('valoracion_gestion', pd.Series('', index=df.index)))
            return t.str.contains('mala', regex=False) | t.str.contains('muy mala', regex=False) | t.str.contains('regular', regex=False)
        if situacion == 'Con servicio de gas declarado':
            t = yq(df.get('servicios_vv', pd.Series('', index=df.index)))
            return t.str.contains('gas', regex=False)
        if situacion == 'Con servicio de internet declarado':
            t = yq(df.get('servicios_vv', pd.Series('', index=df.index)))
            return t.str.contains('internet', regex=False)
        if situacion == 'Sin internet declarado':
            t = yq(df.get('servicios_vv', pd.Series('', index=df.index)))
            return ~t.str.contains('internet', regex=False)
        if situacion == 'Sin baño o sin agua (al menos uno)':
            b = yq(df.get('tienebaño_vv', pd.Series('', index=df.index))).isin(['no', 'sin', 'no tiene'])
            a = yq(df.get('tieneagua_vv', pd.Series('', index=df.index))).isin(['no'])
            return b | a
        if situacion == 'Con basural o sin recolección (al menos uno)':
            b1 = yq(df.get('basuralpermanente_300mts_vv', pd.Series('', index=df.index))).isin(['si', 'sí'])
            b2 = yq(df.get('servicioregular_recoleccionresiduos_vv', pd.Series('', index=df.index))).isin(['no'])
            return b1 | b2
        if situacion == 'Con equipamiento declarado':
            e = df.get('equipamiento_vv', pd.Series('', index=df.index)).fillna('').astype(str).str.strip()
            return e.ne('')
        if situacion == 'Sin equipamiento declarado':
            e = df.get('equipamiento_vv', pd.Series('', index=df.index)).fillna('').astype(str).str.strip()
            return e.eq('')
        if situacion == 'Sin computadora en equipamiento':
            e = yq(df.get('equipamiento_vv', pd.Series('', index=df.index)))
            return (~e.str.contains('computadora', regex=False)) & (~e.str.contains('pc', regex=False)) & (~e.str.contains('notebook', regex=False))
        if situacion == 'Con computadora en equipamiento':
            e = yq(df.get('equipamiento_vv', pd.Series('', index=df.index)))
            return e.str.contains('computadora', regex=False) | e.str.contains('pc', regex=False) | e.str.contains('notebook', regex=False)
        if situacion == 'Con internet y sin gas':
            t = yq(df.get('servicios_vv', pd.Series('', index=df.index)))
            return t.str.contains('internet', regex=False) & ~t.str.contains('gas', regex=False)
        if situacion == 'Sin servicios declarados':
            t = df.get('servicios_vv', pd.Series('', index=df.index)).fillna('').astype(str).str.strip()
            return t.eq('')
        if situacion == 'Con mascotas y ninguna castrada':
            t = yq(df.get('tiene_mascotas', pd.Series('', index=df.index))).isin(['si', 'sí'])
            m = pd.to_numeric(df.get('cuantas_mascotas', pd.Series('', index=df.index)), errors='coerce')
            c = pd.to_numeric(df.get('cuantas_mascotas_castradas', pd.Series('', index=df.index)), errors='coerce')
            return t & m.gt(0) & c.fillna(0).eq(0)
        if situacion == 'Con 3 o más mascotas':
            m = pd.to_numeric(df.get('cuantas_mascotas', pd.Series('', index=df.index)), errors='coerce')
            return m.ge(3)
        if situacion == 'Con más de 3 hogares en la vivienda':
            c = pd.to_numeric(df.get('cantidad_hogares_vv', pd.Series('', index=df.index)), errors='coerce')
            return c.gt(3)
        if situacion == 'Con valoración de gestión positiva (aproximado)':
            t = yq(df.get('valoracion_gestion', pd.Series('', index=df.index)))
            return t.str.contains('buena', regex=False) | t.str.contains('muy buena', regex=False) | t.str.contains('excelente', regex=False)
        if situacion == 'Con movilidad sustentable en vivienda (bicis >0)':
            b1 = pd.to_numeric(df.get('bicis_particulares', pd.Series('', index=df.index)), errors='coerce').fillna(0)
            b2 = pd.to_numeric(df.get('bicis_frayenbici', pd.Series('', index=df.index)), errors='coerce').fillna(0)
            return (b1 + b2).gt(0)
        if situacion == 'Sin internet y sin computadora':
            s = yq(df.get('servicios_vv', pd.Series('', index=df.index)))
            e = yq(df.get('equipamiento_vv', pd.Series('', index=df.index)))
            sin_internet = ~s.str.contains('internet', regex=False)
            sin_pc = (~e.str.contains('computadora', regex=False)) & (~e.str.contains('pc', regex=False)) & (~e.str.contains('notebook', regex=False))
            return sin_internet & sin_pc

        b1 = yq(df.get('tienebaño_vv', pd.Series('', index=df.index))).isin(['no', 'sin', 'no tiene']).astype(int)
        b2 = yq(df.get('servicioregular_recoleccionresiduos_vv', pd.Series('', index=df.index))).isin(['no']).astype(int)
        b3 = yq(df.get('basuralpermanente_300mts_vv', pd.Series('', index=df.index))).isin(['si', 'sí']).astype(int)
        b4 = yq(df.get('problema_napas_vv', pd.Series('', index=df.index))).isin(['si', 'sí']).astype(int)
        return (b1 + b2 + b3 + b4) >= 2

    if not foco_q_list:
        st.info('Seleccioná al menos una situación para ejecutar la consulta.')
        res_q = base_q.head(0).copy()
        foco_q_desc = '(sin situaciones)'
    else:
        masks_q = [mask_situacion(base_q, f) for f in foco_q_list]
        if logica_q.startswith('AND'):
            mask_final = masks_q[0].copy()
            for m in masks_q[1:]:
                mask_final = mask_final & m
            foco_q_desc = ' AND '.join(foco_q_list)
        else:
            mask_final = masks_q[0].copy()
            for m in masks_q[1:]:
                mask_final = mask_final | m
            foco_q_desc = ' OR '.join(foco_q_list)
        res_q = base_q[mask_final].copy()

    st.write(
        f"Consulta: **{universo_q}** con condición **{foco_q_desc}** en **{localidad_q}**. "
        f"Resultado: **{len(res_q)}** casos ({(len(res_q)/max(len(base_q),1)*100):.2f}% del universo)."
    )
    show_q_cols = select_existing_columns(
        res_q,
        ['origen_persona', 'dni', 'nombrecompleto', 'nombre_jefehogar', 'dni_jefehogar', 'localidad-vv', 'censado_fecha', 'ubicacion-vv',
         'trabajo_tb', 'cobertura_salud', 'consiguio_medicamentos_salud', 'discapacidad_disc_norm', 'CUD-Discapacidad_disc',
         'tienebaño_vv', 'servicioregular_recoleccionresiduos_vv', 'basuralpermanente_300mts_vv', 'problema_napas_vv', 'tiene_mascotas',
         'cuantas_mascotas', 'cuantas_mascotas_castradas', 'buscando_trabajo_tb', 'tiene_oficio_tb', 'cual_oficio_tb',
         'miembro_flia_auh_asistencia', 'esta_embarazada_salud', 'enfermedadescronicas_tratamientosprolongados_salud',
         'matricula_catastral', 'celulares_activos_vv', 'bicis_frayenbici', 'bicis_particulares', 'cantidad_hogares_vv',
         'tieneagua_vv', 'electricidad_vv', 'servicios_vv', 'tenencia_vv', 'valoracion_gestion', 'telefono',
         'edad_norm', 'nivel-estudiosalcanzados', 'dependencia_discapacidad_disc', 'elementos_ortopedicos_disc',
         'aportes_jubilatorios_tb', 'juridisccion_trabajo_tb', 'frecuencia_merenderocomedor_asistencia', 'conoce_dependenciasmunicipales',
         'nacionalidad', 'equipamiento_vv', 'cantidadtotal_embarazos_salud', 'genero_norm', 'discapacidad_disc']
    )
    view_q = add_google_maps_link(res_q[show_q_cols].copy() if show_q_cols else res_q.copy())
    m1, m2 = st.columns(2)
    m1.metric('Casos', len(res_q))
    m2.metric('Universo', len(base_q))
    st.dataframe(view_q, use_container_width=True, height=260)
    exq1, exq2, exq3 = st.columns(3)
    exq1.download_button(
        'Descargar CSV (consulta simple)',
        data=view_q.to_csv(index=False).encode('utf-8-sig'),
        file_name='consulta_simple_guiada.csv',
        mime='text/csv',
    )
    if EXCEL_ENABLED:
        exq2.download_button(
            'Descargar Excel (consulta simple)',
            data=to_excel_bytes(view_q, sheet_name='consulta_simple'),
            file_name='consulta_simple_guiada.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    else:
        exq2.caption('Excel no disponible (instalar xlsxwriter u openpyxl)')
    if PDF_ENABLED:
        exq3.download_button(
            'Descargar PDF (consulta simple)',
            data=to_pdf_bytes(
                view_q,
                title='Consulta simple guiada - Censo FME 2025',
                filters=global_filter_lines + [f'Universo: {universo_q}', f'Condición: {foco_q_desc}', f'Lógica: {logica_q}', f'Territorio: {localidad_q}'],
            ),
            file_name='consulta_simple_guiada.pdf',
            mime='application/pdf',
        )
    else:
        exq3.caption('PDF no disponible (instalar reportlab)')

    if len(res_q) == 0:
        st.warning('No se encontraron resultados. Probá otra situación o territorio.')
    elif len(res_q) < 10:
        st.info('Pocos casos. Interpretar con cautela.')

with tabs[4]:
    st.subheader('Consulta avanzada')
    st.caption('Condicionales, lógica AND/OR, cruces y exportación.')

    fuente_q = st.selectbox('Fuente', ['personas (padron + grupo)', 'padron (viviendas/jefes)', 'grupo_familiar (miembros)'], key='adv_fuente')
    if fuente_q.startswith('personas'):
        src_q = all_f.copy()
    elif fuente_q.startswith('padron'):
        src_q = pad_f.copy()
    else:
        src_q = gru_f.copy()

    if src_q.empty:
        st.info('No hay datos para consulta avanzada con el filtro actual.')
    else:
        n_cond = st.slider('Cantidad de condiciones', 1, 6, 2, key='adv_ncond')
        logica_global = st.radio('Combinar con', ['AND', 'OR'], horizontal=True, key='adv_logic')
        masks, cond_desc = [], []
        ops = ['igual', 'distinto', 'contiene', '>', '>=', '<', '<=', 'entre', 'esta vacio', 'no vacio', 'token multirespuesta']
        cols_q = sorted(src_q.columns.tolist())

        for i in range(n_cond):
            st.markdown(f'**Condición {i+1}**')
            c1, c2, c3 = st.columns([2, 2, 3])
            col_q = c1.selectbox('Columna', cols_q, key=f'adv_col_{i}')
            op_q = c2.selectbox('Operador', ops, key=f'adv_op_{i}')
            v1 = ''; v2 = ''; toks = []; tok_mode = 'ANY'
            if op_q in {'igual', 'distinto'}:
                vals = sorted(src_q[col_q].fillna('').astype(str).str.strip().replace('', pd.NA).dropna().unique().tolist())[:400]
                v1 = c3.selectbox('Valor', vals, key=f'adv_val_{i}') if vals else ''
            elif op_q == 'contiene':
                v1 = c3.text_input('Texto', key=f'adv_txt_{i}')
            elif op_q in {'>', '>=', '<', '<='}:
                v1 = c3.text_input('Valor numérico', key=f'adv_num_{i}')
            elif op_q == 'entre':
                a, b = c3.columns(2)
                v1 = a.text_input('Desde', key=f'adv_from_{i}')
                v2 = b.text_input('Hasta', key=f'adv_to_{i}')
            elif op_q == 'token multirespuesta':
                toks = c3.multiselect('Tokens', token_universe(src_q, col_q), key=f'adv_tok_{i}')
                tok_mode = c3.radio('Lógica', ['ANY', 'ALL'], horizontal=True, key=f'adv_tmode_{i}')
            cond_desc.append(f"{col_q} {op_q} {v1 if v1 else ''}{('..'+v2) if v2 else ''}")
            masks.append(apply_condition(src_q, col_q, op_q, v1=v1, v2=v2, tokens=toks, token_mode=tok_mode))

        mask = masks[0].copy()
        for m in masks[1:]:
            mask = (mask & m) if logica_global == 'AND' else (mask | m)
        result = src_q[mask].copy()
        st.write(f'Resultados: {len(result)}')
        # Cruce avanzado
        cx1, cx2, cx3 = st.columns(3)
        col_row = cx1.selectbox('Fila', cols_q, key='adv_cruce_row')
        col_col = cx2.selectbox('Columna', cols_q, key='adv_cruce_col')
        metric = cx3.selectbox('Métrica', ['Conteo', '% sobre resultados', '% sobre viviendas censadas'], key='adv_cruce_metric')
        if col_row in result.columns and col_col in result.columns:
            r = result[col_row]
            c = result[col_col]
            if isinstance(r, pd.DataFrame):
                r = r.iloc[:, 0]
            if isinstance(c, pd.DataFrame):
                c = c.iloc[:, 0]
            t = pd.DataFrame({'r': r, 'c': c})
            t = t[non_empty(t['r']) & non_empty(t['c'])]
            if not t.empty:
                ct = pd.crosstab(t['r'], t['c'])
                if metric == '% sobre resultados':
                    ct = (ct / max(len(result), 1) * 100).round(2)
                elif metric == '% sobre viviendas censadas':
                    ct = (ct / max(viviendas_censadas, 1) * 100).round(2)
                st.dataframe(ct, use_container_width=True, height=260)

        view_adv = add_google_maps_link(result.copy())
        st.dataframe(view_adv, use_container_width=True, height=320)
        ex1, ex2, ex3 = st.columns(3)
        ex1.download_button('Descargar CSV', data=view_adv.to_csv(index=False).encode('utf-8-sig'), file_name='consulta_avanzada.csv', mime='text/csv')
        if EXCEL_ENABLED:
            ex2.download_button(
                'Descargar Excel',
                data=to_excel_bytes(view_adv, sheet_name='consulta_avanzada'),
                file_name='consulta_avanzada.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
        else:
            ex2.caption('Excel no disponible (instalar xlsxwriter u openpyxl)')
        if PDF_ENABLED:
            ex3.download_button(
                'Descargar PDF',
                data=to_pdf_bytes(
                    view_adv,
                    title='Consulta avanzada - Censo FME 2025',
                    filters=global_filter_lines + [f'Fuente: {fuente_q}', f'Lógica: {logica_global}'] + cond_desc,
                ),
                file_name='consulta_avanzada.pdf',
                mime='application/pdf',
            )
        else:
            ex3.caption('PDF no disponible (instalar reportlab)')

st.divider()
st.subheader('Base analitica filtrada (personas)')
cols_base = [
    c for c in [
        'origen_persona', 'dni', 'nombrecompleto', 'censado_fecha_norm', 'fecha_nacimiento_norm', 'localidad-vv', 'genero_norm',
        'edad_norm', 'trabajo_tb', 'movilidad_trabajo_tb', 'tipo_vv', 'tenencia_vv',
        'discapacidad_disc_norm', 'cobertura_salud', 'dni_estado', 'telefono_estado'
    ] if c in all_f.columns
]
base_view = add_google_maps_link(all_f[cols_base].copy())
st.dataframe(base_view, use_container_width=True, height=340)
st.write('**Exportar base analítica filtrada**')
b1, b2, b3 = st.columns(3)
b1.download_button(
    'Descargar CSV',
    data=base_view.to_csv(index=False).encode('utf-8-sig'),
    file_name='base_analitica_filtrada.csv',
    mime='text/csv',
)
if EXCEL_ENABLED:
    b2.download_button(
        'Descargar Excel',
        data=to_excel_bytes(base_view, sheet_name='base_filtrada'),
        file_name='base_analitica_filtrada.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
else:
    b2.caption('Excel no disponible (instalar xlsxwriter u openpyxl)')
if PDF_ENABLED:
    b3.download_button(
        'Descargar PDF',
        data=to_pdf_bytes(
            base_view,
            title='Base analitica filtrada - Censo FME 2025',
            filters=global_filter_lines + [f'Filas exportadas: {len(base_view)}'],
        ),
        file_name='base_analitica_filtrada.pdf',
        mime='application/pdf',
    )
else:
    b3.caption('PDF no disponible (instalar reportlab)')
