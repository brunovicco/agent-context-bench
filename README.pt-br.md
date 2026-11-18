# agent-context-bench

[English](README.md) | **Português (Brasil)**

**Meça o fluxo completo do agente: custo, contexto e tempo.**

Uma CLI Python para experimentos controlados de cache, resumo, delegação e atualizações
de status com os SDKs oficiais da OpenAI e da Anthropic. Contabiliza cada chamada e avalia
respostas estruturadas de um incidente, usando arquivos locais JSONL/CSV e telemetria opcional.

**Achado mais recente:** no replay sintético, o modelo barato sozinho passou nos mesmos
checks configurados e foi mais barato e rápido que a delegação barata nos dois provedores.

[Resultados recentes](#resultados-recentes-o-controle-com-modelo-barato-sozinho) ·
[Início rápido](#início-rápido-offline) · [Como funciona](#como-funciona) ·
[Método](#método-e-limites) · [Evidências](#evidências-públicas-e-contribuição-técnica) ·
[Desenvolvimento](#desenvolvimento)

## Resultados recentes: o controle com modelo barato sozinho

![Custo calculado por papel e tempo mediano da sessão nos quatro controles de tarefa](benchmarks/controls-cost-comparison.pt-br.svg)

O controle adicional muda a interpretação: **o modelo barato sozinho custou 91,1% menos
que a delegação barata na OpenAI e 85,3% menos na Anthropic**, além de terminar mais rápido
que esse fluxo de delegação. Todas as condições passaram nos checks configurados em 3/3 sessões.

| Estratégia | OpenAI · USD/sessão | Tempo mediano | Anthropic · USD/sessão | Tempo mediano |
| --- | ---: | ---: | ---: | ---: |
| Principal + cache | 0,08455 | 8,69 s | 0,07502 | 8,11 s |
| **Barato sozinho + cache** | **0,00218** | **9,29 s** | **0,00419** | **8,11 s** |
| Delegação ao mesmo modelo | 0,11363 | 19,23 s | 0,10491 | 31,41 s |
| Delegação ao modelo barato | 0,02462 | 19,92 s | 0,02857 | 27,68 s |

**Medição de 18 de novembro de 2026:** 204 chamadas de API, 24 sessões, nenhum erro de API
e custo calculado total de US$ 1,31303. Os custos incluem os dois papéis. O tempo de sessão
inclui o fluxo sequencial completo. Houve leituras de cache e contabilidade completa nos dois lotes.
Fonte: [CSV completo](benchmarks/controls-validation.csv) ·
[evidências sanitizadas](benchmarks/controls-evidence).

O modelo barato sozinho economizou 97,4% e 94,4% frente ao principal com cache,
respectivamente. A delegação barata levou 2,14× e 3,41× o tempo do modelo barato sozinho.
Na OpenAI, o modelo barato sozinho foi um pouco mais lento que o principal com cache;
na Anthropic, ambas as medianas arredondam para 8,11 segundos. São medianas observadas,
sem estabelecer um ranking de velocidade.

**Interpretação:** este replay não demonstra benefício em combinar dois modelos frente
a usar o modelo barato sozinho. Três repetições de uma tarefa sintética de preservação de
evidências não estabelecem generalização ou qualidade equivalente em outras tarefas.
Passar nos checks configurados não significa que toda a resposta esteja correta.

<details>
<summary>Modelos, avaliação, cache e reprodução offline</summary>

| Provedor | Modelo principal | Modelo barato | Leitura de cache/entrada: principal sozinho / barato sozinho |
| --- | --- | --- | ---: |
| OpenAI | `gpt-5.6-sol` | `gpt-6-luna` | 75,03% / 75,03% |
| Anthropic | `claude-sonnet-5-5` | `claude-haiku-5-5` | 74,93% / 74,94% |

As strings de modelo solicitado e retornado coincidiram nas 204 chamadas. Esses aliases
não identificam versões imutáveis. As frações de cache contam tokens de entrada entre
requisições, incluindo entrada comum, leituras e escritas de cache. Descrevem estes fluxos
e condições, sem estabelecer uma propriedade geral dos provedores.

As quatro condições foram executadas novamente em blocos aleatorizados sob schema 4 e
o avaliador fortalecido `evidence-v2`: IDs em strings, distintos, com suporte causal permitido
e disponibilidade temporal. O replay original fornece a causa explicitamente e identifica
logs irrelevantes. Mede retenção de identificadores, correções e restrições de aprovação,
com pouca descoberta causal. Os controles de status, a agenda `final_only` e os cenários
adicionais ainda não foram medidos com modelos reais.

As tarifas configuradas mantêm a data de 6 de novembro; os mesmos preços foram conferidos
nas fontes oficiais em 18 de novembro, conforme
[provenance.json](benchmarks/controls-evidence/provenance.json). Os custos são calculados,
sem conciliação com faturas. Os lotes novos ficam separados dos anteriores e não são agrupados
entre revisões dos avaliadores.

```bash
uv run python scripts/reproduce_controls.py
# Opcional: manter o CSV reconstruído em um diretório novo
uv run python scripts/reproduce_controls.py --output /tmp/controls-tables-NEW
```

A reprodução verifica checksums, modelos, agenda completa, uso normalizado, custos pelas
tarifas configuradas e igualdade exata do CSV. Reutiliza julgamentos booleanos registrados;
não reavalia respostas descartadas. O pacote sanitizado não contém prompts, respostas ou IDs de telemetria.

</details>

## Início rápido (offline)

```bash
uv sync --frozen
uv run context-bench demo --output results/demo-NEW
uv run python scripts/reproduce_controls.py
uv run python scripts/reproduce_benchmarks.py
```

O demo usa respostas fixas e não mede desempenho. Os scripts verificam dois lotes
atuais de controles e oito anteriores, reconstruindo quatro CSVs sem credenciais nem chamadas de API.
Escolha um diretório novo para cada demo.

## Como funciona

O cenário original é um replay sintético de incidente de pagamentos para testar preservação de evidências. Evidências
chegam em cinco turnos: uma hipótese inicial, sua correção, a causa identificada e duas
verificações em sandbox. O sexto turno pede uma resposta JSON. Logs irrelevantes aumentam
progressivamente o contexto.

A avaliação verifica cinco pontos: causa, ação, necessidade de aprovação humana,
hipótese descartada e evidência causal. Novos lotes exigem IDs em strings, distintos,
causalmente relevantes e já disponíveis, incluindo todas as referências causais obrigatórias.
Os lotes anteriores, de schema 3, usavam o check: lista contendo E3. Seus vereditos históricos
são preservados, sem atualização retroativa. A avaliação usa regras determinísticas, sem LLM juiz.
Os valores esperados vêm das evidências; o prompt de sistema descreve o formato, sem entregar o gabarito.

| Estratégia | O que muda | Chamadas por sessão |
| --- | --- | ---: |
| `full` | Histórico completo, controle sem cache | 6 |
| `cache` | Histórico completo com cache | 6 |
| `cache_small_model` | Histórico completo e cache, com o modelo alternativo resolvendo sozinho | 6 |
| `summary` | Uma chamada de resumo substitui o histórico após três turnos; cache ativo | 7 |
| `delegate_same_model` | Um subagente mantém as evidências brutas; principal recebe notas; mesmo modelo | 11 |
| `delegate_small_model` | Mesmo fluxo de delegação, com outro modelo no subagente | 11 |

O subagente é único, mantém seu próprio histórico e recebe apenas as evidências já liberadas.
A cada turno, produz notas de até 200 palavras - limite solicitado no prompt, sem truncamento
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

## Experimentos anteriores de cache e delegação

<details>
<summary>Resultados originais, interpretação e proveniência</summary>

## Cache e resumo: experimento original

| Provedor | Estratégia | Total USD, 3 sessões | Economia frente a full | Mediana da sessão | Aprovação |
| --- | --- | ---: | ---: | ---: | ---: |
| openai | `cache` | 0.2576734 | 59.28% | 11.59 s | 3/3 |
| openai | `full` | 0.6328240 | 0.00% | 9.61 s | 3/3 |
| openai | `summary` | 0.2558794 | 59.57% | 15.38 s | 3/3 |
| anthropic | `cache` | 0.2168504 | 62.98% | 11.70 s | 3/3 |
| anthropic | `full` | 0.5857420 | 0.00% | 13.16 s | 3/3 |
| anthropic | `summary` | 0.2351411 | 59.86% | 17.19 s | 3/3 |

Fonte: [cache-validation.csv](benchmarks/cache-validation.csv), lotes `openai-validation-01`
e `anthropic-validation-01`, de 2026-11-06, 57 chamadas por provedor. Cache economizou
59,28% e 62,98% frente ao histórico sem cache. Resumo economizou apenas 0,70% frente ao
cache na OpenAI e custou 8,43% a mais na Anthropic. Três repetições não estabelecem vantagem
para a diferença pequena da OpenAI. O custo inclui gerar o resumo.

Esses lotes usam o hash de cenário
`883044d6839c213467631deda8879749bb35eedb36c743eacb63a2225a582c32`, anterior à revisão
do contrato JSON. Não são agrupados com os controles posteriores de delegação/status.
As tarifas são históricas, conforme os manifestos publicados.

## Resultados do piloto

Custo calculado pelas tarifas configuradas. Tempo corresponde à mediana da sessão completa,
incluindo as chamadas de delegação. O controle `cache` é a referência de cada provedor.

| Provedor | Estratégia | USD por tarefa aprovada | Variação de custo | Tempo mediano | Aprovação |
| --- | --- | ---: | ---: | ---: | ---: |
| OpenAI | Cache | 0,08477 | - | 9,75 s | 3/3 |
| OpenAI | Subagente do mesmo modelo | 0,11160 | +31,6% | 19,59 s | 3/3 |
| OpenAI | Subagente mais barato | **0,02266** | **−73,3%** | 19,38 s | 3/3 |
| Anthropic | Cache | 0,07313 | - | 10,21 s | 3/3 |
| Anthropic | Subagente do mesmo modelo | 0,11039 | +51,0% | 32,69 s | 3/3 |
| Anthropic | Subagente mais barato | **0,02470** | **−66,2%** | 24,28 s | 3/3 |

Configuração dos modelos: GPT-5.6 Sol com GPT-6 Luna na OpenAI; Claude Sonnet 5.5 com
Claude Haiku 5.5 na Anthropic. A variante de mesmo modelo usa o modelo principal nos dois papéis.
Os nomes acima são os modelos solicitados nas configurações, não uma verificação de versões retornadas.

Os dois lotes somaram **168 chamadas**, 18 sessões e **US$ 1,28175** de custo calculado.
Uso completo e controle de cache válido foram reportados em todas as condições.

### O que esses números mostram

- **Um contexto menor no principal não significa a mesma redução nos tokens de entrada acumulados.**
  Com o subagente barato, os tokens de entrada acumulados do principal caíram 93,2% na OpenAI
  e 85,8% na Anthropic. Somando os dois papéis, as quedas foram 18,0% e 8,7%.
- **O modelo escolhido para a delegação importa.** O fluxo com o mesmo modelo custou mais
  que o controle. A economia apareceu com o modelo barato; sua troca não foi isolada das mudanças em
  comprimento das saídas, notas, respostas ou comportamento observado de cache.
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
Os agregados são reproduzíveis a partir das evidências sanitizadas versionadas em
[benchmarks/evidence](benchmarks/evidence).
Rodadas anteriores, com outro contrato de saída, não entram nesta tabela.

</details>

## Experimento de atualização de status (v0.3.0)

![Custo médio por sessão: investigação, notas e status](benchmarks/status-cost-composition.svg)

Experimento histórico de status: três atualizações por snapshot de notas.
Nesse nível de reutilização, notas + renderização barata economizaram 21,2% do custo total
na OpenAI e 43,5% na Anthropic. A medição é anterior aos novos controles de status; compare
estratégias dentro de cada provedor. Com uma atualização na OpenAI, as notas superaram a economia de renderização.

<details>
<summary>Metodologia, resultados e reprodução de status</summary>

Este experimento compara o custo de produzir atualizações de status a partir do histórico
completo do agente principal e de notas curtas. O principal continua executando a mesma
investigação de seis turnos em todas as condições. Notas e status não entram no seu histórico.

| Estratégia | Entrada do status | Modelo do status | Chamadas por sessão, com uma atualização por ponto |
| --- | --- | --- | ---: |
| `status_full_history` | Todas as mensagens de usuário/assistente recebidas pelo principal até o momento | Modelo principal | 6 tarefa + 5 status = 11 |
| `status_notes_same_model` | Notas incrementais mais recentes | Modelo principal | 6 tarefa + 5 notas + 5 status = 16 |
| `status_notes_small_model` | Notas incrementais mais recentes | `[subagent].model` | 6 tarefa + 5 notas + 5 status = 16 |

O gerador de notas usa o modelo principal e suas tarifas nas duas condições com notas.
Ele recebe as notas anteriores e somente a troca mais recente da tarefa, e deve produzir
até 200 palavras. O prompt pede cerca de 150 palavras, permitindo notas menores, para deixar
margem até o limite obrigatório. As notas são geradas uma vez por ponto de observação e reutilizadas nas
atualizações repetidas daquele ponto. Seu custo de geração entra na conta. As saídas não são
truncadas para cumprir o limite. As duas condições usam os mesmos prompts e parâmetros;
as respostas dos modelos podem variar entre sessões.

A geração de status tem contexto isolado e um prompt comum às três condições. Retorna JSON
com o ID da evidência mais recente, causa confirmada (ou null), hipótese descartada (ou null),
exigência de aprovação humana e uma descrição de até 40 palavras. O prompt comum pede cerca
de 25 palavras; isso é uma orientação, não um novo limite de validação. A avaliação verifica os
fatos estruturados conforme o ponto atual e a presença/tamanho do texto. Ela não avalia toda
a qualidade semântica da descrição livre. Nenhum status é enviado como entrada à atualização
seguinte. Cache fica ativo na tarefa, nas notas e no status, com a política existente de cada
provedor e prefixos isolados por sessão/papel.

**Tempo:** as atualizações são sequenciais, após cada um dos cinco turnos de evidência,
antes da resposta final. `status_updates_per_turn` define quantas atualizações usam cada
snapshot (padrão 1, inteiro de 1 a 20). Não há agendador por relógio, espera de 30 segundos
ou execução concorrente. Aumentar esse parâmetro mede observações repetidas da mesma evidência;
o comportamento do cache e o tempo podem diferir de um agente real em segundo plano.
É um experimento da aplicação, sem reproduzir a implementação interna do Claude Code nem
medir limites de uso de assinatura.

A resposta final e todos os status previstos precisam passar, e as notas precisam respeitar
o limite de tamanho, para a sessão contar como `passed`. `task_passed` conta separadamente
as respostas finais corretas; `status_pass_rate` considera as atualizações previstas, inclusive
as ausentes após uma interrupção. Os checks de notas/status aparecem em `calls.jsonl`, sem
seus textos. Chamadas de notas também registram `notes_text_diagnostics` com `text_nonempty`,
`text_word_count` e `text_within_limit`, usando `split()` e o mesmo limite de 200 palavras.
Os metadados são registrados inclusive para respostas incompletas, sem salvar as notas.
Chamadas de status também registram `status_text_diagnostics` com
`text_present`, `text_is_string`, `text_nonempty`, `text_word_count` e `text_within_limit`.
Esses metadados são registrados para respostas retornadas, inclusive incompletas, sem salvar
o texto. A contagem usa `split()` e é null para campos ausentes ou de outro tipo. Textos vazios
têm zero palavras e atendem ao limite, mas reprovam no requisito de conteúdo não vazio.
JSON inválido ou resposta que não seja um objeto é tratado como campos ausentes. O limite
de 40 palavras e os critérios de aprovação permanecem iguais. Execuções antigas não podem
recuperar esses metadados retroativamente.
`--diagnostics` continua limitado à resposta final do principal. Erros de API
interrompem o lote; respostas auxiliares vazias ou incompletas interrompem a sessão atual,
preservando o uso conhecido.

As orientações de 150/25 palavras alteram `status_prompt_sha256` no manifesto. Preserve as
execuções anteriores, inclusive as falhas, e use novos diretórios de saída para esta revisão.
Compare as três condições com a mesma revisão dos prompts; não agrupe hashes diferentes
como um único experimento. Não há nova tentativa automática nem truncamento. As rodadas
completas abaixo usam os prompts revisados.

| Campo do relatório | Significado |
| --- | --- |
| `workload_cost_usd` | Chamadas da investigação principal |
| `notes_cost_usd` | Geração incremental das notas |
| `status_cost_usd` | Somente as chamadas que produzem status |
| `status_update_cost_mean_usd` | Custo médio por tentativa de atualização de status |
| `status_pipeline_cost_usd` | Notas mais geração de status |
| `total_cost_usd` / `session_cost_mean_usd` | Todas as chamadas, em total do lote / média por sessão iniciada |
| `status_overhead_fraction` | Fração do custo total gasta com notas e status |
| `status_update_savings_fraction` | Redução do custo de gerar status frente a `status_full_history` |
| `status_pipeline_savings_fraction` | Redução de notas + status frente a `status_full_history` |
| `session_savings_fraction` | Redução do custo total da sessão frente a `status_full_history` |
| `cost_per_passed_task_usd` | Custo de todas as chamadas tentadas dividido pelas sessões integralmente aprovadas (`passed`), não só pelas respostas finais corretas (`task_passed`); nome legado |

As economias são frações: `0.25` representa 25% menos; valores negativos indicam aumento.
Só são calculadas em lotes completos com todas as repetições previstas e quando as duas
condições têm a mesma quantidade de sessões/atualizações,
todas as sessões passam e os custos necessários à comparação são conhecidos, com referência
positiva. Caso contrário, ficam `null`. Uma atualização mais barata não implica uma sessão
mais barata. Lotes parciais não devem ser comparados como trabalhos completos, mesmo quando
seus custos parciais são conhecidos.

O manifesto registra `status_prompt_sha256`, a sequência e a quantidade de atualizações.
O schema 3 adiciona essas métricas e mantém a leitura dos relatórios JSONL anteriores.
Os resultados de delegação acima pertencem a outro experimento.

### Resultados reais de status auditados

O [CSV consolidado](benchmarks/status-validation.csv) contém 12 condições de quatro
rodadas completas. Cada rodada tem três repetições de cada estratégia: **9/9 sessões
aprovadas**, com **45/45 status em `k = 1`** ou **135/135 em `k = 3`**. Cada rodada gerou
30 notas e realizou 129 ou 219 chamadas, respectivamente. No conjunto: 696 chamadas,
36 sessões aprovadas, 360 status aprovados e 120 gerações de notas. `synthetic: false`
indica chamadas reais às APIs; o incidente continua sendo sintético.

As fontes são `manifest.json`, `calls.jsonl` e `sessions.jsonl` locais em:

| Provedor | Atualizações por turno | Diretório de origem em `results/` | Data (UTC) |
| --- | ---: | --- | --- |
| OpenAI | 1 | `openai-status-length-validation-01` | 2026-11-07 |
| Anthropic | 1 | `anthropic-status-length-validation-01` | 2026-11-07 |
| OpenAI | 3 | `openai-status-reuse3-03` | 2026-11-07 |
| Anthropic | 3 | `anthropic-status-reuse3-03` | 2026-11-16 |

As quatro usam o SHA-256 dos prompts de status
`ec1cbb883ff80e79b5883d0fb88ba8b47f50d8e4f4776252b776337075d2326b`
e o SHA-256 do cenário
`1fec88977685fae92acf8800484af594e3ddded0c1a74b21ecfd89fbd7561de5`.
O replay padrão e os prompts legados de notas em texto continuam correspondendo aos dois
hashes. Novos hashes dos avaliadores e manifestos de schema 4 distinguem os checks
fortalecidos dos vereditos publicados. As configurações correspondem aos manifestos:
seed 42, limite de 2.048 tokens de saída, timeout de 60 segundos por chamada, três repetições,
SDKs OpenAI 2.54.0 e Anthropic 0.125.0. As strings dos modelos solicitados e retornados
coincidem em todas as chamadas; esses aliases não estabelecem versões imutáveis dos modelos.

Tarifas configuradas em USD por milhão de tokens, datadas de **2026-11-06**, reproduzidas
como entradas históricas do experimento, sem constituir uma cotação atual:

| Provedor / papel | Modelo | Entrada comum | Leitura de cache | Escrita de cache (total) | Saída |
| --- | --- | ---: | ---: | ---: | ---: |
| OpenAI principal / notas | `gpt-5.6-sol` | 4 | 0.4 | 5 | 20 |
| OpenAI status barato | `gpt-6-luna` | 0.1 | 0.01 | 0.125 | 0.5 |
| Anthropic principal / notas | `claude-sonnet-5-5` | 2 | 0.1 | 2.5 | 10 |
| Anthropic status barato | `claude-haiku-5-5` | 0.1 | 0.01 | 0.125 | 0.5 |

A normalização do uso bruto e o custo de cada chamada foram conferidos com essas tarifas.
O uso está completo, todas as chamadas solicitaram cache e os controles são válidos.
A OpenAI usa breakpoints explícitos em Responses no conteúdo de sistema/developer e nas
mensagens de usuário, com TTL de 30 minutos; a Anthropic envia cache automático efêmero
com TTL de cinco minutos. A política é a mesma nas três estratégias de status, inclusive
nas chamadas de notas e da tarefa. Solicitar cache não comprova acerto. Nas **chamadas de
status** com histórico completo, as frações observadas de leitura/entrada foram 66,707118%
(OpenAI, `k = 1`), 0% (Anthropic, `k = 1`), 88,860982% (OpenAI, `k = 3`) e 66,648076%
(Anthropic, `k = 3`). O denominador inclui entrada comum, leituras e escritas de cache;
conta tokens entre chamadas, não contexto único. O CSV contém cache e tokens por condição.

**Uma atualização por turno de evidência (`k = 1`):**

| Provedor | Estratégia | Total USD, 3 sessões | Média USD/sessão | Mediana da sessão |
| --- | --- | ---: | ---: | ---: |
| OpenAI | Histórico completo | 0.5061482 | 0.168716067 | 20,84 s |
| OpenAI | Notas, mesmo modelo | 0.5489598 | 0.182986600 | 33,67 s |
| OpenAI | Notas, modelo barato | 0.5165627 | 0.172187567 | 38,40 s |
| Anthropic | Histórico completo | 0.7795653 | 0.259855100 | 17,65 s |
| Anthropic | Notas, mesmo modelo | 0.5255119 | 0.175170633 | 37,98 s |
| Anthropic | Notas, modelo barato | 0.47431685 | 0.158105617 | 38,02 s |

**Três atualizações compartilhando cada snapshot (`k = 3`):**

| Provedor | Estratégia | Total USD, 3 sessões | Média USD/sessão | Mediana da sessão |
| --- | --- | ---: | ---: | ---: |
| OpenAI | Histórico completo | 0.6567402 | 0.218913400 | 36,91 s |
| OpenAI | Notas, mesmo modelo | 0.6396958 | 0.213231933 | 42,75 s |
| OpenAI | Notas, modelo barato | 0.5173699 | 0.172456633 | 55,68 s |
| Anthropic | Histórico completo | 0.8580869 | 0.286028967 | 44,65 s |
| Anthropic | Notas, mesmo modelo | 0.5617414 | 0.187247133 | 62,30 s |
| Anthropic | Notas, modelo barato | 0.485141845 | 0.161713948 | 64,53 s |

Cada comparação abaixo usa `1 - custo_estratégia / custo_histórico_completo` dentro do
mesmo provedor e quantidade de atualizações. Percentuais positivos indicam economia;
negativos indicam custo adicional.

| Provedor | Atualizações/turno | Estratégia com notas | Economia no status | Economia em notas + status | Economia na sessão |
| --- | ---: | --- | ---: | ---: | ---: |
| OpenAI | 1 | Mesmo modelo | 83,487117% | −17,423849% | −8,458313% |
| OpenAI | 1 | Modelo barato | 99,407986% | −3,482398% | −2,057599% |
| Anthropic | 1 | Mesmo modelo | 91,781068% | 46,201803% | 32,589111% |
| Anthropic | 1 | Modelo barato | 99,125974% | 53,930846% | 39,156239% |
| OpenAI | 3 | Mesmo modelo | 68,039085% | 2,946856% | 2,595303% |
| OpenAI | 3 | Modelo barato | 98,834827% | 33,768857% | 21,221527% |
| Anthropic | 3 | Mesmo modelo | 86,635873% | 46,747130% | 34,535605% |
| Anthropic | 3 | Modelo barato | 98,066242% | 58,099438% | 43,462388% |

Gerar notas tem custo relevante mesmo quando produzir status é muito barato.
Em `k = 3`, a composição do total das três sessões é:

| Provedor | Estratégia | Investigação USD | Notas USD | Status USD |
| --- | --- | ---: | ---: | ---: |
| OpenAI | Histórico completo | 0.2568768 | 0 | 0.3998634 |
| OpenAI | Notas, mesmo modelo | 0.2516158 | 0.2602800 | 0.1278000 |
| OpenAI | Notas, modelo barato | 0.2525358 | 0.2601750 | 0.0046591 |
| Anthropic | Histórico completo | 0.2239669 | 0 | 0.6341200 |
| Anthropic | Notas, mesmo modelo | 0.2240543 | 0.2529425 | 0.0847446 |
| Anthropic | Notas, modelo barato | 0.2194420 | 0.2534375 | 0.012262345 |

A operação de notas ausente custa zero; uma chamada tentada sem contabilização conhecida
permanece `null`. Cada condição com notas gera 15 notas nas três sessões, nos dois valores
de `k`. O custo amortizado das notas por atualização é `notes_cost / (15 × k)`; na condição
com modelo barato, cai de USD 0.0174133 para 0.0057817 na OpenAI e de USD 0.0167460 para
0.0056320 na Anthropic entre essas rodadas. As notas produzidas e os custos da tarefa
principal variam, por isso a economia efetiva da sessão é medida separadamente. Na OpenAI,
reutilizar cada nota três vezes fez as duas condições passarem de custo adicional para
economia frente ao controle correspondente. Executar mais atualizações **não** reduziu o
custo absoluto da sessão: compare cada estratégia ao histórico completo com o mesmo `k`.
Economia próxima de 99% no status não equivale à economia na sessão. A mediana do tempo
sequencial aumentou com notas em todas as comparações; em `k = 3`, notas + modelo barato
levaram 55,68 contra 36,91 segundos na OpenAI e 64,53 contra 44,65 segundos na Anthropic.
Essas medições incluem geração serial de notas/status, rede e custos locais; não isolam
velocidade do modelo nem preveem execução concorrente.

### Histórico de falhas e limites

Os prompts anteriores violaram restrições de tamanho. Por exemplo,
`anthropic-status-validation-01` aprovou 7/9 sessões, com dois checks de texto de status
reprovados e um de limite de palavras das notas. Essas rodadas preservam o hash anterior
e ficam fora do CSV consolidado. Os prompts revisados passaram nas quatro rodadas completas
sem relaxar os limites de 40/200 palavras, truncar saídas ou tentar novamente para obter
aprovação. É um piloto pequeno, sem comprovar confiabilidade geral.

As tentativas interrompidas permanecem em `results/`. OpenAI `reuse3-01` e `reuse3-02`
registram `APIError`; o usuário informou falta de saldo e recarga. O usuário também
relatou falta de saldo na Anthropic, uma tentativa posterior malsucedida e uma chamada
mínima bem-sucedida. Os arquivos locais da Anthropic diferem do relato fornecido:
`reuse3-01` registra `BadRequestError` (160 chamadas, 7 sessões iniciadas), enquanto
`reuse3-02` registra `APITimeoutError` (141 chamadas, 6 sessões iniciadas), em vez de um
`BadRequestError` rápido em `02`. As classes de erro salvas não estabelecem a causa,
e nenhuma mensagem de exceção foi preservada. Atualizações ausentes após interrupção
são lacunas na execução prevista, não falhas de conteúdo observadas no modelo.
Custos desconhecidos permanecem `null`.

Os quatro conjuntos completos de origem estão presentes. Os originais continuam locais e ignorados pelo Git; cópias sanitizadas de contabilização
e avaliação estão disponíveis em [benchmarks/evidence](benchmarks/evidence). Custos por tarifas configuradas não são uma conciliação
com a fatura. As rodadas dos provedores em `k = 3` ocorreram com nove dias de diferença;
aliases de modelos, variação do serviço e condições de execução limitam comparações diretas.
Os resultados não estabelecem um ranking OpenAI versus Anthropic, não reproduzem a
implementação interna do Claude Code e não medem assinaturas. Somente campos estruturados
e tamanho da descrição dos status foram verificados nesses lotes publicados; não foram
avaliadas a semântica das notas nem toda a qualidade semântica das descrições livres.
Novas notas estruturadas verificam também os fatos de cada snapshot. O texto livre gerado
por LLM continua sem avaliação de coerência semântica; o template evita prosa gerada de forma independente.

### Reproduzir relatórios ou executar nova comparação

Regenere o CSV público offline a partir das origens locais selecionadas explicitamente e
compare com o resultado versionado:

```bash
uv run context-bench consolidate \
  results/openai-status-length-validation-01 \
  results/anthropic-status-length-validation-01 \
  results/openai-status-reuse3-03 \
  results/anthropic-status-reuse3-03 \
  --output /tmp/status-validation-recomputed.csv
diff -u benchmarks/status-validation.csv /tmp/status-validation-recomputed.csv
```

O caminho de saída deve ser novo. `consolidate` lê execuções reais de status com schema 3 e
valida manifestos concluídos, repetições previstas, associações de sessões e toda a sequência
de chamadas da tarefa/notas/status. Arquivos ausentes, lotes interrompidos, respostas
incompletas e chamadas previstas ausentes ou duplicadas falham antes da criação do CSV.
Os indicadores de aprovação das sessões e as contagens de status aprovados devem concordar
com os checks booleanos salvos; metadados de qualidade contraditórios ou malformados são
recusados. Arquivos de origem existentes, inclusive `summary.csv`, nunca são reescritos.
Cada execução mantém seu próprio controle, provedor, quantidade de atualizações, aliases e hashes; não há
agrupamento entre execuções, mesmo fornecidas no mesmo comando. Sessões concluídas com
checks de qualidade reprovados podem ser exportadas, sem alegar economia nessas condições.
Custos desconhecidos são escritos como `null`. O comando reutiliza custos e avaliações
registrados; não reaplica tarifas nem reavalia respostas que não foram preservadas.

Recalcule os relatórios locais existentes sem chamadas às APIs:

```bash
uv run context-bench report results/openai-status-length-validation-01
uv run context-bench report results/anthropic-status-length-validation-01
uv run context-bench report results/openai-status-reuse3-03
uv run context-bench report results/anthropic-status-reuse3-03
```

Para uma reprodução paga autorizada separadamente, use a configuração versionada
`*-status-validation.toml` (`k = 1`) ou `*-status-reuse3.toml` (`k = 3`) e um diretório
**novo** de saída:

```bash
uv run --env-file .env context-bench run \
  --config configs/openai-status-validation.toml --output results/openai-status-validation-NEW --live --diagnostics
uv run --env-file .env context-bench run \
  --config configs/anthropic-status-validation.toml --output results/anthropic-status-validation-NEW --live --diagnostics
uv run --env-file .env context-bench run \
  --config configs/openai-status-reuse3.toml --output results/openai-status-reuse3-NEW --live --diagnostics
uv run --env-file .env context-bench run \
  --config configs/anthropic-status-reuse3.toml --output results/anthropic-status-reuse3-NEW --live --diagnostics
```

Cada configuração de validação prevê 129 chamadas; cada configuração de reutilização,
219. Os exemplos menores `*-status.toml` usam uma repetição e preveem 43 chamadas.
Em geral, as três estratégias fazem `28 + 15 × k` chamadas por repetição.
`--diagnostics` é opcional e salva apenas campos finais avaliados e limitados desse incidente
sintético. Confira disponibilidade dos modelos e tarifas históricas antes de qualquer
rodada paga; não reutilize um diretório de saída existente.

</details>

<details>
<summary>Referência de configuração: controles de tarefa, status, agendas e cenários</summary>

## Novas comparações controladas (schema 4)

Os quatro controles de tarefa foram medidos nos resultados recentes acima. Os controles
de status, a agenda sem acknowledgements e os cenários adicionais estão implementados e
testados offline, mas ainda sem medição real. O piloto anterior não incluía o modelo barato
sozinho e não estabelece delegação como a melhor arquitetura disponível.
O replay original fornece a causa explicitamente, identifica logs irrelevantes e exige
principalmente acknowledgements e preservação de evidências, com pouca descoberta causal.
No experimento de histórico completo versus resumo, cache respondeu por quase toda a
economia observada; os 0,70% adicionais na OpenAI são uma diferença observada com três
repetições, sem vantagem estabelecida.

| Estratégia de tarefa | Modelo com evidência bruta | Modelo da resposta final | Chamadas/sessão: cada turno / só final |
| --- | --- | --- | ---: |
| `cache` | Principal | Principal | 6 / 1 |
| `cache_small_model` | Alternativo | Alternativo | 6 / 1 |
| `delegate_same_model` | Subagente principal | Principal | 11 / 6 |
| `delegate_small_model` | Subagente alternativo | Principal | 11 / 6 |

`[subagent]` configura o modelo alternativo e suas tarifas, inclusive quando resolve a tarefa
sozinho. Nesse caso, `cache_small_model` é contabilizado no papel `main`. Os nomes de modelos
e as tarifas dos novos TOMLs foram copiados do piloto e são entradas históricas, não cotações atuais.

Para uma nova execução paga explicitamente autorizada, escolha uma configuração e saída nova:

```bash
uv run --env-file .env context-bench run \
  --config configs/openai-controls.toml --output results/openai-controls-NEW --live
```

Use `anthropic-controls.toml` para o outro provedor. Cada configuração agenda três repetições
das quatro condições: 102 chamadas por provedor. `*-decision-controls.toml` usa
`task_schedule = "final_only"`: 42 chamadas por provedor. Todas as cinco evidências se acumulam
antes da resposta final. Os subagentes continuam produzindo um brief por evidência liberada;
o principal recebe os cinco briefs em ordem. Não são fabricados acknowledgements do assistente.
Essa variante compara agendas de chamadas; não representa decisões intermediárias autônomas.
`summary` exige a agenda `every_turn`.

As configurações `*-status-controls.toml` comparam seis condições sob o mesmo contrato de
notas estruturadas, usando `notes_format = "structured"`:

| Estratégia | Contexto do status | Modelo das notas | Renderizador |
| --- | --- | --- | --- |
| `status_full_history` | Histórico completo | — | Principal |
| `status_full_history_small_model` | Histórico completo | — | Alternativo |
| `status_notes_same_model` | Notas | Principal | Principal |
| `status_notes_small_model` | Notas | Principal | Alternativo |
| `status_notes_template` | Notas | Principal | Template local |
| `status_small_notes_small_model` | Notas | Alternativo | Alternativo |

Com uma atualização por evidência, são 243 chamadas de API e 15 eventos locais por provedor
nas três repetições. Com três atualizações, são 393 chamadas e 45 eventos locais. Cada nota
é produzida uma vez por evidência. Para testar outra agenda, copie o TOML e altere
`task_schedule = "final_only"` (153 chamadas com uma atualização), ou mude
`status_updates_per_turn`, mantendo os controles correspondentes no novo lote.
`status_baseline` identifica explicitamente o controle de status, por padrão
`status_full_history`. Controle ausente ou sem aprovação produz economia null.

As notas estruturadas contêm `latest_evidence`, `cause`, `discarded_hypothesis`,
`approval_required` e IDs distintos em `evidence`. Devem preservar os fatos atuais e as
referências causais obrigatórias dentro de 200 palavras. Notas estruturadas inválidas ou
truncadas interrompem aquela sessão sem reparo ou fallback; a chamada cobrável e seus checks
ficam registrados. As condições seguintes ainda podem executar. Notas legadas em `text`
continuam disponíveis, verificando apenas presença e tamanho. Não agrupe lotes com notas
em texto e estruturadas: a mudança de representação também é uma intervenção.

O template local usa apenas campos das notas. Não consulta gabaritos, evidências brutas ou
um índice de turno. Descreve causa, hipótese descartada e exigência de aprovação sem criar
alegações independentes de execução. Não faz chamada de API. `observations.jsonl` registra
somente agenda, tempo e checks booleanos, sem texto, uso, tarifas ou cache. O tempo local
entra no tempo total da sessão; `status_latency_*` continua medindo chamadas LLM. Renderização
local tem custo de API zero, mas custo desconhecido das notas mantém pipeline e total em null.
Passar nos campos estruturados e no limite de palavras não estabelece coerência semântica
da prosa livre de um LLM.

Novos manifestos registram schema 4, versão e hashes do código dos avaliadores, hash do cenário,
hash dos prompts de notas/status, ordem aleatorizada e modelos e turnos agendados por papel.
`summary.csv` inclui modelos, formato das notas, agenda, hashes, eventos locais, economia de
status frente a `status_baseline` e `task_savings_vs_cache_fraction` para os quatro controles
de tarefa. Percentuais exigem agenda completa e válida, sessões aprovadas, custos conhecidos,
contabilidade completa e controle de cache válido. Hits continuam vindo dos contadores das APIs;
política válida de cache não comprova hits.

```bash
uv run context-bench consolidate results/openai-controls-NEW \
  --output /tmp/openai-controls-NEW.csv
```

A consolidação offline aceita subconjuntos novos de tarefa/status e valida modelos, agenda
exata de chamadas/eventos locais, associações e vereditos salvos. Preserva os arquivos de origem.
Mantém compatibilidade com status de schema 3; exportar apenas fontes legadas mantém exatamente
as colunas históricas. Exportações mistas acrescentam proveniência, deixando metadados legados
desconhecidos em null. Não combina resultados entre cenários, critérios, provedores, formatos
ou agendas. A reprodução pública continua reconstruindo os mesmos oito lotes e três tabelas.
Ela reutiliza julgamentos registrados; respostas descartadas não podem ser reavaliadas com
o novo avaliador.

Os cenários adicionais têm critérios definidos no código antes de novas chamadas pagas:

| `scenario` | Mudança | Evidências causais finais obrigatórias |
| --- | --- | --- |
| `replay` | Preservação original | E3 |
| `ambiguous` | Mudança de configuração e aumento de tráfego simultâneos; confirmação só em E4 | E3, E4 |
| `corrections` | Conclusão preliminar retirada em E4 e corrigida em E5 | E5 |
| `dense` | Identificador exato da ação revisado; duas aprovações continuam pendentes | E3 |

Use `*-ambiguous-controls.toml`, `*-corrections-controls.toml` ou `*-dense-controls.toml`.
Cada um executa quatro condições em três repetições (102 chamadas). Para status em uma
variante, copie um TOML de status-controls e altere `scenario`. Os logs das variantes não
são rotulados explicitamente como irrelevantes. Continuam sendo testes sintéticos pequenos,
com conclusões finais explícitas; `dense` verifica retenção exata de restrições, sem medir
um limite geral de compressibilidade. Amplie repetições depois de avaliar os controles,
fixando critérios e regras de relatório antes das execuções reais.

No lote OpenAI publicado com uma atualização por nota, renderização de status caiu de
US$ 0,2538624 para US$ 0,0015029, mas produzir notas custou US$ 0,2612. As notas superaram
a economia de renderização; o total passou de US$ 0,5061482 para US$ 0,5165627, cerca de 2,1% a mais.

</details>

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

Para três repetições dos quatro controles de tarefa:

```bash
uv run --env-file .env context-bench run \
  --config configs/openai-controls.toml \
  --output results/openai-controls-NEW --live

uv run --env-file .env context-bench run \
  --config configs/anthropic-controls.toml \
  --output results/anthropic-controls-NEW --live
```

Essas configurações agendam 102 chamadas por provedor. Incluem o modelo barato sozinho,
com o mesmo cenário, política de cache e contrato de saída das outras condições. Use sempre
um diretório novo. As configurações anteriores de três condições continuam disponíveis
como `*-delegation-validation.toml` (84 chamadas por provedor).

Para comparar `full`, `cache` e `summary`, use `configs/openai.toml` ou
`configs/anthropic.toml`. Com três repetições, são 57 chamadas previstas por provedor.

### Leia os resultados

```bash
uv run context-bench report results/openai-controls-NEW
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
  --config configs/anthropic-controls.toml \
  --output results/anthropic-traced-01 --live \
  --otlp-endpoint http://localhost:4318/v1/traces
```

Forneça seu próprio coletor. Os spans registram operação, papel (`component`), correlação,
duração e resultado. A contabilização de tokens fica no JSONL, vinculada pelo trace ID.
Prompts, notas intermediárias, headers, chaves e mensagens de exceção não são gravados.
`--diagnostics` permite apenas o registro limitado dos campos finais descrito acima.
Mantenha a mesma configuração de telemetria ao comparar tempos.

## Evidências públicas e contribuição técnica

`benchmarks/controls-evidence/` contém os dois lotes de controles de tarefa de 18 de novembro,
em schema 4; `benchmarks/evidence/` preserva os oito lotes anteriores. Ambos contêm manifestos,
contabilização por chamada e checks por sessão, com checksums e comandos de reprodução próprios. Exclui prompts, respostas, `answer_diagnostics` e IDs
de telemetria. `sha256.json` detecta alterações nos arquivos; não é uma assinatura independente.
O script de reprodução confere uso normalizado, tarifas históricas e igualdade dos CSVs.
Não reavalia texto descartado. Falhas históricas permanecem documentadas e nos originais locais;
o conjunto público seleciona os lotes completos usados nas tabelas.

```bash
uv run python scripts/reproduce_controls.py --output /tmp/controls-tables-NEW
uv run python scripts/reproduce_benchmarks.py --output /tmp/bench-tables-NEW
```

O diretório de saída deve ser novo. Os resultados não são combinados entre hashes ou
provedores. A contribuição específica deste projeto é o desenho dos controles, isolamento
de contexto, contabilização dos papéis, avaliação determinística, recuperação de falhas e
análise de amortização das notas sobre o harness de engenharia atribuído abaixo.

## Desenvolvimento

Para regenerar os gráficos sem alterar as dependências do projeto:

```bash
uv run python scripts/plot_controls.py
uv run python scripts/plot_controls.py --language pt-br
uv run python scripts/plot_status_costs.py
```

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

O próximo passo empírico é medir os controles de status e as variantes de cenário com
critérios definidos antes das execuções. Os resultados atuais não estabelecem generalização
ou qualidade equivalente fora deste replay.
