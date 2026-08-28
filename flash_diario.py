"""
flash_diario.py — QH Operações v4
Flash operacional diário: partida → ocorrência → ação.
Tolerâncias: atraso > 9 min / adiantamento > 5 min
97TR, 98TR, 99TR excluídas de toda análise.
Gera flash_QH.html + flash_QH.pdf no Desktop.
Uso: python flash_diario.py [YYYY-MM-DD] [--preview]
  --preview  Gera HTML e abre no navegador. Não envia email nem WhatsApp.
"""
import psycopg2, sqlite3, json, os, sys, glob, subprocess
from datetime import date, timedelta, datetime

PREVIEW = "--preview" in sys.argv
ENVIAR  = "--enviar"  in sys.argv
ARGS    = [a for a in sys.argv[1:] if not a.startswith("--")]

ENV      = r"C:\Users\monit\OneDrive\Área de Trabalho\Ferramenta QH\.env"
METAS    = r"C:\Users\monit\OneDrive\Área de Trabalho\Ferramenta QH\metas_qh.json"
DB_LOCAL = r"C:\Users\monit\OneDrive\Área de Trabalho\Ferramenta QH\ocorrencias_qh.db"
OUT      = r"C:\Users\monit\OneDrive\Área de Trabalho\flash_QH.html"

TAD, TAI = 8, -5
# Filtros alinhados ao BI: FLOOR(diff) > TAD  →  atraso só a partir de 9 min inteiros
# (DIFF>8 contaria 8.1 min; FLOOR>8 só conta 9+ min, igual ao BI)
EX   = "'97TR','98TR','99TR'"
DIFF = ("CASE WHEN iniciorealizado='' THEN NULL ELSE "
        "EXTRACT(EPOCH FROM (iniciorealizado::timestamp"
        " - inicioprogramado::timestamp))/60 END")
DIFF_ATD = f"FLOOR(({DIFF})) > {TAD}"   # atraso: inteiro > 8  →  >= 9 min
DIFF_ADI = f"({DIFF}) < {TAI}"          # adiantado: < -5 min (já correto)

TERMINAIS_PY = {
    "Manoel Feio":        ["03TR","05TR","07TR","09TR","11TR","20TR","02TR"],
    "GCM - Ítalo Adami":  ["04TR","06TR","15TR","16TR","19VP","34TR"],
    "Estação Itáqua":    ["01TR","21TR","29TR"],
    "Santa Tereza":       ["08TR","10TR","19TR"],   # confirmar linhas com operação
}

ONTEM   = ARGS[0] if ARGS else str(date.today() - timedelta(days=1))
ONTEM_7 = str((datetime.strptime(ONTEM, "%Y-%m-%d") - timedelta(days=7)).date())
DFmt    = f"{ONTEM[8:10]}/{ONTEM[5:7]}/{ONTEM[0:4]}"
DWKS    = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"]
DSem    = DWKS[datetime.strptime(ONTEM, "%Y-%m-%d").weekday()]

_mes = str(int(ONTEM[5:7]))
_met = json.load(open(METAS, encoding="utf-8"))
MCP  = float(_met["cp"].get(_mes, 97.5))
MPT  = float(_met["pt"].get(_mes, 90.0))

DB   = open(ENV).read().split("DATABASE_URL=")[1].split()[0]
conn = psycopg2.connect(DB)
cur  = conn.cursor()
print(f"Conectado. Extraindo {ONTEM} (tol: atd>{TAD} min / adi>{abs(TAI)} min)…")

# ── 1. RESUMO GERAL ──────────────────────────────────────────────────────────
cur.execute(f"""
SELECT COUNT(*) as v,
  COUNT(*) FILTER(WHERE iniciorealizado='') as perd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi,
  ROUND(AVG(({DIFF})::numeric) FILTER(WHERE ({DIFF})>0 AND ({DIFF})<60),1) as am
FROM viagens_qh
WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
  AND linha NOT IN ({EX})
""")
v,perd,atd,adi,am = cur.fetchone()
v=int(v or 0); perd=int(perd or 0); atd=int(atd or 0); adi=int(adi or 0)
cp_dia = round(100.0*(v-perd)/v,1) if v else 0
pt_dia = round(100.0*(v-perd-atd-adi)/(v-perd),1) if (v-perd) else 0
resumo = {"v":v,"perd":perd,"atd":atd,"adi":adi,"am":float(am or 0),"cp":cp_dia,"pt":pt_dia}
print(f"  Resumo: {v} viagens | CP={cp_dia}% | PT={pt_dia}% | {perd} perdidas")

# semana anterior removida — comparativo semanal será tratado em outro formato

# ── 3. POR LINHA ─────────────────────────────────────────────────────────────
cur.execute(f"""
SELECT linha,
  COUNT(*) as v,
  COUNT(*) FILTER(WHERE iniciorealizado='') as perd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi,
  ROUND(AVG(({DIFF})::numeric) FILTER(WHERE ({DIFF})>0 AND ({DIFF})<60),1) as am
FROM viagens_qh
WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
  AND linha NOT IN ({EX})
GROUP BY linha HAVING COUNT(*)>=3
ORDER BY linha
""")
por_linha = []
for ln,v2,p2,atd2,adi2,am2 in cur.fetchall():
    v2=int(v2 or 0); p2=int(p2 or 0); atd2=int(atd2 or 0); adi2=int(adi2 or 0)
    por_linha.append({"l":ln,"v":v2,"perd":p2,"atd":atd2,"adi":adi2,
                      "am":float(am2 or 0),
                      "cp":round(100.0*(v2-p2)/v2,1) if v2 else 0,
                      "pt":round(100.0*(v2-p2-atd2-adi2)/(v2-p2),1) if (v2-p2) else 0})

# ── 3b. TOTAIS BRUTOS POR LINHA (sem HAVING — para cálculo de terminal) ───────
# Não usado para exibição; usado exclusivamente para somar nos terminais,
# garantindo que linhas com 1-2 viagens no dia entrem nos totais do terminal.
cur.execute(f"""
SELECT linha,
  COUNT(*) as v,
  COUNT(*) FILTER(WHERE iniciorealizado='') as perd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi
FROM viagens_qh
WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
  AND linha NOT IN ({EX})
GROUP BY linha
ORDER BY linha
""")
_raw: dict = {}
for ln,v2,p2,atd2,adi2 in cur.fetchall():
    _raw[ln] = {"v":int(v2 or 0),"perd":int(p2 or 0),
                "atd":int(atd2 or 0),"adi":int(adi2 or 0)}

# ── 4. IDA vs VOLTA por linha ─────────────────────────────────────────────────
cur.execute(f"""
SELECT linha, COALESCE(NULLIF(TRIM(sentido),''),'?') as sent,
  COUNT(*) as v,
  COUNT(*) FILTER(WHERE iniciorealizado='') as perd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi
FROM viagens_qh
WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
  AND linha NOT IN ({EX})
GROUP BY linha, sent ORDER BY linha, sent
""")
sentidos = []
for r in cur.fetchall():
    ln,sent,v2,p2,at2,ad2 = r
    v2=int(v2 or 0); p2=int(p2 or 0); at2=int(at2 or 0); ad2=int(ad2 or 0)
    sentidos.append({"l":ln,"sent":sent,"v":v2,"perd":p2,"atd":at2,"adi":ad2,
                     "cp":round(100.0*(v2-p2)/v2,1) if v2 else 0,
                     "pt":round(100.0*(v2-p2-at2-ad2)/(v2-p2),1) if (v2-p2) else 0})

# ── 5. POR TURNO ─────────────────────────────────────────────────────────────
cur.execute(f"""
SELECT COALESCE(NULLIF(TRIM(tabela),''),'Sem tabela') as tab,
  COUNT(*) as v,
  COUNT(*) FILTER(WHERE iniciorealizado='') as perd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi
FROM viagens_qh
WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
  AND linha NOT IN ({EX})
GROUP BY tab ORDER BY tab
""")
por_turno = []
for tab,v2,p2,at2,ad2 in cur.fetchall():
    v2=int(v2 or 0); p2=int(p2 or 0); at2=int(at2 or 0); ad2=int(ad2 or 0)
    por_turno.append({"tab":tab,"v":v2,"perd":p2,"atd":at2,"adi":ad2,
                      "cp":round(100.0*(v2-p2)/v2,1) if v2 else 0,
                      "pt":round(100.0*(v2-p2-at2-ad2)/(v2-p2),1) if (v2-p2) else 0})

# ── 6. OFENSORES PT ──────────────────────────────────────────────────────────
cur.execute(f"""
WITH base AS (
  SELECT linha, matricula,
    MODE() WITHIN GROUP(ORDER BY motorista) as nome,
    MODE() WITHIN GROUP(ORDER BY veiculo) as vei,
    COUNT(*) as v,
    COUNT(*) FILTER(WHERE iniciorealizado='') as perd,
    COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
    COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi,
    ROUND(AVG(({DIFF})::numeric) FILTER(WHERE ({DIFF})>0 AND ({DIFF})<60),1) as am
  FROM viagens_qh
  WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
    AND linha NOT IN ({EX})
  GROUP BY linha, matricula HAVING COUNT(*)>=3
)
SELECT linha, matricula, TRIM(nome), vei, v, perd, atd, adi, am,
  ROUND(100.0*(v-perd)/v,1) as cp,
  ROUND(100.0*(v-perd-atd-adi)/NULLIF(v-perd,0),1) as pt
FROM base
WHERE ROUND(100.0*(v-perd-atd-adi)/NULLIF(v-perd,0),1) < {MPT}
ORDER BY pt ASC
""")
of_pt = []
for r in cur.fetchall():
    of_pt.append({"l":r[0],"m":r[1],"nome":(r[2] or ""),"vei":(r[3] or "—"),
                  "v":int(r[4] or 0),"perd":int(r[5] or 0),"atd":int(r[6] or 0),
                  "adi":int(r[7] or 0),"am":float(r[8] or 0),
                  "cp":float(r[9] or 0),"pt":float(r[10] or 0)})
print(f"  Ofensores PT: {len(of_pt)}")

# ── 7. OFENSORES CP ──────────────────────────────────────────────────────────
cur.execute(f"""
WITH base AS (
  SELECT linha, matricula,
    MODE() WITHIN GROUP(ORDER BY motorista) as nome,
    MODE() WITHIN GROUP(ORDER BY veiculo) as vei,
    COUNT(*) as v,
    COUNT(*) FILTER(WHERE iniciorealizado='') as perd,
    COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
    COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi,
    ROUND(AVG(({DIFF})::numeric) FILTER(WHERE ({DIFF})>0 AND ({DIFF})<60),1) as am
  FROM viagens_qh
  WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
    AND linha NOT IN ({EX})
  GROUP BY linha, matricula HAVING COUNT(*)>=3
)
SELECT linha, matricula, TRIM(nome), vei, v, perd, atd, adi, am,
  ROUND(100.0*(v-perd)/v,1) as cp,
  ROUND(100.0*(v-perd-atd-adi)/NULLIF(v-perd,0),1) as pt
FROM base
WHERE ROUND(100.0*(v-perd)/v,1) < {MCP}
ORDER BY cp ASC
""")
of_cp = []
for r in cur.fetchall():
    of_cp.append({"l":r[0],"m":r[1],"nome":(r[2] or ""),"vei":(r[3] or "—"),
                  "v":int(r[4] or 0),"perd":int(r[5] or 0),"atd":int(r[6] or 0),
                  "adi":int(r[7] or 0),"am":float(r[8] or 0),
                  "cp":float(r[9] or 0),"pt":float(r[10] or 0)})

# ── 8. TODAS AS VIAGENS PROBLEMÁTICAS (perdidas + atrasadas + adiantadas) ────
cur.execute(f"""
SELECT matricula,
       COALESCE(NULLIF(TRIM(motorista),''),'') as nome,
       linha,
       COALESCE(NULLIF(TRIM(veiculo),''),'—') as vei,
       COALESCE(NULLIF(TRIM(numeroviagem),''),'') as nr,
       COALESCE(NULLIF(TRIM(tabela),''),'') as tab,
       COALESCE(NULLIF(TRIM(sentido),''),'') as sent,
       TO_CHAR(inicioprogramado::timestamp,'HH24:MI') as hp,
       CASE WHEN iniciorealizado='' THEN ''
            ELSE TO_CHAR(iniciorealizado::timestamp,'HH24:MI') END as hr,
       ROUND(({DIFF})::numeric,1) as dif,
       COALESCE(NULLIF(TRIM(ponto_inicio),''),'') as local,
       CASE WHEN iniciorealizado='' THEN 'Perdida'
            WHEN {DIFF_ATD} THEN 'Atrasada'
            ELSE 'Adiantada' END as st
FROM viagens_qh
WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
  AND linha NOT IN ({EX})
  AND (iniciorealizado=''
       OR (iniciorealizado<>'' AND {DIFF_ATD})
       OR (iniciorealizado<>'' AND {DIFF_ADI}))
ORDER BY
  CASE WHEN iniciorealizado='' THEN 0 ELSE 1 END,
  ABS(ROUND(({DIFF})::numeric,1)) DESC NULLS LAST,
  linha, inicioprogramado
LIMIT 400
""")
viagens = []
for r in cur.fetchall():
    viagens.append({"m":r[0],"nome":(r[1] or ""),"l":r[2],"vei":r[3],"nr":r[4],
                    "tab":r[5],"sent":r[6],"hp":r[7],"hr":r[8],
                    "dif":float(r[9] or 0) if r[9] is not None else None,
                    "local":r[10],"st":r[11]})

# ── 9. VEÍCULO REINCIDENTE ────────────────────────────────────────────────────
cur.execute(f"""
SELECT COALESCE(NULLIF(TRIM(veiculo),''),'—') as vei,
  COUNT(*) FILTER(WHERE iniciorealizado='' OR (iniciorealizado<>'' AND
    ({DIFF_ATD} OR {DIFF_ADI}))) as n_crit,
  COUNT(*) as v_total,
  ARRAY_AGG(DISTINCT linha) as linhas,
  MODE() WITHIN GROUP(ORDER BY matricula) as mat,
  MODE() WITHIN GROUP(ORDER BY TRIM(motorista)) as nome
FROM viagens_qh
WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
  AND linha NOT IN ({EX}) AND veiculo IS NOT NULL AND TRIM(veiculo)<>''
GROUP BY vei
HAVING COUNT(*) FILTER(WHERE iniciorealizado='' OR (iniciorealizado<>'' AND
  ({DIFF_ATD} OR {DIFF_ADI}))) >= 3
ORDER BY n_crit DESC LIMIT 8
""")
vei_reinc = []
for r in cur.fetchall():
    linhas_vei = sorted(r[3]) if r[3] else []
    vei_reinc.append({"vei":r[0],"n_crit":int(r[1] or 0),"v":int(r[2] or 0),
                      "linhas":", ".join(linhas_vei),"mat":(r[4] or ""),"nome":(r[5] or "")})

# ── 10. RANKING DE MOTORISTAS ─────────────────────────────────────────────────
cur.execute(f"""
SELECT matricula,
  MODE() WITHIN GROUP(ORDER BY TRIM(motorista)) as nome,
  MODE() WITHIN GROUP(ORDER BY veiculo) as vei,
  COUNT(*) FILTER(WHERE iniciorealizado='') as perd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ATD}) as atd,
  COUNT(*) FILTER(WHERE iniciorealizado<>'' AND {DIFF_ADI}) as adi,
  COUNT(*) FILTER(WHERE iniciorealizado='' OR (iniciorealizado<>'' AND
    ({DIFF_ATD} OR {DIFF_ADI}))) as n_crit,
  COUNT(*) as v_total,
  ARRAY_AGG(DISTINCT linha) as linhas
FROM viagens_qh
WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
  AND linha NOT IN ({EX}) AND matricula IS NOT NULL
GROUP BY matricula
HAVING COUNT(*) FILTER(WHERE iniciorealizado='' OR (iniciorealizado<>'' AND
  ({DIFF_ATD} OR {DIFF_ADI}))) >= 3
ORDER BY n_crit DESC LIMIT 12
""")
mot_reinc = []
for r in cur.fetchall():
    linhas_m = sorted(r[8]) if r[8] else []
    v_t = int(r[7] or 0)
    perd_m = int(r[3] or 0)
    atd_m  = int(r[4] or 0)
    adi_m  = int(r[5] or 0)
    mot_reinc.append({
        "mat": r[0], "nome": (r[1] or ""), "vei": (r[2] or "—"),
        "perd": perd_m, "atd": atd_m, "adi": adi_m,
        "n_crit": int(r[6] or 0), "v": v_t,
        "linhas": ", ".join(linhas_m),
        "cp": round(100.0*(v_t-perd_m)/v_t, 1) if v_t else 0,
        "pt": round(100.0*(v_t-perd_m-atd_m-adi_m)/(v_t-perd_m), 1) if (v_t-perd_m) else 0,
    })

# ── 11. PRIMEIRA VIAGEM DO DIA POR LINHA ──────────────────────────────────────
cur.execute(f"""
WITH primeiras AS (
  SELECT DISTINCT ON (linha) linha,
    TO_CHAR(inicioprogramado::timestamp,'HH24:MI') as hp,
    CASE WHEN iniciorealizado='' THEN 'Perdida'
         WHEN {DIFF_ATD} THEN 'Atrasada'
         WHEN {DIFF_ADI} THEN 'Adiantada'
         ELSE 'OK' END as st,
    ROUND(({DIFF})::numeric,1) as dif
  FROM viagens_qh
  WHERE data='{ONTEM}' AND atividade='Viagem Normal' AND inicioprogramado<>''
    AND linha NOT IN ({EX})
  ORDER BY linha, inicioprogramado::timestamp ASC
)
SELECT linha, hp, st, dif FROM primeiras ORDER BY linha
""")
prim_vgs = [{"l":r[0],"hp":r[1],"st":r[2],"dif":float(r[3] or 0)} for r in cur.fetchall()]

conn.close()
print(f"  Vei. reincid.: {len(vei_reinc)} | Mot. reincid.: {len(mot_reinc)}")
print(f"  Viagens problemáticas: {len(viagens)}")

# ── CÁLCULO POR TERMINAL ──────────────────────────────────────────────────────
import math as _math
_line_to_term = {ln: t for t, lns in TERMINAIS_PY.items() for ln in lns}

por_terminal = []
for t_name, t_lines in TERMINAIS_PY.items():
    linhas_t = [l for l in por_linha if l["l"] in t_lines]  # para exibição
    # totais usando dados brutos (inclui linhas com <3 viagens no dia)
    raw_t = [_raw[ln] for ln in t_lines if ln in _raw]
    if not raw_t:
        continue
    t_tot  = sum(r["v"]    for r in raw_t)
    t_perd = sum(r["perd"] for r in raw_t)
    t_atd  = sum(r["atd"]  for r in raw_t)
    t_adi  = sum(r["adi"]  for r in raw_t)
    t_real = t_tot - t_perd
    t_cp   = round(100.0 * (t_tot - t_perd) / t_tot,  1) if t_tot  else 0.0
    t_pt   = round(100.0 * (t_real - t_atd - t_adi) / t_real, 1) if t_real else 0.0
    # margens (quantas partidas ainda pode perder/ter ofensivas e manter a meta)
    max_perd  = _math.floor(t_tot  * (1 - MCP / 100))
    max_ofens = _math.floor(t_real * (1 - MPT / 100))
    marg_cp   = max_perd  - t_perd
    marg_pt   = max_ofens - (t_atd + t_adi)
    # linhas críticas deste terminal
    lns_crit = sorted(
        [l for l in linhas_t if l["cp"] < MCP or l["pt"] < MPT],
        key=lambda x: x["pt"]
    )
    # ofensores PT deste terminal (top 8)
    of_t = [o for o in of_pt if o["l"] in t_lines][:8]
    # viagens problemáticas deste terminal (top 15: perdidas primeiro)
    vgs_t = [v for v in viagens if v["l"] in t_lines][:15]
    por_terminal.append({
        "nome":     t_name,
        "linhas":   t_lines,
        "total":    t_tot,
        "perd":     t_perd,
        "atd":      t_atd,
        "adi":      t_adi,
        "realiz":   t_real,
        "cp":       t_cp,
        "pt":       t_pt,
        "marg_cp":  marg_cp,
        "marg_pt":  marg_pt,
        "lns_crit": lns_crit,
        "of_t":     of_t,
        "vgs_t":    vgs_t,
        "tem_dados": t_tot > 0,
    })
print(f"  Terminais: {len(por_terminal)}")

# ── DATA READY GATE ───────────────────────────────────────────────────────────
_gate = []
if v < 800:
    _gate.append(f"CRITICO: Volume anormal — {v} viagens (esperado >800)")
if v > 0 and perd / v > 0.25:
    _gate.append(f"CRITICO: Taxa de perdas anormal — {round(100*perd/v,1)}%")
if not por_linha:
    _gate.append("CRITICO: Nenhuma linha encontrada nos dados")
GATE_OK = not any(g.startswith("CRITICO") for g in _gate)
for g in _gate:
    print(f"  [DATA GATE] {g}")
if GATE_OK:
    print(f"  [DATA GATE] OK — {v} viagens, {len(por_linha)} linhas, dados validados")
else:
    print(f"  [DATA GATE] FALHA — dados insuficientes")
    if ENVIAR:
        print("  [DATA GATE] ENVIO BLOQUEADO por falha critica nos dados.")
        sys.exit(2)

# ── MOTIVOS (SQLite local) ────────────────────────────────────────────────────
# chave: "linha|inicioprogramado"  ex: "07TR|14:50"
motivos_map = {}
if os.path.exists(DB_LOCAL):
    try:
        lcon = sqlite3.connect(DB_LOCAL)
        for row in lcon.execute(
            "SELECT linha, inicioprogramado, motivo, justificado, observacao "
            "FROM ocorrencias WHERE data=? ORDER BY id DESC",
            (ONTEM,)
        ):
            k = f"{row[0]}|{row[1]}"
            if k not in motivos_map:   # guarda o mais recente
                motivos_map[k] = {
                    "motivo": row[2],
                    "just": bool(row[3]),
                    "obs": row[4] or ""
                }
        lcon.close()
        if motivos_map:
            print(f"  Motivos cadastrados: {len(motivos_map)}")
    except Exception as e:
        print(f"  Aviso: não foi possível ler ocorrências locais ({e})")

# Injeta motivo nas viagens
for v in viagens:
    k = f"{v['l']}|{v['hp']}"
    if k in motivos_map:
        v["motivo"]  = motivos_map[k]["motivo"]
        v["just"]    = motivos_map[k]["just"]
        v["obs"]     = motivos_map[k]["obs"]

# ── EMPACOTA ──────────────────────────────────────────────────────────────────
D = {
    "dt": ONTEM, "fmt": DFmt, "sem": DSem,
    "res": resumo,
    "terminais_data": por_terminal,
    "mcp": MCP, "mpt": MPT, "tad": TAD, "tai": abs(TAI)
}
DJ = json.dumps(D, ensure_ascii=False, separators=(",",":")).replace("</script>","<\\/script>")

# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flash QH</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;background:#fff;color:#111827;font-size:13px;line-height:1.5;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{width:210mm;margin:0 auto;padding:12mm 14mm 10mm}
.page-break{page-break-after:always}
.hdr{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #1BBEAA;padding-bottom:8px;margin-bottom:14px}
.hdr-left{flex:1}
.page-title{font-size:22px;font-weight:800;color:#111827;letter-spacing:-.3px;line-height:1.1}
.page-title .title-date{color:#1BBEAA}
.page-subtitle{font-size:10px;color:#6B7280;margin-top:3px}
.page-source{font-size:9px;color:#9CA3AF;margin-top:1px}
.date-box{background:#1A3252;color:#fff;border-radius:6px;padding:7px 14px;text-align:center;min-width:90px;flex-shrink:0}
.date-box-l{font-size:8px;text-transform:uppercase;letter-spacing:.1em;color:#93C5FD;margin-bottom:2px}
.date-box-v{font-size:16px;font-weight:800;white-space:nowrap}
.kpi-row{display:flex;gap:10px;margin-bottom:12px}
.kpi{flex:1;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:10px 12px}
.kpi.accent{border-left-width:4px;border-left-style:solid}
.kpi.accent.ok{border-left-color:#18C46A}
.kpi.accent.warn{border-left-color:#F5A623}
.kpi.accent.crit{border-left-color:#E04B4B}
.kpi-l{font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:#6B7280;margin-bottom:2px}
.kpi-v{font-size:30px;font-weight:800;line-height:1}
.kpi-v.ok{color:#15803D}.kpi-v.warn{color:#D97706}.kpi-v.crit{color:#DC2626}.kpi-v.neu{color:#1E40AF}
.kpi-s{font-size:10px;color:#6B7280;margin-top:2px}
.sec{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#6B7280;margin:10px 0 5px;display:flex;align-items:center;gap:8px}
.sec::after{content:'';flex:1;height:1px;background:#E2E8F0}
table{width:100%;border-collapse:collapse;font-size:11px;margin-bottom:8px}
th{background:#1E3A5F;color:#fff;padding:5px 8px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.04em;font-weight:700}
td{padding:5px 8px;border-bottom:1px solid #F1F5F9;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:nth-child(even) td{background:#F8FAFC}
.bd{display:inline-block;padding:2px 7px;border-radius:10px;font-size:10px;font-weight:700}
.bd-ok{background:#DCFCE7;color:#15803D}
.bd-warn{background:#FEF3C7;color:#D97706}
.bd-crit{background:#FEE2E2;color:#DC2626}
.tcard{border:1px solid #E2E8F0;border-radius:10px;margin-bottom:14px;overflow:hidden;page-break-inside:avoid}
.tcard-hdr{background:#1A3252;color:#fff;padding:9px 14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.tcard-nome{font-size:15px;font-weight:800;flex:1}
.tkpi{text-align:center;background:rgba(255,255,255,.12);border-radius:6px;padding:4px 10px;min-width:68px}
.tkpi-l{font-size:8px;color:rgba(255,255,255,.55);text-transform:uppercase;letter-spacing:.05em}
.tkpi-v{font-size:20px;font-weight:800}
.tkpi-v.ok{color:#4ADE80}.tkpi-v.warn{color:#FCD34D}.tkpi-v.crit{color:#FCA5A5}
.tcard-body{padding:10px 14px}
.margens{display:flex;gap:8px;margin-bottom:10px}
.mbox{flex:1;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:6px;padding:7px 10px}
.mbox.danger{background:#FEF2F2;border-color:#FECACA}
.mbox-l{font-size:9px;text-transform:uppercase;letter-spacing:.05em;color:#6B7280;margin-bottom:2px}
.mbox-n{font-size:22px;font-weight:800;line-height:1}
.mbox-n.ok{color:#15803D}.mbox-n.warn{color:#D97706}.mbox-n.crit{color:#DC2626}
.mbox-s{font-size:10px;color:#6B7280;margin-top:2px}
.pt-item{border-radius:6px;padding:7px 10px;margin-bottom:5px;border-left:4px solid;font-size:11px;line-height:1.4}
.pt-item.perdida{background:#FFF1F2;border-color:#E04B4B}
.pt-item.atrasada{background:#FFFBEB;border-color:#F5A623}
.pt-item.adiantada{background:#EFF6FF;border-color:#60A5FA}
.pt-top{display:flex;align-items:center;gap:6px;margin-bottom:3px;flex-wrap:wrap}
.pt-tipo{font-weight:800;font-size:11px}
.pt-tipo.p{color:#DC2626}.pt-tipo.a{color:#D97706}.pt-tipo.d{color:#2563EB}
.pt-id{font-weight:700;font-size:12px}
.sep{color:#D1D5DB}
.pt-det{font-size:10px;color:#374151}
.pt-mot{font-size:10px;color:#6B7280;font-style:italic;margin-top:1px}
.ok-bar{background:#F0FDF4;border:1px solid #BBF7D0;border-radius:6px;padding:7px 12px;color:#15803D;font-size:11px;font-weight:600;margin-bottom:8px}
.ftr{text-align:center;font-size:9px;color:#9CA3AF;padding-top:8px;margin-top:12px;border-top:1px solid #F1F5F9}
@media print{
  *{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}
  body{font-size:10pt}
  .page{width:100%;padding:8mm 10mm}
  .page-break{page-break-after:always}
  .tcard{page-break-inside:avoid}
}
</style>
</head>
<body>
<div id="root"></div>
<script>
const D=""" + DJ + r""";
const MCP=D.mcp,MPT=D.mpt,TAD=D.tad,TAI=D.tai;
const r=D.res;

function fP(v){return (+(v||0)).toFixed(1).replace('.',',')+'\u00a0%';}
function fN(v){return (+(v||0)).toLocaleString('pt-BR');}
function fM(v){return v>0?'+'+v:String(v);}
function pcl(v,m){return v>=m?'ok':v>=m-3?'warn':'crit';}
function bcl(v,m){return v>=m?'bd-ok':v>=m-3?'bd-warn':'bd-crit';}
function abrev(n){if(!n)return '';var p=n.trim().split(' ');return p.length<=2?n:p[0]+' '+p[p.length-1];}
function sentStr(s){return s==='I'?'IDA':s==='V'?'VOLTA':s||'';}

function page1(){
  var cc=pcl(r.cp,MCP),pc=pcl(r.pt,MPT);
  var tbl=D.terminais_data.filter(function(t){return t.tem_dados;}).map(function(t){
    var mc=t.marg_cp>=0
      ?'pode perder at\u00e9 <strong>'+t.marg_cp+'<\/strong>'
      :'<strong style="color:#DC2626">excedeu '+(-t.marg_cp)+'<\/strong>';
    var mp=t.marg_pt>=0
      ?'pode ter at\u00e9 <strong>'+t.marg_pt+'<\/strong> ofensoras'
      :'<strong style="color:#DC2626">excedeu '+(-t.marg_pt)+'<\/strong>';
    return '<tr><td><strong>'+t.nome+'<\/strong><\/td>'
      +'<td><span class="bd '+bcl(t.cp,MCP)+'">'+fP(t.cp)+'<\/span><\/td>'
      +'<td><span class="bd '+bcl(t.pt,MPT)+'">'+fP(t.pt)+'<\/span><\/td>'
      +'<td>'+fN(t.total)+'<\/td>'
      +'<td style="'+(t.perd>0?'color:#DC2626;font-weight:700':'')+'">'+t.perd+'<\/td>'
      +'<td>'+t.atd+'<\/td>'
      +'<td style="font-size:10px">'+mc+'<\/td>'
      +'<td style="font-size:10px">'+mp+'<\/td><\/tr>';
  }).join('');
  return '<div class="page page-break">'
    +'<div class="hdr">'
    +'<div class="hdr-left">'
    +'<div class="page-title">FLASH OPERACIONAL DO DIA <span class="title-date">( '+D.fmt+' )<\/span><\/div>'
    +'<div class="page-subtitle">FLASH REPORT DIÁRIO · Cumprimento de Partida &amp; Pontualidade · '+D.fmt+' ('+D.sem+')<\/div>'
    +'<div class="page-source">Fonte: viagens_qh · tol. atraso &gt;'+TAD+' min · adiant. &gt;'+TAI+' min<\/div>'
    +'<\/div>'
    +'<div class="date-box"><div class="date-box-l">DATA<\/div><div class="date-box-v">'+D.fmt+'<\/div><\/div>'
    +'<\/div>'
    +'<div class="kpi-row">'
    +'<div class="kpi accent '+cc+'" style="flex:2"><div class="kpi-l">Cumprimento de Partida<\/div>'
    +'<div class="kpi-v '+cc+'">'+fP(r.cp)+'<\/div><div class="kpi-s">Meta '+MCP+'\u00a0%<\/div><\/div>'
    +'<div class="kpi accent '+pc+'" style="flex:2"><div class="kpi-l">Pontualidade<\/div>'
    +'<div class="kpi-v '+pc+'">'+fP(r.pt)+'<\/div><div class="kpi-s">Meta '+MPT+'\u00a0%<\/div><\/div>'
    +'<div class="kpi" style="flex:1"><div class="kpi-l">Previstas<\/div>'
    +'<div class="kpi-v neu">'+fN(r.v)+'<\/div><div class="kpi-s">Realizadas: '+fN(r.v-r.perd)+'<\/div><\/div>'
    +'<div class="kpi" style="flex:1"><div class="kpi-l">Perdidas<\/div>'
    +'<div class="kpi-v '+(r.perd>30?'crit':r.perd>10?'warn':'ok')+'">'+r.perd+'<\/div><div class="kpi-s">\u00a0<\/div><\/div>'
    +'<div class="kpi" style="flex:1"><div class="kpi-l">Atrasos \u003e'+TAD+'min<\/div>'
    +'<div class="kpi-v warn">'+r.atd+'<\/div><div class="kpi-s">Adiant.: '+r.adi+'<\/div><\/div>'
    +'<\/div>'
    +'<div class="sec">Resumo por Terminal<\/div>'
    +'<table><thead><tr><th>Terminal<\/th><th>CP\u00a0%<\/th><th>PT\u00a0%<\/th>'
    +'<th>Previstas<\/th><th>Perdidas<\/th><th>Atrasos<\/th>'
    +'<th>Margem CP<\/th><th>Margem PT<\/th><\/tr><\/thead><tbody>'+tbl+'<\/tbody><\/table>'
    +'<div class="ftr">QH Opera\u00e7\u00f5es \u00b7 Flash Di\u00e1rio \u00b7 '+D.sem+' '+D.fmt+'<\/div>'
    +'<\/div>';
}

function ptItem(v){
  var iP=v.st==='Perdida',iA=v.st==='Atrasada';
  var cls=iP?'perdida':iA?'atrasada':'adiantada';
  var tc=iP?'p':iA?'a':'d';
  var dif=v.dif!=null?Math.round(v.dif):null;
  var lbl=iP?'PERDIDA':iA?'ATRASO '+fM(dif)+' min':'ADIANT. '+fM(dif)+' min';
  var hr=!iP&&v.hr&&v.hr!=='\u2014'?' \u2192 '+v.hr:'';
  var mot=v.motivo?'<div class="pt-mot">Motivo: '+v.motivo+'<\/div>':'';
  return '<div class="pt-item '+cls+'">'
    +'<div class="pt-top">'
    +'<span class="pt-tipo '+tc+'">'+lbl+'<\/span>'
    +'<span class="sep">\u00b7<\/span><span class="pt-id">'+v.l+'<\/span>'
    +'<span class="sep">\u00b7<\/span><span>'+(v.tab||'\u2014')+'<\/span>'
    +'<span class="sep">\u00b7<\/span><span><strong>'+v.hp+'<\/strong>'+hr+'<\/span>'
    +'<span class="sep">\u00b7<\/span><span>'+sentStr(v.sent)+'<\/span>'
    +'<\/div>'
    +'<div class="pt-det">Operador: '+(abrev(v.nome)||v.m||'\u2014')+' \u00b7 Carro: '+(v.vei||'\u2014')+'<\/div>'
    +mot+'<\/div>';
}

function buildTerm(t){
  if(!t.tem_dados) return '';
  var cc=pcl(t.cp,MCP),pc=pcl(t.pt,MPT);
  var mcN=t.marg_cp,mpN=t.marg_pt;
  var mcCl=mcN>=5?'ok':mcN>=1?'warn':'crit';
  var mpCl=mpN>=5?'ok':mpN>=1?'warn':'crit';
  var mcTxt=mcN>=0
    ?'<div class="mbox-n '+mcCl+'">'+mcN+'<\/div><div class="mbox-s">partida'+(mcN!==1?'s':'')+' a perder<\/div>'
    :'<div class="mbox-n crit">\u2212'+(-mcN)+'<\/div><div class="mbox-s">acima do limite<\/div>';
  var mpTxt=mpN>=0
    ?'<div class="mbox-n '+mpCl+'">'+mpN+'<\/div><div class="mbox-s">ofensora'+(mpN!==1?'s':'')+' restante'+(mpN!==1?'s':'')+'<\/div>'
    :'<div class="mbox-n crit">\u2212'+(-mpN)+'<\/div><div class="mbox-s">acima do limite<\/div>';
  var lnRows=t.lns_crit.map(function(l){
    return '<tr><td><strong>'+l.l+'<\/strong><\/td>'
      +'<td><span class="bd '+bcl(l.cp,MCP)+'">'+fP(l.cp)+'<\/span><\/td>'
      +'<td><span class="bd '+bcl(l.pt,MPT)+'">'+fP(l.pt)+'<\/span><\/td>'
      +'<td>'+l.v+'<\/td>'
      +'<td style="'+(l.perd>0?'color:#DC2626;font-weight:700':'')+'">'+l.perd+'<\/td>'
      +'<td>'+l.atd+'<\/td><td>'+l.adi+'<\/td><\/tr>';
  }).join('');
  var ofRows=t.of_t.map(function(o){
    return '<tr><td><strong>'+(abrev(o.nome)||o.m)+'<\/strong><\/td>'
      +'<td style="font-size:9px;color:#6B7280">'+o.l+' \u00b7 '+o.vei+'<\/td>'
      +'<td><span class="bd '+bcl(o.cp,MCP)+'">'+fP(o.cp)+'<\/span><\/td>'
      +'<td><span class="bd '+bcl(o.pt,MPT)+'">'+fP(o.pt)+'<\/span><\/td>'
      +'<td>'+o.v+'<\/td>'
      +'<td style="'+(o.perd>0?'color:#DC2626;font-weight:700':'')+'">'+o.perd+'<\/td>'
      +'<td>'+o.atd+'<\/td><\/tr>';
  }).join('');
  var sem=!t.lns_crit.length&&!t.of_t.length&&!t.vgs_t.length;
  return '<div class="tcard">'
    +'<div class="tcard-hdr">'
    +'<div class="tcard-nome">'+t.nome+'<\/div>'
    +'<div class="tkpi"><div class="tkpi-l">CP<\/div><div class="tkpi-v '+cc+'">'+fP(t.cp)+'<\/div><\/div>'
    +'<div class="tkpi"><div class="tkpi-l">PT<\/div><div class="tkpi-v '+pc+'">'+fP(t.pt)+'<\/div><\/div>'
    +'<div style="font-size:10px;color:rgba(255,255,255,.55);margin-left:auto">'
    +fN(t.total)+' prev. \u00b7 '+t.perd+' perd. \u00b7 '+t.atd+' atr.<\/div>'
    +'<\/div>'
    +'<div class="tcard-body">'
    +'<div class="margens">'
    +'<div class="mbox'+(mcN<0?' danger':'')+'"><div class="mbox-l">Margem CP (meta '+MCP+'%)<\/div>'+mcTxt+'<\/div>'
    +'<div class="mbox'+(mpN<0?' danger':'')+'"><div class="mbox-l">Margem PT (meta '+MPT+'%)<\/div>'+mpTxt+'<\/div>'
    +'<\/div>'
    +(sem?'<div class="ok-bar">\u2705 Terminal dentro das metas \u2014 sem ocorr\u00eancias relevantes<\/div>':'')
    +(t.lns_crit.length
      ?'<div class="sec">Linhas Cr\u00edticas<\/div>'
      +'<table><thead><tr><th>Linha<\/th><th>CP\u00a0%<\/th><th>PT\u00a0%<\/th>'
      +'<th>Viagens<\/th><th>Perdidas<\/th><th>Atrasos<\/th><th>Adiant.<\/th><\/tr><\/thead>'
      +'<tbody>'+lnRows+'<\/tbody><\/table>':'')
    +(t.of_t.length
      ?'<div class="sec">Principais Ofensores<\/div>'
      +'<table><thead><tr><th>Operador<\/th><th>Linha \u00b7 Ve\u00edculo<\/th>'
      +'<th>CP\u00a0%<\/th><th>PT\u00a0%<\/th><th>Vgs<\/th><th>Perdidas<\/th><th>Atrasos<\/th><\/tr><\/thead>'
      +'<tbody>'+ofRows+'<\/tbody><\/table>':'')
    +(t.vgs_t.length?'<div class="sec">Partidas Cr\u00edticas<\/div>'+t.vgs_t.map(ptItem).join(''):'')
    +'<\/div><\/div>';
}

(function(){
  var p1=page1();
  var terms=D.terminais_data.map(buildTerm).join('');
  var p2='<div class="page">'+terms
    +'<div class="ftr">QH Opera\u00e7\u00f5es \u00b7 Flash Di\u00e1rio \u00b7 '+D.sem+' '+D.fmt+'<\/div><\/div>';
  document.getElementById('root').innerHTML=p1+p2;
})();
</script>
</body>
</html>"""


with open(OUT,"w",encoding="utf-8") as f:
    f.write(HTML)

sz = os.path.getsize(OUT)//1024
print(f"Flash gerado: {OUT} ({sz} KB)")
print(f"Dia: {ONTEM} ({DSem}) | CP={cp_dia}% | PT={pt_dia}% | {perd} perdidas")
print(f"Ofensores PT: {len(of_pt)} | Ofensores CP: {len(of_cp)}")
print(f"Vei. reincid.: {len(vei_reinc)} | Mot. reincid.: {len(mot_reinc)}")

if PREVIEW:
    print("\n[PREVIEW] HTML aberto no navegador. Envio de email e WhatsApp ignorados.")
    sys.exit(0)

# ── PDF via Edge headless ──────────────────────────────────────────────────────
PDF = OUT.replace(".html",".pdf")
_edge_candidates = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_edge_candidates += glob.glob(r"C:\Program Files*\Microsoft\Edge\Application\msedge.exe")
_browser = next((c for c in _edge_candidates if os.path.exists(c)), None)
if _browser:
    _url = "file:///" + OUT.replace("\\","/").replace(" ","%20")
    subprocess.run([_browser,"--headless=new","--disable-gpu","--no-sandbox",
                    "--disable-extensions",f"--print-to-pdf={PDF}",
                    "--print-to-pdf-no-header",_url],
                   timeout=30,capture_output=True)
    if os.path.exists(PDF):
        print(f"PDF gerado: {PDF} ({os.path.getsize(PDF)//1024} KB)")
    else:
        print("PDF: falhou — use o botão 'Salvar PDF' no HTML")
else:
    print("PDF: Edge não encontrado — use o botão 'Salvar PDF' no HTML")

# ── ENVIO DE EMAIL ────────────────────────────────────────────────────────────
import smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

def _env_val(key):
    for line in open(ENV):
        if line.startswith(key+"="):
            return line.split("=",1)[1].strip()
    return None

_efrom = _env_val("EMAIL_FROM")
_epass = _env_val("EMAIL_PASS")
_eto   = [e.strip() for e in (_env_val("EMAIL_TO") or "").split(",") if e.strip()]

if not ENVIAR:
    print("Email: ignorado (use --enviar para enviar)")
elif _efrom and _epass and _eto:
    try:
        _dia_fmt = datetime.strptime(ONTEM, "%Y-%m-%d").strftime("%d/%m/%Y")
        _sem = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"][datetime.strptime(ONTEM,"%Y-%m-%d").weekday()]
        _r   = resumo
        _sub = f"Flash QH – {_sem} {_dia_fmt}"
        _body = f"""Flash Diário QH — {_sem}, {_dia_fmt}

CP:  {_r['cp']}%   |   PT:  {_r['pt']}%
Viagens: {_r['v']}  |  Perdidas: {_r['perd']}  |  Atrasadas: {resumo.get('atd','—')}  |  Adiantadas: {resumo.get('adi','—')}

O relatório completo está em anexo (HTML — abra no navegador).
"""
        msg = MIMEMultipart()
        msg["From"]    = _efrom
        msg["To"]      = ", ".join(_eto)
        msg["Subject"] = _sub
        msg.attach(MIMEText(_body, "plain", "utf-8"))

        # anexa HTML
        with open(OUT, "rb") as f:
            _att = MIMEBase("application", "octet-stream")
            _att.set_payload(f.read())
            encoders.encode_base64(_att)
            _att.add_header("Content-Disposition", f'attachment; filename="flash_QH_{ONTEM}.html"')
            msg.attach(_att)

        # anexa PDF se existir
        if os.path.exists(PDF):
            with open(PDF, "rb") as f:
                _att2 = MIMEBase("application", "octet-stream")
                _att2.set_payload(f.read())
                encoders.encode_base64(_att2)
                _att2.add_header("Content-Disposition", f'attachment; filename="flash_QH_{ONTEM}.pdf"')
                msg.attach(_att2)

        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as srv:
            srv.login(_efrom, _epass)
            srv.sendmail(_efrom, _eto, msg.as_string())
        print(f"Email enviado para: {', '.join(_eto)}")
    except Exception as e:
        print(f"Email: falhou — {e}")
else:
    print("Email: EMAIL_FROM/EMAIL_PASS/EMAIL_TO não configurados no .env")

# ── ENVIO WHATSAPP (Fonnte) ───────────────────────────────────────────────────
import urllib.request, urllib.parse
try:
    import requests as _req_lib
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

_ftoken = _env_val("FONNTE_TOKEN")
_wto    = [n.strip() for n in (_env_val("WHATSAPP_TO") or "").split(",") if n.strip()]

if not ENVIAR:
    print("WhatsApp: ignorado (use --enviar para enviar)")
elif _ftoken and _wto:
    try:
        _dia_fmt2 = datetime.strptime(ONTEM, "%Y-%m-%d").strftime("%d/%m/%Y")
        _sem2 = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"][datetime.strptime(ONTEM,"%Y-%m-%d").weekday()]
        _r = resumo
        _cp_icon = "✅" if _r['cp'] >= MCP else "⚠️"
        _pt_icon = "✅" if _r['pt'] >= MPT else "⚠️"

        # Faz upload do PDF para gofile.io e obtém link
        _pdf_link = ""
        if _HAS_REQUESTS and os.path.exists(PDF):
            try:
                _srv = _req_lib.get("https://api.gofile.io/servers", timeout=10).json()
                _gserver = _srv["data"]["servers"][0]["name"]
                with open(PDF, "rb") as _pf:
                    _gres = _req_lib.post(
                        f"https://{_gserver}.gofile.io/contents/uploadfile",
                        files={"file": (f"flash_QH_{ONTEM}.pdf", _pf, "application/pdf")},
                        timeout=60
                    ).json()
                if _gres.get("status") == "ok":
                    _pdf_link = "\n\n📄 Relatório completo: " + _gres["data"]["downloadPage"]
            except Exception as _ue:
                print(f"Upload PDF: {_ue}")

        _msg = (
            f"📊 *Flash QH — {_sem2} {_dia_fmt2}*\n\n"
            f"CP: *{_r['cp']}%* {_cp_icon}  |  PT: *{_r['pt']}%* {_pt_icon}\n"
            f"Viagens: {_r['v']}  |  Perdidas: {_r['perd']}\n"
            f"Atrasadas: {_r['atd']}  |  Adiantadas: {_r['adi']}\n\n"
            f"Ofensores PT: {len(of_pt)} linhas\n"
            f"Meta CP: {MCP}%  |  Meta PT: {MPT}%"
            + _pdf_link
        )
        for _num in _wto:
            _data = urllib.parse.urlencode({
                "target": _num,
                "message": _msg,
                "countryCode": "55",
            }).encode()
            _wreq = urllib.request.Request(
                "https://api.fonnte.com/send",
                data=_data,
                headers={"Authorization": _ftoken},
                method="POST"
            )
            with urllib.request.urlopen(_wreq, timeout=15) as _resp:
                _res = _resp.read().decode()
            print(f"WhatsApp enviado para {_num}: {_res[:80]}")
    except Exception as e:
        print(f"WhatsApp: falhou — {e}")
else:
    print("WhatsApp: FONNTE_TOKEN/WHATSAPP_TO não configurados no .env")
