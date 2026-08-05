# Rodando o Market Scan como Claude Code Routine

Este documento descreve o que configurar na interface da Routine (fora
deste repositório) para rodar `run_market_scan.py` de forma automática e
semanal, sem supervisão.

## Comando

```bash
uv run python run_market_scan.py --input Template-iPhone.xlsx
```

Ajuste `--input` para a planilha de dispositivos correta a cada execução,
se houver mais de uma fonte de entrada.

**Atenção**: como `*.xlsx`/`*.xls` são gitignored (dados internos de
precificação, ver `.gitignore`), `Template-iPhone.xlsx` **não** existirá no
clone fresco que a Routine faz do repositório — o arquivo de entrada precisa
ser provisionado por algum outro caminho (por exemplo: commitado em outro
local fora do padrão ignorado, ou disponibilizado via mecanismo de
upload/secrets da própria Routine). Isso ainda não foi resolvido; precisa
ser decidido no momento de configurar a Routine de fato.

## Frequência

Semanal. Recomendado rodar aos sábados: o cache por dispositivo já renova
toda semana no sábado (`_last_saturday`, em `run_market_scan.py`), então
rodar nesse dia garante que a rodada sempre trabalha com cache fresco.

## Variáveis de ambiente exigidas

| Variável | Propósito | Onde obter |
|---|---|---|
| `SLACK_BOT_TOKEN` | Autentica o upload do xlsx e o post no canal `pricing-trade-in` via Slack Web API | Gerado em `api.slack.com/apps` → app do bot → **OAuth & Permissions** → Bot User OAuth Token (escopos `files:write`, `chat:write`) |
| Credencial de push do GitHub | Permite `git push` para `feat/market-scan` a partir do ambiente da Routine, que clona o repositório do zero e não tem a chave SSH pessoal do usuário | Token de acesso pessoal (PAT) com permissão de push neste repositório, ou GitHub App configurado para a Routine — a decidir no momento de configurar o ambiente |

Nenhuma das duas credenciais deve ser commitada ou hardcoded em nenhum
arquivo do repositório.

Confirmado contra a API real do Slack: `channel_id` em `notify_slack()`
(`run_market_scan.py`) exige o ID do canal, não o nome. O canal
`pricing-trade-in` tem ID `C0BN18AS38T`, já configurado como valor padrão
da função. Se o canal for recriado ou trocado, atualize esse valor.

## Autenticação do Claude CLI dentro da Routine

O script chama `claude -p` internamente (busca via WebSearch, arquitetura
inalterada por esta automação). A própria sessão da Routine já roda
autenticada como Claude Code — não é necessária nenhuma credencial adicional
para essas chamadas internas.

## O que a rodada faz, nesta ordem

1. Lê a planilha de entrada e monta os lotes de dispositivos
2. Para cada lote, consulta o cache por dispositivo+semana; só chama
   `claude -p` para os dispositivos pendentes
3. Gera `out/referencia-mercado_<data>.xlsx`
4. Commita e dá push desse arquivo direto na branch `feat/market-scan`
   (sem PR intermediário)
5. Sobe o mesmo arquivo como anexo no canal Slack `pricing-trade-in`,
   com um resumo da rodada (dispositivos processados, custo, falhas)

Falha no passo 4 impede o passo 5 — a Routine não deve notificar sucesso
sem ter persistido o resultado. Falha no passo 5 é só logada; não desfaz
o commit já feito.

## Fora de escopo desta automação

Não há teto de gasto automático, kill switch, nem revisão via PR — decisão
explícita para manter a arquitetura de busca e o fluxo de aprovação
inalterados. Ver `docs/superpowers/specs/2026-08-05-routine-automation-design.md`
para o racional completo.
