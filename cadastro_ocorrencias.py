"""
cadastro_ocorrencias.py — QH Operações
Registra motivos de partidas perdidas/atrasadas/adiantadas.
Os motivos aparecem automaticamente no Flash Diário.
Uso: python cadastro_ocorrencias.py [YYYY-MM-DD]
"""
import sqlite3, psycopg2, json, sys, os
from datetime import date, timedelta, datetime

DB_LOCAL = os.path.join(os.path.dirname(__file__), "ocorrencias_qh.db")
ENV      = os.path.join(os.path.dirname(__file__), ".env")
TAD, TAI = 8, -5
EX       = "'97TR','98TR','99TR'"
DIFF     = ("CASE WHEN iniciorealizado='' THEN NULL ELSE "
            "EXTRACT(EPOCH FROM (iniciorealizado::timestamp"
            " - inicioprogramado::timestamp))/60 END")

MOTIVOS = [
    "Trânsito",
    "Má-fé",
    "Falha do operador",
    "Falha mecânica",
    "Refeição",
    "Saída de garagem",
    "Articulação",
    "Baixa estatística",
    "Interdição de via",
    "Acidente",
    "Chuva / condição climática",
    "Outros",
]

SEP = "─" * 60

def init_db():
    con = sqlite3.connect(DB_LOCAL)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ocorrencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            linha TEXT NOT NULL,
            tabela TEXT,
            inicioprogramado TEXT NOT NULL,
            sentido TEXT,
            matricula TEXT,
            veiculo TEXT,
            tipo_ocorrencia TEXT NOT NULL,
            motivo TEXT NOT NULL,
            justificado INTEGER DEFAULT 0,
            observacao TEXT,
            registrado_em TEXT DEFAULT (datetime('now','localtime')),
            registrado_por TEXT DEFAULT 'sistema'
        )
    """)
    con.commit()
    return con

def buscar_viagens(data):
    raw = open(ENV, encoding="utf-8").read()
    DB  = raw.split("DATABASE_URL=")[1].split()[0]
    conn = psycopg2.connect(DB)
    cur  = conn.cursor()
    cur.execute(f"""
        SELECT matricula,
               COALESCE(NULLIF(TRIM(motorista),''),'') as nome,
               linha,
               COALESCE(NULLIF(TRIM(veiculo),''),'—') as vei,
               COALESCE(NULLIF(TRIM(tabela),''),'') as tab,
               COALESCE(NULLIF(TRIM(sentido),''),'') as sent,
               TO_CHAR(inicioprogramado::timestamp,'HH24:MI') as hp,
               CASE WHEN iniciorealizado='' THEN ''
                    ELSE TO_CHAR(iniciorealizado::timestamp,'HH24:MI') END as hr,
               ROUND(({DIFF})::numeric,1) as dif,
               CASE WHEN iniciorealizado='' THEN 'Perdida'
                    WHEN ROUND(({DIFF})::numeric,1)>{TAD} THEN 'Atrasada'
                    ELSE 'Adiantada' END as st,
               inicioprogramado
        FROM viagens_qh
        WHERE data='{data}' AND atividade='Viagem Normal' AND inicioprogramado<>''
          AND linha NOT IN ({EX})
          AND (iniciorealizado=''
               OR (iniciorealizado<>'' AND ROUND(({DIFF})::numeric,1)>{TAD})
               OR (iniciorealizado<>'' AND ROUND(({DIFF})::numeric,1)<{TAI}))
        ORDER BY
          CASE WHEN iniciorealizado='' THEN 0 ELSE 1 END,
          ABS(ROUND(({DIFF})::numeric,1)) DESC NULLS LAST,
          linha, inicioprogramado
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

def ja_tem_ocorrencia(con, data, linha, hp):
    r = con.execute(
        "SELECT id, motivo, justificado FROM ocorrencias WHERE data=? AND linha=? AND inicioprogramado=?",
        (data, linha, hp)
    ).fetchone()
    return r

def cls():
    os.system("cls" if os.name == "nt" else "clear")

def escolher(prompt, opcoes, permitir_vazio=False):
    while True:
        for i, o in enumerate(opcoes, 1):
            print(f"  {i:2}. {o}")
        try:
            resp = input(f"\n{prompt}: ").strip()
            if permitir_vazio and resp == "":
                return None
            n = int(resp)
            if 1 <= n <= len(opcoes):
                return n - 1
        except (ValueError, KeyboardInterrupt):
            pass
        print("  Opção inválida. Tente novamente.\n")

def main():
    DATA = sys.argv[1] if len(sys.argv) > 1 else str(date.today() - timedelta(days=1))
    try:
        datetime.strptime(DATA, "%Y-%m-%d")
    except ValueError:
        print("Data inválida. Use: python cadastro_ocorrencias.py YYYY-MM-DD")
        sys.exit(1)

    DFmt = f"{DATA[8:10]}/{DATA[5:7]}/{DATA[0:4]}"

    con = init_db()

    cls()
    print(f"\n{'='*60}")
    print(f"  QH OPERAÇÕES — CADASTRO DE OCORRÊNCIAS")
    print(f"  Data: {DFmt}")
    print(f"{'='*60}\n")
    print("  Buscando viagens problemáticas…")

    try:
        viagens = buscar_viagens(DATA)
    except Exception as e:
        print(f"\n  ERRO ao conectar ao banco: {e}")
        sys.exit(1)

    if not viagens:
        print(f"\n  Nenhuma viagem problemática encontrada em {DFmt}.")
        con.close()
        return

    print(f"  {len(viagens)} ocorrências encontradas.\n")

    while True:
        cls()
        print(f"\n{'='*60}")
        print(f"  QH — OCORRÊNCIAS DE {DFmt}")
        print(f"{'='*60}")
        print(f"\n  {'#':>3}  {'Linha':<6}  {'Tab':<5}  {'Hora':>5}  {'Status':<18}  {'Desvio':>8}  {'Operador'}")
        print(f"  {SEP}")

        for i, r in enumerate(viagens, 1):
            mat, nome, linha, vei, tab, sent, hp, hr, dif, st, _ = r
            nome_ab = (nome.strip().split()[0] + " " + nome.strip().split()[-1]) if nome and len(nome.split()) >= 2 else (nome or mat or "—")
            if st == "Perdida":
                dev = "PERDIDA"
                mark = "🔴"
            elif st == "Atrasada":
                dev = f"+{dif:.0f} min" if dif else "+? min"
                mark = "🟡"
            else:
                dev = f"{dif:.0f} min" if dif else "? min"
                mark = "🔵"

            existe = ja_tem_ocorrencia(con, DATA, linha, hp)
            reg = f"  ✓ {existe[1][:20]}" if existe else ""
            sent_s = "IDA" if sent in ("I","IDA") else "VOLTA" if sent in ("V","VOLTA") else sent or "?"
            print(f"  {i:>3}. {mark} {linha:<6}  {tab:<5}  {hp:>5}  {st:<18}  {dev:>8}  {nome_ab[:20]}{reg}")

        print(f"\n  {SEP}")
        print(f"  Digite o número da ocorrência para registrar o motivo.")
        print(f"  [Enter] para sair.")

        try:
            resp = input("\n  Escolha: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if resp == "":
            break

        try:
            idx = int(resp) - 1
            if not (0 <= idx < len(viagens)):
                raise ValueError
        except ValueError:
            continue

        mat, nome, linha, vei, tab, sent, hp, hr, dif, st, ip = viagens[idx]
        nome_s = nome.strip() if nome else mat or "—"
        sent_s = "IDA" if sent in ("I","IDA") else "VOLTA" if sent in ("V","VOLTA") else sent or "?"

        # Verifica ocorrência existente
        existe = ja_tem_ocorrencia(con, DATA, linha, hp)

        cls()
        print(f"\n{'='*60}")
        if st == "Perdida":
            print(f"  🔴 PARTIDA PERDIDA")
        elif st == "Atrasada":
            print(f"  🟡 ATRASO +{dif:.0f} min")
        else:
            print(f"  🔵 ADIANTAMENTO {dif:.0f} min")
        print(f"{'='*60}")
        print(f"  Linha:     {linha}  |  Tabela: {tab or '—'}  |  Sentido: {sent_s}")
        print(f"  Prog.:     {hp}  |  Real.: {hr or '—'}")
        print(f"  Veículo:   {vei}")
        print(f"  Operador:  {nome_s} ({mat or '—'})")
        if existe:
            jus = "Sim" if existe[2] else "Não"
            print(f"\n  ⚠  Já possui registro: {existe[1]} | Justificado: {jus}")
            print(f"     Registrar novamente irá substituir.")
        print(f"\n{SEP}")
        print(f"  MOTIVO DA OCORRÊNCIA\n")

        im = escolher("Escolha o motivo (número)", MOTIVOS)
        motivo = MOTIVOS[im]

        print(f"\n{SEP}")
        print(f"  JUSTIFICADO?\n")
        ij = escolher("A ocorrência foi justificada?", ["Não", "Sim"])
        justificado = ij == 1

        print(f"\n{SEP}")
        obs = input("  Observação (Enter para pular): ").strip()

        # Salva
        if existe:
            con.execute("""
                UPDATE ocorrencias SET motivo=?, justificado=?, observacao=?,
                  registrado_em=datetime('now','localtime')
                WHERE id=?
            """, (motivo, int(justificado), obs or None, existe[0]))
        else:
            con.execute("""
                INSERT INTO ocorrencias
                  (data, linha, tabela, inicioprogramado, sentido, matricula, veiculo,
                   tipo_ocorrencia, motivo, justificado, observacao)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (DATA, linha, tab or None, hp, sent or None,
                  mat or None, vei or None, st, motivo, int(justificado), obs or None))
        con.commit()

        print(f"\n  ✓ Registrado: {linha} | {hp} | {motivo}")
        print(f"    Justificado: {'Sim' if justificado else 'Não'}")
        input("\n  [Enter] para continuar…")

    total = con.execute(f"SELECT COUNT(*) FROM ocorrencias WHERE data=?", (DATA,)).fetchone()[0]
    con.close()
    cls()
    print(f"\n  QH — Cadastro encerrado.")
    print(f"  {total} ocorrência(s) registrada(s) para {DFmt}.")
    print(f"  O Flash Diário irá exibir os motivos automaticamente.\n")

if __name__ == "__main__":
    main()
