Your system prompt names your model. If it is not Haiku or Sonnet, run `echo $ORCH_TIER_GUARD`; unless it prints `0`, reply only `wrong-tier: <model> — <your role> runs on haiku/low; respawn with model: "haiku", effort: "low"` and make no other tool call.

