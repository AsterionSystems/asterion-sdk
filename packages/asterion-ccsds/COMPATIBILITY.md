# Compatibility policy

`asterion-ccsds` supports CPython 3.12, 3.13, and 3.14 and is tested against all
three versions in CI. Other Python implementations may work but are not currently
part of the compatibility guarantee.

The package is still version `0.1.0`. Until 1.0, public APIs exported from
`asterion.ccsds` may change between minor releases when needed to correct
standards conformance or establish a sustainable interface. Changes will be
recorded in the changelog and avoid needless breakage.

Modules and names beginning with an underscore are private. The distribution is
marked with `py.typed`; public annotations are part of the supported developer
experience.
