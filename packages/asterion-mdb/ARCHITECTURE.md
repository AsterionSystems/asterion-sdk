# Architecture

```text
XTCE / SCOS / project loaders
            ↓
MissionDatabaseBuilder
            ↓ compile and validate
immutable MissionDatabase
            ↓
telemetry decoding and future command encoding
```

The package accepts raw bytes and optional context. Protocol adapters may provide
values such as a CCSDS APID, but `asterion-mdb` never imports transport packages.
Format-specific source fidelity belongs to adapters, not runtime definitions.
