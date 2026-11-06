# agent-context-bench

Benchmark pequeno de cache de prompt e gerenciamento de contexto com OpenAI e Anthropic.
Python 3.13, SDKs oficiais, CLI e resultados locais. Sem framework de agentes, servidor ou banco.

## Começar

```bash
uv sync --frozen
uv run context-bench demo --output results/demo
uv run context-bench report results/demo
```

`demo` funciona sem credenciais ou rede. Retorna respostas fixas, tokens sintéticos zerados
e custos nulos: valida a instalação e os arquivos, **não mede qualidade, cache ou desempenho**.

Para executar chamadas reais, disponibilize `OPENAI_API_KEY` ou `ANTHROPIC_API_KEY` no ambiente,
revise modelo e preços em `configs/`, e execute:

```bash
uv run context-bench run --config configs/openai.toml --output results/openai-01 --live
uv run context-bench run --config configs/anthropic.toml --output results/anthropic-01 --live
```

O diretório de saída precisa ser novo. Cada configuração padrão permite no máximo 57 chamadas
(3 repetições de 6 + 6 + 7 chamadas). Falha de API interrompe o lote; não há retries automáticos.
Use `repetitions = 1` para o primeiro smoke test pago. Nenhuma chamada real faz parte dos testes.

## O que está implementado

| Estratégia | Contexto | Cache |
|---|---|---|
| `full` | Histórico textual completo | Desativado explicitamente |
| `cache` | Mesmo histórico, preservando prefixos | Ativado |
| `summary` | Uma chamada de resumo após três turnos; substitui os turnos anteriores | Ativado, inclusive no resumo |

Na OpenAI, o controle usa Responses com `prompt_cache_options.mode = "explicit"`:
sem breakpoints em `full`, dois breakpoints em `cache`/`summary` (instruções e última mensagem).
Requer modelo/API compatível com cache explícito, documentado para GPT-5.6+.
Na Anthropic, `full` omite `cache_control`; as outras estratégias usam cache automático.
TTLs fixos: 30 minutos na OpenAI e 5 minutos na Anthropic. Não se comparam TTLs iguais.
Um modelo incompatível falha: não há fallback silencioso que altere o experimento.

`summary` é uma sumarização controlada pela aplicação, usando o mesmo modelo, **não compactação
nativa**. A comparação `cache` versus `summary` inclui a chamada extra e a possível perda de cache.
O MVP não implementa Message Threads, `previous_response_id`, compactação nativa, context editing,
MCP/A2A nem ferramentas reais. São experimentos posteriores, não funcionalidades simuladas aqui.

## Método e leitura dos resultados

Um incidente sintético tem seis turnos: hipótese inicial, correção, causa confirmada,
reprodução, validação e resposta final JSON. Logs irrelevantes fixos fazem o contexto crescer.
Cinco verificações determinísticas avaliam causa, ação, aprovação humana, hipótese descartada e
referência E3. A rubrica fica fora dos prompts. Respostas são avaliadas em memória, sem persistência.

- A ordem das estratégias é embaralhada por repetição, com seed registrada.
- Cada sessão começa com um identificador próprio no prefixo para evitar reutilização entre braços.
  Dentro da sessão, esse prefixo permanece estável. Esse isolamento adiciona um pequeno overhead comum.
- A primeira chamada e as posteriores ficam identificadas por turno no JSONL. Não suponha que a
  primeira seja fria ou as seguintes sejam hits: confira os contadores retornados.
- O mesmo texto é usado nos dois provedores; tokenizações e respostas podem diferir. Compare primeiro
  as estratégias dentro de cada modelo, mantendo versão, contexto, parâmetros e preços constantes.
- Os limites de saída são iguais; o esforço de raciocínio usa o padrão do modelo. Não é um teste de
  equivalência de capacidade entre modelos. A conversa preserva texto, não blocos privados de raciocínio.
- Três repetições são um piloto. p95 com poucas amostras não sustenta conclusões estatísticas.
- APIs, carga e cache podem variar. Este cenário não prova superioridade geral de nenhum provedor.

Arquivos de cada execução:

| Arquivo | Conteúdo |
|---|---|
| `manifest.json` | Configuração, versões de SDK, hash do cenário/rubrica, ordem e estado do lote |
| `calls.jsonl` | Uso numérico, custos, modelo retornado, operação, bytes, latência e trace ID |
| `sessions.jsonl` | Resultado de cada sessão e verificações de qualidade |
| `summary.csv` | Agregados por estratégia, incluindo custo por tarefa aprovada |

`ttft_text_ms` mede até o primeiro delta de **texto**, não até um evento de raciocínio ou conexão.
Latência da chamada inclui o recebimento do uso final. Bytes correspondem ao corpo serializado pelo
SDK, sem cabeçalhos/TLS. O p50 da sessão inclui sumarização, telemetria e escrita dos resultados.
O CSV agrega chamadas frias e quentes; separe turnos no JSONL para comparações de cache.
`cache_control_valid=false` invalida o braço de controle se uma chamada sem cache reportar leituras
ou escritas. `cache_requested=true` significa configuração enviada, não acerto confirmado.

### Custo

Informe as quatro tarifas atuais em `[rates]`, em USD por milhão de tokens, com a data `as_of`.
O preço de escrita é o preço **total**, não uma sobretaxa. Use a faixa de contexto correta.
Este MVP não aplica faixas de preço, batch, descontos contratuais ou cobranças de ferramentas.
Configurações sem preços produzem custo nulo, nunca uma economia inventada.

`custo = (entrada_comum × preço_entrada + leitura × preço_leitura + escrita × preço_escrita
+ saída × preço_saída) / 1.000.000`

Na Anthropic, entrada total = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.
Na OpenAI, `input_tokens` já inclui leituras e escritas; ambas são subtraídas para obter entrada comum.
Tokens de raciocínio faturados já incluídos em output não são somados novamente.
O adaptador OpenAI exige `cache_write_tokens`; ausência não é interpretada como zero.
Uma sessão truncada retém o uso recebido. Uma falha sem uso final deixa custo desconhecido;
não publique o subtotal como custo total. Custo por tarefa aprovada inclui tentativas malsucedidas,
mas fica nulo se algum custo é desconhecido ou se nenhuma tarefa passou.

## Telemetria opcional

Usa o núcleo de [a2a-otel-kit](https://github.com/brunovicco/a2a-otel-kit), sem extras A2A/MCP:

```bash
uv sync --frozen --extra telemetry
uv run context-bench run --config configs/anthropic.toml --output results/traced-01 --live \
  --otlp-endpoint http://localhost:4318/v1/traces
```

O destino OTLP é fornecido por você; não é necessário para executar o benchmark.
Spans têm nome fixo, operação, correlation ID, duração e resultado. Exceções não são capturadas.
O kit filtra atributos contendo `token`; a contabilização completa fica nos arquivos locais,
relacionados aos spans pelo trace ID/session ID. Não contornamos o sanitizador.
Não gravamos prompts, respostas, resumos, headers, chaves ou mensagens de erro.
Compare execuções com a mesma configuração de telemetria.

## Desenvolvimento e origem

```bash
uv sync --frozen --all-extras --all-groups
uv run python scripts/quality_gate.py
uv build
```

Gerado de [codex-python-engineering-harness](https://github.com/brunovicco/codex-python-engineering-harness),
perfil `library`, versão 1.2.1, commit `e9e297456573d216f070a41a7cdaa108dd599c5b`.
Mantidos layout `src`, uv, Ruff, Mypy, Pytest, Bandit, auditoria de dependências, quality gate e CI.
Removidos hooks, skills, governança e documentos sem uso no MVP. `.harness.json` registra a origem;
o projeto é uma derivação personalizada, não uma cópia integral para atualização automática.
Código sob MIT; licença do harness preservada em `LICENSE`.

Validação da entrega: 40 testes passaram, cobertura de 98,67%, quality gate completo,
build de wheel/sdist e demo offline. Os testes incluem SDKs com transporte simulado e
recebimento OTLP real em loopback. Chamadas pagas e desempenho dos modelos não foram validados.

Fontes consultadas em 05/11/2026:
[OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching),
[Anthropic caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[OpenAI compaction](https://developers.openai.com/api/docs/guides/compaction),
[Anthropic compaction](https://platform.claude.com/docs/en/build-with-claude/compaction),
[Message Threads](https://platform.claude.com/docs/en/build-with-claude/message-threads).
