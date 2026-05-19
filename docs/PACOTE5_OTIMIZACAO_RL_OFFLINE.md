# Pacote 5 — Otimização offline e RL proxy

## Objetivo

O Pacote 5 adiciona uma camada de otimização offline sobre o Pacote 4. O baseline continua a ser o motor TSP explicável, e a Safety Layer é obrigatória para qualquer decisão candidata.

```text
Cenários offline
        ↓
Baseline TSP Pacote 4
        ↓
Candidatos de política otimizada
        ↓
Safety Layer obrigatória
        ↓
Política segura exportada
```

## Implementação

Localização:

```text
src/pps57_opt/
```

Componentes:

- `dataset.py`: gera cenários offline sintéticos para green extension, no action, early green, reevaluate, reject e bloqueios de safety.
- `optimizer.py`: avalia ações candidatas, calcula reward proxy e seleciona a melhor ação segura.
- `models.py`: define amostras offline, avaliações de candidatos e regras exportadas.
- `config.py`: carrega `configs/optimization_config.json`.

## Safety Layer obrigatória

Cada candidato é convertido numa `TSPDecision` e validado por `TSPSafetyLayer`. Se a safety layer devolver `blocked_by_safety`, o candidato fica registado no log, mas não pode ser escolhido pela política otimizada.

Isto evita que uma política offline proponha ações como:

- extensão de verde fora da fase verde do corredor;
- truncagem durante amarelo;
- early green sem sequência de fase válida;
- violação de limites de verde, cooldown ou intervenções consecutivas.

## Como executar

```bash
make optimize-offline
```

Outputs:

```text
outputs/pacote5_offline_samples.jsonl
outputs/pacote5_policy_candidates.jsonl
reports/pacote5_policy_report.json
reports/pacote5_optimization_summary.json
```

## Limitações

Este Pacote 5 ainda não treina uma rede neuronal nem interage com SUMO em loop fechado. É uma política tabular offline/proxy, desenhada para validar arquitetura, reward, logs, comparação com baseline e filtro obrigatório de segurança antes de avançar para treino com logs reais ou simulações calibradas.
