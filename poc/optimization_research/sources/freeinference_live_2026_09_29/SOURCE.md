# FreeInference live telemetry snapshot

Source: [official FreeInference Open Data hub](https://data.freeinference.org/), public `https://openinfra.freeinference.org/api/dashboard` and `https://openinfra.freeinference.org/api/history?hours=N` (`N=2,24,168`). Retrieved 2026-09-29 08:58–09:01 UTC. These are changing live endpoints; the local JSON files preserve one observed response from each endpoint.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `dashboard.json` | 55,192 | `72bf30e02401bcac6fb63ac3e34758198e36c8e34d1cf35dc3ceddadcd6adb50` |
| `history_2h.json` | 1,138,060 | `94dbd79d239719426003ca83cae0c793a7e76562fbf9c985eb3e2a4555f366b6` |
| `history_24h.json` | 672,034 | `775029f29708032bf3bdd9890384079ebd9993222e3bb03b9edc7d6c1499723e` |
| `history_168h.json` | 1,477,157 | `9019eb970675031ad0c1fb41dd0607d188893cbd08d82b410ce571bff8616ac4` |

The audit report (`../../../reports/freeinference_live_telemetry_feasibility_2026_09_29.md`) explains supported observations and why this snapshot cannot supervise AI-App scale actions.
