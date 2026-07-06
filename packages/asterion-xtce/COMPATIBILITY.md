# XTCE compatibility

XTCE 1.3 is the primary target. XTCE 1.2 documents using the official
`20180204` namespace are accepted where their representation maps identically.

Supported telemetry includes scalar and numeric time types, arrays, aggregates,
parameter-driven dynamic sizes, repeat entries, polynomial calibration, static
alarms, containers, inheritance, and comparisons. Multidimensional arrays are
represented as nested zero-based arrays.

The loader deliberately rejects nonzero array indices, indirect entries,
arbitrary expressions, spline calibrators, nested repeats, advanced repeat
semantics, commands, and any recognized construct that cannot be represented
without losing meaning.

Dynamic XTCE values that do not declare a maximum receive explicit bounds from
`XtceLoadOptions`. Loading never performs network access or XML schema fetching.
