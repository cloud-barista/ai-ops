# BurstGPT earlier-period output-length feasibility audit

2026-09-28. The exact official release assets and hashes are recorded in the [source manifest](../data/external/burstgpt_v2_12/SOURCE.md). This audit uses only source quality and cohort counts, before computing any held-out prediction score. The earlier files come from the **same service** as `BurstGPT_3.csv`; they are a different, earlier time window, not an independent service or a fully blinded dataset.

| Source | Original rows | Relative day | `Request tokens > 0` | Zero outputs within that input-side subset |
| --- | ---: | --- | ---: | ---: |
| `BurstGPT_1.csv` | 1,429,737 | 0–60 | 1,404,310 | 16 |
| `BurstGPT_2.csv` | 3,858,436 | 61–120 | 3,784,215 | 2 |
| `BurstGPT_3.csv` (previous study) | 5,344,021 | 225–334 | 4,956,059 | 1 |

The input-side rule keeps the rare zero outputs instead of filtering by the outcome. The publisher describes some zero-response rows as failed requests, but there is no status column to identify all failures. Restricting to positive request tokens removes most such rows without claiming to isolate all successful requests. Four `Model × Log Type` cohorts occur in each file. Among eligible requests, file 1 has `ChatGPT/API` 1,091,754, `ChatGPT/conversation` 97,419, `GPT-4/API` 166,839, and `GPT-4/conversation` 48,298. File 2 has 3,641,545, 55,319, 63,225, and 24,126 respectively.

All six source columns are present, with no missing values, negative token counts, or violations of `Total tokens = Request tokens + Response tokens`. Timestamps are nondecreasing within each file. File 1 spans seconds 5–5,269,973; file 2 spans 5,270,414–10,454,395; file 3 spans 19,440,110–28,943,983. Thus none of the three files shares a timestamp range, and files 1 and 2 have no gap in relative days (61 + 60 = 121 consecutive days). There are no missing days. Repeated timestamp and same-column rows occur within each source (file 1: 54,040 full selected-column duplicates; file 2: 150,150); the files have no request ID, so these are not deduplicated without evidence that they are duplicate events. The smallest eligible day in file 2 contains 52 requests, so day-level results and sparse-cell fallback matter.

**Decision:** A request-side output-token p90 prediction study is feasible using `Model`, `Log Type`, and `Request tokens` as admission-time information under an explicit tokenizer-availability assumption. It can test whether the prior `rolling_bin` hypothesis carries to this earlier period. The trace cannot identify the minimum CPU/GPU/VRAM shape, latency SLO, effects of an action, or actual feedback delivery time. The evaluation therefore cannot validate deployment or scaling decisions.
