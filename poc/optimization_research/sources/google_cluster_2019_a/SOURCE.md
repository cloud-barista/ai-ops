# Google ClusterData2019 cell a: complete collection-event JSON object set

Retrieved 2026-09-29 from the official Google GCS buckets. The [2019 trace landing page](https://github.com/google/cluster-data/blob/master/ClusterData2019.md) describes eight Borg cells from May 2019, about 2.4 TiB compressed, and the CC-BY license. It says BigQuery is the only access path, but its linked [official v3 format document, JSON access section](https://drive.google.com/file/d/10r6cnJ5cJ89fPWCgj7j4LtLBqYN9RiI9/view) also describes public GCS JSON objects. The object URLs below were reachable without BigQuery credentials on the retrieval date. All **10** JSON objects listed under cell a's `collection_events-` prefix are now stored locally; the other four v3 tables and the other seven cells are not.

Object path template: `https://storage.googleapis.com/clusterdata_2019_a/collection_events-${ID}.json.gz?generation=${GENERATION}`. The original GCS metadata manifest (`collection_events_object_manifest.json`) has all object names, sizes, generations, MD5, and CRC32C values (SHA-256 `452c60360ff98db27d10417494361a664c7ef34da229ef3093e9df9149f12f89`). The verified local manifest (`verified_collection_events_manifest.json`) adds each downloaded file's SHA-256 and has SHA-256 `b01dcc58bfbfea500942c7812b0add7242446fcb5c8fa75546d89635447d3e34`.

| Object ID | Bytes | GCS generation | GCS MD5 (base64) | Local SHA-256 |
| --- | ---: | --- | --- | --- |
| `000000000000` | 133,462,892 | `1596841425790024` | `XWYyLf0GfH73SLAquvx1jQ==` | `2288aa6bb4ef5157a68ca73719685b5bf438edcc85ef5ea5eb472c294cb271d4` |
| `000000000001` | 133,464,852 | `1596841419395830` | `pcaEK/a+AaXiOPQOGbgYLQ==` | `17d75ed95bc331d850f81c3f735783bded1be222785ad6909a2160e823f63350` |
| `000000000002` | 133,474,491 | `1596841411236456` | `I/eU8DAkuo2HK932pdN4Kg==` | `b2dc471592845041d929a210dc6a4a02cc5ee28ea839565fb974110d56afab3a` |
| `000000000003` | 133,409,529 | `1596841411279463` | `x8ZAgL7Ot3/9D7y/XKREOQ==` | `253b6448b8b368e78f036ff77f10554e7f754600350ac2154c08985753b07362` |
| `000000000004` | 133,282,757 | `1596841412945022` | `SQUrk6UoPHRNiOpOW6GtFw==` | `580b38d8fa8ace34899684c60aeef87fc25e6507be23cd3cfe81e2cb67f0b478` |
| `000000000005` | 133,331,057 | `1596841416107864` | `K39DWYW3BbggtfGI7dGozA==` | `0604346a1323741839fc74c2bbe46ea74a5fae683712bc32d9bc37b0aba4ec2a` |
| `000000000006` | 133,406,675 | `1596841414784869` | `Dx1uDhSHjiYnE/EevXshwQ==` | `674ebb3e3553a99e777b7c00a785d85ec34a3deb44164adb96602101417c770c` |
| `000000000007` | 133,465,093 | `1596841412916881` | `noyn5M1DSTC4t+ER5HgJyA==` | `27275eb9e47691914a807afa526692d1c868aed75ce623c11c028e765da51280` |
| `000000000008` | 133,367,280 | `1596841414979436` | `oFt0wsYTjVSzqy1ovf0F2g==` | `932fe43a4c5446fd3e20f6eff4580203377fdf06a602874087c80df94ab6b3d9` |
| `000000000009` | 133,498,809 | `1596841416300624` | `HVNiiDQqKixKczsdH5BAhg==` | `0480cd13d5b114feaa4e6ae38c451da6f821fd272eba6ab711d59ecceca9aa33` |

Total compressed bytes: **1,334,163,435** (1,272.36 MiB). The table schema is `collection_events.schema.json` (`collection_events.schema.json`), from [GCS schema object](https://storage.googleapis.com/clusterdata_2019_schema/collection_events.schema.json?generation=1596854498220506), 1,440 B, generation `1596854498220506`, GCS MD5 `kL3yk+8pEQqKkOQ6h6q+Mg==`, local SHA-256 `63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3`.

The [public object API](https://storage.googleapis.com/storage/v1/b/clusterdata_2019_a/o?prefix=collection_events-) and every local file were independently rechecked: names `000000000000`–`000000000009`, size, generation, GCS MD5, CRC32C metadata, and local SHA-256 all matched. Fetcher (`../../../scripts/fetch_google_2019_collection_events.py`) uses generation-pinned URLs, downloads sequentially, validates before publishing each compressed file, and writes the verified manifest. The separate `parquet.gz` objects in the bucket were not used. No decompressed copy was saved. The first JSON object's rows were found to be unordered and span almost the full trace, so one object is not a contiguous date range or a complete cell arrival log. **Full trace row-level completeness and duplicate keys still require streaming all ten objects**, which is separate from verifying their bytes.

Reproduce the first-object field and event audit from the research directory with `python3 scripts/audit_google_2019_collection_events.py`. See feasibility findings (`../../../reports/google_2019_collection_events_feasibility.md`). These files belong only to the external research area; they were not joined to the local AI-MCMP SQLite or wired into the deployment agent.
