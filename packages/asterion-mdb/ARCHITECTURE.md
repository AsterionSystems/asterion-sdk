# Architecture

```text
XTCE / SCOS / project loaders
            ↓
MissionDatabaseBuilder
            ↓ compile and validate
immutable MissionDatabase
            ↓
extract → calibrate → validate → alarm → select container
```

The package accepts raw bytes and optional context. Protocol adapters may provide
values such as a CCSDS APID, but `asterion-mdb` never imports transport packages.
Format-specific source fidelity belongs to adapters, not runtime definitions.

Evaluation is intentionally stateless. Historical values, latching, hysteresis,
and change alarms belong to a future processing layer with an explicit lifecycle.

Structured decoding is recursive but bounded. Dynamic dimensions have local
maximums, while compiled databases enforce nesting and total decoded-value
limits. Repeat groups retain row structure outside the scalar parameter index so
qualified-name lookup never silently selects one occurrence.

Time values wrap numeric encodings and remain epoch-plus-Decimal coordinates.
The core records UTC, TAI, GPS, and TT identity but does not perform time-scale,
leap-second, or spacecraft-clock correlation.
