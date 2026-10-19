# Google ClusterData2019 cell-g collection events

Retrieved 2026-09-29 from Google's public GCS objects for the [2019 Borg trace](https://github.com/google/cluster-data/blob/master/ClusterData2019.md). The producer describes eight Borg cells recorded in May 2019 and licenses the trace and documentation under CC BY. The [v3 format document](https://drive.google.com/file/d/10r6cnJ5cJ89fPWCgj7j4LtLBqYN9RiI9/view) defines `JOB`, `SUBMIT`, synthesized events and special timestamps. These are generic cluster collection events, not AI-App user requests or scale commands.

The [official cell-g prefix list](https://storage.googleapis.com/storage/v1/b/clusterdata_2019_g/o?prefix=collection_events-&maxResults=1000) has one JSON gzip object and a separate Parquet object. The JSON object was fetched by its fixed GCS generation **after** the [study freeze](../../../artifacts/google_2019_cell_g_seasonal_v1/freeze.json) was written. It was checked against official size and MD5 and a local SHA-256 was recorded in the verified manifest (`verified_collection_events_manifest.json`). The frozen extraction script stores only aggregate 5-minute counts in the research artifact.

| JSON object | Compressed bytes | GCS generation | Local SHA-256 |
| --- | ---: | --- | --- |
| `collection_events-000000000000.json.gz` | 203,257,147 | `1596913609702066` | `ed7c850444c09d51112f7b15d67324700e01e9768b403e6a294941a241062c13` |

The official object metadata (`collection_events_object_manifest.json`) has SHA-256 `6e3adb51c5eeba3b7c0218cf9c01234fe5d6ec266c25f7f78598c54c9f7c2dc0`; the verified manifest (`verified_collection_events_manifest.json`) has SHA-256 `cb878f8581e20de71d2d751fc1d4ce7cee75149d40be5ec3e7b418cf998faceb`. The common schema (`collection_events.schema.json`) has SHA-256 `63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3`.
