"""Core records (ADR-027): every core-facing business and port record.

Modules form an acyclic import graph (migration plan, section 3). Import from the modules inside this
package; it re-exports nothing, so every record has exactly one import path.
"""
