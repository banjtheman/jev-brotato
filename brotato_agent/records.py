"""Recover recorded model inputs for replay and later teacher labeling."""


def decision_inputs(record, default_instructions):
    options = record.get("model_options", record["candidates"])
    if not isinstance(options, dict) or set(options) != set(record["candidates"]):
        raise ValueError("Recorded model options must retain the legal action IDs")
    instructions = record.get("instructions", default_instructions)
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("Recorded decision instructions must be nonempty text")
    return record["decision_state"], options, instructions
