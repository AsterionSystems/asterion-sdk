# asterion-mdb

`asterion-mdb` is Asterion's protocol-neutral mission database foundation. It
models fixed-layout telemetry independently from CCSDS, PUS, XTCE, SCOS-2000,
or any transport and has no runtime dependencies.

```python
from asterion.mdb import (
    AlarmSeverity, IntegerParameterType, MissionDatabaseBuilder,
    NumericAlarmRange, ParameterDefinition, ParameterEntry,
    PolynomialCalibrator, QualifiedName, SequenceContainer, SpaceSystem,
)

q = QualifiedName.parse
builder = MissionDatabaseBuilder("cubesat")
builder.add_space_system(SpaceSystem(q("/Satellite")))
builder.add_parameter_type(
    IntegerParameterType(
        q("/Satellite/u8"),
        size_bits=8,
        unit="degC",
        calibrator=PolynomialCalibrator((-40.0, 0.5)),
        alarm_ranges=(
            NumericAlarmRange(AlarmSeverity.WARNING, maximum=-20),
        ),
    )
)
builder.add_parameter(ParameterDefinition(q("/Satellite/mode"), type_ref="u8"))
builder.add_container(
    SequenceContainer(q("/Satellite/Telemetry"), entries=(ParameterEntry("mode"),))
)
database = builder.compile()
decoded = database.decode(b"\x02", root_container="/Satellite/Telemetry")
assert decoded.by_name[q("/Satellite/mode")].raw_value == 2
assert decoded.by_name[q("/Satellite/mode")].value == -39.0
assert decoded.by_name[q("/Satellite/mode")].alarm_severity is AlarmSeverity.WARNING
```

Compilation resolves references, validates layouts, detects inheritance cycles,
and creates an immutable indexed database suitable for shared runtime use.

Bit zero is the most-significant bit of the first input octet. Big-endian integer
fields may use arbitrary bit sizes and offsets. Little-endian integers, floats,
fixed binary fields, and strings are byte-aligned.

Derived containers, contextual calibrators, and validity criteria use parameter
or caller-context comparisons. Selection is strict: ambiguous calibrators and
zero or multiple matching derived containers raise typed errors. A contextual
calibrator overrides the type's default `calibrator`; otherwise decoding uses
the default or identity conversion.

Validity and alarms are stateless. Invalid samples retain their raw and
engineering values with `is_valid=False`, but do not receive an alarm severity.
Valid numeric samples use the strongest matching alarm range. Enumerated alarms
are keyed by raw integer value. The supported severities, in increasing order,
are `WATCH`, `WARNING`, `DISTRESS`, `CRITICAL`, and `SEVERE`.

The package supports fixed integer, float, boolean, enumeration, binary, and
string fields, polynomial calibration, contextual calibration, validity, and
static alarms.

## Dynamic structures

`ArrayParameterType` and `AggregateParameterType` compose scalar or structured
types recursively. `DynamicDimension` obtains an element count or bit size from
a previously decoded integer parameter or caller context:

```python
from asterion.mdb import ArrayParameterType, DynamicDimension, ParameterReference

builder.add_parameter_type(
    ArrayParameterType(
        q("/Satellite/samples_t"),
        element_type_ref="u8",
        element_count=DynamicDimension(
            ParameterReference("sample_count"), maximum=256
        ),
    )
)
```

Every dynamic dimension has a mandatory maximum. The database additionally
limits structured nesting and the total scalar values decoded per call. These
limits are configurable on `MissionDatabaseBuilder` and default to 32 levels
and 100,000 scalar values.

`RepeatEntry` decodes bounded rows of arbitrary parameter entries. Repeated rows
are available through `DecodedContainer.repeated_entries` and
`repeats_by_name`; they are deliberately excluded from scalar `by_name` lookup
and cannot drive later dimensions or criteria. Absolute offsets inside a repeat
are relative to the beginning of each row.

Commands, stateful/change alarms, time encodings, general expressions, indexed
repeat references, XTCE, and SCOS import are future milestones.
