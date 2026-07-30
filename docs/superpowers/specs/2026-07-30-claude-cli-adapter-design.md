# Design: ClaudeCliAdapter — sessão do CLI em vez de API key

Data: 2026-07-30

## Contexto

O projeto coleta referência de mercado de seminovos chamando a Messages API
(`AnthropicSearchAdapter`, `web_search_20260209`) faturada por token e por
busca. Isso sai. A coleta passa a rodar via **Claude Code CLI em modo
headless** (`claude -p`), autenticado pela sessão do navegador (assinatura
Pro/Max/Team/Enterprise) ou por `CLAUDE_CODE_OAUTH_TOKEN` em servidor sem
navegador — sem nenhuma API key no fluxo.

Existe um script de referência, `run_market_scan.py`, que prova o contrato
do `claude -p` (prompt por stdin, envelope `--output-format json`,
`extract_json` tolerante a code fence, preflight de auth, Slack). Ele é
**standalone** e duplica leitura de planilha, estatística e escrita de
relatório que já existem no projeto, de forma mais simples e sem os
refinamentos já calibrados (Tasks 1–19: matcher com qualificadores/anos,
blacklist em duas camadas, evidência por citação, relatório de 4 abas,
template copy). Ele não entra no repo como está — serve só de prova de
contrato para o adapter novo.

## Decisão de integração

`ClaudeCliAdapter` implementa o `SearchAdapter` Protocol já existente
(`renov_market_scan/collect/base.py`):

```python
class SearchAdapter(Protocol):
    async def search(self, queries: list[Query]) -> SearchOutcome: ...
```

Isso preserva 100% do pipeline atual: `run.py`/`execute()`, cache sqlite
(`cache/store.py`), matcher/blacklist/evidence (`filtering/`), estatística
(`stats/aggregate.py`), relatório de 4 abas e template copy (`report/`).
Nada nesses módulos muda. Só a implementação por baixo do Protocol troca.

`AnthropicSearchAdapter`, `cost.py` (estimador de custo em US$/token) e a
dependência `anthropic` são removidos — não fazem sentido sem faturamento
por token.

`FixtureAdapter` continua sendo o único adapter usado pelos testes; nenhum
teste chama `claude` de verdade.

## `ClaudeCliAdapter`

### Construção e preflight

```python
class ClaudeCliAdapter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        preflight()  # levanta cedo, antes de qualquer search()
```

`preflight()` roda uma vez, na construção:

1. `shutil.which("claude") is None` → aborta: CLI não instalado.
2. `subprocess.run(["claude", "auth", "status"], env=child_env())` — sai 0
   logado (sessão de navegador **ou** `CLAUDE_CODE_OAUTH_TOKEN` presente),
   1 deslogado → aborta com mensagem explicando os dois caminhos:
   - máquina com navegador: `claude` + `/login`
   - servidor sem navegador: gerar token numa máquina com navegador via
     `claude setup-token`, exportar `CLAUDE_CODE_OAUTH_TOKEN`
3. Se `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` estiverem no ambiente,
   avisa em stderr que serão removidas dos subprocessos (em modo `-p`, se a
   key existir ela é usada e volta a cobrar por token sem avisar).

`child_env()` retorna `os.environ` copiado, com `ANTHROPIC_API_KEY` e
`ANTHROPIC_AUTH_TOKEN` explicitamente removidas — nunca confia em elas não
estarem no `.env`, remove sempre.

### `search(queries)` — um subprocess por par (model, source)

Mantém a granularidade atual do Protocol: uma chamada cobre todas as frases
de um par (model, source). Cada chamada é um `claude -p` isolado, restrito
a 1 domínio via prompt (`Considere apenas resultados do domínio {domain}`).

**Serial, não concorrente.** `settings.concurrency` é ignorado pelo adapter
(documentado no docstring da classe) — `claude -p` consome da janela de 5h
/semanal compartilhada do plano, não de um rate limit por token; rodar N
processos CLI em paralelo arrisca estourar essa janela mais rápido e
misturar sessões. `run.py`/`_collect` já é assíncrono/concorrente por
design (para `AnthropicSearchAdapter`); `ClaudeCliAdapter.search()`
simplesmente não usa `asyncio.Semaphore` nenhum — cada chamada aguarda a
anterior terminar via `asyncio.to_thread` + subprocess bloqueante,
sequencial por construção (sem semáforo compartilhado entre chamadas).

Prompt via **stdin** (`subprocess.run(cmd, input=prompt, ...)`), nunca em
`argv` — evita `ARG_MAX` em lotes grandes.

```python
cmd = [
    "claude", "-p",
    "--output-format", "json",
    "--allowedTools", "WebSearch,WebFetch",
    "--max-turns", str(max_turns),
    "--model", settings.model,
]
```

As flags exatas devem ser confirmadas contra `claude --help` na hora da
implementação — elas mudam entre versões do CLI; o brief da implementação
deve incluir "rode `claude --help` e confirme cada flag antes de fixar"
como passo obrigatório, não uma lista congelada.

Nada de `pexpect`/`pyautogui`/automação de TUI interativa — `-p` já é
"abrir o CLI e preencher o prompt" sem automação frágil.

### Prompt e extração — uma chamada, não duas

Diferente do `AnthropicSearchAdapter` (duas chamadas Messages API: busca
livre depois extração JSON separada, porque JSON puro suprime citations da
API), aqui **é uma chamada só**. `claude -p` via WebSearch/WebFetch não
expõe o mecanismo de citations criptografadas da Messages API para o
Python — não existe uma "etapa sem tool que preserva citations" porque
citations formais nunca existiram nesse caminho. O prompt já pede o JSON
final direto, incluindo um campo de evidência auto-relatado:

```
RESPONDA APENAS COM JSON, sem markdown, sem code fence, sem preâmbulo:
{"anuncios":[{"titulo","preco_brl","condicao","url","fonte","cited_text"}]}
```

`cited_text` aqui é **auto-relato do modelo** (ele mesmo copia o trecho que
diz ter visto), não uma citação verificável pela API. Trade-off aceito
explicitamente: `price_has_evidence()` (`filtering/evidence.py`) continua
funcionando sem mudança — só valida que o número em `R$` aparece no texto
— mas a garantia passa de "API garantiu que veio da página" para "modelo
alega que veio da página". Isso é documentado no docstring do adapter e no
README como limitação conhecida da migração.

Parse: `_strip_markdown_fence` (reaproveitado de `anthropic_search.py`) +
`json.loads` + `ExtractionPayload.model_validate` (reaproveitado). JSON
inválido → 1 retry com prompt de correção → `STATUS_PARSE_ERROR` se
persistir.

### Tratamento de erro do subprocess

- código de saída ≠ 0, timeout, ou `envelope["is_error"]` → falha do par:
  listings vazios, status de erro, log da falha. `run.py`/`_collect` já
  isola falha por item — um par ruim não derruba a rodada.
- **Detecção de limite de plano: best-effort.** O adapter tenta reconhecer
  padrões conhecidos no stderr/stdout (`usage limit`, `rate limit`, `try
  again` — a confirmar rodando o CLI de verdade durante a implementação).
  Se reconhecer, trata como pausa longa. Se não reconhecer o padrão, cai no
  caminho genérico: log da falha, pausa maior, segue para o próximo par.
  Nunca trava esperando um formato de mensagem que pode não existir ou ter
  mudado de versão.

## Config

`Settings` (`config.py`) remove:
- `anthropic_api_key`
- `web_search_tool_version`
- `max_uses_per_call`
- a validação `_check_model_tool_pair` (só fazia sentido para a Messages API)

`.env.example` perde a entrada de API key. `SLACK_WEBHOOK_URL` é lida
direto do ambiente pelo módulo de notificação, não entra em `Settings`
(evita acoplar config de infra a config de domínio).

## Slack

Módulo novo `renov_market_scan/notify.py`:

```python
def notify_slack(text: str) -> None: ...
```

Webhook incoming via `SLACK_WEBHOOK_URL` (env, nunca no código), `urllib`
stdlib — sem dependência nova. Se a env var não estiver setada, loga aviso
e não falha a rodada.

Chamada em `cli.py`, em volta do `asyncio.run(_main())` existente:

```python
try:
    asyncio.run(_main())
except KeyboardInterrupt:
    notify_slack(":warning: Rodada interrompida manualmente.")
    raise
except Exception as exc:
    notify_slack(f":rotating_light: Rodada ABORTOU: `{exc}`")
    raise
else:
    notify_slack(resumo_de_sucesso)  # contagens + caminho do relatório
```

O checkpoint de retomada continua sendo o cache sqlite existente
(`cache/store.py`) — não precisa do `.json` por lote do script standalone,
porque `run.py` já retoma pelo cache via `--retomar`.

Notificação sai do Python, nunca delegada ao modelo — se a rodada morrer no
meio, o modelo não estará "vivo" para mandar a mensagem.

## Testes

- `FixtureAdapter` inalterado, continua sendo o adapter de todo teste do
  pipeline.
- Testes novos para `ClaudeCliAdapter` (mock de `subprocess.run`, nunca
  chama `claude` real):
  - `preflight()` aborta quando `claude auth status` retorna 1
  - `preflight()` aborta quando `shutil.which("claude")` é `None`
  - `child_env()` remove `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`
  - `extract_json`/parse sobrevive a resposta embrulhada em code fence
  - subprocess com código de saída ≠ 0 vira status de falha, sem exceção
- Testes de `notify_slack`: mock de `urllib.request.urlopen`; sem
  `SLACK_WEBHOOK_URL` não falha, só loga.
- `cost.py` e `tests/test_cost.py` são deletados.
- `pytest`, `ruff`, `mypy` verdes; nenhum teste toca rede ou `claude` real.

## Fora de escopo

- **Relatório/colunas**: `report/xlsx.py` + `template_copy.py` (Tasks
  18/19) já fazem cópia da planilha, colunas na linha 2 e aba de amostras
  auditável — superset do que a Fase 4 do brainstorm original pedia. Não
  muda neste refactor.
- **Agendamento**: só execução manual do comando CLI. Cron/disparo via
  Slack fica para um design futuro.
- **Concorrência real entre processos CLI**: decidido serial nesta versão;
  se a janela de plano permitir folga, pode ser revisitado depois com
  dados reais de consumo.

## Critérios de aceite

1. `grep -ri "ANTHROPIC_API_KEY" renov_market_scan/` só encontra o código
   que remove a variável do ambiente dos subprocessos.
2. `--dry-run` lista lotes/pares e não executa nenhum subprocess.
3. Rodada com `--limite` pequeno gera xlsx com as colunas novas
   preenchidas e aba de amostras, via `ClaudeCliAdapter`.
4. Matar o processo no meio manda Slack de interrupção; cache sqlite
   preserva o que já foi coletado.
5. Rodar de novo com `--retomar` não repete par já cacheado.
6. `pytest`, `ruff`, `mypy` verdes; nenhum teste toca a rede ou `claude`.

## Anti-padrões

1. Automatizar a TUI interativa em vez de usar `-p`.
2. Deixar `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` no ambiente do
   subprocesso.
3. Rodar múltiplos processos `claude -p` concorrentes.
4. Pedir mediana/mínimo/máximo ao modelo — estatística é Python
   (`stats/aggregate.py`, inalterado).
5. Escrever direto na planilha de entrada.
6. Delegar a notificação Slack ao modelo.
7. Passar prompt gigante por `argv`.
8. Fixar flags do CLI sem conferir `claude --help` na implementação.
9. Tratar `cited_text` do CLI como citação verificável — é auto-relato,
   documentar como tal.
