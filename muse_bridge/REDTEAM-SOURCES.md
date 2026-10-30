# Muse VM isolated LLM adversarial-research sources

This **source checkout** covers ten known upstream research/evaluation projects:

| Local name | Upstream | Focus |
|---|---|---|
| garak | NVIDIA/garak | LLM vulnerability probing |
| pyrit | microsoft/PyRIT | Automated red team framework |
| promptfoo | promptfoo/promptfoo | AI application security evaluation |
| jailbreakbench | JailbreakBench/jailbreakbench | Robustness benchmark |
| harmbench | centerforaisafety/HarmBench | Multi-method attack/defense evaluation |
| agentdojo | ethz-spylab/agentdojo | Prompt injection against agents |
| llm-attacks | llm-attacks/llm-attacks | GCG adversarial input research |
| pair | patrickrchao/JailbreakingLLMs | PAIR research |
| tap | RICommunity/TAP | TAP research |
| autodan | SheltonLiu-N/AutoDAN | Automated adversarial prompts |

Each source repository is **cloned only**; no dependencies are
installed, no code or attacks are run, and no Muse or Hermes settings
are changed.

## On the authorized Muse VM

From the reviewed Hermes checkout:

```sh
python3 -m muse_bridge.redteam_sources
python3 -m unittest discover -s tests -p 'test_muse_redteam_sources.py' -v
python3 -m muse_bridge.redteam_sources --fetch
```

Target: `~/workspace/muse-redteam-sources` (private directory 0700).
Report: `~/workspace/muse-redteam-sources/checkout-report.json` (0600).
All fetched HEAD commit hashes are recorded for provenance. Failures are
reported individually; existing folders are not overwritten or updated.

## Operational boundaries

Separate Python and Node dependencies per project, and keep evaluation
data and reports outside persistent Hermes/Muse configuration. Evaluate
only against an explicitly authorized test target with its own test
credentials. Nothing in these upstream repositories grants access to
hosted Muse safety controls or the OpenAI system prompt.

No shell command is triggered merely by receiving the Bridge work order.
The Muse VM owner or authorized local agent must perform the checkout
command explicitly. This is deliberate: the Courier does not have a
generic shell execution capability.
