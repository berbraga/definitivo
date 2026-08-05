# Automação do market scan como Claude Code Routine

## Contexto

`run_market_scan.py` roda hoje manualmente: usuário abre terminal, executa
`uv run python run_market_scan.py`, acompanha a saída, confere o
`referencia-mercado_<data>.xlsx` gerado em `out/`.

Este documento cobre **só automatizar essa execução**, sem tocar na
arquitetura de busca. A arquitetura atual (o modelo decide quando usar
WebSearch, ~26 turnos por lote) permanece exatamente como está. Uma
refatoração para arquitetura determinística (Python faz o scraping, modelo só
julga) foi discutida e descartada para este escopo — não faz parte deste
documento.

## Objetivo

O scan roda sozinho, semanalmente, via Claude Code Routine, e entrega o
resultado sem que ninguém precise abrir terminal:
1. Gera o xlsx atualizado (fluxo já existente, inalterado)
2. Commita e envia o xlsx para a branch `feat/market-scan`
3. Envia o xlsx e um resumo da rodada para o canal Slack `pricing-trade-in`

## Fora de escopo

- Mudar como o modelo busca preços (WebSearch agêntico continua)
- Teto de gasto, kill switch, ou qualquer guardrail de aborto automático —
  decisão explícita do usuário para esta automação
- Abrir PR para revisão do commit automático — o commit vai direto para a
  branch ativa

## Arquitetura

Três peças novas adicionadas ao fim do `main()` existente, executadas nesta
ordem:

```
gera xlsx (fluxo atual, inalterado)
    ↓
git_commit_and_push()   — só prossegue se commit+push tiver sucesso
    ↓
notify_slack()           — só executa se o passo anterior teve sucesso
```

A ordem importa: notificar o Slack antes de garantir que o commit foi
persistido criaria uma mensagem afirmando que o resultado foi salvo quando
pode não ter sido.

### `git_commit_and_push()`

- `git add out/referencia-mercado_<data>.xlsx`
- `git commit -m "chore(scan): atualiza referência de mercado <data>"`
  (conventional commit, em português, seguindo o padrão já usado no repo)
- `git push origin feat/market-scan` — direto na branch ativa, sem branch
  nova por rodada e sem PR intermediário
- Falha em qualquer subetapa (conflito, sem permissão de push): a função
  propaga o erro, `main()` não chama `notify_slack()` a seguir, e o script
  sai com código de erro não-zero

### `notify_slack(xlsx_path, summary)`

- Usa a biblioteca `slack_sdk` (adicionar a `requirements.txt`)
- `client.files_upload_v2(channel="pricing-trade-in", file=xlsx_path,
  initial_comment=summary)`
- `summary` inclui: data da rodada, total de dispositivos processados, custo
  total USD da rodada (já calculado hoje por `summarize_costs`), contagem de
  dispositivos com erro ou sem resultado, se houver
- Autenticação via Bot Token (`SLACK_BOT_TOKEN`), escopos `files:write` e
  `chat:write`, bot já convidado no canal `pricing-trade-in`
- Falha de rede ou da API do Slack é logada mas não derruba a execução — o
  commit já foi persistido antes, é a parte que importa mais

### `docs/routine.md`

Novo documento, registra o que a configuração da Routine (feita fora deste
repositório, na interface do Claude Code) precisa saber:

- **Comando**: `uv run python run_market_scan.py`
- **Frequência**: semanal — alinhado ao ciclo de cache já existente, que
  renova todo sábado
- **Variáveis de ambiente exigidas**:
  - `SLACK_BOT_TOKEN` — token de bot Slack, secret
  - Credencial de push do GitHub (token de acesso pessoal ou GitHub App,
    a definir no momento da implementação) — necessária porque o ambiente
    da Routine clona o repo do zero, sem a chave SSH pessoal do usuário
- Nota de que o ambiente já roda `claude -p` autenticado internamente
  (a sessão da própria Routine), sem necessidade de credencial adicional
  para as chamadas de busca

## Credenciais

Duas credenciais reais já foram geradas pelo usuário durante o
brainstorming (webhook do Slack e bot token). Ambas tratadas como secrets:
nunca hardcoded, nunca commitadas, apenas como variável de ambiente
configurada na Routine. Recomendação registrada ao usuário: revogar e gerar
novas versões após a implementação estar validada, já que ambas trafegaram
em texto puro durante a conversa.

## Testes

- `git_commit_and_push`: testável mockando `subprocess.run`, cobrindo
  sucesso e falha (conflito/sem permissão) sem tocar em git real
- `notify_slack`: testável mockando o cliente `slack_sdk`, cobrindo sucesso
  e falha de API sem chamar a rede
- Nenhum teste deste conjunto toca rede ou credencial real, seguindo o
  padrão já estabelecido em `tests/conftest.py`

## Critérios de aceite

- Rodada completa gera xlsx, commita e dá push em `feat/market-scan`, e
  envia mensagem com anexo no canal Slack `pricing-trade-in`
- Falha no commit/push impede o envio ao Slack
- Falha no Slack não impede o commit já realizado
- `docs/routine.md` documenta comando, frequência e variáveis de ambiente
  o suficiente para configurar a Routine sem consultar o autor
- Nenhuma credencial aparece em código ou histórico de commit
