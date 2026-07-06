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

The supported subset covers nested space systems, scalar telemetry types,
parameters, simple units and aliases, polynomial calibration, static alarms,
numeric time types, sequence containers, inheritance, entries, comparison
restrictions, arrays, aggregates, dynamic binary/string sizes, repeat entries,
and contextual polynomial calibrators.

Multidimensional arrays become nested MDB arrays and must use zero-based indices.
XTCE dynamic values preserve `useCalibratedValue` and receive explicit safety
bounds from `XtceLoadOptions`: 65,536 elements, 65,536 repeats, and 67,108,864
bits by default. These limits are independently configurable.

Commands, indirect entries, arbitrary expressions, spline calibrators, advanced
repeat semantics, nonzero array indices, and XML export are not yet supported.
