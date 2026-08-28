# Ferramenta QH — Projeto Daniel

## Contexto do projeto

Ferramenta de planejamento e ajuste de quadros de horário de ônibus urbano para o **Daniel** (Operações).

**Usuário**: Daniel — Operações de transporte urbano de passageiros.

## Arquivos principais

| Arquivo | Função |
|---|---|
| `gerar_quadro.py` | Motor de geração de quadro de horários |
| `flash_diario.py` | Relatório diário automático (WhatsApp via Fonnte) |
| `cadastro_ocorrencias.py` | Registro de ocorrências operacionais |
| `metas_qh.json` | Metas mensais de CP e PT |
| `ocorrencias_qh.db` | Banco SQLite de ocorrências |
| `.env` | Credenciais (nunca imprimir ou expor) |
| `saidas/` | Quadros Excel gerados (não commitar) |

## Skill obrigatória

Sempre carregar a skill `quadro-horario-onibus` para trabalhos relacionados ao quadro de horários da linha 08TR ou qualquer outra linha. Ela contém as regras de negócio, dados reais de demanda e histórico de decisões.

## Regras do projeto

### Arquivo de saída

O arquivo de saída é sempre `saidas/08TR/Quadro_08TR_AJUSTADO.xlsx` — **sobrescrever, nunca versionar** (o usuário pediu explicitamente para não gerar versões numeradas).

**Exceção**: se o Excel estiver aberto, salvar no scratchpad e avisar o usuário.

### Banco de dados

O banco `viagens_qh` é acessado via Supabase (credenciais no `.env`). Sempre verificar o row limit padrão do Supabase (1000 linhas) e usar paginação quando necessário.

### Segurança

O arquivo `.env` contém credenciais reais (Supabase, Fonnte, gofile.io). **Nunca imprimir, logar ou expor seu conteúdo.**

### Encoding Windows

Scripts Python devem usar `encoding='utf-8'` explicitamente em operações de arquivo. O Windows usa cp1252 por padrão, o que quebra acentos.

## Flash diário — fluxo inteligente

Ao trabalhar com o `flash_diario.py`, seguir esta ordem:

```
DADOS DO DIA
    ↓
PRÉ-PROCESSAMENTO determinístico (cálculos CP/PT, contagens)
    ↓
DETECÇÃO de anomalias e desvios
    ↓
PRIORIZAÇÃO (o que realmente importa hoje)
    ↓
RACIOCÍNIO somente nas exceções detectadas
    ↓
VALIDAÇÃO
    ↓
FLASH gerado → ENCERRAR
```

**Flash adaptativo**: tamanho proporcional ao dia.
- Dia normal → flash curto e direto.
- Há atenção → flash moderado com destaque.
- Situação crítica → flash detalhado com contexto.

Não usar raciocínio pesado para um dia normal. Não prolongar o flash além do que o dia justifica.

## Agentes disponíveis

- `data-analyst` — análise de dados operacionais, indicadores CP/PT, demanda
- `python-debugger` — debugging dos scripts do projeto
- `planner` — decomposição de tarefas complexas antes de implementar
- `researcher` — pesquisa técnica antes de instalar novas ferramentas
- `security-reviewer` — auditoria de segurança de código e componentes externos
