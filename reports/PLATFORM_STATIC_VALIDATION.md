# Validation Platform — Validação estática

Validação executada no ambiente de desenvolvimento do patch:

```bash
python3 -m unittest tests/test_platform_dashboard.py
python3 scripts/generate_platform_demo_data.py --overwrite
python3 scripts/check_platform_data.py
```

Resultado dos testes unitários:

```text
Ran 4 tests
OK
```

Resultado do `check_platform_data.py` com dados demo:

```text
Resumo da plataforma PPS57:
- total_cits_messages: 6
- total_tsp_decisions: 2
- total_actuation_events: 2
- applied_actuation_events: 1
- blocked_by_safety: 0
- policy_candidate_count: 3
- reward_delta: 8.5
- missing_critical_artifacts: none
```

Nota: a dashboard Streamlit deve ser testada localmente com `make platform`, porque este ambiente não expõe uma sessão web interativa.
