#!/usr/bin/env python3
"""
test_tool.py - Test if model returns tool JSON AND execute it
"""
import sys
sys.path.insert(0, '.')
import api, config, skill_runner
from api import new_msg

cfg   = config.load()
model = cfg.get("defaultModel", "gemini-2.5-flash")

history = [
    new_msg("system", skill_runner.build_system_prompt()),
    new_msg("user",   "Navigate to https://www.google.com"),
]

print(f"Model  : {model}")
print(f"Prompt : Navigate to https://www.google.com")
print()

reply = api.send(cfg, history, model, "disable")
print(f"Raw reply:\n{reply}\n")

tool_call = skill_runner.parse_tool_call(reply)
if tool_call:
    print(f"Parsed tool call:")
    print(f"  tool : {tool_call['tool']}")
    print(f"  args : {tool_call['args']}")
    print()
    print("Executing...")
    result = skill_runner.run_tool(tool_call)
    print(f"Result: {result}")
else:
    print("No tool call detected - model replied in plain text")
    print("Try running: python main.py --skills")
