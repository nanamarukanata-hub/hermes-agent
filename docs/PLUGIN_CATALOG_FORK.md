# Plugin Catalog — this Hermes fork

Copied the official upstream `plugin-catalog/` registry from NousResearch/hermes-agent,
including all YAML entries, exact reviewed commit pins, and `removed.yaml`.

Commands in this fork:

```sh
hermes plugins browse
hermes plugins search music
hermes plugins info blender
hermes plugins install NAME --no-enable
hermes plugins enable NAME
hermes plugins update NAME   # re-pin to current catalog-reviewed SHA
```

The compatibility installer checks out the catalog's exact SHA, keeps a provenance
record and applies the removal blocklist. It rejects unsupported modern plugin
types: this fork's loader supports traditional Python directory plugins only.
For the full current Desktop/portable/catalog experience, upgrade to a recent
Hermes runtime. This repository snapshot is refreshed only when deliberately
updated; it does not automatically import every upstream catalog change.

**Catalog data ≠ all plugins installed or enabled.** No third-party code was
downloaded/installed on a running Hermes host by this repository change.
Some entries require API keys or conflict (e.g. provider single-choice plugins).
Review each entry's capabilities before enabling.
