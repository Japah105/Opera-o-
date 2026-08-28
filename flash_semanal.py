"""
Flash Report Semanal — Operações CCO
Uso: python flash_semanal.py [YYYY-WW] [--preview] [--enviar]
  YYYY-WW   : semana ISO (ex: 2026-35). Padrão: semana mais recente com dados completos
  --preview : abre HTML no navegador
  --enviar  : gera PDF e envia WhatsApp
"""
import sys, os, re, glob, json, subprocess, calendar, shutil, time
from datetime import date, datetime, timedelta
from collections import defaultdict
import psycopg2

sys.stdout.reconfigure(encoding='utf-8')

# ── CONFIG ────────────────────────────────────────────────────────────────────
ENV = r"C:\Users\monit\OneDrive\Área de Trabalho\Ferramenta QH\.env"
_env_raw = open(ENV, encoding='utf-8', errors='ignore').read()
def _env(key):
    if f"{key}=" not in _env_raw: return None
    return _env_raw.split(f"{key}=")[1].split()[0]

TERMINAIS = {
    "Manoel Feio":       ["03TR","05TR","07TR","09TR","11TR","20TR","02TR"],
    "GCM — Ítalo Adami": ["04TR","06TR","15TR","16TR","19VP","34TR"],
    "Estação Itáqua":    ["01TR","21TR","29TR"],
    "Santa Tereza":      ["08TR","10TR","19TR"],
}
EX   = "'97TR','98TR','99TR','99'"
ATIV = "('Viagem Normal','Viagem Extra')"
TAD, TAI = 8, -5
META_CP, META_PT = 98.0, 92.0
DIFF = ("CASE WHEN iniciorealizado='' THEN NULL ELSE "
        "EXTRACT(EPOCH FROM (iniciorealizado::timestamp"
        " - inicioprogramado::timestamp))/60 END")
DIFF_ATD = f"FLOOR(({DIFF})) > {TAD}"
DIFF_ADI = f"({DIFF}) < {TAI}"

SAIDA_DIR = r"C:\Users\monit\OneDrive\Área de Trabalho\Ferramenta QH\saidas\semanal"
os.makedirs(SAIDA_DIR, exist_ok=True)

GRUPOS_MOTIVO = {
    "Trânsito":        ["Operação Atrasada(Transito Congestionado)","Operação Atrasada (Transito Congestionado)","Ope. Atrasada(Necessidade Operacional)"],
    "Falha Mecânica":  ["Falha mecânica.","Falha Mecânica (SOS)","Ope. atrasada por falha mecânica","Ope. adiantada por falha mecânica"],
    "Baixa Estat.":    ["Baixa Estatística","Baixa Estatistica","Baixa estatística"],
    "Falta Operador":  ["Falta de operador.","Falta de Operador","Adiantado falta de Operador.","Adiantado por falta de operador."],
    "Falta de Carro":  ["Falta de Carro","Adiantado falta de carro"],
    "Má-Fé":           ["Operação Atrasada(Má fé)","Operação Atrasada (Má Fé)","Atraso Má fé","Ope. Adiantada(Ma Fé)"],
    "Obstrução":       ["Obstrução de Via","Obstrução de via."],
    "Articulação":     ["Articulação Operacional"],
    "Acidente":        ["Acidente de Trânsito"],
    "Assalto":         ["Assalto","Ato de Violência"],
    "Falha GPS/Com.":  ["Falha de comunicação.","Falha de Comunicação (GPS)","Falha de Comunicação(sombra ponto final)"],
}
def norm_motivo(m):
    if not m or not m.strip(): return "Não informado"
    for g, vs in GRUPOS_MOTIVO.items():
        if any(v.lower() == m.strip().lower() for v in vs): return g
    return m.strip()[:30]

def linha_terminal(l):
    for t, ls in TERMINAIS.items():
        if l in ls: return t
    return "Outro"

# ── ARGS ──────────────────────────────────────────────────────────────────────
PREVIEW = "--preview" in sys.argv
ENVIAR  = "--enviar"  in sys.argv
arg_sem = next((a for a in sys.argv[1:] if re.match(r'\d{4}-\d{2}$', a)), None)

# ── BANCO ─────────────────────────────────────────────────────────────────────
conn = psycopg2.connect(_env("DATABASE_URL"))
cur  = conn.cursor()

# ── PERÍODO ───────────────────────────────────────────────────────────────────
if arg_sem:
    ano_s, sem_s = int(arg_sem[:4]), int(arg_sem[5:])
    INICIO = date.fromisocalendar(ano_s, sem_s, 1)
    FIM    = date.fromisocalendar(ano_s, sem_s, 7)
else:
    # Detecta última semana com dados completos (pelo menos 5 dias com >100 viagens)
    cur.execute(f"""
    SELECT data FROM viagens_qh
    WHERE data::date >= CURRENT_DATE - 21
      AND atividade IN {ATIV} AND linha NOT IN ({EX})
      AND inicioprogramado<>''
    GROUP BY data HAVING COUNT(*) > 100 ORDER BY data DESC
    """)
    dias_ok = sorted([date.fromisoformat(r[0][:10]) for r in cur.fetchall()])
    if not dias_ok:
        print("Sem dados disponíveis para gerar o relatório."); sys.exit(1)
    FIM    = dias_ok[-1]
    INICIO = FIM - timedelta(days=6)

ISO_WEEK = INICIO.isocalendar()[1]
ANO_SEM  = INICIO.isocalendar()[0]
INICIO_ANT = INICIO - timedelta(days=7)
FIM_ANT    = FIM    - timedelta(days=7)
TOTAL_DIAS = (FIM - INICIO).days + 1
PERIODO_STR = f"{INICIO.strftime('%d/%m/%Y')} → {FIM.strftime('%d/%m/%Y')}"
GERADO_EM   = datetime.now().strftime("%d/%m/%Y às %H:%M")
NOME_ARQ    = f"FLASH_REPORT_SEMANAL_{ANO_SEM}_S{ISO_WEEK:02d}"

print(f"Semana {ISO_WEEK}/{ANO_SEM}: {INICIO} a {FIM}")
print(f"Comparação: {INICIO_ANT} a {FIM_ANT}")

# ── HELPERS DE QUERY ──────────────────────────────────────────────────────────
def q_cppt(ini, fim, extra=""):
    cur.execute(f"""
    SELECT COUNT(*) as v,
      COUNT(*) FILTER(WHERE iniciorealizado='') as p,
      COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
      COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi
    FROM viagens_qh
    WHERE data::date BETWEEN '{ini}' AND '{fim}'
      AND atividade IN {ATIV} AND linha NOT IN ({EX}) AND inicioprogramado<>''
      {extra}
    """)
    v,p,a,d = (int(x or 0) for x in cur.fetchone())
    r = v - p
    return dict(v=v,p=p,atd=a,adi=d,real=r,
                cp=round(100*(v-p)/v,1) if v else 0,
                pt=round(100*(r-a-d)/r,1) if r else 0)

# ── QUERIES SEMANA ATUAL ──────────────────────────────────────────────────────
G  = q_cppt(INICIO, FIM)
GA = q_cppt(INICIO_ANT, FIM_ANT)

# Folga (quantas pode perder/ter e manter meta)
folga_cp = max(0, int(G['v'] * (1 - META_CP/100)) - G['p'])
folga_pt = max(0, int(G['real'] * (1 - META_PT/100)) - G['atd'] - G['adi'])

# Evolução diária
cur.execute(f"""
SELECT data,
  COUNT(*) as v,
  COUNT(*) FILTER(WHERE iniciorealizado='') as p,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi
FROM viagens_qh
WHERE data::date BETWEEN '{INICIO}' AND '{FIM}'
  AND atividade IN {ATIV} AND linha NOT IN ({EX}) AND inicioprogramado<>''
GROUP BY data ORDER BY data
""")
EVOL = []
for row in cur.fetchall():
    d = date.fromisoformat(str(row[0])[:10])
    v2,p2,a2,d2 = int(row[1]),int(row[2]),int(row[3]),int(row[4])
    r2 = v2 - p2
    EVOL.append(dict(data=str(d), dia=d.strftime("%a %d/%m"),
                     v=v2,p=p2,atd=a2,adi=d2,real=r2,
                     cp=round(100*(v2-p2)/v2,1) if v2 else 0,
                     pt=round(100*(r2-a2-d2)/r2,1) if r2 else 0))

# Melhor/pior dia
MELHOR_CP = max(EVOL, key=lambda x: x['cp']) if EVOL else None
PIOR_CP   = min(EVOL, key=lambda x: x['cp']) if EVOL else None
MELHOR_PT = max(EVOL, key=lambda x: x['pt']) if EVOL else None
PIOR_PT   = min(EVOL, key=lambda x: x['pt']) if EVOL else None

# Por terminal — atual e anterior
TERM = {}
for t, ls in TERMINAIS.items():
    ln_l = ",".join(f"'{l}'" for l in ls)
    TERM[t] = q_cppt(INICIO, FIM, f"AND linha IN ({ln_l})")
    TERM[t]['ant'] = q_cppt(INICIO_ANT, FIM_ANT, f"AND linha IN ({ln_l})")

# Por linha — atual
cur.execute(f"""
SELECT linha,
  COUNT(*) as v,
  COUNT(*) FILTER(WHERE iniciorealizado='') as p,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi
FROM viagens_qh
WHERE data::date BETWEEN '{INICIO}' AND '{FIM}'
  AND atividade IN {ATIV} AND linha NOT IN ({EX}) AND inicioprogramado<>''
GROUP BY linha ORDER BY linha
""")
LINHAS = {}
for r2 in cur.fetchall():
    ln,v2,p2,a2,d2 = r2[0],int(r2[1]),int(r2[2]),int(r2[3]),int(r2[4])
    r3=v2-p2
    LINHAS[ln] = dict(v=v2,p=p2,atd=a2,adi=d2,real=r3,
                      cp=round(100*(v2-p2)/v2,1) if v2 else 0,
                      pt=round(100*(r3-a2-d2)/r3,1) if r3 else 0,
                      terminal=linha_terminal(ln))

# Por linha — anterior
cur.execute(f"""
SELECT linha,
  COUNT(*) as v,
  COUNT(*) FILTER(WHERE iniciorealizado='') as p,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi
FROM viagens_qh
WHERE data::date BETWEEN '{INICIO_ANT}' AND '{FIM_ANT}'
  AND atividade IN {ATIV} AND linha NOT IN ({EX}) AND inicioprogramado<>''
GROUP BY linha ORDER BY linha
""")
LINHAS_ANT = {}
for r2 in cur.fetchall():
    ln,v2,p2,a2,d2 = r2[0],int(r2[1]),int(r2[2]),int(r2[3]),int(r2[4])
    r3=v2-p2
    LINHAS_ANT[ln] = dict(cp=round(100*(v2-p2)/v2,1) if v2 else 0,
                          pt=round(100*(r3-a2-d2)/r3,1) if r3 else 0)

# Motivos CP
cur.execute(f"""
SELECT motivo, COUNT(*) as n, COUNT(DISTINCT data) as dias,
  STRING_AGG(DISTINCT linha, ',') as linhas
FROM cco_eventos_cp
WHERE data::date BETWEEN '{INICIO}' AND '{FIM}'
GROUP BY motivo ORDER BY 2 DESC
""")
mc_grupos = defaultdict(lambda: dict(n=0,dias=0,linhas=set()))
for r2 in cur.fetchall():
    g = norm_motivo(r2[0] or '')
    mc_grupos[g]['n']    += int(r2[1])
    mc_grupos[g]['dias']  = max(mc_grupos[g]['dias'], int(r2[2]))
    mc_grupos[g]['linhas'].update((r2[3] or '').split(','))
MOTIVOS_CP = sorted([dict(g=g,n=v['n'],dias=v['dias'],linhas=sorted(v['linhas'])[:4])
                     for g,v in mc_grupos.items() if g != 'Não informado'],
                    key=lambda x: -x['n'])[:10]

# Motivos PT
cur.execute(f"""
SELECT motivo, COUNT(*) as n, COUNT(DISTINCT date) as dias,
  STRING_AGG(DISTINCT linha, ',') as linhas
FROM cco_eventos_pt
WHERE date::date BETWEEN '{INICIO}' AND '{FIM}'
GROUP BY motivo ORDER BY 2 DESC
""")
mp_grupos = defaultdict(lambda: dict(n=0,dias=0,linhas=set()))
for r2 in cur.fetchall():
    g = norm_motivo(r2[0] or '')
    mp_grupos[g]['n']    += int(r2[1])
    mp_grupos[g]['dias']  = max(mp_grupos[g]['dias'], int(r2[2]))
    mp_grupos[g]['linhas'].update((r2[3] or '').split(','))
MOTIVOS_PT = sorted([dict(g=g,n=v['n'],dias=v['dias'],linhas=sorted(v['linhas'])[:4])
                     for g,v in mp_grupos.items() if g != 'Não informado'],
                    key=lambda x: -x['n'])[:10]

# Ofensores CP
cur.execute(f"""
SELECT motorista, linha, COUNT(*) as oc, COUNT(DISTINCT data) as dias,
  MODE() WITHIN GROUP (ORDER BY motivo) as mot_princ,
  MODE() WITHIN GROUP (ORDER BY EXTRACT(HOUR FROM inicioprogramado::timestamp)::int) as hora
FROM cco_eventos_cp
WHERE data::date BETWEEN '{INICIO}' AND '{FIM}'
  AND motorista IS NOT NULL AND motorista <> ''
  AND linha NOT IN ({EX})
GROUP BY motorista, linha HAVING COUNT(*)>=2
ORDER BY dias DESC, oc DESC LIMIT 15
""")
OFENS_CP = [dict(mat=r[0],ln=r[1],oc=int(r[2]),dias=int(r[3]),mot=norm_motivo(r[4] or ''),h=int(r[5] or 0)) for r in cur.fetchall()]

# Ofensores PT
cur.execute(f"""
SELECT motorista, linha, COUNT(*) as oc, COUNT(DISTINCT date) as dias,
  MODE() WITHIN GROUP (ORDER BY motivo) as mot_princ,
  MODE() WITHIN GROUP (ORDER BY EXTRACT(HOUR FROM inicioprogramado::timestamp)::int) as hora
FROM cco_eventos_pt
WHERE date::date BETWEEN '{INICIO}' AND '{FIM}'
  AND motorista IS NOT NULL AND motorista <> ''
  AND linha NOT IN ({EX})
GROUP BY motorista, linha HAVING COUNT(*)>=2
ORDER BY dias DESC, oc DESC LIMIT 15
""")
OFENS_PT = [dict(mat=r[0],ln=r[1],oc=int(r[2]),dias=int(r[3]),mot=norm_motivo(r[4] or ''),h=int(r[5] or 0)) for r in cur.fetchall()]

# Horários críticos PT
cur.execute(f"""
SELECT EXTRACT(HOUR FROM inicioprogramado::timestamp)::int as h,
  linha,
  COUNT(*) FILTER(WHERE {DIFF_ATD}) as atd,
  COUNT(*) FILTER(WHERE {DIFF_ADI}) as adi,
  COUNT(*) FILTER(WHERE iniciorealizado='') as perd,
  COUNT(*) as tot
FROM viagens_qh
WHERE data::date BETWEEN '{INICIO}' AND '{FIM}'
  AND atividade IN {ATIV} AND linha NOT IN ({EX}) AND inicioprogramado<>''
GROUP BY h, linha
HAVING COUNT(*) FILTER(WHERE {DIFF_ATD} OR {DIFF_ADI} OR iniciorealizado='') >= 3
ORDER BY (COUNT(*) FILTER(WHERE {DIFF_ATD}) + COUNT(*) FILTER(WHERE {DIFF_ADI})) DESC LIMIT 20
""")
HOR_CRIT = [dict(h=int(r[0]),ln=r[1],atd=int(r[2]),adi=int(r[3]),perd=int(r[4]),tot=int(r[5]),
                 irr=int(r[2])+int(r[3])) for r in cur.fetchall()]

# Partidas críticas (top perdas e atrasos extremos)
cur.execute(f"""
SELECT linha, inicioprogramado, iniciorealizado,
  {DIFF} as diff,
  motorista
FROM viagens_qh
WHERE data::date BETWEEN '{INICIO}' AND '{FIM}'
  AND atividade IN {ATIV} AND linha NOT IN ({EX}) AND inicioprogramado<>''
  AND (iniciorealizado='' OR FLOOR(({DIFF})) > {TAD+4})
ORDER BY CASE WHEN iniciorealizado='' THEN 99999 ELSE -({DIFF}) END DESC
LIMIT 15
""")
PART_CRIT = []
for r2 in cur.fetchall():
    ln,prog,real,diff,mat = r2
    tipo = "PERDIDA" if (not real or real=='') else "ATRASO"
    diff_min = round(float(diff),0) if diff else None
    PART_CRIT.append(dict(ln=ln,prog=str(prog)[:16],real=str(real)[:16] if real else '',
                          tipo=tipo,diff=diff_min,mat=mat or ''))

conn.close()
print(f"CP={G['cp']}% PT={G['pt']}% | Perdidas={G['p']} Atrasos={G['atd']} Adiant={G['adi']}")

# ── ANALYTICS ─────────────────────────────────────────────────────────────────
def fp(v): return f"{v:.1f}%"
def fn(v): return f"{v:,}".replace(",",".")
def cor_pct(v, meta):
    if v >= meta:     return "#15803D"
    if v >= meta-3:   return "#D97706"
    return "#DC2626"
def badge_pct(v, meta):
    if v >= meta:     return "bd-ok"
    if v >= meta-3:   return "bd-w"
    return "bd-c"

def score_opp(oc, dias, total_dias, conc=0.5):
    imp = min(oc / 20.0, 1.0)
    rec = min(dias / total_dias, 1.0)
    return imp*0.45 + rec*0.40 + conc*0.15

def prioridade(s):
    if s >= 0.55: return "ALTA",   "#DC2626", "🔴"
    if s >= 0.28: return "MÉDIA",  "#D97706", "🟡"
    return              "BAIXA",   "#15803D", "🟢"

# Oportunidades por linha
OPPS = []
for ln, ld in LINHAS.items():
    irr = ld['atd'] + ld['adi']
    if irr >= 3:
        s = score_opp(irr, TOTAL_DIAS, TOTAL_DIAS)
        pr,cor,ico = prioridade(s)
        OPPS.append(dict(tipo='PT',ln=ln,terminal=ld['terminal'],oc=irr,
                         pt=ld['pt'],cp=ld['cp'],atd=ld['atd'],adi=ld['adi'],
                         score=s,pri=pr,cor=cor,ico=ico))
    if ld['p'] >= 2:
        s = score_opp(ld['p'], TOTAL_DIAS, TOTAL_DIAS)
        pr,cor,ico = prioridade(s)
        OPPS.append(dict(tipo='CP',ln=ln,terminal=ld['terminal'],oc=ld['p'],
                         pt=ld['pt'],cp=ld['cp'],atd=0,adi=0,
                         score=s,pri=pr,cor=cor,ico=ico))
OPPS.sort(key=lambda x: -x['score'])
TOP_OPPS = OPPS[:10]

# Análise por terminal: bom, ruim, evoluiu, piorou
def analisa_terminal(t_nome):
    td   = TERM[t_nome]
    ls   = TERMINAIS[t_nome]
    t_ls = [(ln, LINHAS[ln]) for ln in ls if ln in LINHAS]
    ant  = td['ant']

    bom, ruim, evoluiu, piorou = [], [], [], []

    linhas_cp_ok  = [ln for ln,ld in t_ls if ld['cp'] >= META_CP]
    linhas_pt_ok  = [ln for ln,ld in t_ls if ld['pt'] >= META_PT]
    linhas_cp_bad = [ln for ln,ld in t_ls if ld['cp'] < META_CP-3]
    linhas_pt_bad = [ln for ln,ld in t_ls if ld['pt'] < META_PT-3]
    linhas_cp_att = [ln for ln,ld in t_ls if META_CP-3 <= ld['cp'] < META_CP]
    linhas_pt_att = [ln for ln,ld in t_ls if META_PT-3 <= ld['pt'] < META_PT]

    if linhas_cp_ok:
        bom.append(f"CP dentro da meta: {', '.join(linhas_cp_ok)}")
    if linhas_pt_ok:
        bom.append(f"PT dentro da meta: {', '.join(linhas_pt_ok)}")
    if linhas_cp_bad:
        ruim.append(f"CP abaixo da meta: {', '.join(linhas_cp_bad)}")
    if linhas_pt_bad:
        ruim.append(f"PT abaixo da meta: {', '.join(linhas_pt_bad)}")
    if linhas_cp_att:
        ruim.append(f"CP em atenção: {', '.join(linhas_cp_att)}")
    if linhas_pt_att:
        ruim.append(f"PT em atenção: {', '.join(linhas_pt_att)}")

    # Linha com mais perdas
    mais_perdas = max(t_ls, key=lambda x: x[1]['p'], default=None)
    if mais_perdas and mais_perdas[1]['p'] > 0:
        ruim.append(f"{mais_perdas[0]}: {mais_perdas[1]['p']} partidas perdidas")

    # Linha com mais atrasos
    mais_atd = max(t_ls, key=lambda x: x[1]['atd'], default=None)
    if mais_atd and mais_atd[1]['atd'] > 5:
        ruim.append(f"{mais_atd[0]}: {mais_atd[1]['atd']} atrasos ofensivos")

    # Evolução vs semana anterior
    diff_cp = td['cp'] - ant['cp']
    diff_pt = td['pt'] - ant['pt']
    if diff_cp >= 0.5:
        evoluiu.append(f"CP: {fp(ant['cp'])} → {fp(td['cp'])} (+{diff_cp:.1f} p.p.)")
    elif diff_cp <= -0.5:
        piorou.append(f"CP: {fp(ant['cp'])} → {fp(td['cp'])} ({diff_cp:.1f} p.p.)")
    if diff_pt >= 0.5:
        evoluiu.append(f"PT: {fp(ant['pt'])} → {fp(td['pt'])} (+{diff_pt:.1f} p.p.)")
    elif diff_pt <= -0.5:
        piorou.append(f"PT: {fp(ant['pt'])} → {fp(td['pt'])} ({diff_pt:.1f} p.p.)")

    # Por linha vs anterior
    for ln, ld in t_ls:
        ant_l = LINHAS_ANT.get(ln)
        if not ant_l: continue
        dcp = ld['cp'] - ant_l['cp']
        dpt = ld['pt'] - ant_l['pt']
        if dcp >= 2.0:
            evoluiu.append(f"{ln} CP: {fp(ant_l['cp'])} → {fp(ld['cp'])} (+{dcp:.1f} p.p.)")
        elif dcp <= -2.0:
            piorou.append(f"{ln} CP: {fp(ant_l['cp'])} → {fp(ld['cp'])} ({dcp:.1f} p.p.)")
        if dpt >= 2.0:
            evoluiu.append(f"{ln} PT: {fp(ant_l['pt'])} → {fp(ld['pt'])} (+{dpt:.1f} p.p.)")
        elif dpt <= -2.0:
            piorou.append(f"{ln} PT: {fp(ant_l['pt'])} → {fp(ld['pt'])} ({dpt:.1f} p.p.)")

    return bom[:4], ruim[:5], evoluiu[:3], piorou[:3]

# ── SVG HELPERS ───────────────────────────────────────────────────────────────
def svg_line_week(data_list, w=500, h=130):
    if not data_list: return ""
    n = len(data_list)
    PL,PR,PT,PB = 36,10,12,36
    cw = w - PL - PR
    ch = h - PT - PB
    mn,mx = 70,100
    def sx(i): return PL + i * cw / max(n-1,1)
    def sy(v): return PT + ch - (min(max(v,mn),mx) - mn)/(mx-mn)*ch

    ls = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:{w}px">']
    # Grid
    for v in range(int(mn),int(mx)+1,5):
        y=sy(v)
        ls.append(f'<line x1="{PL}" y1="{y:.1f}" x2="{w-PR}" y2="{y:.1f}" stroke="#E2E8F0" stroke-width="1"/>')
        ls.append(f'<text x="{PL-4}" y="{y+3:.1f}" font-size="8" fill="#9CA3AF" text-anchor="end" font-family="Segoe UI,Arial">{v}</text>')
    # Meta lines
    for meta,cor,lbl in [(META_CP,"#15803D","CP"),(META_PT,"#1D4ED8","PT")]:
        ym=sy(meta)
        ls.append(f'<line x1="{PL}" y1="{ym:.1f}" x2="{w-PR}" y2="{ym:.1f}" stroke="{cor}" stroke-width="1" stroke-dasharray="5,3" opacity="0.35"/>')
    # Área CP
    pts_a = " ".join(f"{sx(i):.1f},{sy(d['cp']):.1f}" for i,d in enumerate(data_list))
    # CP polyline
    ls.append(f'<polyline points="{pts_a}" stroke="#15803D" stroke-width="2.5" fill="none" stroke-linejoin="round"/>')
    # PT polyline
    pts_b = " ".join(f"{sx(i):.1f},{sy(d['pt']):.1f}" for i,d in enumerate(data_list))
    ls.append(f'<polyline points="{pts_b}" stroke="#1D4ED8" stroke-width="2.5" fill="none" stroke-linejoin="round"/>')
    # Dots + labels
    for i,d in enumerate(data_list):
        x=sx(i); yc=sy(d['cp']); yp=sy(d['pt'])
        ls.append(f'<circle cx="{x:.1f}" cy="{yc:.1f}" r="4" fill="#15803D"/>')
        ls.append(f'<circle cx="{x:.1f}" cy="{yp:.1f}" r="4" fill="#1D4ED8"/>')
        ls.append(f'<text x="{x:.1f}" y="{yc-6:.1f}" font-size="7.5" fill="#15803D" text-anchor="middle" font-family="Segoe UI,Arial" font-weight="700">{d["cp"]}</text>')
        ls.append(f'<text x="{x:.1f}" y="{yp+13:.1f}" font-size="7.5" fill="#1D4ED8" text-anchor="middle" font-family="Segoe UI,Arial" font-weight="700">{d["pt"]}</text>')
        ls.append(f'<text x="{x:.1f}" y="{h-4}" font-size="8" fill="#6B7280" text-anchor="middle" font-family="Segoe UI,Arial">{d["dia"]}</text>')
    # Legend
    ls += ['<rect x="36" y="2" width="8" height="5" fill="#15803D" rx="1"/>',
           '<text x="47" y="8" font-size="8" fill="#15803D" font-family="Segoe UI,Arial" font-weight="600">CP%</text>',
           '<rect x="82" y="2" width="8" height="5" fill="#1D4ED8" rx="1"/>',
           '<text x="93" y="8" font-size="8" fill="#1D4ED8" font-family="Segoe UI,Arial" font-weight="600">PT%</text>']
    ls.append("</svg>")
    return "\n".join(ls)

def svg_bars_h(items, max_val=None, h_bar=20, w=380, cor="#1BBEAA"):
    if not items: return ""
    mv = max_val or max(x[1] for x in items) or 1
    pad = 3
    rh  = h_bar + pad
    H   = rh * len(items) + 6
    ls  = [f'<svg viewBox="0 0 {w} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:{w}px">']
    for i,(lbl,val,*opt) in enumerate(items):
        c = opt[0] if opt else cor
        y = i*rh + 2
        bw = int((val/mv)*(w-155))
        ls.append(f'<text x="0" y="{y+h_bar-4}" font-size="9.5" fill="#374151" font-family="Segoe UI,Arial">{lbl[:24]}</text>')
        ls.append(f'<rect x="125" y="{y}" width="{bw}" height="{h_bar-pad}" rx="3" fill="{c}" opacity="0.85"/>')
        ls.append(f'<text x="{125+bw+4}" y="{y+h_bar-4}" font-size="9.5" fill="#374151" font-family="Segoe UI,Arial" font-weight="700">{val}</text>')
    ls.append("</svg>")
    return "\n".join(ls)

# ── CSS ───────────────────────────────────────────────────────────────────────
CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;background:#fff;color:#111827;font-size:12px;line-height:1.5;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{width:210mm;min-height:297mm;margin:0 auto;padding:12mm 14mm 10mm;page-break-after:always;position:relative}
.page:last-child{page-break-after:avoid}
.cover{padding:0;overflow:hidden}
@media screen{.page{box-shadow:0 2px 10px rgba(0,0,0,.12);margin-bottom:20px}}

/* Header */
.hdr{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #1BBEAA;padding-bottom:8px;margin-bottom:14px}
.hdr-l .hdr-title{font-size:16px;font-weight:800;color:#0D4F5C;letter-spacing:-.3px}
.hdr-l .hdr-title span{color:#1BBEAA}
.hdr-l .hdr-sub{font-size:9px;color:#6B7280;margin-top:2px}
.date-badge{background:linear-gradient(135deg,#0D4F5C,#1A3252);color:#fff;border-radius:6px;padding:5px 11px;text-align:center}
.date-badge-l{font-size:7px;text-transform:uppercase;letter-spacing:.1em;color:#7DD3FC;margin-bottom:1px}
.date-badge-v{font-size:12px;font-weight:800;white-space:nowrap}

/* KPI */
.kpi-row{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.kpi{flex:1;min-width:70px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:7px;padding:8px 10px}
.kpi.acent{border-left:4px solid}
.kpi-l{font-size:8px;text-transform:uppercase;letter-spacing:.06em;color:#6B7280;margin-bottom:2px}
.kpi-v{font-size:26px;font-weight:800;line-height:1}
.kpi-v.sm{font-size:18px}
.kpi-s{font-size:9px;color:#6B7280;margin-top:2px}

/* Section */
.sec{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#6B7280;margin:11px 0 7px;display:flex;align-items:center;gap:8px}
.sec::after{content:'';flex:1;height:1px;background:#E2E8F0}

/* Table */
table{width:100%;border-collapse:collapse;font-size:11px;margin-bottom:8px}
th{background:#0D4F5C;color:#fff;padding:5px 8px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.04em;font-weight:700}
th.r,td.r{text-align:right;font-variant-numeric:tabular-nums}
td{padding:4px 8px;border-bottom:1px solid #F1F5F9;vertical-align:middle}
tr:nth-child(even) td{background:#FAFBFC}
tr:last-child td{border-bottom:none}

/* Badges */
.bd{display:inline-block;padding:1px 6px;border-radius:8px;font-size:10px;font-weight:700}
.bd-ok{background:#DCFCE7;color:#15803D}
.bd-w{background:#FEF3C7;color:#B45309}
.bd-c{background:#FEE2E2;color:#DC2626}
.bd-b{background:#DBEAFE;color:#1D4ED8}
.bd-p{background:#F3E8FF;color:#7C3AED}

/* Terminal card */
.tcard{border:1px solid #E2E8F0;border-radius:8px;overflow:hidden;margin-bottom:12px}
.tcard-hdr{background:linear-gradient(135deg,#0D4F5C 0%,#1A3252 100%);color:#fff;padding:9px 13px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.tcard-nome{font-size:13px;font-weight:800;flex:1;text-transform:uppercase;letter-spacing:.04em}
.tkpi{text-align:center;background:rgba(255,255,255,.12);border-radius:5px;padding:4px 10px;min-width:58px}
.tkpi-l{font-size:7px;color:rgba(255,255,255,.55);text-transform:uppercase;letter-spacing:.06em}
.tkpi-v{font-size:17px;font-weight:800}
.tcard-body{padding:10px 13px}

/* Bom / Ruim boxes */
.anbox{border-radius:6px;padding:8px 10px;margin-bottom:7px}
.anbox-hdr{font-size:10px;font-weight:700;margin-bottom:4px;display:flex;align-items:center;gap:5px}
.anbox ul{padding-left:14px;font-size:10px;color:#374151}
.anbox ul li{margin-bottom:2px}
.anbox.bom{background:#F0FDF4;border-left:3px solid #15803D}
.anbox.bom .anbox-hdr{color:#15803D}
.anbox.ruim{background:#FFF1F2;border-left:3px solid #DC2626}
.anbox.ruim .anbox-hdr{color:#DC2626}
.anbox.evol{background:#ECFDF5;border-left:3px solid #0D9488}
.anbox.evol .anbox-hdr{color:#0D9488}
.anbox.pior{background:#FFF7ED;border-left:3px solid #D97706}
.anbox.pior .anbox-hdr{color:#D97706}

/* Oportunidades */
.opp{border-radius:7px;padding:9px 12px;border:1px solid #E2E8F0;border-left:4px solid;background:#FAFBFC;margin-bottom:6px}
.opp-hdr{display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap}
.opp-n{width:21px;height:21px;border-radius:50%;background:#0D4F5C;color:#fff;font-size:10px;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.opp-title{font-weight:700;font-size:12px;flex:1}
.opp-pri{font-size:10px;font-weight:700;padding:2px 8px;border-radius:8px}
.opp-det{font-size:10px;color:#6B7280;display:flex;gap:10px;flex-wrap:wrap}
.opp-det strong{color:#374151}

/* Onde Atacar */
.atacar{border-radius:7px;padding:9px 12px;margin-bottom:6px;border-left:4px solid}
.atacar.a{background:#FFF1F2;border-color:#DC2626}
.atacar.m{background:#FFFBEB;border-color:#D97706}
.atacar.b{background:#F0FDF4;border-color:#15803D}
.atacar-hdr{font-weight:700;font-size:11px;margin-bottom:4px}
.atacar-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:4px 12px;font-size:10px}
.atacar-item{color:#6B7280}
.atacar-item strong{color:#374151;display:block}

/* Partida crítica */
.part-card{border:1px solid #FEE2E2;border-radius:6px;padding:7px 10px;margin-bottom:5px;background:#FFF8F8;font-size:10px}
.part-tipo{font-weight:700;padding:1px 6px;border-radius:4px;font-size:9px}
.part-tipo.perd{background:#FEE2E2;color:#DC2626}
.part-tipo.atrs{background:#FEF3C7;color:#B45309}

/* Conclusão boxes */
.concl{border-radius:7px;padding:9px 12px;margin-bottom:7px;border-left:4px solid}
.concl.pos{background:#F0FDF4;border-color:#15803D}
.concl.neg{background:#FFF1F2;border-color:#DC2626}
.concl.att{background:#FFFBEB;border-color:#D97706}
.concl.opp2{background:#EFF6FF;border-color:#2563EB}
.concl.ata{background:#F5F3FF;border-color:#7C3AED}
.concl-hdr{font-weight:700;font-size:11px;margin-bottom:5px}
.concl ul{padding-left:14px;font-size:11px;color:#374151}
.concl ul li{margin-bottom:3px}

/* Footer */
.ftr{position:absolute;bottom:8mm;left:14mm;right:14mm;border-top:1px solid #E2E8F0;padding-top:4px;font-size:8px;color:#9CA3AF;display:flex;justify-content:space-between}
"""

# ── COVER SVG ─────────────────────────────────────────────────────────────────
def svg_cover():
    return f"""<svg viewBox="0 0 595 841" xmlns="http://www.w3.org/2000/svg"
          style="width:210mm;height:297mm;display:block">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0A3544"/>
      <stop offset="45%" stop-color="#0D4F5C"/>
      <stop offset="100%" stop-color="#112244"/>
    </linearGradient>
    <linearGradient id="wv0" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#083A47"/>
      <stop offset="100%" stop-color="#0D5A6B"/>
    </linearGradient>
    <linearGradient id="wv1" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#0D6B7A"/>
      <stop offset="100%" stop-color="#14958A"/>
    </linearGradient>
    <linearGradient id="wv2" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#1BBEAA"/>
      <stop offset="100%" stop-color="#13A89E"/>
    </linearGradient>
    <linearGradient id="wv3" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#0D4F5C"/>
      <stop offset="100%" stop-color="#0A3A48"/>
    </linearGradient>
    <linearGradient id="bus_body" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#1BBEAA"/>
      <stop offset="100%" stop-color="#14958A"/>
    </linearGradient>
    <filter id="glw">
      <feGaussianBlur in="SourceAlpha" stdDeviation="6" result="blur"/>
      <feFlood flood-color="#1BBEAA" flood-opacity="0.5" result="c"/>
      <feComposite in="c" in2="blur" operator="in" result="g"/>
      <feMerge><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="shadow">
      <feGaussianBlur in="SourceAlpha" stdDeviation="10"/>
      <feOffset dx="0" dy="6" result="b"/>
      <feFlood flood-color="#000" flood-opacity="0.35" result="c"/>
      <feComposite in="c" in2="b" operator="in" result="s"/>
      <feMerge><feMergeNode in="s"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>

  <!-- Background -->
  <rect width="595" height="841" fill="url(#bg)"/>

  <!-- Subtle grid dots -->
  <pattern id="dots" x="0" y="0" width="24" height="24" patternUnits="userSpaceOnUse">
    <circle cx="2" cy="2" r="1" fill="rgba(255,255,255,0.04)"/>
  </pattern>
  <rect width="595" height="841" fill="url(#dots)"/>

  <!-- Decorative arc top-right -->
  <circle cx="550" cy="80" r="180" fill="none" stroke="rgba(27,190,170,0.08)" stroke-width="40"/>
  <circle cx="550" cy="80" r="130" fill="none" stroke="rgba(27,190,170,0.06)" stroke-width="30"/>

  <!-- Waves (bottom) -->
  <path d="M0,530 C80,470 160,565 260,510 C360,455 450,555 595,495 L595,841 L0,841 Z" fill="url(#wv0)"/>
  <path d="M0,558 C90,498 185,592 285,540 C385,488 480,582 595,525 L595,841 L0,841 Z" fill="url(#wv1)"/>
  <path d="M0,592 C100,535 195,622 295,572 C395,522 495,608 595,558 L595,841 L0,841 Z" fill="url(#wv2)" opacity="0.9"/>
  <path d="M0,636 C90,585 200,658 310,620 C420,582 510,645 595,615 L595,841 L0,841 Z" fill="url(#wv3)" opacity="0.95"/>

  <!-- Bus (sits on first wave ~y=535) -->
  <g transform="translate(100,440)" filter="url(#shadow)">
    <!-- Shadow under bus -->
    <ellipse cx="200" cy="132" rx="195" ry="12" fill="#000" opacity="0.25"/>
    <!-- Body -->
    <rect x="20" y="18" width="358" height="105" rx="9" fill="url(#bus_body)"/>
    <!-- Front (angled) -->
    <polygon points="378,18 405,38 405,108 378,123" fill="#14958A"/>
    <!-- Roof stripe -->
    <rect x="20" y="18" width="358" height="20" rx="9" fill="#0D8E85"/>
    <!-- Windows strip -->
    <rect x="35" y="36" width="300" height="42" rx="4" fill="#E0FFFE" opacity="0.88"/>
    <!-- Window dividers -->
    <line x1="95" y1="36" x2="95" y2="78" stroke="#14958A" stroke-width="2"/>
    <line x1="155" y1="36" x2="155" y2="78" stroke="#14958A" stroke-width="2"/>
    <line x1="215" y1="36" x2="215" y2="78" stroke="#14958A" stroke-width="2"/>
    <line x1="275" y1="36" x2="275" y2="78" stroke="#14958A" stroke-width="2"/>
    <!-- Door -->
    <rect x="35" y="80" width="48" height="42" rx="2" fill="#0D8E85"/>
    <line x1="59" y1="80" x2="59" y2="122" stroke="#1BBEAA" stroke-width="1.5"/>
    <rect x="37" y="82" width="20" height="38" rx="1" fill="rgba(224,255,254,0.35)"/>
    <rect x="60" y="82" width="20" height="38" rx="1" fill="rgba(224,255,254,0.35)"/>
    <!-- Under panel -->
    <rect x="25" y="112" width="350" height="14" rx="4" fill="#0A6B73"/>
    <!-- Wheel arches -->
    <rect x="40" y="108" width="80" height="20" rx="3" fill="#0A5560"/>
    <rect x="278" y="108" width="80" height="20" rx="3" fill="#0A5560"/>
    <!-- Wheels -->
    <circle cx="80" cy="128" r="25" fill="#0A3544"/>
    <circle cx="80" cy="128" r="16" fill="#1BBEAA" opacity="0.6"/>
    <circle cx="80" cy="128" r="8" fill="#0D4F5C"/>
    <circle cx="80" cy="128" r="3" fill="#E0FFFE"/>
    <circle cx="318" cy="128" r="25" fill="#0A3544"/>
    <circle cx="318" cy="128" r="16" fill="#1BBEAA" opacity="0.6"/>
    <circle cx="318" cy="128" r="8" fill="#0D4F5C"/>
    <circle cx="318" cy="128" r="3" fill="#E0FFFE"/>
    <!-- Headlight -->
    <rect x="405" y="45" width="22" height="16" rx="4" fill="#FCD34D" opacity="0.95"/>
    <rect x="405" y="70" width="22" height="12" rx="3" fill="#FCD34D" opacity="0.7"/>
    <!-- Tail light -->
    <rect x="10" y="70" width="10" height="28" rx="2" fill="#EF4444" opacity="0.85"/>
    <!-- Route number plate -->
    <rect x="148" y="88" width="85" height="22" rx="3" fill="#0A3544" opacity="0.7"/>
    <text x="190" y="103" font-size="11" font-weight="800" fill="#1BBEAA" text-anchor="middle" font-family="Segoe UI,Arial" letter-spacing="1">OPERAÇÕES</text>
  </g>

  <!-- CSC mark / brand circle -->
  <circle cx="72" cy="72" r="38" fill="rgba(27,190,170,0.12)" stroke="rgba(27,190,170,0.3)" stroke-width="2"/>
  <circle cx="72" cy="72" r="26" fill="rgba(27,190,170,0.18)" stroke="rgba(27,190,170,0.4)" stroke-width="1.5"/>
  <!-- Arrow inside circle -->
  <path d="M58,72 L86,72 M76,62 L86,72 L76,82" stroke="#1BBEAA" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  <text x="72" y="122" font-size="9" fill="rgba(255,255,255,0.5)" text-anchor="middle" font-family="Segoe UI,Arial" font-weight="600" letter-spacing="2">CCO</text>

  <!-- Title block -->
  <text x="340" y="155" font-size="11" fill="rgba(255,255,255,0.5)" font-family="Segoe UI,Arial" font-weight="600" letter-spacing="4" text-anchor="middle">OPERAÇÕES</text>
  <text x="340" y="210" font-size="52" fill="#FFFFFF" font-family="Segoe UI,Arial" font-weight="900" text-anchor="middle" letter-spacing="-1">FLASH</text>
  <text x="340" y="258" font-size="36" fill="#1BBEAA" font-family="Segoe UI,Arial" font-weight="800" text-anchor="middle" letter-spacing="4">REPORT</text>

  <!-- Divider line -->
  <line x1="200" y1="275" x2="480" y2="275" stroke="rgba(27,190,170,0.4)" stroke-width="1.5"/>

  <text x="340" y="305" font-size="24" fill="rgba(255,255,255,0.85)" font-family="Segoe UI,Arial" font-weight="700" text-anchor="middle" letter-spacing="8">SEMANAL</text>
  <text x="340" y="330" font-size="11" fill="rgba(255,255,255,0.35)" font-family="Segoe UI,Arial" font-weight="400" text-anchor="middle" letter-spacing="3">ANÁLISE OPERACIONAL</text>

  <!-- Period card -->
  <rect x="205" y="355" width="270" height="88" rx="10" fill="rgba(255,255,255,0.07)" stroke="rgba(27,190,170,0.25)" stroke-width="1.5"/>
  <text x="340" y="377" font-size="8" fill="rgba(27,190,170,0.8)" font-family="Segoe UI,Arial" font-weight="600" text-anchor="middle" letter-spacing="3">SEMANA {ISO_WEEK} / {ANO_SEM}</text>
  <text x="340" y="402" font-size="14" fill="#FFFFFF" font-family="Segoe UI,Arial" font-weight="700" text-anchor="middle">{PERIODO_STR}</text>
  <line x1="225" y1="415" x2="455" y2="415" stroke="rgba(255,255,255,0.1)" stroke-width="1"/>
  <text x="340" y="432" font-size="8" fill="rgba(255,255,255,0.4)" font-family="Segoe UI,Arial" font-weight="400" text-anchor="middle" letter-spacing="2">DADOS ATUALIZADOS EM {GERADO_EM}</text>

  <!-- Bottom label -->
  <text x="340" y="800" font-size="8" fill="rgba(255,255,255,0.25)" font-family="Segoe UI,Arial" text-anchor="middle" letter-spacing="2">FLASH REPORT SEMANAL · OPERAÇÕES CCO</text>
</svg>"""

# ── PAGE HELPERS ──────────────────────────────────────────────────────────────
def hdr(sub):
    return f"""<div class="hdr"><div class="hdr-l">
      <div class="hdr-title">FLASH <span>REPORT</span> SEMANAL</div>
      <div class="hdr-sub">{sub} · {PERIODO_STR}</div>
    </div>
    <div class="date-badge"><div class="date-badge-l">Semana</div>
      <div class="date-badge-v">S{ISO_WEEK:02d}/{ANO_SEM}</div></div>
    </div>"""

def ftr(pg):
    return f"""<div class="ftr">
      <span>Flash Report Semanal · Semana {ISO_WEEK} · {PERIODO_STR}</span>
      <span>Atualizado {GERADO_EM} · Pág. {pg}</span>
    </div>"""

def ul(items, empty="—"):
    if not items: return f'<p style="font-size:10px;color:#9CA3AF">{empty}</p>'
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"

# ── P1: CAPA ──────────────────────────────────────────────────────────────────
P_CAPA = f'<div class="page cover">{svg_cover()}</div>'

# ── P2: VISÃO GERAL ───────────────────────────────────────────────────────────
cor_cp = cor_pct(G['cp'], META_CP)
cor_pt = cor_pct(G['pt'], META_PT)
var_cp = G['cp'] - GA['cp']
var_pt = G['pt'] - GA['pt']

def seta(v):
    if v >= 0.5:  return f'<span style="color:#15803D;font-size:10px">▲ +{v:.1f} p.p.</span>'
    if v <= -0.5: return f'<span style="color:#DC2626;font-size:10px">▼ {v:.1f} p.p.</span>'
    return '<span style="color:#6B7280;font-size:10px">→ estável</span>'

folga_cp_txt = (f'Ainda pode perder <strong>{folga_cp}</strong> partidas e manter a meta.'
                if folga_cp > 0 else f'<span style="color:#DC2626">Meta já ultrapassada por {abs(folga_cp)} partidas.</span>')
folga_pt_txt = (f'Ainda pode ter <strong>{folga_pt}</strong> ofensividades e manter a meta.'
                if folga_pt > 0 else f'<span style="color:#DC2626">Meta já ultrapassada por {abs(folga_pt)} ofensividades.</span>')

linhas_vis = ""
for t,td in TERM.items():
    bcp = badge_pct(td['cp'],META_CP); bpt = badge_pct(td['pt'],META_PT)
    linhas_vis += f"""<tr>
      <td><strong>{t}</strong></td>
      <td class="r"><span class="bd {bcp}">{fp(td['cp'])}</span></td>
      <td class="r"><span class="bd {bpt}">{fp(td['pt'])}</span></td>
      <td class="r">{fn(td['v'])}</td>
      <td class="r" style="{'color:#DC2626;font-weight:700' if td['p']>0 else ''}">{td['p']}</td>
      <td class="r">{td['atd']}</td><td class="r">{td['adi']}</td>
    </tr>"""

P_GERAL = f"""<div class="page">
  {hdr("Visão Geral da Semana")}
  <div class="kpi-row">
    <div class="kpi acent" style="border-left-color:{cor_cp};flex:2">
      <div class="kpi-l">CP — Cumprimento de Partida</div>
      <div class="kpi-v" style="color:{cor_cp}">{fp(G['cp'])}</div>
      <div class="kpi-s">Meta {META_CP}% &nbsp;·&nbsp; {seta(var_cp)}</div>
    </div>
    <div class="kpi acent" style="border-left-color:{cor_pt};flex:2">
      <div class="kpi-l">PT — Pontualidade</div>
      <div class="kpi-v" style="color:{cor_pt}">{fp(G['pt'])}</div>
      <div class="kpi-s">Meta {META_PT}% &nbsp;·&nbsp; {seta(var_pt)}</div>
    </div>
  </div>
  <div class="kpi-row">
    <div class="kpi"><div class="kpi-l">Previstas</div><div class="kpi-v sm" style="color:#1E40AF">{fn(G['v'])}</div></div>
    <div class="kpi"><div class="kpi-l">Realizadas</div><div class="kpi-v sm" style="color:#15803D">{fn(G['real'])}</div></div>
    <div class="kpi"><div class="kpi-l">Perdidas</div><div class="kpi-v sm" style="color:#DC2626">{G['p']}</div></div>
    <div class="kpi"><div class="kpi-l">Atrasos &gt;{TAD}min</div><div class="kpi-v sm" style="color:#D97706">{G['atd']}</div></div>
    <div class="kpi"><div class="kpi-l">Adiant. &gt;{abs(TAI)}min</div><div class="kpi-v sm" style="color:#7C3AED">{G['adi']}</div></div>
  </div>
  <div style="display:flex;gap:8px;margin-bottom:10px;font-size:10px">
    <div style="flex:1;background:#F0FDF4;border-radius:6px;padding:7px 10px;border-left:3px solid #15803D">
      📌 CP — {folga_cp_txt}</div>
    <div style="flex:1;background:#EFF6FF;border-radius:6px;padding:7px 10px;border-left:3px solid #2563EB">
      📌 PT — {folga_pt_txt}</div>
  </div>
  <div class="sec">Resumo por Terminal</div>
  <table><thead><tr><th>Terminal</th><th class="r">CP%</th><th class="r">PT%</th>
    <th class="r">Previstas</th><th class="r">Perdidas</th>
    <th class="r">Atrasos</th><th class="r">Adiant.</th></tr></thead>
    <tbody>{linhas_vis}</tbody></table>
  {ftr(2)}
</div>"""

# ── P3: EVOLUÇÃO ──────────────────────────────────────────────────────────────
grafico_ev = svg_line_week(EVOL)
melhor_txt = (f"Melhor CP: <strong>{MELHOR_CP['dia']}</strong> — {fp(MELHOR_CP['cp'])}" if MELHOR_CP else "")
pior_txt   = (f"Pior CP: <strong>{PIOR_CP['dia']}</strong> — {fp(PIOR_CP['cp'])}" if PIOR_CP else "")
melhor_pt_txt = (f"Melhor PT: <strong>{MELHOR_PT['dia']}</strong> — {fp(MELHOR_PT['pt'])}" if MELHOR_PT else "")
pior_pt_txt   = (f"Pior PT: <strong>{PIOR_PT['dia']}</strong> — {fp(PIOR_PT['pt'])}" if PIOR_PT else "")

tend_cp = "EVOLUIU ▲" if var_cp >= 0.5 else ("PIOROU ▼" if var_cp <= -0.5 else "ESTÁVEL →")
tend_pt = "EVOLUIU ▲" if var_pt >= 0.5 else ("PIOROU ▼" if var_pt <= -0.5 else "ESTÁVEL →")
cor_tend = lambda v: "#15803D" if v>=0.5 else ("#DC2626" if v<=-0.5 else "#6B7280")

P_EVOL = f"""<div class="page">
  {hdr("Evolução da Semana")}
  <div class="sec">CP e PT — Dia a Dia</div>
  {grafico_ev}
  <div style="display:flex;gap:8px;margin:10px 0;font-size:10px;flex-wrap:wrap">
    <div style="flex:1;background:#F8FAFC;border-radius:6px;padding:7px 10px">
      <div style="font-weight:700;color:#15803D;margin-bottom:3px">CP {tend_cp}</div>
      <div style="color:#374151">{melhor_txt}</div>
      <div style="color:#DC2626">{pior_txt}</div>
      <div style="color:#6B7280;margin-top:2px">vs. semana anterior: <strong style="color:{cor_tend(var_cp)}">{seta(var_cp)}</strong></div>
    </div>
    <div style="flex:1;background:#F8FAFC;border-radius:6px;padding:7px 10px">
      <div style="font-weight:700;color:#1D4ED8;margin-bottom:3px">PT {tend_pt}</div>
      <div style="color:#374151">{melhor_pt_txt}</div>
      <div style="color:#DC2626">{pior_pt_txt}</div>
      <div style="color:#6B7280;margin-top:2px">vs. semana anterior: <strong style="color:{cor_tend(var_pt)}">{seta(var_pt)}</strong></div>
    </div>
  </div>
  <div class="sec">Detalhe Diário</div>
  <table><thead><tr><th>Dia</th><th class="r">CP%</th><th class="r">PT%</th>
    <th class="r">Previstas</th><th class="r">Perdidas</th>
    <th class="r">Atrasos</th><th class="r">Adiant.</th></tr></thead>
    <tbody>{''.join(f"""<tr>
      <td><strong>{d['dia']}</strong></td>
      <td class="r"><span class="bd {badge_pct(d['cp'],META_CP)}">{fp(d['cp'])}</span></td>
      <td class="r"><span class="bd {badge_pct(d['pt'],META_PT)}">{fp(d['pt'])}</span></td>
      <td class="r">{fn(d['v'])}</td>
      <td class="r" style="{'color:#DC2626;font-weight:700' if d['p']>0 else ''}">{d['p']}</td>
      <td class="r">{d['atd']}</td><td class="r">{d['adi']}</td>
    </tr>""" for d in EVOL)}</tbody></table>
  {ftr(3)}
</div>"""

# ── P4-7: TERMINAIS ───────────────────────────────────────────────────────────
def pagina_terminal(t_nome, pg_num):
    td    = TERM[t_nome]
    ant   = td['ant']
    ls    = TERMINAIS[t_nome]
    t_ls  = [(ln, LINHAS[ln]) for ln in ls if ln in LINHAS]
    t_ls.sort(key=lambda x: x[1]['pt'])
    bom, ruim, evoluiu, piorou = analisa_terminal(t_nome)

    # Tabela de linhas
    rows = ""
    for ln, ld in t_ls:
        bcp = badge_pct(ld['cp'],META_CP); bpt = badge_pct(ld['pt'],META_PT)
        rows += f"""<tr>
          <td><strong>{ln}</strong></td>
          <td class="r"><span class="bd {bcp}">{fp(ld['cp'])}</span></td>
          <td class="r"><span class="bd {bpt}">{fp(ld['pt'])}</span></td>
          <td class="r">{fn(ld['v'])}</td>
          <td class="r" style="{'color:#DC2626;font-weight:700' if ld['p']>0 else ''}">{ld['p']}</td>
          <td class="r">{ld['atd']}</td><td class="r">{ld['adi']}</td>
        </tr>"""

    # Horários críticos do terminal (top 3)
    t_hor = [h for h in HOR_CRIT if h['ln'] in ls]
    t_hor.sort(key=lambda x: -x['irr'])
    hor_str = " · ".join(f"<strong>{h['h']:02d}h</strong> ({h['ln']}, {h['irr']} irr.)" for h in t_hor[:3]) or "—"

    # Boxes de análise (2 colunas)
    def anbox(cls, ico, titulo, items):
        if not items: return ""
        return f'<div class="anbox {cls}"><div class="anbox-hdr">{ico} {titulo}</div>{ul(items)}</div>'

    col_l = anbox("bom","✅","O que está bom",bom) + anbox("evol","↑","Evoluiu",evoluiu)
    col_r = anbox("ruim","⚠️","Precisa de atenção",ruim) + anbox("pior","↓","Piorou",piorou)

    return f"""<div class="page">
  {hdr(f"Terminal — {t_nome}")}
  <div class="tcard">
    <div class="tcard-hdr">
      <div class="tcard-nome">{t_nome}</div>
      <div class="tkpi"><div class="tkpi-l">CP</div>
        <div class="tkpi-v" style="color:{'#4ADE80' if td['cp']>=META_CP else '#FCD34D' if td['cp']>=META_CP-3 else '#FCA5A5'}">{fp(td['cp'])}</div></div>
      <div class="tkpi"><div class="tkpi-l">PT</div>
        <div class="tkpi-v" style="color:{'#4ADE80' if td['pt']>=META_PT else '#FCD34D' if td['pt']>=META_PT-3 else '#FCA5A5'}">{fp(td['pt'])}</div></div>
      <div class="tkpi"><div class="tkpi-l">Previstas</div>
        <div class="tkpi-v" style="color:#93C5FD">{fn(td['v'])}</div></div>
      <div class="tkpi"><div class="tkpi-l">Perdidas</div>
        <div class="tkpi-v" style="color:{'#FCA5A5' if td['p']>0 else '#4ADE80'}">{td['p']}</div></div>
      <div class="tkpi"><div class="tkpi-l">vs. sem. ant.</div>
        <div class="tkpi-v" style="font-size:11px;color:{'#4ADE80' if td['cp']>=ant['cp'] else '#FCA5A5'}">CP {td['cp']-ant['cp']:+.1f}</div></div>
    </div>
    <div class="tcard-body">
      <div class="sec">Linhas do Terminal</div>
      <table><thead><tr><th>Linha</th><th class="r">CP%</th><th class="r">PT%</th>
        <th class="r">Previstas</th><th class="r">Perdidas</th>
        <th class="r">Atrasos</th><th class="r">Adiant.</th></tr></thead>
        <tbody>{rows}</tbody></table>
      <div style="font-size:10px;color:#6B7280;margin-bottom:8px">⏰ Horários críticos: {hor_str}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div>{col_l}</div><div>{col_r}</div>
      </div>
    </div>
  </div>
  {ftr(pg_num)}
</div>"""

PGS_TERM = []
for i,(t,_) in enumerate(TERMINAIS.items()):
    PGS_TERM.append(pagina_terminal(t, 4+i))

# ── P8: OPORTUNIDADES + ONDE ATACAR ──────────────────────────────────────────
opps_html = ""
for i, o in enumerate(TOP_OPPS[:8]):
    tipo_bd = f'<span class="bd bd-c">PT</span>' if o['tipo']=='PT' else f'<span class="bd bd-b">CP</span>'
    opps_html += f"""<div class="opp" style="border-left-color:{o['cor']}">
      <div class="opp-hdr">
        <div class="opp-n">{i+1}</div>
        <div class="opp-title">{o['ln']} {tipo_bd} — {o['terminal']}</div>
        <span class="opp-pri" style="background:{o['cor']}22;color:{o['cor']}">{o['ico']} {o['pri']}</span>
      </div>
      <div class="opp-det">
        {'<span>PT: <strong>'+fp(o['pt'])+'</strong></span>' if o['tipo']=='PT' else ''}
        {'<span>Atrasos: <strong>'+str(o['atd'])+'</strong></span>' if o['atd'] else ''}
        {'<span>Adiant.: <strong>'+str(o['adi'])+'</strong></span>' if o['adi'] else ''}
        {'<span>Perdas: <strong>'+str(o['oc'])+'</strong></span>' if o['tipo']=='CP' else ''}
        <span>Score: <strong>{o['score']:.2f}</strong></span>
      </div>
    </div>"""

atacar_html = ""
for o in [x for x in TOP_OPPS if x['pri']=='ALTA'][:5]:
    cls = 'a'
    mot_princ = next((m['g'] for m in (MOTIVOS_PT if o['tipo']=='PT' else MOTIVOS_CP)
                      if o['ln'] in m.get('linhas',[])), "—")
    atacar_html += f"""<div class="atacar {cls}">
      <div class="atacar-hdr">{o['ico']} ALTA PRIORIDADE — {o['ln']} ({o['tipo']})</div>
      <div class="atacar-grid">
        <div class="atacar-item"><strong>Terminal</strong>{o['terminal']}</div>
        <div class="atacar-item"><strong>Indicador</strong>{o['tipo']}</div>
        {'<div class="atacar-item"><strong>PT atual</strong>'+fp(o['pt'])+'</div>' if o['tipo']=='PT' else ''}
        {'<div class="atacar-item"><strong>Ocorrências</strong>'+str(o['oc'])+'</div>'}
        <div class="atacar-item"><strong>Causa princ.</strong>{mot_princ}</div>
        <div class="atacar-item"><strong>Score</strong>{o['score']:.2f}</div>
      </div>
    </div>"""
if not atacar_html:
    for o in [x for x in TOP_OPPS if x['pri']=='MÉDIA'][:3]:
        cls = 'm'
        atacar_html += f"""<div class="atacar {cls}">
          <div class="atacar-hdr">{o['ico']} MÉDIA PRIORIDADE — {o['ln']} ({o['tipo']})</div>
          <div class="atacar-grid">
            <div class="atacar-item"><strong>Terminal</strong>{o['terminal']}</div>
            {'<div class="atacar-item"><strong>PT</strong>'+fp(o['pt'])+'</div>' if o['tipo']=='PT' else ''}
            <div class="atacar-item"><strong>Ocorr.</strong>{o['oc']}</div>
          </div>
        </div>"""
if not atacar_html:
    atacar_html = '<p style="font-size:11px;color:#15803D;font-weight:600">✅ Nenhum problema de alta prioridade identificado na semana.</p>'

P_OPPS = f"""<div class="page">
  {hdr("Oportunidades e Onde Atacar")}
  <div class="sec">🎯 Oportunidades da Semana — Impacto + Recorrência + Concentração</div>
  {opps_html or '<p style="color:#6B7280;font-size:11px">Sem oportunidades identificadas.</p>'}
  <div class="sec">📍 Onde Atacar</div>
  {atacar_html}
  {ftr(8)}
</div>"""

# ── P9: OFENSORES + MOTIVOS ───────────────────────────────────────────────────
def ofens_rows(lst):
    rows = ""
    for o in lst[:10]:
        tip = "🔴 Recorrente" if o['dias']>=3 else ("🟡 Frequente" if o['dias']>=2 else "🔵 Pontual")
        rows += f"""<tr>
          <td><strong>{o['mat']}</strong></td><td>{o['ln']}</td>
          <td class="r">{o['oc']}</td><td class="r"><strong>{o['dias']}</strong></td>
          <td>{o['mot']}</td><td class="r">{o['h']:02d}h</td>
          <td><span style="font-size:9px">{tip}</span></td>
        </tr>"""
    return rows or '<tr><td colspan="7" style="text-align:center;color:#9CA3AF">Sem ofensores com ≥ 2 ocorrências</td></tr>'

mc_items = [(m['g'],m['n'],"#DC2626") for m in MOTIVOS_CP[:8]]
mp_items = [(m['g'],m['n'],"#D97706") for m in MOTIVOS_PT[:8]]

P_OFENS = f"""<div class="page">
  {hdr("Ofensores e Motivos")}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    <div>
      <div class="sec">Motivos de Perda — CP</div>
      {svg_bars_h(mc_items, cor="#DC2626", w=270)}
    </div>
    <div>
      <div class="sec">Motivos de Irregularidade — PT</div>
      {svg_bars_h(mp_items, cor="#D97706", w=270)}
    </div>
  </div>
  <div class="sec">Ofensores CP — ≥ 2 Perdas</div>
  <table><thead><tr><th>Matrícula</th><th>Linha</th><th class="r">Ocorr.</th>
    <th class="r">Dias</th><th>Motivo</th><th class="r">Hora</th><th>Tipo</th></tr></thead>
    <tbody>{ofens_rows(OFENS_CP)}</tbody></table>
  <div class="sec">Ofensores PT — ≥ 2 Irregularidades</div>
  <table><thead><tr><th>Matrícula</th><th>Linha</th><th class="r">Ocorr.</th>
    <th class="r">Dias</th><th>Motivo</th><th class="r">Hora</th><th>Tipo</th></tr></thead>
    <tbody>{ofens_rows(OFENS_PT)}</tbody></table>
  {ftr(9)}
</div>"""

# ── P10: HORÁRIO CRÍTICO + PARTIDAS ───────────────────────────────────────────
hor_rows = ""
for h in HOR_CRIT[:12]:
    hor_rows += f"""<tr>
      <td>{h['h']:02d}:00</td><td>{h['ln']}</td>
      <td class="r" style="{'color:#D97706;font-weight:700' if h['atd']>0 else ''}">{h['atd']}</td>
      <td class="r" style="{'color:#7C3AED;font-weight:700' if h['adi']>0 else ''}">{h['adi']}</td>
      <td class="r" style="{'color:#DC2626;font-weight:700' if h['perd']>0 else ''}">{h['perd']}</td>
      <td class="r"><strong style="color:{'#DC2626' if h['irr']>10 else '#D97706' if h['irr']>5 else '#374151'}">{h['irr']}</strong></td>
    </tr>"""

part_html = ""
for p in PART_CRIT[:8]:
    tip_cls = "perd" if p['tipo']=="PERDIDA" else "atrs"
    diff_str = f"+{p['diff']:.0f} min" if p['diff'] else "—"
    part_html += f"""<div class="part-card">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:3px">
        <strong>{p['ln']}</strong>
        <span class="part-tipo {tip_cls}">{p['tipo']}</span>
        {f'<span style="font-size:10px;color:#D97706;font-weight:700">{diff_str}</span>' if p['diff'] else ''}
      </div>
      <div style="font-size:10px;color:#6B7280;display:flex;gap:10px;flex-wrap:wrap">
        <span>Prog.: <strong>{p['prog'][11:16]}</strong></span>
        {'<span>Real.: <strong>'+p["real"][11:16]+'</strong></span>' if p['real'] else ''}
        {'<span>Operador: <strong>'+p["mat"]+'</strong></span>' if p['mat'] else ''}
      </div>
    </div>"""
if not part_html:
    part_html = '<p style="font-size:10px;color:#9CA3AF">Sem partidas críticas identificadas.</p>'

P_HORARIO = f"""<div class="page">
  {hdr("Horário Crítico e Partidas")}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    <div>
      <div class="sec">Faixas Horárias Críticas — PT</div>
      <table><thead><tr><th>Hora</th><th>Linha</th>
        <th class="r">Atrasos</th><th class="r">Adiant.</th><th class="r">Perdas</th>
        <th class="r">Total Irr.</th></tr></thead>
        <tbody>{hor_rows or '<tr><td colspan="6" style="text-align:center;color:#9CA3AF">Sem dados</td></tr>'}</tbody></table>
    </div>
    <div>
      <div class="sec">Partidas Críticas da Semana</div>
      {part_html}
    </div>
  </div>
  {ftr(10)}
</div>"""

# ── P11: CONCLUSÃO ────────────────────────────────────────────────────────────
pos_items = [f"<strong>{t}</strong> — CP {fp(td['cp'])} / PT {fp(td['pt'])}"
             for t,td in TERM.items() if td['cp']>=META_CP and td['pt']>=META_PT]
neg_items  = [f"<strong>{o['ln']}</strong> ({o['terminal']}) — {o['tipo']} {fp(o['pt'] if o['tipo']=='PT' else o['cp'])} ({o['oc']} ocorr.)"
              for o in TOP_OPPS[:4] if o['pri']=='ALTA']
att_items  = [f"<strong>{o['ln']}</strong> — {o['tipo']} com {o['oc']} ocorrências"
              for o in TOP_OPPS[:4] if o['pri']=='MÉDIA']
opp_items  = [f"<strong>{o['ln']}</strong> — {o['tipo']} (score {o['score']:.2f}, {o['terminal']})"
              for o in TOP_OPPS[:5]]
ata_items  = [f"{o['ico']} {o['pri']} — <strong>{o['ln']}</strong> ({o['terminal']}, {o['tipo']})"
              for o in TOP_OPPS if o['pri']!='BAIXA'][:5]

def concl_box(cls,ico,titulo,items,empty="Nenhum destaque nesta semana."):
    body = ul(items, empty) if items else f'<p style="font-size:10px;color:#6B7280">{empty}</p>'
    return f'<div class="concl {cls}"><div class="concl-hdr">{ico} {titulo}</div>{body}</div>'

P_CONCLUSAO = f"""<div class="page">
  {hdr("Conclusão da Semana")}
  {concl_box("pos","🟢","Destaques Positivos",pos_items,"Nenhum terminal atingiu ambas as metas simultaneamente.")}
  {concl_box("neg","🔴","Principais Problemas",neg_items,"Nenhum problema de alta prioridade identificado.")}
  {concl_box("att","🟡","Pontos de Atenção",att_items,"Nenhum ponto de atenção identificado.")}
  {concl_box("opp2","🎯","Principais Oportunidades",opp_items,"Sem oportunidades identificadas.")}
  {concl_box("ata","📍","Onde Atacar",ata_items,"✅ Semana sem prioridades críticas.")}
  <div style="margin-top:10px;padding:8px 12px;background:#F8FAFC;border-radius:6px;border:1px solid #E2E8F0;font-size:9px;color:#6B7280">
    Score = Impacto (0,45) + Recorrência (0,40) + Concentração (0,15).
    🔴 ALTA ≥ 0,55 · 🟡 MÉDIA ≥ 0,28 · 🟢 BAIXA &lt; 0,28.
    Tolerâncias: atraso &gt;{TAD}min / adiantamento &gt;{abs(TAI)}min.
    {fn(G['v'])} viagens analisadas · {PERIODO_STR}.
  </div>
  {ftr(11)}
</div>"""

# ── MONTA HTML ────────────────────────────────────────────────────────────────
HTML = f"""<!DOCTYPE html>
<html lang="pt-BR"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flash Report Semanal — S{ISO_WEEK:02d}/{ANO_SEM}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap">
<style>
body{{font-family:'Inter',Segoe UI,Arial,sans-serif}}
{CSS}
</style>
</head><body>
{P_CAPA}
{P_GERAL}
{P_EVOL}
{''.join(PGS_TERM)}
{P_OPPS}
{P_OFENS}
{P_HORARIO}
{P_CONCLUSAO}
</body></html>"""

# ── OUTPUT ────────────────────────────────────────────────────────────────────
OUT_HTML = os.path.join(r"C:\Users\monit\AppData\Local\Temp", f"{NOME_ARQ}.html")
OUT_PDF  = os.path.join(SAIDA_DIR, f"{NOME_ARQ}.pdf")
_TEMP_PDF = os.path.join(r"C:\Users\monit\AppData\Local\Temp", f"{NOME_ARQ}.pdf")

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(HTML)
print(f"HTML: {OUT_HTML} ({os.path.getsize(OUT_HTML)//1024} KB)")

if PREVIEW:
    import webbrowser; webbrowser.open(OUT_HTML)
    print("[PREVIEW] Abrindo no navegador.")
    sys.exit(0)

# PDF via Edge headless
_edge_cands = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
               r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
_edge_cands += glob.glob(r"C:\Program Files*\Microsoft\Edge\Application\msedge.exe")
_browser = next((c for c in _edge_cands if os.path.exists(c)), None)
if _browser:
    _url = "file:///" + OUT_HTML.replace("\\","/").replace(" ","%20")
    subprocess.run([_browser,"--headless=new","--disable-gpu","--no-sandbox",
                    "--disable-extensions",f"--print-to-pdf={_TEMP_PDF}",
                    "--print-to-pdf-no-header",_url],
                   timeout=60, capture_output=True)
    for _ in range(15):
        if os.path.exists(_TEMP_PDF) and os.path.getsize(_TEMP_PDF) > 1000: break
        time.sleep(1)
    if os.path.exists(_TEMP_PDF):
        shutil.copy2(_TEMP_PDF, OUT_PDF)
        print(f"PDF: {OUT_PDF} ({os.path.getsize(OUT_PDF)//1024} KB)")
    else:
        print("ERRO PDF: Edge não gerou o arquivo.")
else:
    print("Edge não encontrado. PDF não gerado.")

if ENVIAR:
    import requests
    FONNTE_TOKEN = _env("FONNTE_TOKEN") or ""
    WHATSAPP_TO  = [n.strip() for n in (_env("WHATSAPP_TO") or "").split(",") if n.strip()]
    if os.path.exists(OUT_PDF) and FONNTE_TOKEN and WHATSAPP_TO:
        try:
            srv = requests.get("https://api.gofile.io/servers", timeout=10).json()
            server = srv["data"]["servers"][0]["name"]
            with open(OUT_PDF,"rb") as f2:
                up = requests.post(f"https://{server}.gofile.io/contents/uploadfile",
                                   files={"file":(os.path.basename(OUT_PDF),f2)},timeout=60).json()
            link_pdf = up["data"]["downloadPage"]
        except Exception as e:
            link_pdf = f"PDF gerado localmente em {OUT_PDF}"; print(f"gofile.io: {e}")
        msg = (f"📊 *FLASH REPORT SEMANAL — Semana {ISO_WEEK}/{ANO_SEM}*\n"
               f"Período: {PERIODO_STR}\n\n"
               f"🏁 *CP:* {fp(G['cp'])} (meta {META_CP}%)\n"
               f"⏱ *PT:* {fp(G['pt'])} (meta {META_PT}%)\n\n"
               f"📌 Previstas: {fn(G['v'])} | Perdidas: {G['p']} | Atrasos: {G['atd']}\n\n"
               f"📄 {link_pdf}\n\n"
               f"_Atualizado em {GERADO_EM}_")
        for num in WHATSAPP_TO:
            try:
                r2 = requests.post("https://api.fonnte.com/send",
                    headers={"Authorization":FONNTE_TOKEN},
                    data={"target":num,"message":msg},timeout=15)
                print(f"WhatsApp {num}: {r2.status_code}")
            except Exception as e:
                print(f"WhatsApp {num}: ERRO {e}")
    else:
        print("Envio ignorado (PDF ausente ou tokens não configurados).")

print("Concluído.")
