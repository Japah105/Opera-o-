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
<title>Flash Passageiros QH — {SEMANA} {DIA_FMT}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700;800&family=Inter:wght@400;500;600;700&display=swap">
<style>
:root{{
  --bg:#071420;--s0:#0C1D2E;--s1:#122540;--s2:#1A3252;--s3:#213D64;
  --ok:#18C46A;--ok-d:#071A0E;--warn:#F5A623;--warn-d:#1C1000;
  --crit:#E04B4B;--crit-d:#1A0606;--neu:#4A9CC9;--neu-d:#071422;
  --t1:#E4EDF8;--t2:#7BA8CA;--t3:#436485;--bdr:#182E45;--acc:#7B61FF;
  --r:10px;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',sans-serif;background:var(--bg);color:var(--t1);font-size:14px;line-height:1.5}}
.hdr{{background:var(--s0);border-bottom:2px solid var(--acc);position:sticky;top:0;z-index:200}}
.hdr-inner{{display:flex;align-items:center;gap:8px;padding:0 16px;height:52px;flex-wrap:nowrap}}
.brand{{font-family:'Barlow Condensed',sans-serif;font-weight:800;font-size:24px;color:#fff;white-space:nowrap}}
.brand b{{color:var(--acc)}}
.flash-tag{{background:rgba(123,97,255,.15);color:var(--acc);font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}}
.hdr-dt{{margin-left:auto;font-size:11px;color:var(--t3);white-space:nowrap}}
.pdf-btn{{background:var(--acc);border:none;color:#fff;border-radius:6px;padding:6px 14px;font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap;font-family:'Inter',sans-serif}}
.pdf-btn:hover{{opacity:.85}}
.bc{{display:flex;align-items:center;padding:5px 16px;font-size:12px;border-bottom:1px solid var(--bdr);white-space:nowrap;overflow-x:auto;gap:0}}
.bc-s{{color:var(--t3);cursor:pointer;padding:2px 8px;border-radius:4px;transition:.1s}}
.bc-s:hover{{color:var(--t1);background:rgba(255,255,255,.08)}}
.bc-s.cur{{color:var(--t1);font-weight:600;cursor:default}}
.bc-sep{{color:var(--t3);font-size:11px;padding:0 1px}}
main{{padding:16px;max-width:1100px;margin:0 auto}}
.krow{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px}}
.kpi{{background:var(--s1);border:1px solid var(--bdr);border-radius:var(--r);padding:14px 16px;flex:1 1 120px;min-width:100px}}
.kpi.hl{{border-left:3px solid var(--acc)}}
.kpi-l{{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--t3);margin-bottom:4px}}
.kpi-v{{font-family:'Barlow Condensed',sans-serif;font-size:34px;font-weight:800;line-height:1}}
.kpi-s{{font-size:11px;color:var(--t3);margin-top:3px}}
.ok{{color:var(--ok)}}.warn{{color:var(--warn)}}.crit{{color:var(--crit)}}.neu{{color:var(--neu)}}
.sec{{font-family:'Barlow Condensed',sans-serif;font-size:17px;font-weight:700;color:var(--t2);
     margin:20px 0 12px;display:flex;align-items:center;gap:10px;letter-spacing:.02em}}
.sec::after{{content:'';flex:1;height:1px;background:var(--bdr)}}
.card{{background:var(--s1);border:1px solid var(--bdr);border-radius:var(--r);margin-bottom:12px;overflow:hidden}}
.card.acc{{border-left:3px solid var(--acc)}}.card.grn{{border-left:3px solid var(--ok)}}
.card.yel{{border-left:3px solid var(--warn)}}.card.red{{border-left:3px solid var(--crit)}}
.card-hd{{padding:10px 16px;border-bottom:1px solid var(--bdr);font-size:12px;font-weight:600;
  color:var(--t2);display:flex;align-items:center;justify-content:space-between;gap:8px}}
.card-hd-r{{font-size:10px;color:var(--t3);font-weight:400}}
.card-bd{{padding:14px 16px}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:720px){{.g2{{grid-template-columns:1fr}}}}
.tw{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:var(--s2);color:var(--t3);padding:8px 11px;text-align:left;font-size:10px;
    text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}}
td{{padding:8px 11px;border-bottom:1px solid rgba(255,255,255,.04);vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:rgba(123,97,255,.06)}}
.nm{{font-weight:600}}.mt{{font-size:11px;color:var(--t3)}}
.pill{{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:600}}
.p-ok{{background:var(--ok-d);color:var(--ok)}}.p-warn{{background:var(--warn-d);color:var(--warn)}}
.p-crit{{background:var(--crit-d);color:var(--crit)}}.p-neu{{background:var(--neu-d);color:var(--neu)}}
.br{{display:inline-flex;align-items:center;gap:5px}}
.bg{{background:var(--s2);border-radius:3px;height:5px;width:60px;flex-shrink:0}}
.fg{{height:100%;border-radius:3px}}
.mix-stack{{display:flex;height:22px;border-radius:5px;overflow:hidden;margin-bottom:12px}}
.mix-leg{{display:flex;flex-wrap:wrap;gap:6px 16px;margin-bottom:4px}}
.mix-li{{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--t2)}}
.mix-dot{{width:9px;height:9px;border-radius:50%;flex-shrink:0}}
.mix-n{{color:var(--t1);font-weight:600;font-variant-numeric:tabular-nums}}
.mix-pct{{color:var(--t3);font-size:11px}}
.ftr{{text-align:center;font-size:11px;color:var(--t3);padding:16px 0 8px}}
@media(max-width:600px){{main{{padding:10px 8px}}th,td{{padding:6px 8px;font-size:12px}}}}
@media print{{
  *{{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}}
  .hdr{{position:static}}.pdf-btn,.bc{{display:none!important}}
  main{{padding:4px 8px}}body{{font-size:10px;line-height:1.3}}
  .krow{{gap:5px;margin-bottom:8px}}.kpi{{padding:7px 10px;flex:1 1 70px}}
  .kpi-v{{font-size:20px}}.kpi-l{{font-size:8px}}.kpi-s{{font-size:8px}}
  .sec{{font-size:12px;margin:8px 0 5px}}.card{{margin-bottom:6px}}
  th{{padding:4px 7px;font-size:9px}}td{{padding:4px 7px;font-size:10px}}
}}
</style>
</head>
<body>
<div class="hdr">
  <div class="hdr-inner">
    <div class="brand">QH<b>&middot;</b>Passageiros</div>
    <span class="flash-tag">D-2</span>
    <div class="hdr-dt">{SEMANA} &middot; {DIA_FMT} &middot; Gerado {HGer}</div>
    <button class="pdf-btn" onclick="window.print()">&#x1F4E5; Salvar PDF</button>
  </div>
  <div class="bc">
    <span class="bc-s cur">Resumo</span>
    <span class="bc-sep"> › </span>
    <span class="bc-s" onclick="document.getElementById('sec-linhas').scrollIntoView({{behavior:'smooth'}})">Linhas</span>
    <span class="bc-sep"> › </span>
    <span class="bc-s" onclick="document.getElementById('sec-mix').scrollIntoView({{behavior:'smooth'}})">Mix de Tarifa</span>
  </div>
</div>
<main id="main">
  <div class="krow" id="krow"></div>
  <div class="g2">
    <div class="card acc">
      <div class="card-hd">Mix de Tarifa<span class="card-hd-r" id="mix-total"></span></div>
      <div class="card-bd" id="sec-mix">
        <div class="mix-stack" id="mixBar"></div>
        <div class="mix-leg" id="mixLeg"></div>
      </div>
    </div>
    <div class="card acc">
      <div class="card-hd">Proporção Visual</div>
      <div class="card-bd" style="display:flex;align-items:center;gap:16px">
        <canvas id="donut" width="130" height="130" style="flex-shrink:0"></canvas>
        <div id="donutLeg" style="flex:1;font-size:12px"></div>
      </div>
    </div>
  </div>
  <div class="sec" id="sec-linhas">Demanda por Linha</div>
  <div class="card">
    <div class="card-hd">Ranking por Passageiros <span class="card-hd-r" id="tbl-sub"></span></div>
    <div class="tw"><table id="lnTbl"></table></div>
  </div>
  <div class="ftr">QH Operações · Flash Passageiros · {SEMANA} {DIA_FMT} · D-2</div>
</main>
<script>
const D = {D_JS};
function fN(n){{return Math.round(n).toLocaleString('pt-BR');}}
function fI(n){{return n.toFixed(2).replace('.',',');}}

// KPIs
const ipkCls = D.ipk>=2.5?'ok':D.ipk>=1.8?'warn':'crit';
document.getElementById('krow').innerHTML = `
  <div class="kpi hl">
    <div class="kpi-l">Passageiros Transportados</div>
    <div class="kpi-v" style="color:#7B61FF">${{fN(D.total)}}</div>
    <div class="kpi-s">${{D.linhas.length}} linhas com bilhetagem registrada</div>
  </div>
  <div class="kpi hl">
    <div class="kpi-l">KM Rodados</div>
    <div class="kpi-v warn">${{fN(D.km)}}</div>
    <div class="kpi-s">${{D.linhas.length}} linhas em operação</div>
  </div>
  <div class="kpi hl">
    <div class="kpi-l">IPK — Passageiros / KM</div>
    <div class="kpi-v ${{ipkCls}}">${{fI(D.ipk)}}</div>
    <div class="kpi-s">Índice de eficiência de demanda</div>
  </div>`;

// Mix bar + legenda
document.getElementById('mix-total').textContent = 'Total: ' + fN(D.total) + ' pax';
const bar = document.getElementById('mixBar');
const leg = document.getElementById('mixLeg');
D.cats.forEach(c => {{
  const s = document.createElement('div');
  s.style.cssText = `width:${{c.pct}}%;height:100%;background:${{c.cor}};flex-shrink:0`;
  s.title = `${{c.label}}: ${{fN(c.n)}} (${{c.pct}}%)`;
  bar.appendChild(s);
  leg.innerHTML += `<div class="mix-li">
    <span class="mix-dot" style="background:${{c.cor}}"></span>
    <span>${{c.label}}</span>
    <span class="mix-n">${{fN(c.n)}}</span>
    <span class="mix-pct">${{c.pct}}%</span>
  </div>`;
}});

// Donut canvas
const cv = document.getElementById('donut');
const cx2 = cv.getContext('2d');
const CX=65,CY=65,R=58,RI=36;
let ang=-Math.PI/2;
D.cats.forEach(c=>{{
  const a=(c.pct/100)*2*Math.PI;
  cx2.beginPath();cx2.moveTo(CX,CY);cx2.arc(CX,CY,R,ang,ang+a);cx2.closePath();
  cx2.fillStyle=c.cor;cx2.fill();ang+=a;
}});
cx2.beginPath();cx2.arc(CX,CY,RI,0,2*Math.PI);cx2.fillStyle='#122540';cx2.fill();
cx2.fillStyle='#7BA8CA';cx2.font='bold 13px Barlow Condensed,sans-serif';
cx2.textAlign='center';cx2.fillText(fN(D.total)+'pax',CX,CY+5);
document.getElementById('donutLeg').innerHTML = D.cats.map(c=>
  `<div style="display:flex;align-items:center;gap:7px;margin-bottom:6px">
    <span style="width:10px;height:10px;border-radius:2px;background:${{c.cor}};flex-shrink:0;display:inline-block"></span>
    <span style="color:var(--t2)">${{c.label}}</span>
    <span style="margin-left:auto;font-weight:700;color:var(--t1)">${{c.pct}}%</span>
  </div>`).join('');

// Tabela de linhas
const paxMax=D.linhas[0]?.pax||1;
const ipkMax=Math.max(...D.linhas.map(l=>l.ipk));
document.getElementById('tbl-sub').textContent=`IPK máx. ${{fI(ipkMax)}} · ${{D.linhas.length}} linhas · ordenado por passageiros`;
document.getElementById('lnTbl').innerHTML=`<thead><tr>
  <th>Linha</th><th style="text-align:right">Passageiros</th>
  <th style="width:110px">Demanda</th>
  <th style="text-align:right">IPK</th>
  <th style="width:90px">Eficiência</th>
  <th style="text-align:right">KM</th>
  <th style="text-align:right">Viagens</th>
</tr></thead><tbody>${{D.linhas.map(l=>{{
  const pw=Math.round(l.pax/paxMax*100);
  const iw=Math.round(l.ipk/ipkMax*100);
  const ipkPill=l.ipk>=2.5?'p-ok':l.ipk<1.5?'p-crit':'p-neu';
  return `<tr>
    <td class="nm">${{l.l}}</td>
    <td style="text-align:right;font-weight:700;font-family:'Barlow Condensed',sans-serif;font-size:16px;color:#7B61FF">${{fN(l.pax)}}</td>
    <td><div class="br"><div class="bg"><div class="fg" style="width:${{pw}}%;background:#7B61FF;opacity:.7"></div></div></div></td>
    <td style="text-align:right"><span class="pill ${{ipkPill}}">${{fI(l.ipk)}}</span></td>
    <td><div class="bg" style="width:90px"><div class="fg" style="width:${{iw}}%;background:var(--ok);opacity:.75"></div></div></td>
    <td style="text-align:right;color:var(--t3)">${{l.v}}</td>
  </tr>`;
}}).join('')}}</tbody>`;
</script>
</body>
</html>"""

with open(OUT, 'w', encoding='utf-8') as f:
    f.write(HTML)
print(f"HTML gerado: {OUT}")

webbrowser.open("file:///" + OUT.replace("\\", "/"))

if PREVIEW:
    print("\n[PREVIEW] Aberto no navegador. Envio ignorado.")
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
