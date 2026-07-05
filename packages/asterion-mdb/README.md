# asterion-mdb

`asterion-mdb` is Asterion's protocol-neutral mission database foundation. It
models fixed-layout telemetry independently from CCSDS, PUS, XTCE, SCOS-2000,
or any transport and has no runtime dependencies.

```python
from asterion.mdb import (
    IntegerParameterType, MissionDatabaseBuilder, ParameterDefinition,
    ParameterEntry, QualifiedName, SequenceContainer, SpaceSystem,
)

q = QualifiedName.parse
builder = MissionDatabaseBuilder("cubesat")
builder.add_space_system(SpaceSystem(q("/Satellite")))
builder.add_parameter_type(IntegerParameterType(q("/Satellite/u8"), size_bits=8))
builder.add_parameter(ParameterDefinition(q("/Satellite/mode"), type_ref="u8"))
builder.add_container(
    SequenceContainer(q("/Satellite/Telemetry"), entries=(ParameterEntry("mode"),))
)
database = builder.compile()
decoded = database.decode(b"\x02", root_container="/Satellite/Telemetry")
assert decoded.by_name[q("/Satellite/mode")].value == 2
```

Compilation resolves references, validates layouts, detects inheritance cycles,
and creates an immutable indexed database suitable for shared runtime use.

Bit zero is the most-significant bit of the first input octet. Big-endian integer
fields may use arbitrary bit sizes and offsets. Little-endian integers, floats,
fixed binary fields, and strings are byte-aligned.

Derived containers inherit base entries and use parameter or caller-context
comparisons. Selection is strict: zero or multiple matches raise typed errors.

The first release supports fixed integer, float, boolean, enumeration, binary,
and string fields plus polynomial calibration. Commands, alarms, dynamic fields,
arrays, aggregates, time encodings, XTCE, and SCOS import are future milestones.
