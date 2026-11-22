# Codex AI E3D 2.1 PML plugin

`CodexAiDebug.pmlfrm` is a read-only PML form used to exercise the AI bridge.
It displays the bridge target and the current E3D element, then refreshes those
values from the active Design session.

Load it in E3D with:

```text
pml rehash all
pml load form !!codexAiDebug
show !!codexAiDebug centred
```
