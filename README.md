# agent-context-bench

**Quanto custa manter contexto — e quando vale delegar parte do trabalho a outro modelo?**

Este projeto compara histórico completo, cache, resumo e delegação usando as APIs da
OpenAI e da Anthropic. Mede custo, tempo e acerto na resposta final de um mesmo incidente,
com contabilização separada para o agente principal e o subagente.

No piloto de delegação, usar um subagente mais barato reduziu o custo por tarefa aprovada
em **73,3% na OpenAI** e **66,2% na Anthropic**, frente ao controle com cache. O tempo mediano
da sessão aumentou para **1,99×** e **2,38×**, respectivamente. Delegar ao mesmo modelo
ficou mais caro nos dois provedores.

**Escopo desses números:** três repetições por condição, um único cenário sintético,
chamadas reais às APIs. Todas as condições passaram em 3/3 sessões. É uma medição inicial
de repetibilidade, sem evidência de qualidade equivalente em tarefas variadas.

[Resultados](#resultados-do-piloto) · [Como funciona](#como-funciona) ·
[Executar](#executar) · [Método](#método-e-limites) · [Desenvolvimento](#desenvolvimento)

## Resultados do piloto

Custo calculado pelas tarifas configuradas. Tempo corresponde à mediana da sessão completa,
incluindo as chamadas de delegação. O controle `cache` é a referência de cada provedor.

| Provedor | Estratégia | USD por tarefa aprovada | Variação de custo | Tempo mediano | Aprovação |
| --- | --- | ---: | ---: | ---: | ---: |
| OpenAI | Cache | 0,08477 | — | 9,75 s | 3/3 |
| OpenAI | Subagente do mesmo modelo | 0,11160 | +31,6% | 19,59 s | 3/3 |
| OpenAI | Subagente mais barato | **0,02266** | **−73,3%** | 19,38 s | 3/3 |
| Anthropic | Cache | 0,07313 | — | 10,21 s | 3/3 |
| Anthropic | Subagente do mesmo modelo | 0,11039 | +51,0% | 32,69 s | 3/3 |
| Anthropic | Subagente mais barato | **0,02470** | **−66,2%** | 24,28 s | 3/3 |

Configuração dos modelos: GPT-5.6 Sol com GPT-6 Luna na OpenAI; Claude Sonnet 5.5 com
Claude Haiku 5.5 na Anthropic. A variante de mesmo modelo usa o modelo principal nos dois papéis.
Os nomes acima são os modelos solicitados nas configurações, não uma verificação de versões retornadas.

Os dois lotes somaram **168 chamadas**, 18 sessões e **US$ 1,28175** de custo calculado.
Uso completo e controle de cache válido foram reportados em todas as condições.

### O que esses números mostram

- **Um contexto menor no principal não significa a mesma redução no processamento total.**
  Com o subagente barato, os tokens de entrada acumulados do principal caíram 93,2% na OpenAI
  e 85,8% na Anthropic. Somando os dois papéis, as quedas foram 18,0% e 8,7%.
- **O modelo escolhido para a delegação importa.** O fluxo com o mesmo modelo custou mais
  que o controle. Com o modelo barato, parte do processamento passou a ter uma tarifa menor.
  A comparação também inclui mudanças nas notas e respostas geradas.
- **Há um custo de tempo.** A delegação deste experimento é sequencial: cada turno espera
  o subagente antes de chamar o principal. São 11 chamadas por sessão, contra 6 no controle.
- **Cache habilitado não garante cache utilizado.** Na OpenAI, o principal das variantes
  delegadas reportou zero leitura de cache; seus subagentes ficaram em aproximadamente 66,6%.
  As notas curtas podem reduzir as oportunidades de reaproveitamento.

Exemplo de cálculo, usando os custos totais das três repetições:

```text
OpenAI:    (1 − 0,0679741 / 0,2543148) × 100 = 73,3%
Anthropic: (1 − 0,07409805 / 0,2193878) × 100 = 66,2%
```

Os valores publicados estão em [benchmarks/delegation-validation.csv](benchmarks/delegation-validation.csv).
O arquivo é uma transcrição dos agregados de `context-bench report` dos lotes
`openai-delegation-validation-01` e `anthropic-delegation-validation-01`, fornecidos pelo autor.
Os arquivos brutos e manifests dessas execuções permanecem locais e não acompanham este snapshot.
Rodadas anteriores, com outro contrato de saída, não entram nesta tabela.

## Como funciona

O cenário simula a investigação de um incidente em processamento de pagamentos. Evidências
chegam em cinco turnos: uma hipótese inicial, sua correção, a causa identificada e duas
verificações em sandbox. O sexto turno pede uma resposta JSON. Logs irrelevantes aumentam
progressivamente o contexto.

A avaliação verifica cinco pontos: causa, ação, necessidade de aprovação humana,
hipótese descartada e referência à evidência causal. Usa regras determinísticas, sem um LLM juiz.
Os valores esperados vêm das evidências; o prompt de sistema descreve o formato, sem entregar o gabarito.

| Estratégia | O que muda | Chamadas por sessão |
| --- | --- | ---: |
| `full` | Histórico completo, controle sem cache | 6 |
| `cache` | Histórico completo com cache | 6 |
| `summary` | Uma chamada de resumo substitui o histórico após três turnos; cache ativo | 7 |
| `delegate_same_model` | Um subagente mantém as evidências brutas; principal recebe notas; mesmo modelo | 11 |
| `delegate_small_model` | Mesmo fluxo de delegação, com outro modelo no subagente | 11 |

O subagente é único, mantém seu próprio histórico e recebe apenas as evidências já liberadas.
A cada turno, produz notas de até 200 palavras — limite solicitado no prompt, sem truncamento
local. O principal recebe as notas como dados, mantém seu histórico e responde ao turno final.
Ambos usam a mesma política de cache. Nenhum agente executa ações ou acessa ferramentas reais.

### O formato da resposta também faz parte do experimento

Uma rodada de diagnóstico retornou `retry_storm` e `database_latency` acompanhados de
explicações. A informação estava presente, mas os campos reprovaram na comparação exata.
Outra resposta não era JSON válido; o diagnóstico disponível não permite determinar sua forma.

O contrato passou a exigir identificadores sem explicações e um objeto JSON sem Markdown.
A instrução foi aplicada a todos os provedores e estratégias, mantendo a avaliação estrita.
Os controles foram executados novamente. Por isso, os resultados anteriores e posteriores
à mudança devem ser analisados como lotes distintos.

## Executar

Requisitos: Python 3.13 ou 3.14 e `uv`. O núcleo usa SDKs oficiais e arquivos locais;
não precisa de servidor, banco de dados ou framework de agentes.

### Comece sem credenciais

```bash
uv sync --frozen
uv run context-bench demo --output results/demo-01
uv run context-bench report results/demo-01
```

O demo usa respostas fixas, tokens zerados e custos nulos. Serve para verificar a instalação
e o fluxo de arquivos; não produz medidas de desempenho ou qualidade de modelos.

### Execute com as APIs

Disponibilize `OPENAI_API_KEY` e/ou `ANTHROPIC_API_KEY` no ambiente ou em um `.env` local.
Revise os modelos disponíveis na sua conta e as tarifas em `configs/` antes de usar `--live`.
As tarifas dos exemplos são datadas; não representam uma consulta atual de preços.

Para uma repetição das três estratégias de delegação:

```bash
uv run --env-file .env context-bench run \
  --config configs/openai-delegation.toml \
  --output results/openai-delegation-01 --live

uv run --env-file .env context-bench run \
  --config configs/anthropic-delegation.toml \
  --output results/anthropic-delegation-01 --live
```

São 28 chamadas previstas por provedor. Para reproduzir o desenho do piloto, use
`configs/openai-delegation-validation.toml` e `configs/anthropic-delegation-validation.toml`:
três repetições, 84 chamadas por provedor. Use sempre uma pasta de saída nova.

Para comparar `full`, `cache` e `summary`, use `configs/openai.toml` ou
`configs/anthropic.toml`. Com três repetições, são 57 chamadas previstas por provedor.
Os comandos de atualização e reprodução estão em [COMANDOS.md](COMANDOS.md).

### Leia os resultados

```bash
uv run context-bench report results/openai-delegation-01
```

| Arquivo local | Conteúdo |
| --- | --- |
| `manifest.json` | Configuração, versões dos SDKs, hashes do cenário e da delegação, ordem e estado do lote |
| `calls.jsonl` | Uso, custo, modelo solicitado/retornado, papel, operação, bytes e latência por chamada |
| `sessions.jsonl` | Estado da sessão, aprovação e resultado de cada verificação |
| `summary.csv` | Agregados por estratégia, incluindo custo por tarefa aprovada |

Para investigar reprovações, adicione `--diagnostics` ao comando `run`. A opção registra
somente os cinco campos avaliados do JSON final, com presença e tipo. Textos têm limite de
256 caracteres e evidências de dez entradas; outros tipos inesperados não têm seu conteúdo salvo.
A opção é desligada por padrão e não altera as requisições nem a avaliação.

```bash
jq '{strategy, passed, checks, answer_diagnostics}' results/SEU_LOTE/sessions.jsonl
```

`Completed` indica que o lote terminou, não que todas as tarefas passaram. Falhas de API
interrompem o lote; os SDKs estão configurados com `max_retries=0`. Saída vazia ou truncada
reprova a sessão. Não há reparo automático de respostas nem retomada do lote interrompido.

## Método e limites

- A ordem das estratégias é embaralhada por repetição, com seed registrada. Os prefixos
  são próprios de cada sessão/papel e permanecem estáveis dentro de seus históricos.
- Cache é medido pelos contadores retornados. Os turnos ficam identificados no JSONL:
  o primeiro não é presumido frio, nem os seguintes presumidos hits.
- `known_input_tokens` soma entradas das requisições, incluindo leituras e escritas de cache.
  Não representa o pico da janela de contexto nem tokens únicos.
- `task_latency_*` mede somente chamadas de tarefa do principal. Para comparar o fluxo completo,
  use `session_elapsed_p50_ms`. O TTFT considera o primeiro delta de texto.
- O custo inclui as chamadas de ambos os papéis, inclusive tentativas malsucedidas com uso conhecido.
  Uso ou tarifa ausente produz custo desconhecido (`null`), não zero.
- O texto do cenário é comum, mas tokenização, respostas, tarifas e padrões de raciocínio diferem.
  Compare primeiro as estratégias dentro de cada provedor. Este piloto não é um ranking de modelos.
- Três repetições do mesmo caso não medem generalização. O p95 de poucas chamadas é descritivo;
  não há aqui uma estimativa robusta de cauda nem teste de significância.
- O resumo é feito pela aplicação; a delegação também. O projeto não reproduz compactação nativa,
  subagentes do Claude Code, advisor, Message Threads, MCP/A2A ou execução paralela.

<details>
<summary>Detalhes de cache, tarifas e contabilização</summary>

O adaptador OpenAI usa Responses com `prompt_cache_options.mode = "explicit"` e TTL de
30 minutos. `full` omite breakpoints; as demais estratégias mantêm os breakpoints das
instruções e das mensagens de usuário anteriores. É necessário um modelo/API compatível
com esse controle explícito e com o campo de uso `cache_write_tokens`. O adaptador falha
quando o uso obrigatório está ausente, sem converter ausência em zero ou trocar a política.

Na Anthropic, `full` omite `cache_control`; as demais estratégias enviam cache automático
com TTL de cinco minutos. Os TTLs são diferentes entre provedores.
`cache_control_valid` não garante um hit: também fica falso no agregado quando há chamadas
com erro sem validação registrada. No controle sem cache, leituras/escritas inesperadas invalidam o controle.

`[rates]` precifica o principal e o subagente de mesmo modelo. `[subagent.rates]` precifica
o modelo alternativo. São quatro tarifas em USD por milhão de tokens, mais a data `as_of`:

```text
custo = (entrada comum × tarifa de entrada
       + leitura de cache × tarifa de leitura
       + escrita de cache × tarifa de escrita
       + saída × tarifa de saída) / 1.000.000
```

A tarifa de escrita é o preço total, não uma sobretaxa. O cálculo usa apenas as tarifas
configuradas; não ajusta faixas de contexto, batch, descontos contratuais ou ferramentas.
Consulte nos TOMLs as faixas e datas associadas aos exemplos antes de reutilizá-los.

Na Anthropic, a entrada total soma entrada comum, leitura e criação de cache. Na OpenAI,
leituras e escritas já fazem parte de `input_tokens` e são subtraídas para obter entrada comum.
Tokens de raciocínio já faturados em saída não são adicionados novamente.

A latência inclui o recebimento do uso final; bytes medem o corpo serializado pelo SDK,
sem cabeçalhos/TLS. O tempo de sessão inclui telemetria e escrita local.

</details>

## Telemetria opcional

O projeto usa o núcleo do [a2a-otel-kit](https://github.com/brunovicco/a2a-otel-kit) para
spans OTLP. O nome do pacote não implica uso do protocolo A2A neste benchmark.

```bash
uv sync --frozen --extra telemetry
uv run --env-file .env context-bench run \
  --config configs/anthropic-delegation.toml \
  --output results/anthropic-traced-01 --live \
  --otlp-endpoint http://localhost:4318/v1/traces
```

Forneça seu próprio coletor. Os spans registram operação, papel (`component`), correlação,
duração e resultado. A contabilização de tokens fica no JSONL, vinculada pelo trace ID.
Prompts, notas intermediárias, headers, chaves e mensagens de exceção não são gravados.
`--diagnostics` permite apenas o registro limitado dos campos finais descrito acima.
Mantenha a mesma configuração de telemetria ao comparar tempos.

## Desenvolvimento

```bash
uv sync --frozen --all-extras --all-groups
uv run python scripts/quality_gate.py
uv build
```

O quality gate verifica lockfile, lint, formatação, arquitetura, tipos, testes, segurança
e dependências. Os testes usam provedores simulados para verificar isolamento, cronologia,
contabilização, falhas e diagnóstico. A qualidade dos modelos depende dos experimentos reais.

O projeto deriva do [codex-python-engineering-harness](https://github.com/brunovicco/codex-python-engineering-harness),
perfil `library`, versão 1.2.1. A origem está registrada em `.harness.json`.
Mantém layout `src`, SDKs oficiais e CLI pequena. Código sob [licença MIT](LICENSE).

Próxima extensão útil: acrescentar cenários com correções conflitantes e evidências essenciais
mais difíceis de resumir, para avaliar se a economia permanece quando preservar informação é mais exigente.
