# Google ClusterData2019 cell-e collection events

Retrieved 2026-09-29 from Google's public GCS objects for the [2019 Borg trace](https://github.com/google/cluster-data/blob/master/ClusterData2019.md). The producer describes eight Borg cells recorded in May 2019 and licenses the trace and documentation under CC BY. The [v3 format document](https://drive.google.com/file/d/10r6cnJ5cJ89fPWCgj7j4LtLBqYN9RiI9/view) defines `JOB`, `SUBMIT`, synthesized events and special timestamps. These are generic cluster events, not AI-App user requests or issued scale commands.

The [official cell-e prefix list](https://storage.googleapis.com/storage/v1/b/clusterdata_2019_e/o?prefix=collection_events-&maxResults=1000) has one JSON gzip object and separate Parquet data. The JSON object was fetched by its fixed GCS generation **after** the [study freeze](../../../artifacts/google_2019_cell_e_selector_v1/freeze.json) was written. It was checked against official size and MD5 and a local SHA-256 was recorded in the verified manifest (`verified_collection_events_manifest.json`). The extraction script retains only aggregate 5-minute counts in the research artifact.

| JSON object | Compressed bytes | GCS generation | Local SHA-256 |
| --- | ---: | --- | --- |
| `collection_events-000000000000.json.gz` | 254,347,684 | `1596910963753933` | `9b847a5e761f74743d2188a009b04242969b4d37544c4f7f96efa10330d597f5` |

The official object metadata (`collection_events_object_manifest.json`) has SHA-256 `21bead24c5d90136a289c5fadda8cd93a0bebe09394109a6dcd95c843c9afbdd`; the verified manifest (`verified_collection_events_manifest.json`) has SHA-256 `5659b7508110070bbae3dbf1dac0eceaf4084b67601bc365b91d05e5dc759b2e`. The common schema (`collection_events.schema.json`) has SHA-256 `63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3`.
