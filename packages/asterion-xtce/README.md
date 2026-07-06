# asterion-xtce

`asterion-xtce` strictly loads XTCE 1.3 telemetry definitions, plus compatible
XTCE 1.2 documents, into `asterion.mdb.MissionDatabase`.

```python
from asterion.xtce import load, loads

database = load("mission.xml")
decoded = database.decode(packet_data, root_container="/Satellite/Telemetry")

# In-memory XML is kept unambiguous through a separate API.
database = loads(xml_text, source_name="mission.xml")
```

The loader has bounded document-size, element-count, and nesting limits. DTD and
entity declarations are rejected, and loading never performs network access.
Recognized XTCE telemetry semantics that cannot be mapped safely raise
`UnsupportedXtceFeatureError`; no partial database is returned.

The initial subset covers nested space systems, scalar telemetry types,
parameters, simple units and aliases, polynomial calibration, static alarms,
numeric time types, sequence containers, inheritance, entries, and comparison
restrictions. Commands, arrays, aggregates, dynamic values, indirect entries,
advanced calibrators, and XML export are not yet supported.
