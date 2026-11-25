"""Portfolio Manager: an LLM plans weekly, code checks the plan and runs it on a paper portfolio (see note 11).

    mandate.py      what the PM may change (computed from config in one place)
    schema.py       plan format (JSON schema) + business-rule checks
    context.py      assembles everything the PM gets to read; 0 tokens
    runner.py       is a plan due? -> call the model -> check -> store -> notify
    plans.py        plan store (data/plans) + Obsidian pages (Journal/Plans)
    portfolio.py    simulates the llm_pm portfolio against the control at the close, with trading costs
    predictions.py  resolves predictions from prices automatically, plus calibration
    watch.py        watches strong news and price moves -> alerts -> replans at once when needed

The rule across the package: **the model writes the plan, the code holds the rules**. Every constraint lives in code,
because the model can argue with a prompt but cannot change code.
"""
