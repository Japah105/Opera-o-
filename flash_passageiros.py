"""
flash_passageiros.py — Flash de Demanda D-2

Verifica se os dados de passageiros de D-2 estão completos.
Se sim: gera HTML e envia via WhatsApp para WHATSAPP_TO_PAX.
Se não: encerra sem enviar (script é idempotente — controle via .enviados_pax.json).

Uso:
  python flash_passageiros.py               # verifica D-2 automático
  python flash_passageiros.py 2026-08-24    # data específica
  python flash_passageiros.py --preview     # gera HTML e abre, não envia
  python flash_passageiros.py --enviar      # envia mesmo em execução manual
"""
import os, sys, json, glob, subprocess, urllib.request, urllib.parse, webbrowser
from datetime import date, timedelta, datetime

sys.stdout.reconfigure(encoding='utf-8')

ENV   = os.path.join(os.path.dirname(__file__), '.env')
OUT   = os.path.join(os.path.dirname(__file__), 'saidas', 'flash_pax.html')
PDF   = OUT.replace('.html', '.pdf')
CTRL  = os.path.join(os.path.dirname(__file__), '.enviados_pax.json')

PREVIEW = "--preview" in sys.argv
ENVIAR  = "--enviar"  in sys.argv
ARGS    = [a for a in sys.argv[1:] if not a.startswith("--")]

# D-2 por padrão
DATA = ARGS[0] if ARGS else str(date.today() - timedelta(days=2))

os.makedirs(os.path.join(os.path.dirname(__file__), 'saidas'), exist_ok=True)

# Ler .env
def _env_val(key):
    for line in open(ENV, encoding='utf-8'):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return None

DB = open(ENV, encoding='utf-8').read().split("DATABASE_URL=")[1].split()[0]

# Controle de envio — evita reenvio da mesma data
def ja_enviado(d):
    if not os.path.exists(CTRL): return False
    try:
        ctrl = json.load(open(CTRL, encoding='utf-8'))
        return d in ctrl.get("enviados", [])
    except: return False

def marcar_enviado(d):
    ctrl = {"enviados": []}
    if os.path.exists(CTRL):
        try: ctrl = json.load(open(CTRL, encoding='utf-8'))
        except: pass
    if d not in ctrl["enviados"]:
        ctrl["enviados"].append(d)
    json.dump(ctrl, open(CTRL, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

# ── CONEXÃO ───────────────────────────────────────────────────────────────────
import psycopg2

print(f"Verificando passageiros D-2: {DATA}…")
conn = psycopg2.connect(DB)
cur  = conn.cursor()

def si(v):
    try: return int(float(str(v).replace(',','.'))) if v and str(v).strip() not in ('','0') else 0
    except: return 0

def sf(v):
    try: return float(str(v).replace(',','.')) if v and str(v).strip() else 0.0
    except: return 0.0

# Verificar disponibilidade: mínimo de 500 viagens com pax registrado
cur.execute(f"""
    SELECT COUNT(*) FROM viagens_qh
    WHERE data = '{DATA}'
    AND passageiros IS NOT NULL AND passageiros != '' AND passageiros != '0'
""")
n_com_pax = cur.fetchone()[0]
print(f"  Viagens com passageiros registrados: {n_com_pax}")

MINIMO_VIAGENS = 500
if n_com_pax < MINIMO_VIAGENS:
    print(f"  Dados incompletos — {n_com_pax} < {MINIMO_VIAGENS}. Encerrando sem enviar.")
    conn.close()
    sys.exit(0)

if ja_enviado(DATA) and not PREVIEW:
    print(f"  Flash de passageiros de {DATA} já enviado anteriormente. Encerrando.")
    conn.close()
    sys.exit(0)

print(f"  Dados disponíveis ({n_com_pax} viagens). Gerando flash…")

# ── EXTRAÇÃO ─────────────────────────────────────────────────────────────────
rows = []
offset = 0
while True:
    cur.execute(f"""
        SELECT linha, sentido, passageiros,
               pax_vale_transporte, pax_idoso, pax_avulso, pax_escolar,
               pax_outros_cartao, pax_dinheiro,
               pax_deficiente_com_acomp, pax_deficiente_sem_acomp,
               pax_deficiente_esp, pax_superacao, pax_enem,
               pax_estudante_teste, pax_mae_itaqua, km_rodado
        FROM viagens_qh
        WHERE data = '{DATA}'
        AND passageiros IS NOT NULL AND passageiros != '' AND passageiros != '0'
        LIMIT 1000 OFFSET {offset}
    """)
    batch = cur.fetchall()
    if not batch: break
    rows.extend(batch)
    offset += 1000

conn.close()

PAX_CATS = [
    ('pax_vale_transporte', 'Vale Transporte', '#2DC99A'),
    ('pax_idoso',           'Idoso',           '#4A9CC9'),
    ('pax_avulso',          'Avulso',          '#A78BFA'),
    ('pax_escolar',         'Escolar',         '#60C9F8'),
    ('pax_outros_cartao',   'Outros Cartão',   '#F5A623'),
    ('pax_dinheiro',        'Dinheiro',        '#F5A623'),
    ('pax_deficiente_com_acomp',  'Def. c/ Acomp.', '#E04B4B'),
    ('pax_deficiente_sem_acomp',  'Def. s/ Acomp.', '#E04B4B'),
    ('pax_deficiente_esp',        'Def. Especial',  '#E04B4B'),
    ('pax_superacao',       'Superação',       '#436485'),
    ('pax_enem',            'ENEM',            '#436485'),
    ('pax_estudante_teste', 'Est. Teste',      '#436485'),
    ('pax_mae_itaqua',      'Mãe Itaquá',      '#436485'),
]

total_pax = 0
total_km  = 0.0
cats_tot  = {k: 0 for k, _, _ in PAX_CATS}
por_linha = {}

for r in rows:
    ln  = r[0]
    pax = si(r[2])
    km  = sf(r[16])
    total_pax += pax
    total_km  += km
    if ln not in por_linha:
        por_linha[ln] = {'pax': 0, 'km': 0.0, 'v': 0, **{k: 0 for k, _, _ in PAX_CATS}}
    por_linha[ln]['pax'] += pax
    por_linha[ln]['km']  += km
    por_linha[ln]['v']   += 1
    for i, (k, _, _) in enumerate(PAX_CATS):
        v = si(r[3 + i])
        cats_tot[k] += v
        por_linha[ln][k] += v

ipk = total_pax / total_km if total_km > 0 else 0
linhas_sort = sorted(por_linha.items(), key=lambda x: -x[1]['pax'])
ipk_max = max((d['pax']/d['km'] for _, d in por_linha.items() if d['km'] > 0), default=1)

# Agrupa deficiente e zera cats sem dado
cats_display = []
def_total = cats_tot['pax_deficiente_com_acomp'] + cats_tot['pax_deficiente_sem_acomp'] + cats_tot['pax_deficiente_esp']
CORES = {'pax_vale_transporte':'#2DC99A','pax_dinheiro':'#F5A623','pax_idoso':'#4A9CC9',
         'pax_avulso':'#A78BFA','pax_escolar':'#60C9F8'}
for k, lbl, cor in PAX_CATS[:6]:  # VT, Idoso, Avulso, Escolar, OutrosCartão, Dinheiro
    if cats_tot[k] > 0:
        cats_display.append({'label': lbl, 'n': cats_tot[k], 'pct': round(100*cats_tot[k]/total_pax, 1), 'cor': CORES.get(k, cor)})
if def_total > 0:
    cats_display.append({'label': 'Deficiente', 'n': def_total, 'pct': round(100*def_total/total_pax, 1), 'cor': '#E04B4B'})

# Outros não zerados
outros_keys = ['pax_superacao','pax_enem','pax_estudante_teste','pax_mae_itaqua']
outros_n = sum(cats_tot[k] for k in outros_keys)
if outros_n > 0:
    cats_display.append({'label': 'Outros', 'n': outros_n, 'pct': round(100*outros_n/total_pax, 1), 'cor': '#436485'})

# Semana / data formatada
dt_obj = datetime.strptime(DATA, "%Y-%m-%d")
SEMANA = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"][dt_obj.weekday()]
DIA_FMT = dt_obj.strftime("%d/%m/%Y")

print(f"  Total: {total_pax} pax | {total_km:.0f} km | IPK {ipk:.2f}")

# ── HTML ─────────────────────────────────────────────────────────────────────
D_JS = json.dumps({
    'data': DATA, 'fmt': DIA_FMT, 'sem': SEMANA,
    'total': total_pax, 'km': round(total_km), 'ipk': round(ipk, 2),
    'cats': cats_display,
    'linhas': [{'l': ln, 'pax': d['pax'], 'km': round(d['km']),
                'ipk': round(d['pax']/d['km'], 2) if d['km'] > 0 else 0,
                'v': d['v']} for ln, d in linhas_sort if ln != '99'],
}, ensure_ascii=False)

HGer = datetime.now().strftime("%d/%m %H:%M")

HTML = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flash Passageiros — {DIA_FMT}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#fff;color:#111827;font-size:12px;line-height:1.45;
     -webkit-print-color-adjust:exact;print-color-adjust:exact}}
.page{{width:210mm;min-height:auto;margin:0 auto;padding:10mm 12mm 8mm}}
/* HEADER */
.hdr{{display:flex;justify-content:space-between;align-items:flex-start;
      border-bottom:3px solid #1BBEAA;padding-bottom:7px;margin-bottom:10px}}
.hdr-left{{flex:1}}
.htitle{{font-size:20px;font-weight:800;color:#111827;line-height:1.1}}
.htitle .acc{{color:#1BBEAA}}
.hsub{{font-size:9px;color:#6B7280;margin-top:2px}}
.date-box{{background:#1A3252;color:#fff;border-radius:6px;padding:6px 12px;text-align:center;min-width:80px}}
.date-box-l{{font-size:7px;text-transform:uppercase;letter-spacing:.1em;color:#93C5FD;margin-bottom:1px}}
.date-box-v{{font-size:14px;font-weight:800}}
/* KPIs */
.krow{{display:flex;gap:8px;margin-bottom:10px}}
.kpi{{flex:1;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:7px;padding:8px 10px;border-left:3px solid #1BBEAA}}
.kpi-l{{font-size:8px;text-transform:uppercase;letter-spacing:.06em;color:#6B7280;margin-bottom:2px}}
.kpi-v{{font-size:26px;font-weight:800;line-height:1;color:#1A3252}}
.kpi-v.acc{{color:#1BBEAA}}.kpi-v.ok{{color:#15803D}}.kpi-v.warn{{color:#D97706}}
.kpi-s{{font-size:9px;color:#9CA3AF;margin-top:2px}}
/* MIX */
.sec{{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#6B7280;
      margin:8px 0 5px;display:flex;align-items:center;gap:8px}}
.sec::after{{content:'';flex:1;height:1px;background:#E2E8F0}}
.mix-bar{{height:16px;border-radius:4px;overflow:hidden;display:flex;margin-bottom:6px}}
.mix-leg{{display:flex;flex-wrap:wrap;gap:4px 14px;margin-bottom:8px}}
.mix-li{{display:flex;align-items:center;gap:5px;font-size:9px;color:#374151}}
.mix-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.mix-n{{font-weight:700;color:#111827}}
/* TABELA */
table{{width:100%;border-collapse:collapse;font-size:10px}}
th{{background:#1E3A5F;color:#fff;padding:4px 7px;text-align:left;font-size:8px;
    text-transform:uppercase;letter-spacing:.04em;font-weight:700}}
th.r{{text-align:right}}
td{{padding:3px 7px;border-bottom:1px solid #F1F5F9;vertical-align:middle}}
td.r{{text-align:right;font-variant-numeric:tabular-nums}}
tr:last-child td{{border-bottom:none}}
tr:nth-child(even) td{{background:#F8FAFC}}
.ln{{font-weight:700;font-size:11px;color:#1A3252}}
.pn{{font-weight:800;font-size:13px;color:#1BBEAA}}
.pill{{display:inline-block;padding:1px 6px;border-radius:8px;font-size:9px;font-weight:700}}
.p-ok{{background:#DCFCE7;color:#15803D}}
.p-warn{{background:#FEF3C7;color:#D97706}}
.p-crit{{background:#FEE2E2;color:#DC2626}}
.p-neu{{background:#DBEAFE;color:#1D4ED8}}
.bar-wrap{{background:#E2E8F0;border-radius:2px;height:5px;width:55px;display:inline-block;vertical-align:middle}}
.bar-fill{{height:100%;border-radius:2px}}
/* FOOTER */
.ftr{{text-align:center;font-size:8px;color:#9CA3AF;padding-top:6px;
      margin-top:8px;border-top:1px solid #F1F5F9}}
@media print{{
  *{{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}}
  .page{{width:100%;padding:6mm 8mm}}
  @page{{size:A4 portrait;margin:0}}
}}
@media(max-width:600px){{
  .page{{width:100%;padding:4vw}}
  .krow{{flex-wrap:wrap}}
  .kpi{{flex:1 1 45%}}
}}
</style>
</head>
<body>
<div class="page">
  <div class="hdr">
    <div class="hdr-left">
      <div class="htitle">FLASH <span class="acc">PASSAGEIROS</span></div>
      <div class="hsub">Demanda Operacional · {SEMANA} {DIA_FMT} · Gerado {HGer}</div>
    </div>
    <div class="date-box">
      <div class="date-box-l">DATA</div>
      <div class="date-box-v">{DIA_FMT}</div>
    </div>
  </div>

  <div class="krow" id="krow"></div>

  <div class="sec">Mix de Tarifa</div>
  <div class="mix-bar" id="mixBar"></div>
  <div class="mix-leg" id="mixLeg"></div>

  <div class="sec">Demanda por Linha</div>
  <table id="lnTbl"></table>

  <div class="ftr">QH Operações · Flash Passageiros · {SEMANA} {DIA_FMT} · Fonte: viagens_qh</div>
</div>
<script>
const D = {D_JS};
function fN(n){{return Math.round(n).toLocaleString('pt-BR');}}
function fI(n){{return n.toFixed(2).replace('.',',');}}

const ipkCls = D.ipk>=2.5?'ok':D.ipk>=1.8?'':'warn';
document.getElementById('krow').innerHTML = `
  <div class="kpi"><div class="kpi-l">Passageiros Transportados</div>
    <div class="kpi-v acc">${{fN(D.total)}}</div>
    <div class="kpi-s">${{D.linhas.length}} linhas com bilhetagem</div></div>
  <div class="kpi"><div class="kpi-l">KM Rodados</div>
    <div class="kpi-v">${{fN(D.km)}}</div>
    <div class="kpi-s">quilômetros operados</div></div>
  <div class="kpi"><div class="kpi-l">IPK (Pax / KM)</div>
    <div class="kpi-v ${{ipkCls}}">${{fI(D.ipk)}}</div>
    <div class="kpi-s">índice de eficiência</div></div>`;

const bar = document.getElementById('mixBar');
const leg = document.getElementById('mixLeg');
D.cats.forEach(c=>{{
  const s=document.createElement('div');
  s.style.cssText=`width:${{c.pct}}%;height:100%;background:${{c.cor}};flex-shrink:0`;
  s.title=`${{c.label}}: ${{fN(c.n)}} (${{c.pct}}%)`;
  bar.appendChild(s);
  leg.innerHTML+=`<div class="mix-li">
    <span class="mix-dot" style="background:${{c.cor}}"></span>
    <span>${{c.label}}</span>
    <span class="mix-n">&nbsp;${{fN(c.n)}}</span>
    <span style="color:#9CA3AF">&nbsp;${{c.pct}}%</span></div>`;
}});

const paxMax=D.linhas[0]?.pax||1;
const ipkMax=Math.max(...D.linhas.map(l=>l.ipk));
document.getElementById('lnTbl').innerHTML=`<thead><tr>
  <th>Linha</th><th class="r">Pax</th><th>Demanda</th>
  <th class="r">IPK</th><th class="r">KM</th><th class="r">Viagens</th>
</tr></thead><tbody>${{D.linhas.map(l=>{{
  const pw=Math.round(l.pax/paxMax*100);
  const pk=l.ipk>=2.5?'p-ok':l.ipk<1.5?'p-crit':'p-neu';
  return `<tr>
    <td class="ln">${{l.l}}</td>
    <td class="r pn">${{fN(l.pax)}}</td>
    <td><span class="bar-wrap"><span class="bar-fill" style="width:${{pw}}%;background:#1BBEAA;display:block"></span></span></td>
    <td class="r"><span class="pill ${{pk}}">${{fI(l.ipk)}}</span></td>
    <td class="r" style="color:#6B7280">${{fN(l.km)}}</td>
    <td class="r" style="color:#6B7280">${{l.v}}</td>
  </tr>`;
}}).join('')}}</tbody>`;
</script>
</body>
</html>"""

with open(OUT, 'w', encoding='utf-8') as f:
    f.write(HTML)
print(f"HTML gerado: {OUT}")


if PREVIEW:
    print("\n[PREVIEW] HTML gerado. Envio ignorado.")
    sys.exit(0)


# ── PDF via Edge headless ──────────────────────────────────────────────────────
_edge_candidates = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_edge_candidates += glob.glob(r"C:\Program Files*\Microsoft\Edge\Application\msedge.exe")
_browser = next((c for c in _edge_candidates if os.path.exists(c)), None)
if _browser:
    _url = "file:///" + OUT.replace("\\", "/").replace(" ", "%20")
    subprocess.run([_browser, "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--disable-extensions", f"--print-to-pdf={PDF}",
                    "--print-to-pdf-no-header", _url],
                   timeout=30, capture_output=True)
    if os.path.exists(PDF):
        print(f"PDF gerado: {PDF} ({os.path.getsize(PDF)//1024} KB)")
    else:
        print("PDF: falhou — arquivo não gerado")
else:
    print("PDF: Edge não encontrado")

# ── ENVIO WHATSAPP ────────────────────────────────────────────────────────────
if not ENVIAR:
    print("WhatsApp: ignorado (use --enviar para enviar)")
    sys.exit(0)

_ftoken = _env_val("FONNTE_TOKEN")
_wto    = [n.strip() for n in (_env_val("WHATSAPP_TO_PAX") or "").split(",") if n.strip()]

if not (_ftoken and _wto):
    print("WhatsApp: FONNTE_TOKEN ou WHATSAPP_TO_PAX não configurado.")
    sys.exit(1)

# Upload PDF para gofile.io
_pdf_link = ""
try:
    import requests as _req
    _srv = _req.get("https://api.gofile.io/servers", timeout=10).json()
    _gserver = _srv["data"]["servers"][0]["name"]
    with open(PDF, "rb") as _pf:
        _gres = _req.post(
            f"https://{_gserver}.gofile.io/contents/uploadfile",
            files={"file": (f"flash_pax_QH_{DATA}.pdf", _pf, "application/pdf")},
            timeout=60
        ).json()
    if _gres.get("status") == "ok":
        _pdf_link = "\n\n📄 Relatório completo: " + _gres["data"]["downloadPage"]
        print(f"PDF enviado: {_gres['data']['downloadPage']}")
except Exception as _ue:
    print(f"Upload PDF: {_ue}")

_top5 = "\n".join(
    f"  • {ln}: {d['pax']:,} pax (IPK {d['pax']/d['km']:.2f})".replace(",",".")
    for ln, d in linhas_sort[:5] if d['km'] > 0
)
_msg = (
    f"📊 *Flash Passageiros QH — {SEMANA} {DIA_FMT}*\n\n"
    f"👥 Passageiros: *{total_pax:,}*\n".replace(",",".")
    + f"🛣️ KM Rodados: *{int(total_km):,}*\n".replace(",",".")
    + f"📈 IPK: *{ipk:.2f}*\n\n"
    + f"🏆 Top 5 linhas:\n{_top5}"
    + f"\n\n_Referência: {SEMANA} {DIA_FMT} (D-2)_"
    + _pdf_link
)

for _num in _wto:
    try:
        _data = urllib.parse.urlencode({
            "target": _num, "message": _msg, "countryCode": "55",
        }).encode()
        _req = urllib.request.Request(
            "https://api.fonnte.com/send",
            data=_data,
            headers={"Authorization": _ftoken},
            method="POST"
        )
        with urllib.request.urlopen(_req, timeout=15) as _resp:
            _res = _resp.read().decode()
        print(f"WhatsApp enviado para {_num}: {_res[:80]}")
    except Exception as e:
        print(f"WhatsApp falhou para {_num}: {e}")

marcar_enviado(DATA)
print(f"\nConcluído. Data {DATA} registrada como enviada.")
