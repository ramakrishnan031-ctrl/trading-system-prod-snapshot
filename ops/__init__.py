"""Operations tooling (Control Tower).

Explicit package marker. `ops` worked as an implicit namespace package, but every other
package in this repo is a regular one — the odd-one-out invites the tooling
inconsistencies (editable installs, some linters, packaging) that namespace packages are
famous for. No runtime behaviour depends on this file.
"""
