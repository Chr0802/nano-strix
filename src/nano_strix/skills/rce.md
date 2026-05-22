# Remote Code Execution Analysis Guide

## Detection
- Check for eval, exec, compile usage with user input
- Look for os.system, subprocess with shell=True
- Identify pickle/deserialization of user input
- Check template injection (SSTI)

## Common Patterns
- eval(user_input)
- os.system(f"ping {user_host}")
- pickle.loads(user_data)
- subprocess.run(user_cmd, shell=True)

## Recommendations
- Never eval user input
- Use subprocess with shell=False and argument lists
- Use safe serialization (JSON instead of pickle)
