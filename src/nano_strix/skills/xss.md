# Cross-Site Scripting (XSS) Analysis Guide

## Detection
- Check for unsanitized user input rendered in HTML templates
- Look for innerHTML, document.write, eval usage
- Identify missing Content-Security-Policy headers

## Common Patterns
- template.render(user_input=request.args.get('q'))
- return f"<div>{user_name}</div>"
- <script>var data = {{ user_json | safe }};</script>

## Recommendations
- Always escape output (HTML entity encoding)
- Use template auto-escaping
- Set Content-Security-Policy headers
