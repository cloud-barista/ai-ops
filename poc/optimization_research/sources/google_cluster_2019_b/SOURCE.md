# Google ClusterData2019 cell-b collection events

Retrieved 2026-09-29 from Google's public GCS objects for the [2019 Borg trace](https://github.com/google/cluster-data/blob/master/ClusterData2019.md). The producer describes eight cells and licenses the trace/documentation under CC BY. Its [v3 format and access document](https://drive.google.com/file/d/10r6cnJ5cJ89fPWCgj7j4LtLBqYN9RiI9/view) defines `JOB`, `SUBMIT`, synthesized events and special timestamps. These files are **generic Borg collection events**, not AI-App user requests or VM scale action records.

The [official cell-b prefix list](https://storage.googleapis.com/storage/v1/b/clusterdata_2019_b/o?prefix=collection_events-&maxResults=1000) contains four `collection_events-*.json.gz` objects and separate Parquet objects. All four JSON objects were downloaded with generation-pinned URLs and verified against the metadata manifest (`collection_events_object_manifest.json`): GCS size, generation, MD5 and local SHA-256. No Parquet objects or other cells were mixed in. The verified manifest (`verified_collection_events_manifest.json`) has SHA-256 `09571fc6262abca9a54cae00d951be7c9f08e49b890280a00b997904261ddf62`.

| JSON object suffix | Compressed bytes | GCS generation | Local SHA-256 |
| --- | ---: | --- | --- |
| `000000000000` | 127,263,730 | `1596844191969755` | `47d10ebc53e381b4d6184b633d46cc39bc4476dd2e0b05b00f9b4e6dbf888381` |
| `000000000001` | 127,172,784 | `1596844193473169` | `0d422fff76420c499bc8ed9c8207ca9b8ad18f76fc6b1ec48118a08d335664c1` |
| `000000000002` | 126,956,131 | `1596844198071737` | `f65c8750c755ba49f18bad39cd561af22ed8da5e71d358be66151bccdd2b8d13` |
| `000000000003` | 127,273,467 | `1596844196105842` | `290107851acd922e57fc8d0c597868da17dfe91e98dfc5c8b2a01bee2391debc` |

The total compressed size is **508,666,112 B**. The common collection-events schema (`collection_events.schema.json`) was copied from the same official schema object used for cell-a: generation `1596854498220506`, local SHA-256 `63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3`. The fetch/extract script (`../../../scripts/prepare_google_2019_cell_b.py`) refuses downloads without the frozen cell-b study protocol, verifies each object before publishing it, streams all four objects and retains only 5-minute aggregate counts. See the frozen cell-b result (`../../../reports/google_2019_cell_b_adaptive_findings_v1.md`).
